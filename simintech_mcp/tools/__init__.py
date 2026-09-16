"""Инструменты MCP-сервера, разложенные по предметным областям.

Импорт этого пакета регистрирует все инструменты в `app.mcp`: FastMCP
регистрирует их декоратором при импорте модуля. Модуль, который никто не
импортировал, молча не отдаст свои инструменты в `tools/list`.
"""

from . import blocks, files, help, layout, project, simulation

__all__ = ["blocks", "files", "help", "layout", "project", "simulation"]
