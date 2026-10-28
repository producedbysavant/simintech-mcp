"""Поверхность v2: ядро, краткие описания, поиск, полный каталог."""

from __future__ import annotations

import json

import pytest

from simintech_mcp import surface
from simintech_mcp.server import mcp

from _support import _text


@pytest.mark.anyio
async def test_visible_surface_is_core_plus_search():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == set(surface.CORE_TOOLS) | {"search_tools", "call_tool"}
    assert len(names) == len(surface.CORE_TOOLS) + 2


@pytest.mark.anyio
async def test_catalog_still_has_all_tools():
    tools = await surface.catalog_tools(mcp)
    assert len(tools) == 68


@pytest.mark.anyio
async def test_short_descriptions_are_small():
    tools = await surface.catalog_tools(mcp)
    for tool in tools:
        short = surface.short_description(
            tool.name, tool.description or "", tool)
        assert len(short) <= surface.MAX_SHORT_DESCRIPTION, (
            tool.name, len(short))
    total = sum(len(surface.short_description(
        tool.name, tool.description or "", tool)) for tool in tools)
    assert total < 8000, total


@pytest.mark.anyio
async def test_search_finds_key_tools():
    result = await mcp.call_tool(
        "search_tools", {"query": "connect two blocks with a wire"})
    data = json.loads(_text(result))
    names = {item["name"] for item in data}
    assert "connect" in names


@pytest.mark.anyio
async def test_search_finds_annotations():
    result = await mcp.call_tool(
        "search_tools", {"query": "TextLabel annotations"})
    data = json.loads(_text(result))
    names = {item["name"] for item in data}
    assert "list_annotations" in names


@pytest.mark.anyio
async def test_hidden_tool_is_still_callable():
    tool = await mcp.get_tool("add_block")
    assert tool is not None


def test_discovery_mode_default_is_search(monkeypatch):
    monkeypatch.delenv(surface.DISCOVERY_ENV, raising=False)
    assert surface.discovery_mode() == surface.DISCOVERY_SEARCH


def test_discovery_mode_all(monkeypatch):
    monkeypatch.setenv(surface.DISCOVERY_ENV, surface.DISCOVERY_ALL)
    assert surface.discovery_mode() == surface.DISCOVERY_ALL


def test_discovery_mode_unknown_falls_back_to_search(monkeypatch):
    monkeypatch.setenv(surface.DISCOVERY_ENV, "опечатка")
    assert surface.discovery_mode() == surface.DISCOVERY_SEARCH


def test_short_description_never_exceeds_limit():
    text = surface.short_description("x", "a" * 1000)
    assert len(text) <= surface.MAX_SHORT_DESCRIPTION


@pytest.mark.anyio
async def test_help_text_points_to_full_catalog():
    text = _text(await mcp.call_tool("help_text", {}))
    assert "simintech://tools" in text
    assert "search_tools" in text
    assert "simintech://guides/block-sizing" in text


@pytest.mark.anyio
async def test_instructions_link_block_sizing_guide():
    """Инструкция сервера называет правило размеров nport-блоков."""
    from simintech_mcp.server import mcp

    assert "simintech://guides/block-sizing" in (mcp.instructions or "")


@pytest.mark.anyio
async def test_search_finds_set_block_param_by_nport():
    """Поиск по nport ведёт к set_block_param с пересборкой пинов."""
    result = await mcp.call_tool("search_tools", {"query": "nport пины высота"})
    data = json.loads(_text(result))
    names = {item["name"] for item in data}
    assert "set_block_param" in names


@pytest.mark.anyio
async def test_short_descriptions_name_size_fit_and_nport():
    """Краткие описания композитов и параметра называют размеры и nport."""
    tools = await surface.catalog_tools(mcp)
    descriptions = {
        tool.name: surface.short_description(
            tool.name, tool.description or "", tool)
        for tool in tools
    }
    assert "размер" in descriptions["build_model"].lower()
    assert "размер" in descriptions["format_model"].lower()
    assert "nport" in descriptions["set_block_param"].lower()
    assert "пин" in descriptions["set_block_param"].lower()


def test_eval_has_ten_pairs():
    from pathlib import Path

    import defusedxml.ElementTree as ET

    path = (Path(__file__).resolve().parents[1]
            / "eval" / "simintech_mcp_eval.xml")
    root = ET.parse(path).getroot()

    assert len(root.findall("qa_pair")) == 10
