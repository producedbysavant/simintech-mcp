"""FastMCP-сервер для управления SimInTech.

Точка входа и сборка: сам код разложен по модулям.

* `app` — единственный экземпляр `FastMCP`;
* `runtime` — COM-поток, контракт отказа, журнал;
* `session` — состояние сессии;
* `sandbox`, `tables` — чтение результатов и разбор таблиц;
* `catalog`, `skills` — каталог блоков и скиллы;
* `tools/` — инструменты по предметным областям;
* `resources`, `prompts` — ресурсы и промпты.

Запуск:
    simintech-mcp
    python -m simintech_mcp.server
"""

from __future__ import annotations

from . import prompts, resources, stdio, tools  # noqa: F401
from .app import mcp


def main() -> None:
    """Точка входа MCP-сервера (stdio).

    Изоляция stdout выполняется ДО запуска транспорта: иначе первый же
    `print()` из библиотеки повредит JSON-RPC.
    """
    stdio.isolate_stdout()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
