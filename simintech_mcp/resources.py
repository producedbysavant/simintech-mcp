"""Ресурсы MCP: состояние, блоки проекта, каталог блоков, скиллы и язык.

Ресурс — не место для исключений: `simintech://status` отдаёт причину отказа
текстом, тогда как одноимённый инструмент отказывает (`isError`).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastmcp.exceptions import ToolError
from simintech_api import language as language_api
from simintech_api.constants import SUPPORTED_COM_BLOCK_CLASSES

from . import catalog, sandbox, skills
from .app import mcp
from .tools import blocks as blocks_tools
from .tools import help as help_tools
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
    lines: list[str] = []
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
        return skills.skills_missing_message()
    entries = skills.list_skills(root)
    if not entries:
        return (f"В каталоге «{root}» скиллов нет: нужны подкаталоги с файлом "
                f"{skills.SKILL_FILE}.")
    lines: list[str] = []
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
        return skills.skills_missing_message()
    if not skills.SKILL_NAME_RE.match(name):
        return (f"ERROR: недопустимое имя скилла «{name}»: разрешены строчные "
                f"латинские буквы, цифры и дефис")
    path = Path(root) / name / skills.SKILL_FILE
    if not path.is_file():
        return f"ERROR: скилла «{name}» нет в «{root}»"
    try:
        # Чтение ограничено ЗАРАНЕЕ, как и у файлов результатов: проверка
        # размера после чтения защитой не является — файл уже в памяти.
        raw, truncated = sandbox.read_bounded(str(path), skills.MAX_SKILL_BYTES)
    except OSError as exc:
        return f"ERROR: {exc}"
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        return text + f"\n... (скилл обрезан: больше {skills.MAX_SKILL_BYTES} байт)"
    return text


# ─── Реестр функций встроенного языка ─────────────────────────────

@mcp.resource("simintech://language/functions")
def resource_language_functions() -> str:
    """Реестр функций встроенного языка: объём, разделы и как искать.

    Источник — `simintech_api/data/language_functions.json`, собранный из
    справки поставки. Схема 2 реестра несёт **синтаксис и аргументы**, а не
    только имя с назначением: по нему функцию можно позвать, не открывая
    справку. Разделы «Графические и системные» (объекты, порты блоков и
    линии связи) — замена отсутствующих COM-методов: правка модели живёт
    только в языке.

    Форму текста собирает `tools.help.format_language_registry` — тот же, что
    у инструмента с пустым запросом: два текста об одном реестре разошлись
    бы при первой же правке.
    """
    try:
        functions = language_api.language_functions()
        meta = language_api.registry_meta()
    except OSError as exc:
        return f"ERROR: реестр функций языка недоступен: {exc}"
    return help_tools.format_language_registry(functions, meta)


@mcp.resource("simintech://language/functions/{name}")
def resource_language_function(name: str) -> str:
    """Карточка функции: синтаксис, аргументы, назначение.

    Отвечает и на «есть ли такое имя»: имени нет — это ответ, а не отказ
    чтения (ресурс не место для исключений). Форму карточки собирает
    `tools.help.format_language_function` — та же, что у инструмента
    `get_language_function`.
    """
    try:
        found = language_api.find_function(name)
    except OSError as exc:
        return f"ERROR: реестр функций языка недоступен: {exc}"
    if found is None:
        return (f"Функции «{name}» нет в реестре встроенного языка SimInTech — "
                f"правдоподобное имя в языке может отсутствовать. Полный "
                f"поиск — инструмент search_language_functions.")
    return help_tools.format_language_function(found)


@mcp.resource("simintech://model/checklist")
def resource_model_checklist() -> str:
    """Чек-лист «Оформление модели» — что агент проверяет сам после сборки.

    Внутренние стандарты команды (issue #24): результат сборки показывают
    владельцу не раньше, чем он пройдёт этот список. Машинная часть —
    `check_model_layout`; пункты без машинной проверки смотрят глазами в GUI,
    и здесь это названо, чтобы список не читался как «всё проверено».
    """
    return (
        "Чек-лист «Оформление модели» (issue #24)\n"
        "\n"
        "После сборки, импорта или правок — до показа владельцу:\n"
        "\n"
        "1. Логика модели читается слева направо.\n"
        "2. Линии связи ортогональны, без диагоналей: после любых перемещений\n"
        "   вызовите `layout_place` без аргументов — он расставит все блоки\n"
        "   страницы и проложит линии (перемещение → repaint → NormalizeWire\n"
        "   делает он сам; порты он выравнивает по связям, запомненным\n"
        "   `connect`, и ответ скажет, если учтённых связей не было).\n"
        "3. Блоки не накладываются друг на друга — у соседей есть свободный\n"
        "   буфер.\n"
        "4. Порт-блоки: высота строго 16 px на строку сигнала (2 сигнала —\n"
        "   32 px); `set_block_size` соблюдает правило сам и отвергает чужую\n"
        "   высоту.\n"
        "5. Подписи не выходят за рамку блока: длинное имя сигнала шире\n"
        "   порт-блока — `fit_port_blocks` расширит порт-блоки по подписям,\n"
        "   а подписи значений соберёт к своим блокам `fit_value_labels`.\n"
        "6. Пустых портов нет: пустой вход молча останавливает расчёт всей\n"
        "   модели.\n"
        "7. В субмодели порядок как на уровне mdl: входы → логика → выходы.\n"
        "8. Повторные импорты и точечные правки не двигают расставленные\n"
        "   блоки, но после них проверьте пункт 2 ещё раз.\n"
        "9. Разметка: квадратик 8×8; между блоками по вертикали 8…32 px;\n"
        "   порт-блоки — стопкой вплотную (layout_place делает это сам).\n"
        "\n"
        "Машинно проверяют: `check_model_layout` — наложения габаритов,\n"
        "подписи шире рамки, центры по разметке 8 px, пустые порты, связи\n"
        "«прямая / с изломом»; `audit_routing` — пересечения предсказанных\n"
        "маршрутов, общий трек с перекрытием, попадание в чужой габарит,\n"
        "порядок входов. Оба идут контуром и сдвигают модельное время — как\n"
        "`step`. `layout_place` в ответе даёт метрики маршрутов по связям,\n"
        "чьи концы известны: реестр `connect`, а у открытого проекта —\n"
        "прямые пары из выгрузки (число известных и полное — в самой\n"
        "строке); за все линии страницы отвечает `audit_routing`.\n"
        "\n"
        "Чего машинной проверки пока нет (смотрите в GUI): изломы по\n"
        "промежуточным точкам линий.\n"
    )
