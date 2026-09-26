"""Ресурсы MCP: состояние, блоки проекта, каталог блоков, скиллы и язык.

Ресурс — не место для исключений: `simintech://status` отдаёт причину отказа
текстом, тогда как одноимённый инструмент отказывает (`isError`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from fastmcp.exceptions import ToolError
from simintech_api import language as language_api
from simintech_api.constants import SUPPORTED_COM_BLOCK_CLASSES

from . import catalog, sandbox, skills
from .app import mcp
from .tools import blocks as blocks_tools
from .tools import project as project_tools


# ─── Ресурсы (read-only) ──────────────────────────────────────────

#: Сколько классов перечисляет `simintech://blocks/catalog`. Полный каталог —
#: около 958 классов и 130 тыс. знаков (≈40k токенов): одно чтение такого
#: объёма в контекст не влезает, а усечение ответа клиентом отрезало бы ровно
#: те классы, ради которых ресурс существует. Поэтому рабочие классы идут
#: первыми и показываются целиком, а список остальных ограничен.
MAX_CATALOG_CLASSES = 60


def _catalog_order(classes: List[str]) -> List[str]:
    """Порядок классов в ресурсе: рабочие первыми, остальные — по алфавиту.

    `BlockCatalog.classes()` сортирует по коду символа, поэтому кириллица
    («Константа», «Сумматор», «Усилитель») уходит после ASCII-классов
    Arduino/GD32F: из 13 рабочих классов в первые сто строк попадал один.
    Порядок задаётся здесь, а не в библиотеке: сортировка каталога — его
    свойство, и менять общий для всех код из-за одного ресурса нельзя.
    """
    supported = set(classes) & SUPPORTED_COM_BLOCK_CLASSES
    return sorted(supported) + sorted(set(classes) - SUPPORTED_COM_BLOCK_CLASSES)


@mcp.resource("simintech://status")
def resource_status() -> str:
    """Статус COM-сервера SimInTech (аналог инструмента status).

    Инструмент при недоступном COM отказывает (это и есть отказ), а ресурс —
    только читаемое представление, поэтому причину возвращает текстом:
    исключение при чтении ресурса клиенту ничего не объясняет.
    """
    try:
        return project_tools.status()
    except ToolError as exc:
        return f"SimInTech недоступен: {exc}"


@mcp.resource("simintech://project/blocks")
def resource_project_blocks() -> str:
    """Список блоков текущего проекта (read-only представление)."""
    try:
        return blocks_tools.list_blocks()
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.resource("simintech://blocks/catalog")
def resource_blocks_catalog() -> str:
    """Каталог блоков: классы, их параметры и вычисляемые имена.

    Источник — `simintech_api/data/block_catalog.json` из `simintech-code`.
    Агенту он нужен, чтобы не угадывать имена параметров: имена короткие и
    различаются по классам (у «Константы» — `a`, а не `y0`), а запись в
    неизвестное имя COM принимает молча.

    Каталог большой (около 958 классов), поэтому перечисляются не все:
    сначала классы, с которыми сервер умеет работать, затем первые
    `MAX_CATALOG_CLASSES` — об остальных сказано, как их прочитать.
    """
    cat = catalog.load_default_catalog()
    classes = cat.classes()
    if not classes:
        return ("Каталог блоков пуст: `simintech_api/data/block_catalog.json` "
                "не найден. Генерируется командой `simintech-generate-catalog` "
                "(Windows, mmain.exe /regserver).")
    ordered = _catalog_order(classes)
    shown = ordered[:MAX_CATALOG_CLASSES]
    lines = []
    for cls in shown:
        props = ", ".join(cat.props_for(cls))
        readonly = cat.readonly_for(cls)
        tail = f" [вычисляемые, задавать нельзя: {', '.join(readonly)}]" \
            if readonly else ""
        lines.append(f"  {cls}: {props}{tail}")
    text = f"Каталог блоков ({len(classes)} классов):\n" + "\n".join(lines)
    if len(ordered) > len(shown):
        text += (f"\n... Показаны первые {len(shown)} классов из "
                 f"{len(classes)}; параметры класса, которого здесь нет, "
                 f"читает get_block_params(block=…) по блоку в проекте "
                 f"(имя блока даёт list_blocks).")
    return text


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


# ─── Реестр функций встроенного языка ─────────────────────────────

@mcp.resource("simintech://language/functions")
def resource_language_functions() -> str:
    """Реестр функций встроенного языка: сколько их и как проверить имя.

    Источник — `simintech_api/data/language_functions.json`, собранный из
    справки поставки. Реестр знает **имя, категорию и назначение**, но не
    сигнатуры: существование имени по нему проверить можно, состав и порядок
    аргументов — нет (за ними в справку `help.simintech.ru`). Это важно
    потому, что знаниевый контент описывает около 5% имён, а правдоподобное
    имя функции в языке может отсутствовать.

    Существование конкретного имени проверяет
    `simintech://language/functions/<имя>`.
    """
    try:
        functions = language_api.language_functions()
        meta = language_api.registry_meta()
    except OSError as exc:
        return f"ERROR: реестр функций языка недоступен: {exc}"
    unique = len({function.name for function in functions})
    counts: Dict[str, int] = {}
    for function in functions:
        counts[function.category] = counts.get(function.category, 0) + 1
    help_version = str(meta.get("help_version") or "")
    head = (f"Реестр функций встроенного языка SimInTech: {len(functions)} "
            f"записей, {unique} уникальных имён")
    if help_version:
        head += f" (справка поставки {help_version})"
    lines = [f"{head}."]
    for category, count in sorted(counts.items()):
        lines.append(f"  {category} — {count}")
    lines.append("Реестр даёт существование и назначение имени, но не "
                 "сигнатуры: состав и порядок аргументов смотрите в справке "
                 "(https://help.simintech.ru/, раздел языка).")
    lines.append("Проверить имя — ресурс simintech://language/functions/<имя>.")
    return "\n".join(lines)


@mcp.resource("simintech://language/functions/{name}")
def resource_language_function(name: str) -> str:
    """Существует ли функция встроенного языка, и что она делает.

    Реестр даёт **имя, категорию и назначение — без сигнатур**: ресурс
    отвечает, есть ли такое имя, но не как расположить аргументы (за этим —
    в справку поставки). Имени нет в реестре — это ответ, а не отказ чтения:
    ресурс не место для исключений.
    """
    try:
        found = language_api.find_function(name)
    except OSError as exc:
        return f"ERROR: реестр функций языка недоступен: {exc}"
    if found is None:
        return (f"Функции «{name}» нет в реестре встроенного языка SimInTech. "
                f"Реестр даёт существование и назначение имени, но не "
                f"сигнатуры: если имя кажется верным, проверьте его по "
                f"справке (https://help.simintech.ru/, раздел языка).")
    lines = [f"Функция «{found.name}»:",
             f"  категория: {found.full_category}"]
    if found.purpose:
        lines.append(f"  назначение: {found.purpose}")
    else:
        lines.append("  назначение: в справке не указано")
    if found.graphics_only:
        lines.append("  доступна только в графическом контейнере")
    lines.append("  сигнатуры в реестре нет: состав и порядок аргументов "
                 "смотрите в справке (https://help.simintech.ru/)")
    return "\n".join(lines)
