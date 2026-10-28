"""Инструменты MCP-сервера, разложенные по предметным областям.

Импорт этого пакета регистрирует все инструменты в `app.mcp`: FastMCP
регистрирует их декоратором при импорте модуля. Модуль, который никто не
импортировал, молча не отдаст свои инструменты в `tools/list`.
"""

from . import (annotations, block_script, blocks, check_model, files, fits,
               help, layout, model_ir, model_text, pack, page_script,
               project, screenshot, simulation, sizes, wires, workflows)

__all__ = [
    "annotations",
    "block_script",
    "blocks",
    "check_model",
    "files",
    "fits",
    "help",
    "layout",
    "model_ir",
    "model_text",
    "pack",
    "page_script",
    "project",
    "screenshot",
    "simulation",
    "sizes",
    "wires",
    "workflows",
]
