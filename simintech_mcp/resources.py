"""Ресурсы MCP: состояние, блоки проекта, каталог блоков и скиллы.

Ресурс — не место для исключений: `simintech://status` отдаёт причину отказа
текстом, тогда как одноимённый инструмент отказывает (`isError`).
"""

from __future__ import annotations

from pathlib import Path

from fastmcp.exceptions import ToolError

from . import catalog, sandbox, skills
from .app import mcp
from .tools.blocks import list_blocks
from .tools.project import status


# ─── Ресурсы (read-only) ──────────────────────────────────────────

@mcp.resource("simintech://status")
def resource_status() -> str:
    """Статус COM-сервера SimInTech (аналог инструмента status).

    Инструмент при недоступном COM отказывает (это и есть отказ), а ресурс —
    только читаемое представление, поэтому причину возвращает текстом:
    исключение при чтении ресурса клиенту ничего не объясняет.
    """
    try:
        return status()
    except ToolError as exc:
        return f"SimInTech недоступен: {exc}"


@mcp.resource("simintech://project/blocks")
def resource_project_blocks() -> str:
    """Список блоков текущего проекта (read-only представление)."""
    try:
        return list_blocks()
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.resource("simintech://blocks/catalog")
def resource_blocks_catalog() -> str:
    """Каталог блоков: классы, их параметры и вычисляемые имена.

    Источник — `simintech_api/data/block_catalog.json` из `simintech-code`.
    Агенту он нужен, чтобы не угадывать имена параметров: имена короткие и
    различаются по классам (у «Константы» — `a`, а не `y0`), а запись в
    неизвестное имя COM принимает молча.
    """
    cat = catalog.load_default_catalog()
    classes = cat.classes()
    if not classes:
        return ("Каталог блоков пуст: `simintech_api/data/block_catalog.json` "
                "не найден. Генерируется командой `simintech-generate-catalog` "
                "(Windows, mmain.exe /regserver).")
    lines = []
    for cls in classes:
        props = ", ".join(cat.props_for(cls))
        readonly = cat.readonly_for(cls)
        tail = f" [вычисляемые, задавать нельзя: {', '.join(readonly)}]" \
            if readonly else ""
        lines.append(f"  {cls}: {props}{tail}")
    return f"Каталог блоков ({len(classes)} классов):\n" + "\n".join(lines)


@mcp.resource("simintech://skills")
def resource_skills() -> str:
    """Список скиллов: что агент может подгрузить про SimInTech.

    Скиллы живут в отдельном репозитории `simintech-skill`; сервер их только
    читает (искать в этом репозитории нечего).
    """
    root = skills.skills_root()
    if root is None:
        return skills._skills_missing_message()
    entries = skills._list_skills(root)
    if not entries:
        return (f"В каталоге «{root}» скиллов нет: нужны подкаталоги с файлом "
                f"{skills.SKILL_FILE}.")
    lines = []
    for name, summary in entries:
        if summary is None:
            # Ошибку чтения показываем явно: иначе она неотличима от скилла
            # без описания, и агент не поймёт, почему описания нет.
            lines.append(f"  {name} — (описание недоступно: ошибка чтения)")
        elif summary:
            lines.append(f"  {name} — {summary}")
        else:
            lines.append(f"  {name}")
    return (f"Скиллы ({root}):\n" + "\n".join(lines)
            + "\n\nСодержимое скилла — ресурс simintech://skills/<имя>.")


@mcp.resource("simintech://skills/{name}")
def resource_skill(name: str) -> str:
    """Текст скилла (`SKILL.md`) — инструкции по работе с SimInTech."""
    root = skills.skills_root()
    if root is None:
        return skills._skills_missing_message()
    if not skills._SKILL_NAME_RE.match(name):
        return (f"ERROR: недопустимое имя скилла «{name}»: разрешены строчные "
                f"латинские буквы, цифры и дефис")
    path = Path(root) / name / skills.SKILL_FILE
    if not path.is_file():
        return f"ERROR: скилла «{name}» нет в «{root}»"
    try:
        # Чтение ограничено ЗАРАНЕЕ, как и у файлов результатов: проверка
        # размера после чтения защитой не является — файл уже в памяти.
        raw, truncated = sandbox._read_bounded(str(path), skills.MAX_SKILL_BYTES)
    except OSError as exc:
        return f"ERROR: {exc}"
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        return text + f"\n... (скилл обрезан: больше {skills.MAX_SKILL_BYTES} байт)"
    return text
