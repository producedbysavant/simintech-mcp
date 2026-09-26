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


# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
    version=__version__,
    instructions=(
        "Управление SimInTech через COM API (Windows, mmain.exe /regserver). "
        "Сборка и расчёт модели: create_project → add_block → connect → run → "
        "read_output_file (результат пишет блок «В файл»). "
        "Имена параметров блоков берите из ресурса simintech://blocks/catalog: "
        "неизвестное имя — отказ, запись в вычисляемый параметр — тоже. "
        "Полный список инструментов — в tools/list, он же источник истины. "
        "Особенности среды — в репозитории simintech-code: CLAUDE.md и "
        "docs/reference/com_api_inventory.md."
    ),
)
