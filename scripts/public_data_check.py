"""DLP-гейт: приватные данные в публичном дереве.

Проверяются не секреты (это делает gitleaks по всей истории), а потенциально
приватные идентификаторы: адреса, телефоны, внутренние имена и пути, чужие
проектные файлы. Категории данных — в `DATA_POLICY.md`.

Правила намеренно узкие, а исключения стоят рядом с ними: гейт, который
краснеет на собственной документации, отключают — и вместе с ним исчезает
проверка. Поэтому публичные факты вендора (`simintech.ru`, `help.simintech.ru`,
`C:\\SimInTech64`) разрешены явно, а не «по недосмотру», и закреплены тестами.

Запуск: `python scripts/public_data_check.py` (из корня репозитория).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Pattern, Tuple

#: Разрешённые образцы: проверяются ДО правил и гасят находку в строке.
ALLOWED: Tuple[Pattern[str], ...] = (
    re.compile(r"help\.simintech\.ru"),
    re.compile(r"simintech\.ru"),
    re.compile(r"C:(\\{1,2})SimInTech64"),
    re.compile(r"example\.(com|org)"),
    re.compile(r"noreply@"),
    re.compile(r"C:\\Users\\(<[^>]+>|Public|Default|user|username|%USERNAME%)"),
    re.compile(r"CLAUDE\.local"),          # имя файла проекта, не хост
)

#: Зоны, где находка правила — заведомо синтетический пример, а не данные:
#: тесты разбора Windows-путей и документация о форматах (в исторических планах
#: примеры не переписываются). Список закрытый: он не отключает правило, а
#: фиксирует, что вхождения просмотрены при разборе. Префикс пути + правило
#: (`*` — любое правило зоны).
ALLOWLIST_PATHS: Tuple[Tuple[str, str], ...] = (
    ("scripts/public_data_check.py", "*"),           # определения правил
    ("tests/unit/test_public_data_check.py", "*"),   # их проверка
    ("tests/", "unc-path"),                          # тесты разбора сетевых путей
    # План по этому гейту: в нём примеры правил и разбор находок.
    ("docs/superpowers/plans/2026-09-26-ecosystem-hardening.md", "*"),
    # Исторический план: синтетический пример UNC-пути в описании отказа.
    ("docs/superpowers/plans/2026-09-21-script-bridge.md", "unc-path"),
)

RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    (
        "private-ip",
        re.compile(
            r"\b(10\.\d{1,3}|172\.(1[6-9]|2\d|3[01])|192\.168)"
            r"\.\d{1,3}\.\d{1,3}\b"
        ),
    ),
    # `\\.\pipe\…` — пространство имён устройств Windows (именованные каналы),
    # а не сетевой ресурс: учётные данные там не живут, и находка была ложной.
    ("unc-path", re.compile(r"\\\\(?!\.\\)[A-Za-z0-9._-]+\\[A-Za-z0-9._$-]+")),
    ("user-profile", re.compile(r"[A-Za-z]:\\Users\\[^\\<>%\s]+")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "phone-ru",
        re.compile(r"\+7[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b"),
    ),
    ("internal-host", re.compile(r"\b[a-z0-9-]+\.(local|corp|internal|lan)\b", re.I)),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
#: Текстового правила «путь к .prt/.xprt — это дамп» здесь НЕТ, и это решение,
#: а не пропуск. Замер на дереве `simintech-code` (2026-09-26): оно дало 43
#: находки из 62, и все — синтетические примеры в докстрингах и документации о
#: форматах («сохранённый проект `.xprt` разбирается без COM»). Отличить пример
#: от реального пути по тексту нечем, а правило с таким шумом отключают — и
#: вместе с ним теряется настоящая проверка. Реальные дампы ловит проверка
#: файлов (`FORBIDDEN_SUFFIXES`), а путь из чужого окружения — `user-profile`.

#: Расширения файлов, которых в публичном дереве быть не должно.
FORBIDDEN_SUFFIXES = {".prt", ".xprt", ".sdb", ".db", ".saraface", ".pak", ".tbl"}

#: Каталог легитимных вендорских фикстур: исключение по пути, а не по гейту.
FIXTURE_DIR = "tests/fixtures/vendor-public/"

#: Каталоги, которые не обходятся.
SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "build", "dist", ".venv",
}

#: Двоичное и сжатое: читать текстом нечего.
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz"}


@dataclass
class Findings:
    """Находки: путь, строка, правило, фрагмент."""

    items: List[Tuple[str, int, str, str]] = field(default_factory=list)

    def add(self, path: str, line: int, rule: str, fragment: str) -> None:
        self.items.append((path, line, rule, fragment.strip()[:80]))

    def __bool__(self) -> bool:
        return bool(self.items)

    def __str__(self) -> str:
        return "\n".join(
            f"{path}:{line}: [{rule}] {fragment}"
            for path, line, rule, fragment in self.items
        )


def _rule_allowed(path: str, rule: str) -> bool:
    """Зона, в которой это правило не применяется (см. ALLOWLIST_PATHS)."""
    return any(
        path.startswith(prefix) and zone in ("*", rule)
        for prefix, zone in ALLOWLIST_PATHS
    )


def scan_text(text: str, *, path: str = "<text>") -> Findings:
    """Найти приватные маркеры в тексте (allowlist гасит находку в строке)."""
    findings = Findings()
    for number, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in ALLOWED):
            continue
        for rule, pattern in RULES:
            if _rule_allowed(path, rule):
                continue
            match = pattern.search(line)
            if match:
                findings.add(path, number, rule, match.group(0))
    return findings


def tracked_files(root: Path) -> Optional[List[Path]]:
    """Файлы под контролем git — то, что действительно публикуется.

    Гейт проверяет публикуемое, а не всё, что лежит в каталоге: рабочие копии
    инструкций агента (`CLAUDE.md`, `.claude/`, `.remember/`) в git не попадают
    и содержат домашние пути — находки на них были бы шумом, из-за которого
    гейт отключают. `None` — git недоступен (временный каталог в тесте): тогда
    дерево обходится целиком, как раньше.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [root / name for name in result.stdout.split("\0") if name]


def scan_tree(root: Path, *, files: Optional[Iterable[Path]] = None) -> Findings:
    """Пройти дерево: сначала расширения файлов, затем их содержимое."""
    findings = Findings()
    if files is None:
        files = tracked_files(root)
    if files is None:
        files = sorted(root.rglob("*"))
    for path in sorted(files):
        if not path.is_file():
            continue
        if SKIP_DIRS & set(path.parts) or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        relative = str(path.relative_to(root))
        if (path.suffix.lower() in FORBIDDEN_SUFFIXES
                and not relative.startswith(FIXTURE_DIR)):
            findings.add(relative, 0, "forbidden-file", path.name)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.items.extend(scan_text(text, path=relative).items)
    return findings


def main(root: Path) -> int:
    """0 — приватных маркеров нет; 1 — есть (печатает находки)."""
    findings = scan_tree(root)
    if findings:
        print(findings)
        print(f"\nНаходок: {len(findings.items)}. См. DATA_POLICY.md.")
        return 1
    print("Приватных маркеров не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parents[1]))
