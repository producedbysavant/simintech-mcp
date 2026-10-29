"""MCP-инструменты Model IR: load_model, паспорт, запросы и свежесть."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_mcp import model_store, session  # noqa: E402
from simintech_mcp.server import mcp  # noqa: E402

from _support import _error, _text  # noqa: E402

MODEL = """<Header><project><page>
  <object>
    <name>`K`</name>
    <class_name>`Константа`</class_name>
    <obj_type>`100`</obj_type>
    <ports><port><name>`y`</name><mode>`1`</mode></port></ports>
  </object>
  <object>
    <name>`G`</name>
    <class_name>`Временной график`</class_name>
    <obj_type>`106`</obj_type>
    <ports><port><name>`u`</name><mode>`0`</mode></port></ports>
  </object>
  <object>
    <name>`W`</name>
    <class_name>`Математическая связь`</class_name>
    <obj_type>`101`</obj_type>
    <start_bl_index>`0`</start_bl_index><start_port_index>`0`</start_port_index>
    <end_bl_index>`1`</end_bl_index><end_port_index>`0`</end_port_index>
  </object>
</page></project></Header>"""

TEXT = """(
  K: (type = "Константа", a = 1),
  G: (type = "Временной график"),
  W: (type = "wire", src = "K:out:0", dst = "G:in:0")
)"""

TOPOLOGY = """object\tK\tКонстанта
object\tG\tВременной график
port\tK\t0\tout\ty
port\tG\t0\tin\tu
conn\tK\t0\tG\t0
"""


class _FakeSimulation:
    def get_state(self) -> int:
        return 0


class _FakeProject:
    id = 7

    def simulation(self) -> _FakeSimulation:
        return _FakeSimulation()

    def show_form(self) -> None:
        return None

    def save_xml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(MODEL)


def setup_function():
    model_store.clear()
    session.clear_unsaved()


@pytest.mark.anyio
async def test_load_file_manifest_and_query(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.xprt").write_text(MODEL, encoding="utf-8")

    result = _text(await mcp.call_tool("load_model", {"path": "m.xprt"}))
    data = json.loads(result)
    assert data["model_ref"].startswith("mir1.")
    assert data["counts"]["objects"] == 2
    assert "<?xml" not in result and "<object" not in result

    manifest = _text(await mcp.call_tool(
        "get_model_manifest",
        {"model_ref": data["model_ref"], "budget_tokens": 2000}))
    assert "# Model IR" in manifest
    assert "## Coverage" in manifest

    query = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": data["model_ref"], "query": "inspect_node",
         "arguments": {"node": "main::K"}})))
    assert query["items"][0]["address"] == "main::K"
    assert query["model_ref"] == data["model_ref"]


@pytest.mark.anyio
async def test_text_and_topology_files(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.txt").write_text(TEXT, encoding="utf-8")
    text = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.txt"})))
    assert text["sources"] == ["model_text"]
    assert text["counts"]["ports_synthetic"] > 0

    (tmp_path / "m.topology").write_text(TOPOLOGY, encoding="utf-8")
    topo = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.topology"})))
    assert topo["sources"] == ["topology"]
    assert "geometry_not_in_topology" in topo["coverage"]["skipped"]


@pytest.mark.anyio
async def test_current_project_becomes_stale_after_mutation(monkeypatch):
    monkeypatch.setattr(session, "ensure_project", lambda: _FakeProject())
    result = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "", "sources": ["xprt"]})))
    ref = result["model_ref"]
    assert result["counts"]["objects"] == 2

    session.mark_mutated()
    text = await _error("query_model",
                        {"model_ref": ref, "query": "coverage"})
    assert "устарел" in text
    session.clear_unsaved()


@pytest.mark.anyio
async def test_current_project_snapshot_shows_form_before_save(monkeypatch):
    calls = []

    class Simulation:
        def get_state(self) -> int:
            calls.append("state")
            return 0

    class Project(_FakeProject):
        def simulation(self) -> Simulation:
            return Simulation()

        def show_form(self) -> None:
            calls.append("show")

        def save_xml(self, path: str) -> None:
            calls.append("save")
            super().save_xml(path)

    monkeypatch.setattr(session, "ensure_project", lambda: Project())
    _text(await mcp.call_tool(
        "load_model", {"path": "", "sources": ["xprt"]}))

    assert calls == ["state", "show", "save"]


@pytest.mark.anyio
async def test_current_project_snapshot_refuses_running(monkeypatch):
    class Simulation:
        def get_state(self) -> int:
            return 3

    class Project(_FakeProject):
        def simulation(self) -> Simulation:
            return Simulation()

    monkeypatch.setattr(session, "ensure_project", lambda: Project())
    text = await _error("load_model", {"path": "", "sources": ["xprt"]})
    assert "не в остановленном состоянии" in text


@pytest.mark.anyio
async def test_evicted_ref_and_compare_models(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "one.xprt").write_text(MODEL, encoding="utf-8")
    (tmp_path / "two.xprt").write_text(
        MODEL.replace("`Константа`", "`Интегратор`"), encoding="utf-8")
    one = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "one.xprt"})))
    two = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "two.xprt"})))
    diff = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": one["model_ref"], "query": "compare_models",
         "arguments": {"other_ref": two["model_ref"]}})))
    assert any(item["kind"] == "class_changed" for item in diff["items"])

    model_store.clear()
    text = await _error("query_model",
                        {"model_ref": one["model_ref"], "query": "coverage"})
    assert "неизвестна" in text


@pytest.mark.anyio
async def test_bad_source_and_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.txt").write_text(TEXT, encoding="utf-8")
    text = await _error("load_model",
                        {"path": "m.txt", "sources": ["xprt"]})
    assert "задаёт источник" in text
    (tmp_path / "m.bin").write_bytes(b"x")
    text = await _error("load_model", {"path": "m.bin"})
    assert "ожидается" in text
