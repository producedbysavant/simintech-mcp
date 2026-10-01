"""Проверить, что git-пины из pyproject.toml существуют в origin.

Зачем: закрепление зависимости по ревизии защищает от незаметной подмены, но
не от исчезновения самой ревизии. Историю `simintech-code` уже пересоздавали,
и `simintech-mcp` уехал в `main` с мёртвым пином: CI падал не на тестах, а на
`pip install`, сообщением `upload-pack: not our ref` — по которому не видно,
что чинить. Здесь это ловится ДО установки и называется человеческим текстом.

Ревизия — полный SHA-1 или релизный тег `vX.Y.Z`. Ветки запрещены: их двигают
штатно. Теги `v*` двигать тоже нельзя — в репозитории стоит ruleset
`protect-release-tags` (удаление и перезапись запрещены, bypass-акторов нет),
поэтому читаемый тег не слабее SHA. Для тега проверка — `git ls-remote`
(объекты не скачиваются), плюс печать коммита, в который тег разрешается.

Для тега проверяется и содержимое: `__version__` в разрешённом коммите обязан
совпадать с именем тега — иначе повторяется ловушка v0.2.0 (тег стоял на коде
с другой версией), и её не ловят ни ruleset, ни якорь: якорь пишется с того же
разрешения тега и подтвердит любой коммит.

Проверка SHA выполняется тем же действием, что и у pip (`git fetch --depth=1
<url> <sha>`), поэтому её результат совпадает с тем, что увидит установка.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

#: Пин в строке зависимости: имя, git-URL и ревизия после последней `@`.
PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*@\s*git\+(?P<url>https://[^@\s]+)@(?P<rev>\S+)$"
)

#: Ревизия, которую нельзя передвинуть: полный SHA-1.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Релизный тег: строго `vX.Y.Z`. Произвольный тег и ветку не принимаем —
#: их двигают штатной записью, а `v*` прикрыт ruleset'ом репозитория.
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

#: Комментарий-якорь у тег-пина: `# sha: <40 hex>` — коммит выпуска.
#: Для тега якорь обязателен: `check_pins` сверяет, во что тег разрешается
#: **сейчас**, с записанным коммитом и отказывает на расхождении. Ruleset
#: репозитория запрещает перемещение `v*`, но проверка не должна полагаться
#: только на настройки репозитория: переставленный тег — это подмена кода.
#: Хвост после коммита допускается (`— выпуск …`): здешний стиль комментариев
#: поясняет через тире, и якорь не должен ломаться из-за пояснения.
SHA_NOTE_RE = re.compile(r"^#\s*sha:\s*(?P<sha>[0-9a-f]{40})\b")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class CheckPinsError(Exception):
    """Пин разобран, но ревизия не годится — либо проверку выполнить не вышло.

    Два случая: ревизия не SHA и не релизный тег `vX.Y.Z`; либо тег-пин не
    удалось проверить (сбой транспорта у `git ls-remote`) — второе наружу
    выходит тем же исключением, чтобы сбой связи не выдавался за отсутствие
    тега.
    """


def git_pins(pyproject: Path) -> List[Tuple[str, str, str]]:
    """Пины вида ``name @ git+https://…@<sha|vX.Y.Z>`` из `project.dependencies`.

    Raises:
        CheckPinsError: ревизия — ни полный SHA, ни релизный тег. Молча
            пропустить её нельзя: ветку двигают штатной записью, и пин
            разрешится в другой код без единой правки здесь.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    pins: List[Tuple[str, str, str]] = []
    for spec in data.get("project", {}).get("dependencies", []):
        match = PIN_RE.match(spec.strip())
        if not match:
            continue
        rev = match["rev"]
        if not (SHA_RE.match(rev) or TAG_RE.match(rev)):
            raise CheckPinsError(
                f"{match['name']}: ревизия «{rev}» — ни полный SHA, ни "
                f"релизный тег `vX.Y.Z`. Ветку и произвольный тег можно "
                f"передвинуть — закрепите коммит или выпуск."
            )
        pins.append((match["name"], match["url"], rev))
    return pins


def pin_reachable(url: str, sha: str, *, run: Run = subprocess.run) -> bool:
    """Отдаёт ли сервер объект по этому SHA — то же, что делает pip.

    Временный каталог сначала инициализируется: `git fetch` **вне
    репозитория** падает, и без `init` функция возвращала бы False на любом
    пине — то есть гейт был бы вечно красным, а такой гейт отключают.
    Дефект реальный: первый прогон скрипта объявил мёртвым живой SHA.
    """
    with tempfile.TemporaryDirectory() as tmp:
        run(["git", "init", "--quiet"], cwd=tmp, capture_output=True, text=True)
        result = run(
            ["git", "fetch", "--depth=1", "--quiet", url, sha],
            cwd=tmp, capture_output=True, text=True,
        )
    return result.returncode == 0


def expected_shas(pyproject: Path) -> Dict[str, str]:
    """Ожидаемые коммиты тег-пинов из комментариев `# sha: …`.

    Комментарий ставится строкой непосредственно над строкой зависимости
    (внутри того же блока комментариев), например:

        # v0.10.1 — выпуск …
        # sha: ab8ad85408b3e0e128176e1fa75fb7782709166c — коммит выпуска
        "simintech-api @ git+…@v0.10.1",

    Returns:
        {(имя пина, тег): коммит} — своя запись каждому тег-пину: одинаковые
        теги у разных зависимостей не перекрывают друг друга. Пин без якоря
        в словарь не попадает; его отсутствие `main` объявляет отказом (для
        тега якорь обязателен).
    """
    expected: Dict[str, str] = {}
    pending: Optional[str] = None
    for raw in pyproject.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        note = SHA_NOTE_RE.match(line)
        if note:
            pending = note["sha"]
            continue
        if line.startswith("#"):
            continue
        candidate = line.rstrip(",").strip().strip('"\'')
        pin = PIN_RE.match(candidate)
        if pin and TAG_RE.match(pin["rev"]) and pending is not None:
            expected[(pin["name"], pin["rev"])] = pending
        pending = None
    return expected


def resolve_tag(url: str, tag: str, *, run: Run = subprocess.run) -> Optional[str]:
    """Коммит, в который разрешается релизный тег в origin; None — тега нет.

    `git ls-remote` объекты не скачивает — для вопроса «существует ли выпуск»
    этого достаточно; неизменяемость даёт ruleset `protect-release-tags`
    (удаление и перезапись `v*` запрещены), а не сам факт тега. Для
    аннотированного тега возвращается коммит (строка `^{}`), а не
    идентификатор tag-объекта: пину и читателю нужен код, а не обёртка.

    Обе ссылки — сам тег и его `^{}`-разрешение — запрашиваются **явно**: с
    одним паттерном `git ls-remote` отдаёт только tag-объект, без строки
    `^{}` (живой прогон 01.10.2026 — резолвер вернул id обёртки, и гейт
    объявил переставленным только что поставленный тег).
    """
    result = run(
        ["git", "ls-remote", url,
         f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Сбой транспорта — не «тега нет»: иначе гейт назовёт не ту причину
        # и пошлёт проверять выпуск, который на месте.
        raise CheckPinsError(
            f"не удалось спросить origin ({url}): git ls-remote ответил кодом "
            f"{result.returncode} — это сбой связи или доступа, а не "
            f"отсутствие тега.")
    direct = peeled = None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        if parts[1] == f"refs/tags/{tag}":
            direct = parts[0]
        elif parts[1] == f"refs/tags/{tag}^{{}}":
            peeled = parts[0]
    return peeled or direct


#: Файл версии библиотеки — тот же путь, что читает hatch в simintech-code
#: (`[tool.hatch.version]`). Тег выпуска обязан указывать на код с той же
#: версией: иначе повторяется ловушка v0.2.0 (имя тега новее содержимого),
#: которую ни ruleset, ни якорь не ловят — якорь пишется с того же
#: разрешения тега и подтвердит любой коммит.
VERSION_FILE = "simintech_api/__init__.py"
VERSION_RE = re.compile(r'^__version__\s*=\s*"(?P<version>[^"]+)"', re.M)


def tag_content_version(url: str, commit: str, *,
                        run: Run = subprocess.run) -> Optional[str]:
    """`__version__` из коммита, на который разрешился тег; None — не прочитали.

    Шаг выполняется тем же действием, что и установка
    (`git fetch --depth=1 <url> <commit>`), затем файл версии читается из
    `FETCH_HEAD`. Сравнение с именем тега делает вызывающий — чтобы сообщение
    называло обе стороны. None означает «прочитать не удалось», а не
    «версии нет»: отсутствие `__version__` в файле возможно лишь при смене
    раскладки библиотеки, и это тоже лечится сообщением вызывающего.
    """
    with tempfile.TemporaryDirectory() as tmp:
        run(["git", "init", "--quiet"], cwd=tmp, capture_output=True, text=True)
        fetched = run(
            ["git", "fetch", "--depth=1", "--quiet", url, commit],
            cwd=tmp, capture_output=True, text=True,
        )
        if fetched.returncode != 0:
            return None
        shown = run(["git", "show", f"FETCH_HEAD:{VERSION_FILE}"],
                    cwd=tmp, capture_output=True, text=True)
    if shown.returncode != 0:
        return None
    found = VERSION_RE.search(shown.stdout)
    return found["version"] if found else None


def main(pyproject: Path, *, run: Run = subprocess.run) -> int:
    """0 — все пины достижимы; 1 — есть мёртвый или неразобранный пин."""
    try:
        pins = git_pins(pyproject)
    except CheckPinsError as exc:
        print(f"Пин невалиден: {exc}")
        return 1
    if not pins:
        print("Git-пинов нет — проверять нечего.")
        return 0
    notes = expected_shas(pyproject)
    failed = False
    for name, url, rev in pins:
        if SHA_RE.match(rev):
            if pin_reachable(url, rev, run=run):
                print(f"OK   {name} @ {rev[:12]} ({url})")
                continue
            failed = True
            print(
                f"МЁРТВЫЙ ПИН: {name} @ {rev} не найден в {url}. "
                f"Обновите pin в pyproject.toml на существующий коммит."
            )
            continue
        expected = notes.get((name, rev))
        if expected is None:
            failed = True
            print(
                f"ПИН БЕЗ ЯКОРЯ: тег {rev} — над строкой зависимости нужен "
                f"комментарий «# sha: <коммит выпуска>»: без него перестановка "
                f"тега неотличима от выпуска."
            )
            continue
        try:
            commit = resolve_tag(url, rev, run=run)
        except CheckPinsError as exc:
            failed = True
            print(f"ПИН НЕ ПРОВЕРЕН: {name} @ {rev} — {exc}")
            continue
        if commit is None:
            failed = True
            print(
                f"МЁРТВЫЙ ПИН: релизный тег {rev} не найден в {url}. Проверьте, "
                f"что выпуск состоялся и тег поставлен на коммит слияния."
            )
            continue
        if commit != expected:
            failed = True
            print(
                f"ЯКОРЬ НЕ СОВПАЛ: {name} @ {rev} разрешается в {commit[:12]}, "
                f"а в якоре записан {expected[:12]} ({url}). Либо тег указывает "
                f"на другой коммит (подмена), либо якорь не обновили при "
                f"выпуске — сверьте выпуск до установки."
            )
            continue
        version = tag_content_version(url, commit, run=run)
        if version is None:
            failed = True
            print(
                f"ПИН НЕ ПРОВЕРЕН ПО СОДЕРЖИМОМУ: коммит {commit[:12]} тега "
                f"{rev} не удалось прочитать в {url} — проверьте связь и "
                f"повторите; до этого пин не подтверждён."
            )
            continue
        if version != rev.lstrip("v"):
            failed = True
            print(
                f"ТЕГ УКАЗЫВАЕТ НЕ НА ТОТ КОММИТ: {rev} разрешается в "
                f"{commit[:12]}, но `__version__` в нём «{version}» — это "
                f"ловушка v0.2.0 (имя тега новее содержимого)."
            )
            continue
        print(f"OK   {name} @ {rev} -> {commit[:12]} (версия {version})")
    return 1 if failed else 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.exit(main(root / "pyproject.toml"))
