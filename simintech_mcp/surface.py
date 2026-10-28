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
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec


#: Переменная окружения: `search` (по умолчанию) или `all`.
DISCOVERY_ENV = "SIMINTECH_MCP_DISCOVERY"
DISCOVERY_SEARCH = "search"
DISCOVERY_ALL = "all"

#: Сколько результатов отдаёт поиск за один запрос.
SEARCH_RESULTS = 5

#: Предел краткого описания вместе со ссылкой на полный ресурс.
MAX_SHORT_DESCRIPTION = 320

#: Инструменты, которые видны без поиска. Композиты идут первыми в работе
#: агента, низкоуровневые остаются доступны через `search_tools`.
CORE_TOOLS = (
    "doctor",
    "create_project",
    "open_project",
    "build_model",
    "build_and_verify",
    "inspect_model",
    "load_model",
    "get_model_manifest",
    "query_model",
    "simulate",
    "format_model",
    "read_output_file",
    "help_text",
)

#: Ручные краткие описания: первый абзац docstring у этих инструментов
#: слишком короткий или слишком общий для поиска.
SHORT_DESCRIPTIONS = {
    "doctor": (
        "Проверить окружение SimInTech: COM, mmain, каталог результатов, "
        "скиллы, версии. Начните отсюда."
    ),
    "build_model": (
        "Собрать модель из ModelSpec: блоки, субмодели, провода через "
        "connect, подгонка размеров, раскладка, проверка, снимок."
    ),
    "build_and_verify": (
        "Собрать модель по SystemSpec, прочитать IR и вердикт; "
        "partial не выдаётся за passed."
    ),
    "inspect_model": (
        "Кратко показать блоки, линии и параметры текущей страницы без "
        "запуска расчёта."
    ),
    "load_model": (
        "Загрузить текущий проект или файл в Model IR; вернуть model_ref, "
        "счётчики, покрытие и первые расхождения без XML."
    ),
    "get_model_manifest": (
        "Компактный паспорт снимка IR в бюджете 2000..5000 токенов."
    ),
    "query_model": (
        "Точный запрос к полному графу IR: trace, пути, циклы, проверки, "
        "сравнение моделей."
    ),
    "simulate": (
        "Задать время, запустить расчёт или шаги, прочитать и свести "
        "результат."
    ),
    "format_model": (
        "Подогнать размеры по числу входов, разложить блоки, нормализовать "
        "линии, вписать кадр, проверить оформление, сделать снимок."
    ),
    "set_block_param": (
        "Записать параметр блока; nport пересобирает пины, ставит высоту "
        "16 px на вход и нормализует линии."
    ),
    "fit_port_blocks": (
        "Подогнать высоту блоков по числу входов и ширину порт-блоков по "
        "подписям; назвать наложения."
    ),
    "import_model_text": (
        "Импортировать декларативный текст модели; верхнеуровневые провода "
        "лучше создавать connect."
    ),
    "export_model_text": (
        "Выгрузить модель в декларативный текст: блоки, субмодели, концы "
        "проводов."
    ),
    "list_annotations": (
        "Прочитать текст подписей TextLabel/Комментарий и имена ячеек "
        "памяти по всем страницам из снимка проекта; расчёт не запускается."
    ),
    "connect": "Соединить выход блока src с входом dst по фактическим портам.",
    "layout_place": (
        "Расставить блоки по слоям, применить координаты и нормализовать линии."
    ),
    "save_screenshot": (
        "Сохранить снимок схемы PNG/BMP/SVG и посмотреть раскладку глазами."
    ),
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
    "model_ir": "model",
    "system_spec": "model",
    "annotations": "text",
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
    "list_signals", "get_signal", "read_output_file", "list_annotations",
    "summarize_output_file", "inspect_project_file", "project_network_role",
    "list_pack_projects", "help_text", "search_language_functions",
    "get_language_function", "doctor", "inspect_model",
    "get_model_manifest", "query_model",
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
    # остаётся безопасным, а doctor называет фактическое значение.
    return DISCOVERY_SEARCH


def _first_paragraph(text: str) -> str:
    paragraph = re.split(r"\n\s*\n", (text or "").strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", paragraph).strip()


def _tag_for(name: str, tool: Tool | None) -> str:
    if tool is not None:
        fn = getattr(tool, "fn", None)
        module = getattr(fn, "__module__", "")
        tag = _MODULE_TAGS.get(module.rsplit(".", 1)[-1])
        if tag:
            return tag
    return ""


def short_description(name: str, description: str,
                      tool: Tool | None = None) -> str:
    """Краткое описание: первый абзац, тег и ссылка на полный ресурс."""
    text = SHORT_DESCRIPTIONS.get(name) or _first_paragraph(description)
    if not text:
        text = name
    tag = _tag_for(name, tool)
    if tag:
        text = f"[{tag}] {text}"
    suffix = f"\n\nFull docs: simintech://tools/{name}"
    limit = MAX_SHORT_DESCRIPTION - len(suffix)
    if len(text) > limit:
        cut = text[:max(limit - 3, 0)]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:.-") + "..."
    return text + suffix


class ToolSurfaceTransform(Transform):
    """Сократить описания и заполнить недостающие аннотации."""

    def _apply(self, tool: Tool) -> Tool:
        annotations = tool.annotations
        if annotations is not None:
            update: dict[str, bool] = {}
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

    async def get_tool(self, name: str, call_next: GetToolNext, *,
                       version: VersionSpec | None = None) -> Tool | None:
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
