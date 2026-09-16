"""Скиллы: инструкции агента читаются из `simintech-skill`, а не хранятся тут.

`SIMINTECH_SKILLS_DIR` указывает на `skills-catalog` репозитория
`simintech-skill`; без переменной ищется соседний checkout. Переменная, заданная
в несуществующий каталог, — ошибка конфигурации, а не повод молча взять другой
checkout: иначе читался бы не тот каталог, который назвали.

Имя скилла приходит от клиента и подставляется в путь, поэтому проверяется
шаблоном (`_SKILL_NAME_RE`) — `..` не проходит.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from . import sandbox


# ─── Скиллы (инструкции агента) ───────────────────────────────────

#: Переменная окружения: каталог скиллов — репозиторий `simintech-skill`,
#: подкаталог `skills-catalog`. При разработке подхватывается и соседний
#: checkout, но в установленном виде сервер видит только её.
SKILLS_DIR_ENV = "SIMINTECH_SKILLS_DIR"

#: Файл скилла внутри его каталога.
SKILL_FILE = "SKILL.md"

#: Скилл адресуется именем каталога: строчные буквы, цифры, дефис. Имя
#: приходит от клиента и подставляется в путь, поэтому «..» и разделители
#: сюда не проходят by design.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Предел объёма текста скилла, отдаваемого в контекст.
MAX_SKILL_BYTES = 64 * 1024


def skills_root() -> Optional[str]:
    """Каталог скиллов или None, если его нет.

    ``SIMINTECH_SKILLS_DIR`` — явный выбор пользователя, поэтому при заданной
    переменной берётся **только** она. Подставлять вместо несуществующего
    каталога соседний checkout нельзя: читался бы не тот каталог, который
    назвали (проверено — так и происходило, пока переменная указывала на
    опечатку, а рядом лежал рабочий checkout). Без переменной соседний
    checkout ``simintech-skill/skills-catalog`` ищется — это удобно при
    разработке, когда репозитории лежат рядом.
    """
    raw = os.environ.get(SKILLS_DIR_ENV)
    if raw:
        return raw if os.path.isdir(raw) else None
    here = Path(__file__).resolve()
    for base in list(here.parents)[:4]:
        candidate = base / "simintech-skill" / "skills-catalog"
        if candidate.is_dir():
            return str(candidate)
    return None


def _skills_missing_message() -> str:
    """Почему скиллов нет — с именем переменной, если она задана."""
    raw = os.environ.get(SKILLS_DIR_ENV)
    if raw:
        return (f"Каталог скиллов из {SKILLS_DIR_ENV}=«{raw}» не найден. "
                f"Укажите существующий каталог.")
    return (f"Скиллы не найдены. Задайте каталог репозитория simintech-skill "
            f"переменной {SKILLS_DIR_ENV} (например, "
            f".../simintech-skill/skills-catalog).")


def _skill_summary(path: Path) -> Optional[str]:
    """Краткое описание скилла — первая содержательная строка SKILL.md.

    `None` — файл не прочитался. Это не то же самое, что скилл без описания,
    и вызывающий обязан показать разницу: иначе ошибка ввода-вывода
    выглядела бы как отсутствие описания.
    """
    try:
        # Читается ограниченно — описание берётся из начала файла, а сам файл
        # может быть большим; предел тот же, что у тела скилла.
        data, _ = sandbox._read_bounded(str(path), MAX_SKILL_BYTES)
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    in_frontmatter = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        return line.lstrip("#").strip()[:200]
    return ""


def _list_skills(root: str) -> List[Tuple[str, Optional[str]]]:
    """Скиллы каталога: пары (имя, краткое описание или None при ошибке)."""
    result = []
    for entry in sorted(Path(root).iterdir()):
        if not entry.is_dir() or not _SKILL_NAME_RE.match(entry.name):
            continue
        skill_file = entry / SKILL_FILE
        if skill_file.is_file():
            result.append((entry.name, _skill_summary(skill_file)))
    return result
