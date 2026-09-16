"""Единственный экземпляр MCP-сервера.

`FastMCP` создаётся здесь и только здесь: инструменты, ресурсы и промпты
регистрируются в нём декораторами из своих модулей. Один экземпляр на процесс —
условие того, что клиент видит цельный набор инструментов.
"""

from __future__ import annotations

from fastmcp import FastMCP


# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
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
