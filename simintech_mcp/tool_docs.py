"""Ресурсы документации: полный каталог, карточка инструмента, руководства."""

from __future__ import annotations

import json

from . import surface
from .app import mcp
from .rules import (NPORT_BLOCK_WIDTH, NPORT_OTHER_CLASSES,
                    NPORT_OTHER_REASONS, NPORT_SIZE_CLASSES, PORT_MARGIN,
                    PORT_ROW_HEIGHT)


@mcp.resource("simintech://tools")
async def resource_tools() -> str:
    """Каталог всех инструментов: имя, группа, краткое описание."""
    tools = await surface.catalog_tools(mcp)
    lines = [
        "Инструменты simintech-mcp. Полное описание: "
        "simintech://tools/<name>.",
        "Видимое ядро и поиск: search_tools и call_tool.",
        "",
    ]
    for tool in sorted(tools, key=lambda item: item.name):
        short = surface.short_description(
            tool.name, tool.description or "", tool)
        first = short.split("\n\n", 1)[0]
        lines.append(f"  {tool.name}: {first}")
    return "\n".join(lines)


@mcp.resource("simintech://tools/{name}")
async def resource_tool(name: str) -> str:
    """Полный docstring, схема входа и аннотации инструмента."""
    tools = await surface.catalog_tools(mcp)
    by_name = {tool.name: tool for tool in tools}
    tool = by_name.get(name)
    if tool is None:
        return (f"Инструмента «{name}» нет. Полный список: "
                f"simintech://tools; поиск: search_tools.")
    annotations = tool.annotations.model_dump() if tool.annotations else {}
    schema = json.dumps(tool.parameters, ensure_ascii=False, indent=2)
    return (
        f"# {tool.name}\n"
        f"title: {tool.title or ''}\n"
        f"annotations: {json.dumps(annotations, ensure_ascii=False)}\n\n"
        f"{tool.description or ''}\n\n"
        f"input_schema:\n{schema}\n"
    )


@mcp.resource("simintech://guides/model-text")
def resource_model_text_guide() -> str:
    """Канон декларативного текста createmodel."""
    return (
        "Декларативный текст модели SimInTech для createmodel\n"
        "\n"
        "Форма: const model : ( Имя: (type = \"Класс\", points=[...], ...), "
        "... );\n"
        "В import_model_text передаются записи без внешней обёртки; выгрузка\n"
        "export_model_text принимается как есть.\n"
        "\n"
        "points: четыре точки блока: центр, правый порт, верх, подпись.\n"
        "Пример: points=[(0,0),(16,0),(0,-16),(0,32)].\n"
        "\n"
        "Провод: type = \"wire\", points=[], src = \"блок:out:N\", "
        "dst = \"блок:in:N\".\n"
        "Ветвь: src = \"имя_линии:K\", K с нуля.\n"
        "Субмодель: type = \"Субмодель\", вложенная секция subsystem: (...).\n"
        "Порт входа/выхода: portnames = \"X\"; несколько имён задаются\n"
        "конкатенацией через clrf: portnames = \"A\"+clrf+\"B\".\n"
        "По справке поставки порт-блоки допустимы внутри субмоделей; живой\n"
        "импорт сохранённого проекта создал и верхнеуровневые порты, но это\n"
        "не документированный путь.\n"
        "\n"
        "Кавычка внутри строки задаётся chr(34): удвоение кавычек в этой\n"
        "сборке даёт пустую строку. Последнее свойство записи пишется без\n"
        "запятой.\n"
        "\n"
        "Геометрия: верхнеуровневые провода в тексте получают маршрут, не\n"
        "совпадающий с фактическими портами. Создавайте их connect после\n"
        "импорта блоков; подробности: simintech://guides/wire-geometry.\n"
        "\n"
        "Число входов блоков с параметром nport в тексте задаёт nport = N:\n"
        "пины создаются при создании блока. Размер текст не задаёт: высоту\n"
        "по числу входов подгоняют build_model и format_model; правило:\n"
        "simintech://guides/block-sizing.\n"
    )


@mcp.resource("simintech://guides/model-ir")
def resource_model_ir_guide() -> str:
    """Как читать модель через Model IR, а не XML-снимком."""
    return (
        "Model IR: полное представление модели вне контекста агента\n"
        "\n"
        "load_model(path='', sources=['xprt'], strict=False) читает текущий\n"
        "проект снимком SaveProjectXML без запуска расчёта. path указывает\n"
        "файл в каталоге результатов: .xprt/.xml, .txt (savemodeltofile),\n"
        ".topology/.tsv. model_text и topology идут контуром и сдвигают\n"
        "модельное время.\n"
        "\n"
        "Ответ содержит model_ref, хеш, счётчики, покрытие и первые\n"
        "расхождения. XML, скрипты и полные значения свойств не возвращаются.\n"
        "\n"
        "get_model_manifest(model_ref, budget_tokens=3000, focus) отдаёт\n"
        "Markdown-паспорт 2000..5000 токенов с блоками Coverage, Structure,\n"
        "Classes, Signals, Hot nodes, Discrepancies и Omitted.\n"
        "\n"
        "query_model(model_ref, query, arguments) выполняет точный запрос:\n"
        "inspect_node, find_objects, trace_signal, ancestors, descendants,\n"
        "find_path, find_cycles, check_connections, compare_models,\n"
        "validate_invariants, list_discrepancies, coverage. Для\n"
        "compare_models в arguments нужен other_ref.\n"
        "\n"
        "Покрытие важнее ответа: source_status=partial, unresolved_ends,\n"
        "unknown и not_evaluated не означают пустое множество. Снимок живёт\n"
        "в памяти; после правки проекта или смены проекта старый model_ref\n"
        "устаревает, а вытесненный ref неизвестен. В обоих случаях нужен\n"
        "новый load_model.\n"
    )


@mcp.resource("simintech://guides/wire-geometry")
def resource_wire_geometry_guide() -> str:
    """Живой замер геометрии проводов и правило сборки."""
    return (
        "Геометрия проводов: живой замер 2026-10-18\n"
        "\n"
        "Минимальная модель:\n"
        "  A. блоки и провод одним текстом: points=[(132,0),(132,224)],\n"
        "     на снимке вертикальное и диагональное звено, модель не считает.\n"
        "  B. блоки текстом, провод connect, NormalizeWire: points=[], прямая.\n"
        "  D. блоки текстом, layout_place, провод текстом:\n"
        "     points=[(112,56),(112,76),(16,76),(16,96)], обход слева.\n"
        "  E. блоки текстом, сохранение и переоткрытие, провод текстом:\n"
        "     points=[], прямая (для простой модели).\n"
        "\n"
        "Сложная модель: 144 блока, 213 линий. После блоков, сохранения и\n"
        "переоткрытия провода текстом дали веер диагоналей из левого верхнего\n"
        "угла; в данных сегменты ортогональны, но концы маршрута не совпадают\n"
        "с фактическими портами, и отрисовщик соединяет порт с первой точкой.\n"
        "\n"
        "Правило: блоки и субмодели можно создавать createmodel; "
        "верхнеуровневые\n"
        "провода создаются connect по фактическим портам, затем layout_place.\n"
        "Провода внутри субмоделей остаются текстовыми и требуют снимка.\n"
    )


@mcp.resource("simintech://guides/block-sizing")
def resource_block_sizing_guide() -> str:
    """Размеры блоков с переменным числом входов: один канон правила.

    Числа и список классов берутся из `rules`, а не переписываются здесь:
    подгонка, проверка и руководство обязаны меняться одной правкой.
    Остальные агентские тексты ссылаются сюда, а не повторяют список.
    """
    classes = ", ".join(f"«{name}»" for name in NPORT_SIZE_CLASSES)
    other = "; ".join(
        f"«{name}»: "
        f"{NPORT_OTHER_REASONS.get(name, 'nport не число входных контактов')}"
        for name in NPORT_OTHER_CLASSES)
    return (
        "Размеры блоков с переменным числом входов\n"
        "\n"
        f"Правило: высота = {PORT_ROW_HEIGHT} px × число входных контактов;\n"
        f"входы с шагом {PORT_ROW_HEIGHT} px, первый на {PORT_MARGIN} px ниже\n"
        f"верхнего края, последний на {PORT_MARGIN} px выше нижнего; выход\n"
        "один, посередине правой стороны; ширина штатная\n"
        f"{NPORT_BLOCK_WIDTH} px, подгонка поднимает ширину до неё, но не\n"
        "сужает вручную расширенный блок; обе оси пишутся в «Значение», не в\n"
        "«Формулу».\n"
        "\n"
        f"Классы с nport: {classes}.\n"
        f"Оговорка: {other}; правило к ним не относится.\n"
        "\n"
        "Смена числа входов:\n"
        "  1. Основной путь: set_block_param(block, \"nport\", N). Инструмент\n"
        "     вызывает SetPortCount, сверяет фактические пины, ставит высоту\n"
        f"     {PORT_ROW_HEIGHT} × N и ширину не уже {NPORT_BLOCK_WIDTH},\n"
        "     нормализует линии и называет наложения. Проект должен быть\n"
        "     остановлен; пины не пересобрались - отказ без записи размера.\n"
        "  2. import_model_text с nport = N: пины создаются при создании\n"
        "     блока; build_model подгоняет размер до connect и раскладки.\n"
        "  3. add_block(props=\"nport=N\") пины не пересобирает; nport и\n"
        "     in_ports у этих классов отвергаются: после создания вызывайте\n"
        "     set_block_param.\n"
        "\n"
        "После роста высоты:\n"
        "  1. Соедините новые входы: пустой вход блока расчёта молча\n"
        "     останавливает расчёт всей модели.\n"
        "  2. layout_place(): расстановка и NormalizeWire. Без неё рост\n"
        "     высоты даёт наложения блоков.\n"
        "  3. Если блоки двигать нельзя: layout_place(normalize_only=True)\n"
        "     или format_model(mode=\"normalize\"). Линии нормализуются,\n"
        "     блоки стоят; наложения разводятся вручную set_block_center и\n"
        "     проверяются check_model_layout.\n"
        "\n"
        "Композиты: build_model(layout=True) и format_model(mode=\"place\")\n"
        "подгоняют размеры до раскладки; format_model(mode=\"sizes\") делает\n"
        "подгонку и нормализацию без расстановки; при смене ширины центр\n"
        "может сдвинуться на ΔX, чтобы сохранить пин.\n"
        "\n"
        "Проверка: check_model_layout предупреждает о высоте не по правилу,\n"
        f"ширине меньше {NPORT_BLOCK_WIDTH} и расхождении nport с числом\n"
        "пинов; ширину больше штатной и «Формула vs Значение» через COM не\n"
        "проверяет. Нормализация видна только снимком (save_screenshot):\n"
        "выгрузка показывает прежние точки.\n"
        "\n"
        "Соседнее правило: порт-блоки («Порт входа»/«Порт выхода») имеют\n"
        f"высоту {PORT_ROW_HEIGHT} px на строку сигнала PortNames;\n"
        "simintech://model/checklist, п.4.\n"
    )
