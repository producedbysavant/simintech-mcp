"""P5: контекст и полнота MCP на синтетических моделях."""

from __future__ import annotations

import json

import pytest

from simintech_mcp import model_store, session
from simintech_mcp.server import mcp

from _support import _error, _text
from _ir_synth import (
    chain,
    cycle,
    direct_star,
    loose,
    self_loop,
    star,
    unknown_type,
    verbose_classes,
    write,
)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def setup_function():
    model_store.clear()
    session.clear_unsaved()


async def _load(sandbox, text: str, name: str = "m.xprt") -> dict:
    write(sandbox, name, text)
    return json.loads(_text(await mcp.call_tool(
        "load_model", {"path": name})))


async def _query(loaded: dict, query: str,
                 arguments: dict | None = None) -> dict:
    return json.loads(_text(await mcp.call_tool(
        "query_model", {"model_ref": loaded["model_ref"],
                        "query": query,
                        "arguments": arguments or {}})))


@pytest.mark.anyio
async def test_load_model_summary_is_bounded_and_xml_free(sandbox):
    models = [
        chain(300),
        unknown_type(300),
        verbose_classes(300, classes=20, width=300),
    ]
    for index, text in enumerate(models):
        data = await _load(sandbox, text, f"m{index}.xprt")
        packed = json.dumps(data, ensure_ascii=False).encode("utf-8")
        assert len(packed) <= 4096, (index, len(packed))
        for forbidden in ("<?xml", "<object", "<ports>", "<name>"):
            assert forbidden not in packed.decode("utf-8")


@pytest.mark.anyio
async def test_load_model_summary_does_not_grow_with_model(sandbox):
    small = await _load(sandbox, chain(10), "small.xprt")
    big = await _load(sandbox, chain(300), "big.xprt")
    small_size = len(json.dumps(small, ensure_ascii=False).encode("utf-8"))
    big_size = len(json.dumps(big, ensure_ascii=False).encode("utf-8"))
    assert big_size - small_size <= 256


@pytest.mark.anyio
async def test_load_model_counts_and_coverage_match_generator(sandbox):
    chain_data = await _load(sandbox, chain(300), "chain.xprt")
    assert chain_data["counts"]["objects"] == 300
    assert chain_data["counts"]["wires"] == 299
    assert chain_data["counts"]["ports_real"] == 600
    assert chain_data["counts"]["branches"] == 0
    assert chain_data["coverage"]["source_status"] == "ok"
    assert chain_data["coverage"]["unresolved_ends"] == 0
    assert chain_data["coverage"]["unconnected_ends"] == 0

    star_data = await _load(sandbox, star(300), "star.xprt")
    assert star_data["counts"]["objects"] == 301
    assert star_data["counts"]["wires"] == 300
    assert star_data["counts"]["branches"] == 299


@pytest.mark.anyio
async def test_load_model_reports_unknown_object_types(sandbox):
    data = await _load(sandbox, unknown_type(300), "unknown.xprt")
    assert data["counts"]["objects"] == 300
    assert data["coverage"]["unknown_object_types"] == 300
    assert data["discrepancies"]["total"] == 300
    assert data["discrepancies"]["by_code"] == {"unknown_object_type": 300}
    assert len(data["discrepancies"]["sample"]) == 5


@pytest.mark.anyio
@pytest.mark.parametrize("objects", [10, 300, 1200])
async def test_query_find_cycles_acyclic_chain_is_empty(sandbox, objects):
    loaded = await _load(sandbox, chain(objects), f"chain{objects}.xprt")
    result = await _query(loaded, "find_cycles")
    assert result["items"] == []
    assert result["truncated"] is False


@pytest.mark.anyio
async def test_query_find_cycles_reports_real_loop(sandbox):
    loaded = await _load(sandbox, cycle(), "cycle.xprt")
    result = await _query(loaded, "find_cycles")
    assert len(result["items"]) == 1
    assert set(result["items"][0]["objects"]) == {"main::A", "main::B"}


@pytest.mark.anyio
async def test_query_find_cycles_keeps_wire_self_loop(sandbox):
    loaded = await _load(sandbox, self_loop(), "self.xprt")
    result = await _query(loaded, "find_cycles")
    assert result["items"][0]["objects"] == ["main::A"]


@pytest.mark.anyio
async def test_query_check_connections_caps_items_after_contracts(sandbox):
    loaded = await _load(sandbox, loose(300), "loose.xprt")
    result = await _query(loaded, "check_connections")
    assert len(result["items"]) == 200
    assert result["truncated"] is True
    assert result["evidence"]["violations_total"] == 600
    assert result["evidence"]["contracts"]["not_evaluated"] == 300


@pytest.mark.anyio
async def test_query_check_connections_names_unknown_class(sandbox):
    loaded = await _load(
        sandbox, chain(300, class_name="Нет такого класса"),
        "unknown-class.xprt")
    result = await _query(loaded, "check_connections")
    contracts = result["evidence"]["contracts"]
    assert contracts["evaluated"] == 0
    assert contracts["not_evaluated"] == 300
    assert contracts["by_reason"] == {"class_not_in_registry": 300}
    assert len(result["items"]) <= 200


@pytest.mark.anyio
async def test_query_inspect_node_caps_nested_wires(sandbox):
    loaded = await _load(sandbox, direct_star(300), "star.xprt")
    result = await _query(
        loaded, "inspect_node", {"node": "main::S"})
    assert len(result["items"][0]["wires"]) == 200
    assert result["items"][0]["wires_total"] == 300
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_query_ancestors_caps_items(sandbox):
    loaded = await _load(sandbox, chain(300), "ancestors.xprt")
    result = await _query(
        loaded, "ancestors", {"node": "main::B299", "max_depth": 10000})
    assert len(result["items"]) == 200
    assert result["truncated"] is True
    assert result["evidence"]["items_total"] == 300


@pytest.mark.anyio
async def test_query_find_objects_limit_cannot_exceed_cap(sandbox):
    loaded = await _load(sandbox, chain(300), "objects.xprt")
    result = await _query(
        loaded, "find_objects", {"query": "B", "limit": 1000})
    assert len(result["items"]) == 200
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_query_compare_models_caps_items(sandbox):
    one = await _load(sandbox, chain(300), "one.xprt")
    write(sandbox, "two.xprt", chain(300, class_name="Интегратор"))
    two = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "two.xprt"})))
    result = await _query(
        one, "compare_models", {"other_ref": two["model_ref"]})
    assert len(result["items"]) == 200
    assert result["truncated"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("budget", [2000, 3000, 5000])
async def test_manifest_hard_budget(sandbox, budget):
    loaded = await _load(sandbox, chain(300), "manifest.xprt")
    text = _text(await mcp.call_tool(
        "get_model_manifest",
        {"model_ref": loaded["model_ref"], "budget_tokens": budget}))
    assert len(text) <= budget * 3
    assert text.startswith("# Model IR")
    assert "## Coverage" in text
    assert "<?xml" not in text and "<object" not in text
    again = _text(await mcp.call_tool(
        "get_model_manifest",
        {"model_ref": loaded["model_ref"], "budget_tokens": budget}))
    assert again == text


@pytest.mark.anyio
async def test_manifest_omitted_is_reserved(sandbox):
    loaded = await _load(
        sandbox, verbose_classes(300, classes=20, width=300), "wide.xprt")
    text = _text(await mcp.call_tool(
        "get_model_manifest",
        {"model_ref": loaded["model_ref"], "budget_tokens": 2000,
         "focus": "classes"}))
    assert len(text) <= 6000
    assert "## Omitted" in text
    assert "classes" in text.split("## Omitted", 1)[1]


@pytest.mark.anyio
async def test_manifest_budget_outside_range_is_error(sandbox):
    loaded = await _load(sandbox, chain(10), "budget.xprt")
    text = await _error(
        "get_model_manifest",
        {"model_ref": loaded["model_ref"], "budget_tokens": 1000})
    assert "допустимо 2000..5000" in text
