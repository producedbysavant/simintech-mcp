# Рефакторинг поверхности инструментов simintech-mcp: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сократить видимую поверхность MCP-сервера до 9 ключевых инструментов и 5 композитов, сохранив все 59 низкоуровневых возможностей через поиск, и обновить стек до FastMCP 4.1.1.

**Architecture:** Ядро + поиск + композиты. `ToolSurfaceTransform` сокращает описания и заполняет аннотации; `BM25SearchTransform` скрывает каталог за `search_tools`/`call_tool`; композиты `doctor`, `build_model`, `inspect_model`, `simulate`, `format_model` переносят предметную логику из промпта в код. `build_model` создаёт верхнеуровневые провода через `connect`, а не текстом, потому что живой замер подтвердил дефект текстовой геометрии.

**Tech Stack:** Python 3.11, FastMCP 4.1.1, MCP SDK 2.3.0, Pydantic 2.14, pytest 9, simintech-api v0.14.0, COM API SimInTech.

---

## Файловая структура

Создать:

- `simintech_mcp/surface.py` - ядро, краткие описания, transform, поиск, каталог.
- `simintech_mcp/tool_docs.py` - ресурсы `simintech://tools`, `.../tools/<name>`, руководства.
- `simintech_mcp/specs.py` - Pydantic `ModelSpec` и рендер декларативного текста.
- `simintech_mcp/tools/workflows.py` - пять композитов.
- `tests/unit/test_surface_v2.py` - видимая поверхность, описания, поиск.
- `tests/unit/test_specs.py` - валидация и рендер.
- `tests/unit/test_workflows.py` - композиты на фейках.
- `tests/eval/simintech_mcp_eval.xml` - 10 оценочных вопросов.
- `docs/tool-surface.md` - руководство пользователя.

Изменить:

- `pyproject.toml` - версии зависимостей.
- `simintech_mcp/app.py` - transforms и инструкции.
- `simintech_mcp/runtime.py` - `workflow_tool`, `diagnose_error`.
- `simintech_mcp/tools/__init__.py` - импорт `workflows`.
- `simintech_mcp/server.py` - импорт `tool_docs`.
- `simintech_mcp/tools/help.py` - новый текст справки.
- `tests/unit/_support.py` - `all_tools()`.
- `tests/unit/test_surface.py` - полный каталог вместо видимого, snake_case аннотации, новые наборы.
- `README.md` - ядро, поиск, 64 инструмента, геометрия.
- `docs/roadmap-agentic-ecosystem.md` - датированное дополнение.
- `simintech_mcp/__init__.py` - версия 0.3.0.

---

## Task 1: Обновить зависимости до FastMCP 4.1.1

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/unit/test_surface.py` (snake_case аннотации)
- Test: `tests/unit/test_surface.py`

- [ ] **Step 1: Зафиксировать версии в pyproject.toml**

Заменить в `dependencies`:

```toml
    "simintech-api @ git+https://github.com/producedbysavant/simintech-code@v0.14.0",
    "mcp>=2.3.0",
    "fastmcp>=4.1.1",
    "pydantic>=2.14.1",
    "defusedxml>=0.7.1",
```

Заменить в `optional-dependencies.test`:

```toml
    "pytest>=9.0",
    "anyio>=4.15",
    "trio>=0.34",
```

- [ ] **Step 2: Перевести чтение аннотаций в тесте на snake_case**

В `tests/unit/test_surface.py` заменить:

```python
    unset = [name for name, t in by_name.items()
             if t.annotations is None or t.annotations.readOnlyHint is None]
```

на:

```python
    unset = [name for name, t in by_name.items()
             if t.annotations is None or t.annotations.read_only_hint is None]
```

и:

```python
    read_only = {name for name, t in by_name.items()
                 if t.annotations is not None
                 and t.annotations.readOnlyHint is True}
```

на:

```python
    read_only = {name for name, t in by_name.items()
                 if t.annotations is not None
                 and t.annotations.read_only_hint is True}
```

Аналогично заменить `destructiveHint` на `destructive_hint`.

- [ ] **Step 3: Прогнать тесты под FastMCP 4**

Run:

```bash
rm -rf /tmp/simintech-mcp-deps
mkdir -p /tmp/simintech-mcp-deps
python3.11 -m pip install -q --target /tmp/simintech-mcp-deps --upgrade \
  "fastmcp==4.1.1" pytest anyio trio defusedxml
find /mnt/c/git/simintech-mcp /mnt/c/git/simintech-code \
  -name '__pycache__' -type d -prune -exec rm -rf {} +
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit -q
```

Expected: `1154 passed`, предупреждений о camelCase больше нет.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/unit/test_surface.py
git commit -m "build(deps): FastMCP 4.1.1, MCP 2.3.0, Pydantic 2.14"
```

---

## Task 2: Добавить слой поверхности и поиск

**Files:**
- Create: `simintech_mcp/surface.py`
- Modify: `simintech_mcp/app.py`
- Test: `tests/unit/test_surface_v2.py`

- [ ] **Step 1: Написать падающий тест видимой поверхности**

Создать `tests/unit/test_surface_v2.py`:

```python
"""Поверхность v2: ядро, краткие описания, поиск, полный каталог."""

from __future__ import annotations

import json

import pytest

from simintech_mcp import surface
from simintech_mcp.server import mcp

from _support import _text


@pytest.mark.anyio
async def test_visible_surface_is_core_plus_search():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == set(surface.CORE_TOOLS) | {"search_tools", "call_tool"}
    assert len(names) == len(surface.CORE_TOOLS) + 2


@pytest.mark.anyio
async def test_catalog_still_has_all_tools():
    tools = await surface.catalog_tools(mcp)
    assert len(tools) == 64


@pytest.mark.anyio
async def test_short_descriptions_are_small():
    tools = await surface.catalog_tools(mcp)
    for tool in tools:
        short = surface.short_description(tool.name, tool.description or "")
        assert len(short) <= 320, (tool.name, len(short))
    total = sum(len(surface.short_description(t.name, t.description or ""))
                for t in tools)
    assert total < 8000, total


@pytest.mark.anyio
async def test_search_finds_key_tools():
    result = await mcp.call_tool(
        "search_tools", {"query": "connect two blocks with a wire"})
    data = json.loads(_text(result))
    names = {item["name"] for item in data}
    assert "connect" in names


@pytest.mark.anyio
async def test_hidden_tool_is_still_callable():
    tool = await mcp.get_tool("add_block")
    assert tool is not None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_surface_v2.py -q
```

Expected: FAIL, `ModuleNotFoundError: No module named 'simintech_mcp.surface'`.

- [ ] **Step 3: Создать surface.py**

```python
"""Поверхность инструментов: ядро, краткие описания, поиск.

Полные docstring остаются в коде и отдаются ресурсом
`simintech://tools/<name>`. В `tools/list` уходит первый абзац со ссылкой:
контекст клиента не оплачивает документацию, пока она не понадобилась.
"""

from __future__ import annotations

import os
import re
from typing import Sequence

from fastmcp import FastMCP
from fastmcp.server.transforms import Transform
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools import Tool


DISCOVERY_ENV = "SIMINTECH_MCP_DISCOVERY"
DISCOVERY_SEARCH = "search"
DISCOVERY_ALL = "all"

#: Сколько результатов отдаёт поиск за один запрос.
SEARCH_RESULTS = 5

#: Предел краткого описания вместе со ссылкой на полный ресурс.
MAX_SHORT_DESCRIPTION = 320

CORE_TOOLS = (
    "doctor",
    "create_project",
    "open_project",
    "build_model",
    "inspect_model",
    "simulate",
    "format_model",
    "read_output_file",
    "help_text",
)

#: Ручные краткие описания: первый абзац docstring у этих инструментов
#: слишком короткий или слишком общий для поиска.
SHORT_DESCRIPTIONS = {
    "doctor": "Проверить окружение SimInTech: COM, mmain, каталог результатов, скиллы, версии. Начните отсюда.",
    "build_model": "Собрать модель из ModelSpec: блоки, субмодели, провода через connect, раскладка, проверка, снимок.",
    "inspect_model": "Кратко показать блоки, линии и параметры текущей страницы без запуска расчёта.",
    "simulate": "Задать время, запустить расчёт или шаги, прочитать и свести результат.",
    "format_model": "Разложить блоки, нормализовать линии, вписать кадр, проверить оформление, сделать снимок.",
    "import_model_text": "Импортировать декларативный текст модели; верхнеуровневые провода лучше создавать connect.",
    "export_model_text": "Выгрузить модель в декларативный текст: блоки, субмодели, концы проводов.",
    "connect": "Соединить выход блока src с входом dst по фактическим портам.",
    "layout_place": "Расставить блоки по слоям, применить координаты и нормализовать линии.",
    "save_screenshot": "Сохранить снимок схемы PNG/BMP/SVG и посмотреть раскладку глазами.",
}

#: Группа инструмента для поиска. Ключ: имя модуля инструмента.
_MODULE_TAGS = {
    "blocks": "blocks",
    "wires": "wires",
    "layout": "layout",
    "check_model": "check",
    "project": "project",
    "simulation": "simulate",
    "files": "results",
    "model_text": "text",
    "page_script": "script",
    "block_script": "script",
    "pack": "pack",
    "help": "help",
    "screenshot": "screenshot",
    "sizes": "blocks",
    "fits": "layout",
    "workflows": "workflow",
}

#: Инструменты, повторный вызов с теми же аргументами не даёт нового эффекта.
IDEMPOTENT_TOOLS = frozenset({
    "status", "get_time", "list_blocks", "list_wires", "get_block_params",
    "get_page_script", "get_block_script", "get_project_config",
    "list_signals", "get_signal", "read_output_file",
    "summarize_output_file", "inspect_project_file", "project_network_role",
    "list_pack_projects", "help_text", "search_language_functions",
    "get_language_function", "doctor", "inspect_model",
    "set_block_param", "set_calc_time", "set_project_config",
    "layout_place", "fit_view", "set_block_center", "save_project",
    "format_model",
})

#: Инструменты, которые могут разрушить данные или сессию.
DESTRUCTIVE_TOOLS = frozenset({
    "remove_block", "disconnect_wire", "disconnect", "close_project",
    "close_pack", "save_project", "reload_project",
})


def discovery_mode() -> str:
    """Режим раскрытия: `search` по умолчанию, `all` для отладки."""
    raw = (os.environ.get(DISCOVERY_ENV) or "").strip().lower()
    if raw in ("", DISCOVERY_SEARCH, "1", "true", "on", "yes"):
        return DISCOVERY_SEARCH
    if raw in (DISCOVERY_ALL, "0", "false", "off", "no", "none"):
        return DISCOVERY_ALL
    # Опечатка в переменной не должна мешать серверу подняться: режим
    # остаётся безопасным, а doctor назовёт неизвестное значение.
    return DISCOVERY_SEARCH


def _first_paragraph(text: str) -> str:
    paragraph = re.split(r"\n\s*\n", (text or "").strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", paragraph).strip()


def _tags(name: str, tool: Tool | None) -> str:
    if tool is not None:
        module = getattr(getattr(tool, "fn", None), "__module__", "")
        tag = _MODULE_TAGS.get(module.rsplit(".", 1)[-1])
        if tag:
            return tag
    return _MODULE_TAGS.get(name, "")


def short_description(name: str, description: str,
                      tool: Tool | None = None) -> str:
    """Краткое описание: первый абзац, тег и ссылка на полный ресурс."""
    text = SHORT_DESCRIPTIONS.get(name) or _first_paragraph(description)
    if not text:
        text = name
    tag = _tags(name, tool)
    if tag:
        text = f"[{tag}] {text}"
    suffix = f"\n\nFull docs: simintech://tools/{name}"
    limit = MAX_SHORT_DESCRIPTION - len(suffix)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.-") + "..."
    return text + suffix


class ToolSurfaceTransform(Transform):
    """Сократить описания и заполнить недостающие аннотации."""

    def _apply(self, tool: Tool) -> Tool:
        annotations = tool.annotations
        if annotations is not None:
            update = {}
            if annotations.idempotent_hint is None:
                update["idempotent_hint"] = tool.name in IDEMPOTENT_TOOLS
            if annotations.open_world_hint is None:
                update["open_world_hint"] = False
            if annotations.destructive_hint is None:
                update["destructive_hint"] = tool.name in DESTRUCTIVE_TOOLS
            if update:
                annotations = annotations.model_copy(update=update)
        return tool.model_copy(update={
            "description": short_description(
                tool.name, tool.description or "", tool),
            "annotations": annotations,
        })

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._apply(tool) for tool in tools]

    async def get_tool(self, name: str, call_next, *, version=None):
        tool = await call_next(name, version=version)
        return self._apply(tool) if tool is not None else None


def build_transforms() -> list[Transform]:
    """Transforms сервера: краткие описания и, по умолчанию, поиск."""
    transforms: list[Transform] = [ToolSurfaceTransform()]
    if discovery_mode() == DISCOVERY_SEARCH:
        transforms.append(BM25SearchTransform(
            max_results=SEARCH_RESULTS,
            always_visible=list(CORE_TOOLS),
        ))
    return transforms


async def catalog_tools(mcp: FastMCP) -> list[Tool]:
    """Полный каталог без transforms: источник истины для ресурсов и тестов."""
    provider = getattr(mcp, "_local_provider", None)
    if provider is not None:
        return list(await provider.list_tools())
    return list(await mcp.list_tools())
```

- [ ] **Step 4: Подключить transforms в app.py**

В `simintech_mcp/app.py` добавить импорт:

```python
from .surface import build_transforms
```

и передать в конструктор:

```python
mcp = FastMCP(
    "simintech",
    version=__version__,
    instructions=(
        "Управление SimInTech через COM API (Windows, mmain.exe /regserver). "
        "Начните с doctor. Сборка: build_model(spec=...) с ModelSpec; "
        "верхнеуровневые провода создаются connect, не текстом. "
        "Обзор: inspect_model; расчёт: simulate; оформление: format_model. "
        "Нестандартные операции ищутся search_tools; полные описания "
        "инструментов: simintech://tools/<name>. "
        "Имена параметров блоков: simintech://blocks/catalog."
    ),
    transforms=build_transforms(),
)
```

- [ ] **Step 5: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_surface_v2.py -q
```

Expected: часть тестов падает по числу 64, потому что композиты ещё не созданы. Тесты `test_visible_surface_is_core_plus_search` и `test_short_descriptions_are_small` должны пройти, если временно убрать из `CORE_TOOLS` ещё не созданные имена. На этом шаге допустимо оставить падающими только тесты, которые требуют композитов.

- [ ] **Step 6: Commit**

```bash
git add simintech_mcp/surface.py simintech_mcp/app.py tests/unit/test_surface_v2.py
git commit -m "feat(surface): краткие описания и поиск инструментов"
```

---

## Task 3: Ресурсы документации инструментов

**Files:**
- Create: `simintech_mcp/tool_docs.py`
- Modify: `simintech_mcp/server.py`
- Test: `tests/unit/test_resources.py`

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/unit/test_resources.py`:

```python
@pytest.mark.anyio
async def test_tools_resource_lists_catalog():
    from simintech_mcp import surface
    text = await _resource_text("simintech://tools")
    assert "build_model" in text
    assert "search_tools" in text
    assert len(await surface.catalog_tools(mcp)) == 64


@pytest.mark.anyio
async def test_tool_resource_returns_full_docstring():
    text = await _resource_text("simintech://tools/import_model_text")
    assert "Текст - это код, а не данные" in text
    assert "input_schema" in text


@pytest.mark.anyio
async def test_model_text_guide_exists():
    text = await _resource_text("simintech://guides/model-text")
    assert "createmodel" in text
    assert "points" in text
    assert "chr(34)" in text
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_resources.py -q
```

Expected: FAIL, ресурс не найден.

- [ ] **Step 3: Создать tool_docs.py**

```python
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
        "Инструменты simintech-mcp (полные описания: simintech://tools/<name>).",
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
    return (
        f"# {tool.name}\n"
        f"title: {tool.title or ''}\n"
        f"annotations: {json.dumps(annotations, ensure_ascii=False)}\n\n"
        f"{tool.description or ''}\n\n"
        f"input_schema:\n{json.dumps(tool.parameters, ensure_ascii=False, indent=2)}\n"
    )


@mcp.resource("simintech://guides/model-text")
def resource_model_text_guide() -> str:
    """Канон декларативного текста createmodel."""
    return (
        "Декларативный текст модели SimInTech\n"
        "\n"
        "Форма: const model : ( Имя: (type = \"Класс\", points=[...], ...), ... );\n"
        "В import_model_text передаются записи без внешней обёртки; выгрузка\n"
        "export_model_text принимается как есть.\n"
        "\n"
        "points: четыре точки блока: центр, правый порт, верх, подпись.\n"
        "Пример: points=[(0,0),(16,0),(0,-16),(0,32)].\n"
        "\n"
        "Провод: type = \"wire\", points=[], src = \"блок:out:N\", dst = \"блок:in:N\".\n"
        "Ветвь: src = \"имя_линии:K\", K с нуля.\n"
        "Субмодель: type = \"Субмодель\", вложенная секция subsystem: (...).\n"
        "Порт входа/выхода: portnames = \"X\"; по справке поставки порт-блоки\n"
        "допустимы внутри субмоделей, живой импорт сохранённого проекта создал\n"
        "и верхнеуровневые порты, но это не документированный путь.\n"
        "\n"
        "Кавычка внутри строки задаётся chr(34): удвоение кавычек в этой сборке\n"
        "даёт пустую строку. Последнее свойство записи пишется без запятой.\n"
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
        "Правило: блоки и субмодели можно создавать createmodel; верхнеуровневые\n"
        "провода создаются connect по фактическим портам, затем layout_place.\n"
        "Провода внутри субмоделей остаются текстовыми и требуют снимка.\n"
    )
```

- [ ] **Step 4: Подключить модуль в server.py**

В импорте регистрации добавить `tool_docs`:

```python
from . import prompts, resources, stdio, tool_docs, tools  # noqa: F401
```

- [ ] **Step 5: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_resources.py -q
```

Expected: PASS. Тест `test_tools_resource_lists_catalog` пока падает по числу 64, если композиты ещё не созданы; допускается до Task 6.

- [ ] **Step 6: Commit**

```bash
git add simintech_mcp/tool_docs.py simintech_mcp/server.py tests/unit/test_resources.py
git commit -m "feat(tool-docs): ресурсы каталога и руководства по тексту модели"
```

---

## Task 4: Pydantic ModelSpec и рендер текста

**Files:**
- Create: `simintech_mcp/specs.py`
- Test: `tests/unit/test_specs.py`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/unit/test_specs.py`:

```python
"""ModelSpec: валидация и рендер декларативного текста."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simintech_mcp.specs import BlockSpec, ModelSpec, WireSpec, render_model_text


def test_render_blocks_only_has_no_wires():
    spec = ModelSpec(
        blocks=[
            BlockSpec(name="k_0", type="Константа", props={"a": 2}),
            BlockSpec(name="kx_0", type="Усилитель", props={"a": 3}),
        ],
        wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
    )
    text = render_model_text(spec, include_wires=False)
    assert 'k_0: (' in text
    assert 'type = "wire"' not in text
    assert 'kx_0: (' in text


def test_render_includes_subsystem_wires():
    spec = ModelSpec(blocks=[
        BlockSpec(
            name="sub", type="Субмодель",
            subsystem=ModelSpec(
                blocks=[
                    BlockSpec(name="in", type="Порт входа",
                              portnames="X"),
                    BlockSpec(name="g", type="Усилитель", props={"a": 2}),
                ],
                wires=[WireSpec(src="in:out:0", dst="g:in:0")],
            ),
        )
    ])
    text = render_model_text(spec, include_wires=False)
    assert "subsystem:" in text
    assert 'type = "wire"' in text


def test_duplicate_names_case_insensitive_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="K_0", type="Константа"),
            BlockSpec(name="k_0", type="Усилитель"),
        ])


def test_wire_endpoint_must_exist():
    with pytest.raises(ValidationError):
        ModelSpec(
            blocks=[BlockSpec(name="k_0", type="Константа")],
            wires=[WireSpec(src="k_0:out:0", dst="missing:in:0")],
        )


def test_quote_in_string_prop_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="p", type="Порт входа", portnames='X"Y'),
        ])


def test_last_property_has_no_trailing_comma():
    spec = ModelSpec(blocks=[BlockSpec(name="k_0", type="Константа",
                                       props={"a": 2})])
    text = render_model_text(spec, include_wires=False)
    assert ",)" not in text
    assert ",\n)" not in text
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_specs.py -q
```

Expected: FAIL, модуль не найден.

- [ ] **Step 3: Создать specs.py**

```python
"""Структурированная модель: Pydantic-валидация и рендер текста SimInTech."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENDPOINT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*):(?P<kind>out|in):(?P<index>\d+)$")
_DEFAULT_POINTS = [(0.0, 0.0), (16.0, 0.0), (0.0, -16.0), (0.0, 32.0)]


class WireSpec(BaseModel):
    """Провод: концы в формате блок:out:N и блок:in:N."""
    src: str = Field(description="Источник: имя:out:индекс")
    dst: str = Field(description="Приёмник: имя:in:индекс")
    points: List[Tuple[float, float]] = Field(default_factory=list)


class BlockSpec(BaseModel):
    """Блок или субмодель."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)
    props: Dict[str, Any] = Field(default_factory=dict)
    points: Optional[List[Tuple[float, float]]] = None
    portnames: Optional[Union[str, List[str]]] = None
    subsystem: Optional["ModelSpec"] = None


class ModelSpec(BaseModel):
    """Модель: блоки и провода текущего уровня."""
    blocks: List[BlockSpec] = Field(default_factory=list)
    wires: List[WireSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_level(self) -> "ModelSpec":
        names: Dict[str, str] = {}
        for block in self.blocks:
            key = block.name.lower()
            if key in names:
                raise ValueError(
                    f"имена блоков различаются только регистром: "
                    f"{names[key]!r} и {block.name!r}; createmodel приводит "
                    f"имена к нижнему регистру")
            names[key] = block.name
        for block in self.blocks:
            if block.subsystem is not None:
                # Вложенный уровень валидируется собственной моделью.
                pass
        for wire in self.wires:
            _validate_endpoint(wire.src, names, "src")
            _validate_endpoint(wire.dst, names, "dst")
        for block in self.blocks:
            if block.portnames is not None:
                values = ([block.portnames] if isinstance(block.portnames, str)
                          else block.portnames)
                for value in values:
                    _validate_text(value, "portnames")
            for key, value in block.props.items():
                _validate_value(value, key)
        return self


def _validate_endpoint(value: str, names: Dict[str, str], side: str) -> None:
    match = _ENDPOINT_RE.match(value)
    if match is None:
        raise ValueError(
            f"{side}={value!r}: ожидается имя:{side}:индекс")
    name = match.group("name")
    if name.lower() not in names:
        raise ValueError(
            f"{side}={value!r}: блока {name!r} нет на этом уровне")


def _validate_text(value: str, label: str) -> None:
    if '"' in value:
        raise ValueError(
            f"{label}={value!r}: кавычка в строке не поддерживается; "
            f"используйте chr(34) в тексте или уберите кавычку")


def _validate_value(value: Any, label: str) -> None:
    if isinstance(value, str):
        _validate_text(value, label)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_value(item, label)
    elif isinstance(value, dict):
        raise ValueError(f"{label}: словари не поддерживаются")
    elif not isinstance(value, (int, float, bool)):
        raise ValueError(f"{label}: тип {type(value).__name__} не поддерживается")


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, str):
        _validate_text(value, "value")
        return f'"{value}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    raise ValueError(f"неподдерживаемое значение: {value!r}")


def _render_points(points: List[Tuple[float, float]]) -> str:
    return "[" + ",".join(f"({x:g}, {y:g})" for x, y in points) + "]"


def _render_block(block: BlockSpec, level: int) -> str:
    pad = "  " * level
    props: List[str] = [
        f'type = "{block.type}"',
        f"points={_render_points(block.points or _DEFAULT_POINTS)}",
    ]
    if block.portnames is not None:
        names = ([block.portnames] if isinstance(block.portnames, str)
                 else block.portnames)
        props.append(f'portnames = "{";".join(names)}"')
    for key, value in block.props.items():
        props.append(f"{key} = {_render_value(value)}")
    if block.subsystem is not None:
        inner = _render_level(block.subsystem, level + 2)
        props.append(f"subsystem:\n{pad}  (\n{inner}\n{pad}  )")
    body = (",\n" + pad + "  ").join(props)
    return f"{pad}{block.name}: (\n{pad}  {body}\n{pad})"


def _render_wire(wire: WireSpec, index: int, level: int) -> str:
    pad = "  " * level
    points = _render_points(wire.points) if wire.points else "[]"
    return (
        f'{pad}wire_{index}: (\n'
        f'{pad}  type = "wire",\n'
        f'{pad}  points={points},\n'
        f'{pad}  src = "{wire.src}",\n'
        f'{pad}  dst = "{wire.dst}"\n'
        f'{pad})'
    )


def _render_level(spec: ModelSpec, level: int) -> str:
    records: List[str] = []
    for block in spec.blocks:
        records.append(_render_block(block, level))
    for index, wire in enumerate(spec.wires, start=1):
        records.append(_render_wire(wire, index, level))
    return ",\n".join(records)


def render_model_text(spec: ModelSpec, *,
                      include_wires: bool = True) -> str:
    """Текст записей без внешней обёртки const model: (...)."""
    records: List[str] = []
    for block in spec.blocks:
        records.append(_render_block(block, 1))
    if include_wires:
        for index, wire in enumerate(spec.wires, start=1):
            records.append(_render_wire(wire, index, 1))
    return ",\n".join(records)
```

- [ ] **Step 4: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_specs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add simintech_mcp/specs.py tests/unit/test_specs.py
git commit -m "feat(specs): Pydantic ModelSpec и рендер текста модели"
```

---

## Task 5: workflow_tool и диагностика ошибок

**Files:**
- Modify: `simintech_mcp/runtime.py`
- Test: `tests/unit/test_runtime.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/unit/test_runtime.py`:

```python
def test_workflow_tool_runs_without_taking_com_executor(monkeypatch):
    @runtime.workflow_tool
    def workflow():
        return "ok"

    def boom(*args, **kwargs):
        raise AssertionError("workflow занял COM-поток")

    monkeypatch.setattr(runtime._COM_EXECUTOR, "submit", boom)
    assert workflow() == "ok"
    assert getattr(workflow, runtime.WORKFLOW_MARK, False) is True


def test_workflow_tool_converts_error_to_tool_error():
    @runtime.workflow_tool
    def workflow():
        raise ValueError("bad")

    with pytest.raises(ToolError):
        workflow()


def test_diagnose_error_names_session_loss():
    hint = runtime.diagnose_error(RuntimeError("Объект не подключен к серверу"))
    assert "disconnect" in hint
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_runtime.py -q
```

Expected: FAIL, `workflow_tool` и `WORKFLOW_MARK` не существуют.

- [ ] **Step 3: Добавить workflow_tool и diagnose_error**

В `simintech_mcp/runtime.py` после `RESCUE_MARK` добавить:

```python
#: Метка workflow-инструмента: `True` - инструмент оркестрирует другие
#: инструменты и сам в COM-поток не встаёт. Вложенные вызовы занимают
#: единственный COM-поток по очереди; если бы workflow шёл через
#: `com_threaded`, он бы ждал сам себя.
WORKFLOW_MARK = "_simintech_workflow"


def diagnose_error(exc: BaseException) -> str:
    """Подсказка к типовой ошибке: действие, а не только причина."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "объект не подключен" in text or "co_e_objnotconnected" in text:
        return (" Подсказка: COM-сессия потеряна. Вызовите doctor, затем "
                "disconnect и повторите.")
    if "нет открытого проекта" in text:
        return (" Подсказка: сначала create_project или open_project.")
    if "не найден на странице" in text or "блок не найден" in text:
        return (" Подсказка: имена блоков возвращает list_blocks; "
                "ищите фактическое имя, а не name_hint.")
    if "неизвестн" in text and "параметр" in text:
        return (" Подсказка: имена параметров класса смотрите в "
                "simintech://blocks/catalog или get_block_params.")
    if "module" in text and "not registered" in text:
        return (" Подсказка: модуль не лицензирован; проверьте лицензию "
                "и профиль библиотек.")
    if "access violation" in text:
        return (" Подсказка: среда упала. Вызовите doctor и disconnect; "
                "при необходимости перезапустите mmain.exe.")
    return ""
```

В `_call_guarded` заменить:

```python
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc
```

на:

```python
    except Exception as exc:
        raise ToolError(
            f"{type(exc).__name__}: {exc}{diagnose_error(exc)}") from exc
```

После `plain_tool` добавить:

```python
def workflow_tool(fn: Callable[..., Any] | None = None, *,
                  mutates_project: bool = False) -> Callable[..., Any]:
    """Инструмент-оркестратор: COM-поток занимают вложенные вызовы.

    Сам workflow не отправляется в `_COM_EXECUTOR`: иначе он ждал бы
    собственный future и упёрся бы в `COM_CALL_TIMEOUT`. Контракт отказа и
    журнал общие с остальными инструментами.
    """
    if fn is None:
        return functools.partial(workflow_tool,
                                 mutates_project=mutates_project)

    def invoke(*args: Any, **kwargs: Any) -> Any:
        result = _call_guarded(fn, args, kwargs)
        if mutates_project:
            result = _append_mutation_note(result)
        return result

    wrapper = _instrumented(fn, invoke)
    setattr(wrapper, COM_THREAD_MARK, True)
    setattr(wrapper, WORKFLOW_MARK, True)
    if mutates_project:
        setattr(wrapper, MUTATES_PROJECT_MARK, True)
    return wrapper
```

- [ ] **Step 4: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_runtime.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add simintech_mcp/runtime.py tests/unit/test_runtime.py
git commit -m "feat(runtime): workflow-инструменты и подсказки к ошибкам"
```

---

## Task 6: Композиты doctor, build_model, inspect_model, simulate, format_model

**Files:**
- Create: `simintech_mcp/tools/workflows.py`
- Modify: `simintech_mcp/tools/__init__.py`
- Test: `tests/unit/test_workflows.py`

- [ ] **Step 1: Написать падающие тесты на оркестрацию**

Создать `tests/unit/test_workflows.py`:

```python
"""Композиты: порядок вызовов и контракт частичного успеха."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.specs import BlockSpec, ModelSpec, WireSpec
from simintech_mcp.tools import workflows


def _patch(monkeypatch, calls, name, result):
    def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        return result
    monkeypatch.setattr(workflows, name, fake)


def test_build_model_spec_imports_blocks_and_connects(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "create_project", "проект создан")
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)\n  kx_0 [Усилитель] (id=2)")
    _patch(monkeypatch, calls, "connect", "соединено")
    _patch(monkeypatch, calls, "layout_place", "расставлено")
    monkeypatch.setattr(workflows.session, "current_project", lambda: None)

    text = workflows.build_model(
        spec=ModelSpec(
            blocks=[
                BlockSpec(name="k_0", type="Константа", props={"a": 2}),
                BlockSpec(name="kx_0", type="Усилитель", props={"a": 3}),
            ],
            wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
        ),
        layout=True,
    )

    imported = [c for c in calls if c[0] == "import_model_text"]
    assert imported, calls
    assert 'type = "wire"' not in imported[0][1][0]
    connects = [c for c in calls if c[0] == "connect"]
    assert connects and connects[0][1][:2] == ("k_0", "kx_0")
    assert "Модель собрана" in text


def test_build_model_requires_exactly_one_source():
    with pytest.raises(ToolError):
        workflows.build_model()
    with pytest.raises(ToolError):
        workflows.build_model(spec=ModelSpec(), model_text="x")


def test_build_model_reports_partial_success(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "create_project", "проект создан")
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)\n  kx_0 [Усилитель] (id=2)")
    monkeypatch.setattr(workflows, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ToolError("порт занят")))
    monkeypatch.setattr(workflows.session, "current_project", lambda: None)

    text = workflows.build_model(
        spec=ModelSpec(
            blocks=[
                BlockSpec(name="k_0", type="Константа"),
                BlockSpec(name="kx_0", type="Усилитель"),
            ],
            wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
        ),
        layout=False,
    )

    assert "WARNING" in text
    assert "порт занят" in text


def test_inspect_model_uses_only_read_tools(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "list_blocks", "Блоки:\n  k_0 [Константа] (id=1)")
    _patch(monkeypatch, calls, "list_wires", "Линий связи: 0")
    text = workflows.inspect_model()
    assert [c[0] for c in calls] == ["list_blocks", "list_wires"]
    assert "k_0" in text


def test_simulate_runs_and_summarizes(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "set_calc_time", "время задано")
    _patch(monkeypatch, calls, "run", "расчёт завершён")
    _patch(monkeypatch, calls, "get_time", 1.0)
    _patch(monkeypatch, calls, "summarize_output_file", "min/max")
    text = workflows.simulate(end_time=2.0, to_time=1.0, output_path="out.txt")
    assert [c[0] for c in calls] == [
        "set_calc_time", "run", "get_time", "summarize_output_file"]
    assert "1.0" in text


def test_format_model_place_and_check(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "layout_place", "расставлено")
    _patch(monkeypatch, calls, "check_model_layout", "проверено")
    _patch(monkeypatch, calls, "audit_routing", "аудит")
    text = workflows.format_model(mode="place", check=True)
    assert [c[0] for c in calls] == [
        "layout_place", "check_model_layout", "audit_routing"]
    assert "расставлено" in text
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_workflows.py -q
```

Expected: FAIL, модуль не найден.

- [ ] **Step 3: Создать workflows.py**

```python
"""Композиты: задачи агента, а не отдельные COM-вызовы.

Каждый композит идёт под `runtime.workflow_tool`: сам он в COM-поток не
встаёт, а вложенные инструменты занимают его по очереди.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from typing import Any, Dict, List, Literal, Optional

from fastmcp.exceptions import ToolError

from .. import __version__, runtime, sandbox, session, skills, surface
from ..app import mcp
from ..specs import ModelSpec, render_model_text
from . import blocks, check_model, files, layout, model_text, project
from . import screenshot, simulation, wires


_WIRE_ENDPOINT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*):(?P<kind>out|in):(?P<index>\d+)$")
_BLOCK_LINE_RE = re.compile(
    r"^\s+(?P<name>\S+)\s+\[(?P<cls>[^\]]+)\]\s+\(id=(?P<id>\d+)\)\s*$")


def _block_names(text: str) -> Dict[str, str]:
    """Карта нижний регистр имени -> фактическое имя из list_blocks."""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        match = _BLOCK_LINE_RE.match(line)
        if match:
            result[match.group("name").lower()] = match.group("name")
    return result


def _parse_endpoint(value: str) -> tuple[str, int, int]:
    match = _WIRE_ENDPOINT_RE.match(value)
    if match is None:
        raise ToolError(f"конец провода {value!r} не в формате имя:out:N")
    return match.group("name"), match.group("kind"), int(match.group("index"))


def _project_or_create(project_hint: str, end_time: Optional[float],
                       new_project: bool) -> str:
    if new_project or session.current_project() is None:
        return project.create_project(
            project_hint=project_hint, end_time=end_time)
    if end_time is not None:
        return project.set_calc_time(end_time)
    return "используется текущий проект"


def _summary_lines(lines: List[str]) -> str:
    return "\n".join(lines)


@mcp.tool(title="Диагностика",
          annotations={"read_only_hint": True,
                      "idempotent_hint": True,
                      "open_world_hint": False})
@runtime.workflow_tool
def doctor() -> str:
    """Проверить окружение SimInTech и назвать следующие шаги."""
    rows: List[tuple[str, str, str]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        rows.append((name, "OK" if ok else "FAIL", detail))

    add("python", sys.version_info >= (3, 11), platform.python_version())
    add("platform", sys.platform == "win32", sys.platform)

    try:
        status = project.status()
        add("com", "подключён" in status.lower(), status)
    except ToolError as exc:
        add("com", False, str(exc))

    root = sandbox.safe_output_root()
    add("output_dir", os.path.isdir(root), root)
    add("output_writable", os.access(root, os.W_OK), root)
    add("skills_dir", skills.skills_root() is not None,
        str(skills.skills_root()))
    add("project", session.current_project() is not None,
        "проект открыт" if session.current_project() else "проекта нет")
    add("pack", session.current_pack() is not None,
        "пакет открыт" if session.current_pack() else "пакета нет")
    add("fastmcp", True, _fastmcp_version())
    add("simintech_api", True, _api_version())
    add("simintech_mcp", True, __version__)
    add("discovery", True, surface.discovery_mode())

    failed = [row for row in rows if row[1] == "FAIL"]
    lines = ["Диагностика simintech-mcp:", ""]
    for name, state, detail in rows:
        lines.append(f"  [{state}] {name}: {detail}")
    lines.append("")
    if failed:
        lines.append("FAIL: " + ", ".join(row[0] for row in failed))
        lines.append("Следующий шаг: прочитайте detail и выполните "
                     "рекомендацию; для COM это disconnect и повтор.")
    else:
        lines.append("Все проверки пройдены.")
    return _summary_lines(lines)


def _fastmcp_version() -> str:
    try:
        from importlib.metadata import version
        return version("fastmcp")
    except Exception:  # noqa: BLE001
        return "unknown"


def _api_version() -> str:
    try:
        import simintech_api
        return getattr(simintech_api, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


@mcp.tool(title="Собрать модель",
          annotations={"read_only_hint": False,
                      "destructive_hint": False,
                      "idempotent_hint": False,
                      "open_world_hint": False})
@runtime.workflow_tool(mutates_project=True)
def build_model(spec: Optional[ModelSpec] = None,
                model_text: str = "",
                project_hint: str = "model",
                end_time: Optional[float] = None,
                new_project: bool = True,
                layout: bool = True,
                check: bool = False,
                screenshot: bool = False) -> str:
    """Собрать модель из ModelSpec или декларативного текста.

    Для spec верхнеуровневые провода создаются connect по фактическим
    портам; провода внутри субмоделей идут текстом и требуют снимка.
    """
    if (spec is None) == (not model_text.strip()):
        raise ToolError(
            "нужен ровно один источник: spec или model_text")

    notes: List[str] = []
    notes.append(_project_or_create(project_hint, end_time, new_project))

    if spec is not None:
        before = len(spec.blocks)
        blocks_text = render_model_text(spec, include_wires=False)
        notes.append(model_text.import_model_text(blocks_text))
        listing = blocks.list_blocks(limit=0)
        actual = _block_names(listing)
        connected = 0
        failed = 0
        for wire in spec.wires:
            try:
                src_name, _src_kind, out_index = _parse_endpoint(wire.src)
                dst_name, _dst_kind, in_index = _parse_endpoint(wire.dst)
                src = actual.get(src_name.lower())
                dst = actual.get(dst_name.lower())
                if src is None or dst is None:
                    raise ToolError(
                        f"после импорта не найден блок {src_name!r} или "
                        f"{dst_name!r}")
                wires.connect(src, dst, out_index=out_index,
                              in_index=in_index)
                connected += 1
            except ToolError as exc:
                failed += 1
                notes.append(f"WARNING: провод {wire.src} -> {wire.dst}: {exc}")
        if failed:
            notes.append(
                f"WARNING: соединено {connected} из {len(spec.wires)} проводов")
    else:
        notes.append(model_text.import_model_text(model_text))
        notes.append(
            "WARNING: провода из текста могут получить неверную геометрию; "
            "для надёжной сборки используйте spec.")

    if layout:
        try:
            notes.append(layout.layout_place())
        except ToolError as exc:
            notes.append(f"WARNING: раскладка не выполнена: {exc}")
    if check:
        try:
            notes.append(check_model.check_model_layout())
        except ToolError as exc:
            notes.append(f"WARNING: проверка не выполнена: {exc}")
    if screenshot:
        try:
            notes.append(screenshot.save_screenshot(format="png", fit=True))
        except ToolError as exc:
            notes.append(f"WARNING: снимок не сделан: {exc}")

    return "Модель собрана.\n\n" + "\n\n".join(notes)


@mcp.tool(title="Обзор модели",
          annotations={"read_only_hint": True,
                      "idempotent_hint": True,
                      "open_world_hint": False})
@runtime.workflow_tool
def inspect_model(include_params: bool = False, limit: int = 200) -> str:
    """Показать блоки, линии и, при необходимости, параметры."""
    listing = blocks.list_blocks(limit=limit)
    wire_listing = wires.list_wires()
    parts = [listing, wire_listing]
    if include_params:
        names = list(_block_names(listing).values())[:limit]
        for name in names:
            try:
                parts.append(blocks.get_block_params(name))
            except ToolError as exc:
                parts.append(f"WARNING: параметры {name}: {exc}")
    return "\n\n".join(parts)


@mcp.tool(title="Расчёт и результат",
          annotations={"read_only_hint": False,
                      "destructive_hint": False,
                      "idempotent_hint": False,
                      "open_world_hint": False})
@runtime.workflow_tool(mutates_project=True)
def simulate(to_time: Optional[float] = None,
             end_time: Optional[float] = None,
             step_count: Optional[int] = None,
             output_path: str = "",
             summary: bool = True) -> str:
    """Задать время, запустить расчёт или шаги, прочитать результат."""
    parts: List[str] = []
    if end_time is not None:
        parts.append(project.set_calc_time(end_time))
    if step_count is not None:
        parts.append(simulation.step(step_count))
    else:
        parts.append(simulation.run(to_time=to_time))
    parts.append(f"Модельное время: {simulation.get_time():g} с")
    if output_path:
        parts.append(files.read_output_file(output_path))
        if summary:
            parts.append(files.summarize_output_file(output_path))
    return "\n\n".join(parts)


@mcp.tool(title="Оформление модели",
          annotations={"read_only_hint": False,
                      "destructive_hint": False,
                      "idempotent_hint": True,
                      "open_world_hint": False})
@runtime.workflow_tool(mutates_project=True)
def format_model(
        mode: Literal["place", "normalize", "fit", "screenshot"] = "place",
        check: bool = False,
        screenshot: bool = False,
        hires: bool = False,
        zoom: Optional[float] = None) -> str:
    """Разложить, нормализовать, вписать кадр, проверить и снять снимок."""
    parts: List[str] = []
    if mode == "place":
        parts.append(layout.layout_place())
    elif mode == "normalize":
        parts.append(layout.layout_place(normalize_only=True))
    elif mode == "fit":
        parts.append(layout.fit_view())
    elif mode == "screenshot":
        screenshot = True
    if check:
        parts.append(check_model.check_model_layout())
        parts.append(check_model.audit_routing())
    if screenshot:
        parts.append(screenshot.save_screenshot(
            format="png", fit=True, hires=hires, zoom=zoom))
    return "\n\n".join(parts)
```

- [ ] **Step 4: Подключить модуль**

В `simintech_mcp/tools/__init__.py` добавить `workflows` последним:

```python
from . import (block_script, blocks, check_model, files, fits, help,
               layout, model_text, pack, page_script, project, screenshot,
               simulation, sizes, wires, workflows)
```

и в `__all__` добавить `"workflows"`.

- [ ] **Step 5: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_workflows.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add simintech_mcp/tools/workflows.py simintech_mcp/tools/__init__.py tests/unit/test_workflows.py
git commit -m "feat(workflows): doctor, build_model, inspect_model, simulate, format_model"
```

---

## Task 7: Обновить контракт поверхности и README

**Files:**
- Modify: `tests/unit/_support.py`
- Modify: `tests/unit/test_surface.py`
- Modify: `README.md`
- Test: `tests/unit/test_surface.py`

- [ ] **Step 1: Добавить all_tools()**

В `tests/unit/_support.py` добавить:

```python
async def all_tools():
    """Полный каталог без transforms: 64 инструмента."""
    return list(await mcp._local_provider.list_tools())
```

- [ ] **Step 2: Перевести контрактные тесты на полный каталог**

В `tests/unit/test_surface.py` заменить вызовы `await mcp.list_tools()` в тестах состава, аннотаций, декораторов и README на `await all_tools()`. Импортировать `all_tools` из `_support`.

Добавить композиты в наборы:

```python
_MUTATING_TOOLS = {
    ...,
    "build_model", "simulate", "format_model",
}

_READ_ONLY_TOOLS = {
    ...,
    "doctor", "inspect_model",
}
```

Ожидаемый набор `test_all_tools_registered` дополнить:

```python
        "doctor", "build_model", "inspect_model", "simulate",
        "format_model",
```

- [ ] **Step 3: Обновить README**

В таблице инструментов добавить группу:

```markdown
| Композиты | `doctor`, `build_model`, `inspect_model`, `simulate`, `format_model` |
```

Заменить строку числа:

```markdown
Всего инструментов - 64. По умолчанию видимы 9 ключевых и 2 поисковых;
остальные доступны через `search_tools`. `SIMINTECH_MCP_DISCOVERY=all`
возвращает все 64.
```

Добавить раздел «Сборка модели и геометрия проводов» с правилом:
блоки и субмодели можно создавать текстом, верхнеуровневые провода
создаются `connect`, провода внутри субмоделей требуют снимка.

- [ ] **Step 4: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_surface.py tests/unit/test_surface_v2.py -q
```

Expected: PASS, если README перечисляет все 64 имени.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/_support.py tests/unit/test_surface.py README.md
git commit -m "test(surface): контракт полного каталога и видимого ядра"
```

---

## Task 8: Справка, README и дорожная карта

**Files:**
- Modify: `simintech_mcp/tools/help.py`
- Modify: `README.md`
- Create: `docs/tool-surface.md`
- Modify: `docs/roadmap-agentic-ecosystem.md`
- Test: `tests/unit/test_surface.py`

- [ ] **Step 1: Обновить help_text**

В `simintech_mcp/tools/help.py` в начало возвращаемого текста добавить:

```python
        "Видимое ядро: doctor, create_project, open_project, build_model,\n"
        "inspect_model, simulate, format_model, read_output_file, help_text.\n"
        "Нестандартные операции: search_tools; полные описания:\n"
        "  simintech://tools/<name>.\n"
        "Сборка: build_model(spec=...); верхнеуровневые провода создаются\n"
        "connect, а не текстом (замер 2026-10-18).\n"
        "\n"
```

Сохранить строки `add_block`, `create_project`, `tools/list`, которые
проверяет тест.

- [ ] **Step 2: Создать docs/tool-surface.md**

```markdown
# Поверхность инструментов simintech-mcp

## Видимое ядро

doctor, create_project, open_project, build_model, inspect_model,
simulate, format_model, read_output_file, help_text.

Плюс search_tools и call_tool.

## Поиск

Остальные инструменты доступны через search_tools. Полное описание:
simintech://tools/<name>. Переменная SIMINTECH_MCP_DISCOVERY=all
возвращает все инструменты без поиска.

## Сборка модели

build_model(spec=ModelSpec(...)) валидирует имена и концы проводов,
импортирует блоки и субмодели текстом, затем создаёт верхнеуровневые
провода connect по фактическим портам и раскладывает модель.

Провода внутри субмоделей импортируются текстом; их геометрию проверяйте
снимком.

## Геометрия проводов

Живой замер 2026-10-18: провода, импортированные текстом вместе с
блоками, получают маршрут, не совпадающий с портами. На сложной модели
это даёт веер диагоналей. Порт-ориентированный connect даёт чистые
маршруты. Подробности: simintech://guides/wire-geometry.
```

- [ ] **Step 3: Дополнить дорожную карту**

В конец `docs/roadmap-agentic-ecosystem.md` добавить раздел:

```markdown
## 2026-10-18: поверхность инструментов v2

59 инструментов и около 37 тыс. токенов `tools/list` заменены схемой
Core + Search + Composites. Видимы 9 ключевых и 2 поисковых инструмента;
полный каталог доступен через search_tools. Добавлены doctor, build_model,
inspect_model, simulate, format_model. Живой замер подтвердил: текстовые
провода на сложной модели дают неверную геометрию, верхнеуровневые провода
создаются connect. Стек обновлён до FastMCP 4.1.1 и MCP 2.3.0.
```

- [ ] **Step 4: Прогнать тесты**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_surface.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add simintech_mcp/tools/help.py README.md docs/tool-surface.md docs/roadmap-agentic-ecosystem.md
git commit -m "docs(surface): справка, руководство и дорожная карта"
```

---

## Task 9: Оценочные вопросы

**Files:**
- Create: `tests/eval/simintech_mcp_eval.xml`
- Test: `tests/unit/test_surface_v2.py`

- [ ] **Step 1: Создать XML**

```xml
<evaluation>
  <qa_pair>
    <question>Сколько блоков на текущей странице проекта?</question>
    <answer>Число из list_blocks; ответ проверяется по счётчику.</answer>
  </qa_pair>
  <qa_pair>
    <question>Какой класс у блока k_0?</question>
    <answer>Класс из list_blocks для фактического имени k_0.</answer>
  </qa_pair>
  <qa_pair>
    <question>Сколько линий связи на текущей странице?</question>
    <answer>Число из list_wires.</answer>
  </qa_pair>
  <qa_pair>
    <question>Какое текущее модельное время?</question>
    <answer>Значение get_time.</answer>
  </qa_pair>
  <qa_pair>
    <question>Какое конечное время расчёта задано в проекте?</question>
    <answer>Значение endtime из get_project_config.</answer>
  </qa_pair>
  <qa_pair>
    <question>Какие параметры есть у класса «Константа»?</question>
    <answer>Имена из simintech://blocks/catalog или get_block_params.</answer>
  </qa_pair>
  <qa_pair>
    <question>Каков синтаксис функции createmodel?</question>
    <answer>Карточка get_language_function или search_language_functions.</answer>
  </qa_pair>
  <qa_pair>
    <question>В каком каталоге лежат результаты расчёта?</question>
    <answer>Путь из help_text.</answer>
  </qa_pair>
  <qa_pair>
    <question>Какую роль играет проект в распределённом расчёте?</question>
    <answer>Ответ project_network_role.</answer>
  </qa_pair>
  <qa_pair>
    <question>Какие пункты проверяет check_model_layout?</question>
    <answer>Список из simintech://model/checklist и ответа инструмента.</answer>
  </qa_pair>
</evaluation>
```

- [ ] **Step 2: Добавить тест структуры**

В `tests/unit/test_surface_v2.py`:

```python
def test_eval_has_ten_pairs():
    from pathlib import Path
    import defusedxml.ElementTree as ET
    path = Path(__file__).resolve().parents[1] / "eval" / "simintech_mcp_eval.xml"
    root = ET.parse(path).getroot()
    assert len(root.findall("qa_pair")) == 10
```

- [ ] **Step 3: Прогнать тест**

Run:

```bash
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit/test_surface_v2.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/eval/simintech_mcp_eval.xml tests/unit/test_surface_v2.py
git commit -m "test(eval): 10 оценочных вопросов поверхности"
```

---

## Task 10: Полные проверки и живая приёмка

**Files:**
- Modify: по результатам приёмки.

- [ ] **Step 1: Прогнать полный набор**

Run:

```bash
find /mnt/c/git/simintech-mcp /mnt/c/git/simintech-code \
  -name '__pycache__' -type d -prune -exec rm -rf {} +
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/unit -q
```

Expected: PASS, 1154+ тестов.

- [ ] **Step 2: Прогнать стиль и типы**

Run:

```bash
cd /mnt/c/git/simintech-mcp
python3.11 -m flake8 simintech_mcp tests scripts --max-line-length=88 --extend-ignore=E203,W503
PYTHONPATH=/tmp/simintech-mcp-deps:/mnt/c/git/simintech-code:/mnt/c/git/simintech-mcp \
python3.11 -m mypy
```

Expected: PASS. При ошибках типов исправить в новом коде.

- [ ] **Step 3: Живая приёмка после перезапуска сервера**

Попросить владельца перезапустить MCP-сервер на новой ревизии. Затем:

1. `doctor` на чистой сессии.
2. `build_model` с минимальным `ModelSpec` (2 блока, 1 провод), `screenshot=True`; открыть снимок и убедиться, что провод прямой.
3. `inspect_model` на той же модели.
4. `simulate(to_time=1)` и `read_output_file`, если есть блок «В файл».
5. `format_model(mode="place", check=True, screenshot=True)`.
6. Повторить `build_model` на копии сложной модели с 144 блоками и 213 проводами; снимок не должен содержать веер диагоналей.

- [ ] **Step 4: Commit исправлений приёмки**

```bash
git add -A
git commit -m "fix(surface): исправления по живой приёмке"
```

---

## Task 11: PR

**Files:**
- нет.

- [ ] **Step 1: Отправить ветку**

```bash
git push -u origin refactor/tool-surface-v2
```

- [ ] **Step 2: Создать PR**

```bash
gh pr create --repo producedbysavant/simintech-mcp \
  --base main --head refactor/tool-surface-v2 \
  --title "Рефакторинг поверхности инструментов: Core + Search + Composites" \
  --body-file - <<'EOF'
Closes #165

## Что сделано

- Видимое ядро: 9 инструментов и 5 композитов.
- Полный каталог: 59 низкоуровневых инструментов через search_tools.
- Краткие описания со ссылкой simintech://tools/<name>.
- build_model: Pydantic ModelSpec, провода через connect.
- doctor, inspect_model, simulate, format_model.
- FastMCP 4.1.1, MCP 2.3.0, Pydantic 2.14.
- 10 оценочных вопросов и тесты поверхности.

## Приёмка

- pytest tests/unit: 1154+ passed.
- Живой замер геометрии: текстовые провода на сложной модели дают веер диагоналей; connect даёт чистые маршруты.
- Живая приёмка после перезапуска сервера.

EOF
```

- [ ] **Step 3: Проверить, что PR закрывает issue**

Run:

```bash
gh pr view --repo producedbysavant/simintech-mcp --json number,title,body,closingIssuesReferences
```

Expected: `closingIssuesReferences` содержит #165.

---

## Самопроверка плана

- [x] Спека покрыта задачами 1-9.
- [x] Живой замер и геометрия покрыты задачей 6 и руководством.
- [x] Обновление FastMCP 4 покрыто задачей 1.
- [x] Поиск и краткие описания покрыты задачей 2.
- [x] Ресурсы документации покрыты задачей 3.
- [x] Pydantic ModelSpec покрыт задачей 4.
- [x] Композиты покрыты задачей 6.
- [x] README, help и дорожная карта покрыты задачами 7-8.
- [x] Оценочные вопросы покрыты задачей 9.
- [x] Живая приёмка и PR покрыты задачами 10-11.
