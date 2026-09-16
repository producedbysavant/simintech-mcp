"""Регистрация инструментов, ресурсов и промптов: клиент должен видеть цельный набор."""

from __future__ import annotations

import pytest

from simintech_mcp import runtime
from simintech_mcp.server import mcp

from _support import _text


@pytest.mark.anyio
async def test_all_tools_registered():
    """Зарегистрированы все ожидаемые инструменты."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "status", "disconnect",
        "create_project", "open_project", "save_project", "close_project",
        "set_calc_time",
        "add_block", "connect", "list_blocks",
        "get_block_params", "set_block_param",
        "run", "step", "stop", "get_time",
        "list_signals", "get_signal", "set_signal",
        "read_output_file", "summarize_output_file", "inspect_project_file",
        "layout_place", "help_text",
    }
    assert expected <= names, f"Не хватает: {expected - names}"


@pytest.mark.anyio
async def test_help_text_tool():
    """help_text возвращает справку с ключевыми командами."""
    text = _text(await mcp.call_tool("help_text", {}))
    assert "add_block" in text
    assert "create_project" in text
    # Перечень инструментов живёт в tools/list, а не в справке.
    assert "tools/list" in text


@pytest.mark.anyio
async def test_help_text_does_not_use_com_thread(monkeypatch):
    """Справка не трогает COM — и не должна вставать в очередь COM-потока.

    Под `_com_threaded` она занимала бы единственный выделенный поток: при
    занятом `mmain.exe` справка упиралась бы в `COM_CALL_TIMEOUT`, хотя ей это
    не нужно. Проверка сторожит именно выбор декоратора.
    """
    def boom(*args, **kwargs):
        raise AssertionError("справка ушла в COM-поток")

    monkeypatch.setattr(runtime._COM_EXECUTOR, "submit", boom)

    text = _text(await mcp.call_tool("help_text", {}))

    assert "create_project" in text


@pytest.mark.anyio
async def test_resources_registered():
    """Зарегистрированы ресурсы simintech://."""
    res = await mcp.list_resources()
    uris = {str(getattr(r, "uri", r)) for r in res}
    assert "simintech://status" in uris
    assert "simintech://project/blocks" in uris


@pytest.mark.anyio
async def test_prompts_registered():
    """Зарегистрированы промпты-шаблоны."""
    prompts = await mcp.list_prompts()
    names = {getattr(p, "name", str(p)) for p in prompts}
    assert "create_pid_model" in names
    assert "create_rc_chain" in names


@pytest.mark.anyio
async def test_render_prompt_pid():
    """Промпт create_pid_model подставляет параметры."""
    rendered = await mcp.render_prompt(
        "create_pid_model",
        arguments={"kp": "1.5", "setpoint": "2.0"},
    )
    text = str(rendered)
    assert "create_project" in text
    assert "yk=2.0" in text


@pytest.mark.anyio
async def test_render_prompt_rc():
    """Промпт create_rc_chain работает."""
    rendered = await mcp.render_prompt(
        "create_rc_chain",
        arguments={"rc": "1.0", "amplitude": "3.0"},
    )
    text = str(rendered)
    assert "create_project" in text
    assert "yk=3.0" in text


@pytest.mark.anyio
async def test_static_resources_are_registered():
    """Каталог блоков и скиллы отдаются как ресурсы, а не только инструменты."""
    resources = await mcp.list_resources()
    uris = {str(item.uri) for item in resources}

    assert {"simintech://status", "simintech://project/blocks",
            "simintech://blocks/catalog", "simintech://skills"} <= uris
