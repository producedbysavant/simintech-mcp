"""Проверить, что git-пины из pyproject.toml существуют в origin.

Зачем: закрепление зависимости по SHA защищает от подмены тега, но не от
исчезновения самого коммита. Историю `simintech-code` уже пересоздавали, и
`simintech-mcp` уехал в `main` с мёртвым пином: CI падал не на тестах, а на
`pip install`, сообщением `upload-pack: not our ref` — по которому не видно,
что чинить. Здесь это ловится ДО установки и называется человеческим текстом.

Проверка выполняется тем же действием, что и у pip (`git fetch --depth=1 <url>
<sha>`), поэтому её результат совпадает с тем, что увидит установка.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Callable, List, Tuple

#: Пин в строке зависимости: имя, git-URL и ревизия после последней `@`.
PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*@\s*git\+(?P<url>https://[^@\s]+)@(?P<rev>\S+)$"
)

#: Ревизия, которую нельзя передвинуть: полный SHA-1.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class CheckPinsError(Exception):
    """Пин разобран, но не является immutable-коммитом."""


def git_pins(pyproject: Path) -> List[Tuple[str, str, str]]:
    """Пины вида ``name @ git+https://…@<sha>`` из `project.dependencies`.

    Raises:
        CheckPinsError: ревизия в пине — не 40-символьный SHA. Молча пропустить
            её нельзя: именно такой пин и оказался мёртвым.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    pins: List[Tuple[str, str, str]] = []
    for spec in data.get("project", {}).get("dependencies", []):
        match = PIN_RE.match(spec.strip())
        if not match:
            continue
        rev = match["rev"]
        if not SHA_RE.match(rev):
            raise CheckPinsError(
                f"{match['name']}: ревизия «{rev}» — не коммит. "
                f"Закрепляйте полный SHA: тег или ветку можно передвинуть."
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
    failed = False
    for name, url, sha in pins:
        if pin_reachable(url, sha, run=run):
            print(f"OK   {name} @ {sha[:12]} ({url})")
        else:
            failed = True
            print(
                f"МЁРТВЫЙ ПИН: {name} @ {sha} не найден в {url}. "
                f"Обновите pin в pyproject.toml на существующий коммит."
            )
    return 1 if failed else 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.exit(main(root / "pyproject.toml"))
