"""Тесты MCP-сервера: регистрация инструментов и логика без COM."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from simintech_mcp.server import mcp


@pytest.mark.anyio
async def test_all_tools_registered():
    """Зарегистрированы все ожидаемые инструменты."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "status", "disconnect",
        "create_project", "open_project", "save_project", "close_project",
        "add_block", "connect", "list_blocks",
        "run", "step", "stop", "get_time",
        "list_signals", "get_signal", "set_signal",
        "layout_place", "help_text",
    }
    assert expected <= names, f"Не хватает: {expected - names}"


@pytest.mark.anyio
async def test_status_on_linux():
    """status() на Linux сообщает о несовместимости платформы."""
    if sys.platform == "win32":
        pytest.skip("Тест для не-Windows окружения")
    result = await mcp.call_tool("status", {})
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "Windows" in text


@pytest.mark.anyio
async def test_help_text_tool():
    """help_text возвращает справку с ключевыми командами."""
    result = await mcp.call_tool("help_text", {})
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "add_block" in text
    assert "create_project" in text
    assert "get_signal" in text


@pytest.mark.anyio
async def test_layout_place_works_without_com():
    """layout_place работает без COM (чистый алгоритм)."""
    result = await mcp.call_tool(
        "layout_place",
        {"block_ids": "A,B,C", "connections": "A->B,B->C"},
    )
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "A:" in text
    assert "B:" in text
    assert "C:" in text
