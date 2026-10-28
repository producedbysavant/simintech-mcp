"""Единственный экземпляр MCP-сервера.

`FastMCP` создаётся здесь и только здесь: инструменты, ресурсы и промпты
регистрируются в нём декораторами из своих модулей. Один экземпляр на процесс —
условие того, что клиент видит цельный набор инструментов.

Версия — своя, а не библиотечная: без `version=` `FastMCP` объявляет клиенту
версию fastmcp (в `serverInfo.version`), и после апгрейда зависимости число
сменится без единой правки здесь. Источник — `simintech_mcp.__version__`.
"""

from __future__ import annotations

from fastmcp import FastMCP

from . import __version__
from .surface import build_transforms


# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
    version=__version__,
    instructions=(
        "Управление SimInTech через COM API (Windows, mmain.exe /regserver). "
        "Начните с doctor. Сборка: build_model(spec=...) с ModelSpec; "
        "верхнеуровневые провода создаются connect, не текстом. "
        "Обзор: inspect_model; расчёт: simulate; оформление: format_model. "
        "Полная сборка с проверкой: build_and_verify(spec=SystemSpec); "
        "ответ несёт вердикт, partial не маскируется. "
        "Полная модель: load_model -> get_model_manifest -> query_model; "
        "XML в обычный ответ не попадает. "
        "Размеры блоков с nport и повторная раскладка после смены числа "
        "входов: simintech://guides/block-sizing. "
        "Нестандартные операции ищутся search_tools; полные описания "
        "инструментов: simintech://tools/<name>. "
        "Имена параметров блоков: simintech://blocks/catalog. "
        "Особенности среды: репозиторий simintech-code, CLAUDE.md и "
        "docs/reference/com_api_inventory.md."
    ),
    transforms=build_transforms(),
)
