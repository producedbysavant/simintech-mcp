"""Ресурсы документации: полный каталог, карточка инструмента, руководства."""

from __future__ import annotations

import json

from . import surface
from .app import mcp


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
