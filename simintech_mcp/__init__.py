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

__version__ = "0.1.0"
