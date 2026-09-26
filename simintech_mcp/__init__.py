"""MCP-сервер для управления SimInTech через библиотеку simintech-api.

Запуск (Windows):
    simintech-mcp
    # или
    python -m simintech_mcp.server

Подключение в Claude Code (claude.json / claude mcp add):
    {
        "mcpServers": {
            "simintech": {
                "command": "simintech-mcp"
            }
        }
    }
"""

#: Источник истины для версии: `pyproject.toml` объявляет её динамической и
#: читает отсюда. Прописывать её ещё и там — значит однажды снова разойтись.
__version__ = "0.2.0"
