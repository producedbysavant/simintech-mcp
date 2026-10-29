"""build_and_verify: SystemSpec, сборка, readback IR и вердикт."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from simintech_api.model_ir import parse_xprt

from simintech_mcp import model_store, session
from simintech_mcp.server import mcp
from simintech_mcp.specs import SystemSpec, render_model_text
from simintech_mcp.tools import system_spec as tool

from _support import _error, _text
from _ir_synth import blocks_spec, wrong_ports


CHAIN_XML = """<Header><project><page>
  <object>
    <name>`Step`</name>
    <class_name>`Ступенька`</class_name><obj_type>`100`</obj_type>
    <ports><port><name>`y`</name><mode>`1`</mode></port></ports>
  </object>
  <object>
    <name>`Gain`</name>
    <class_name>`Усилитель`</class_name><obj_type>`100`</obj_type>
    <ports>
      <port><name>`u`</name><mode>`0`</mode></port>
      <port><name>`y`</name><mode>`1`</mode></port>
    </ports>
  </object>
  <object>
    <name>`Graph`</name>
    <class_name>`Временной график`</class_name><obj_type>`106`</obj_type>
    <ports><port><name>`u`</name><mode>`0`</mode></port></ports>
  </object>
  <object>
    <name>`W1`</name>
    <class_name>`Математическая связь`</class_name><obj_type>`101`</obj_type>
    <start_bl_index>`0`</start_bl_index><start_port_index>`0`</start_port_index>
    <end_bl_index>`1`</end_bl_index><end_port_index>`0`</end_port_index>
  </object>
  <object>
    <name>`W2`</name>
    <class_name>`Математическая связь`</class_name>
    <obj_type>`101`</obj_type>
    <start_bl_index>`1`</start_bl_index><start_port_index>`1`</start_port_index>
    <end_bl_index>`2`</end_bl_index><end_port_index>`0`</end_port_index>
  </object>
</page></project></Header>"""

W2_XML = """  <object>
    <name>`W2`</name>
    <class_name>`Математическая связь`</class_name>
    <obj_type>`101`</obj_type>
    <start_bl_index>`1`</start_bl_index><start_port_index>`1`</start_port_index>
    <end_bl_index>`2`</end_bl_index><end_port_index>`0`</end_port_index>
  </object>
"""

CHAIN_SPEC = {
    "schema": "system-spec/1",
    "name": "chain",
    "blocks": [
        {"name": "Step", "class": "Ступенька",
         "ports": {"outputs": ["y"]}},
        {"name": "Gain", "class": "Усилитель",
         "ports": {"inputs": ["u"], "outputs": ["y"]}},
        {"name": "Graph", "class": "Временной график",
         "ports": {"inputs": ["u"]}},
    ],
    "wires": [
        {"src": "Step:out:0", "dst": "Gain:in:0"},
        {"src": "Gain:out:1", "dst": "Graph:in:0"},
    ],
}

MEMORY_XML = """<Header><project><page>
  <object>
    <name>`To`</name><class_name>`В память`</class_name><obj_type>`104`</obj_type>
    <ports><port><name>`u`</name><mode>`0`</mode></port></ports>
    <visual_props><data><name>`PortNames`</name><value>`VAR`</value></data></visual_props>
  </object>
  <object>
    <name>`From`</name><class_name>`Из памяти`</class_name><obj_type>`105`</obj_type>
    <ports><port><name>`y`</name><mode>`1`</mode></port></ports>
    <visual_props><data><name>`PortNames`</name><value>`VAR`</value></data></visual_props>
  </object>
</page></project></Header>"""

SUBMODEL_XML = """<Header><project><page>
  <object>
    <name>`Sub`</name><class_name>`Субмодель`</class_name><obj_type>`102`</obj_type>
    <ports>
      <port><name>`x`</name><mode>`0`</mode></port>
      <port><name>`y`</name><mode>`1`</mode></port>
    </ports>
    <page><name></name>
      <object>
        <name>`In`</name><class_name>`Порт входа`</class_name><obj_type>`103`</obj_type>
        <ports><port><name>`x`</name><mode>`1`</mode></port></ports>
      </object>
      <object>
        <name>`K`</name><class_name>`Усилитель`</class_name><obj_type>`100`</obj_type>
        <ports>
          <port><name>`u`</name><mode>`0`</mode></port>
          <port><name>`y`</name><mode>`1`</mode></port>
        </ports>
      </object>
      <object>
        <name>`Out`</name>
        <class_name>`Порт выхода`</class_name><obj_type>`103`</obj_type>
        <ports><port><name>`y`</name><mode>`0`</mode></port></ports>
      </object>
      <object>
        <name>`w1`</name>
        <class_name>`Математическая связь`</class_name>
        <obj_type>`101`</obj_type>
        <start_bl_index>`0`</start_bl_index>
        <start_port_index>`0`</start_port_index>
        <end_bl_index>`1`</end_bl_index><end_port_index>`0`</end_port_index>
      </object>
      <object>
        <name>`w2`</name>
        <class_name>`Математическая связь`</class_name>
        <obj_type>`101`</obj_type>
        <start_bl_index>`1`</start_bl_index>
        <start_port_index>`1`</start_port_index>
        <end_bl_index>`2`</end_bl_index><end_port_index>`0`</end_port_index>
      </object>
    </page>
  </object>
</page></project></Header>"""

SUBMODEL_SPEC = {
    "schema": "system-spec/1",
    "name": "sub",
    "blocks": [{
        "name": "Sub", "class": "Субмодель",
        "ports": {"inputs": ["x"], "outputs": ["y"]},
        "subsystem": {
            "blocks": [
                {"name": "In", "class": "Порт входа",
                 "ports": {"outputs": ["x"]}},
                {"name": "K", "class": "Усилитель",
                 "ports": {"inputs": ["u"], "outputs": ["y"]}},
                {"name": "Out", "class": "Порт выхода",
                 "ports": {"inputs": ["y"]}},
            ],
            "wires": [
                {"src": "In:out:0", "dst": "K:in:0"},
                {"src": "K:out:1", "dst": "Out:in:0"},
            ],
        },
    }],
}

INTEGRATOR_XML = """<Header><project><page>
  <object>
    <name>`Int`</name>
    <class_name>`Интегратор`</class_name><obj_type>`100`</obj_type>
    <ports>
      <port><name>`Port_0`</name><mode>`0`</mode></port>
      <port><name>`Port_1`</name><mode>`1`</mode></port>
    </ports>
  </object>
</page></project></Header>"""


def setup_function():
    model_store.clear()
    session.clear_unsaved()


def _patch_build(monkeypatch, calls, log=None):
    def fake(**kwargs):
        calls.append(kwargs)
        return log or "Модель собрана.\n\nСоединено проводов: 2 из 2"
    monkeypatch.setattr(tool, "build_model", fake)


def _patch_read(monkeypatch, xml):
    def fake(path, sources, strict):
        assert path == ""
        assert sources == ["xprt"]
        return parse_xprt(xml), True, 1
    monkeypatch.setattr(tool, "read_model", fake)


@pytest.mark.anyio
async def test_build_and_verify_passed(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    _patch_read(monkeypatch, CHAIN_XML)

    text = _text(await mcp.call_tool("build_and_verify", {"spec": CHAIN_SPEC}))

    assert "Вердикт: passed" in text, text
    assert "Сборка: провода 2 из 2, предупреждений 0" in text, text
    spec = calls[0]["spec"]
    assert calls[0]["new_project"] is True
    assert [block.type for block in spec.blocks] == [
        "Ступенька", "Усилитель", "Временной график"]
    assert spec.wires[0].src == "Step:out:0"


@pytest.mark.anyio
async def test_missing_wire_fails_and_warning_is_kept(monkeypatch):
    calls = []
    _patch_build(
        monkeypatch, calls,
        "Модель собрана.\n\nСоединено проводов: 1 из 2\n\n"
        "WARNING: провод Gain:out:1 -> Graph:in:0: порт занят")
    _patch_read(monkeypatch, CHAIN_XML.replace(W2_XML, ""))

    text = _text(await mcp.call_tool("build_and_verify", {"spec": CHAIN_SPEC}))

    assert "Вердикт: failed" in text, text
    assert "wire_missing" in text, text
    assert "WARNING: провод Gain:out:1 -> Graph:in:0: порт занят" in text


@pytest.mark.anyio
async def test_compile_collects_all_errors(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("сборка не должна начинаться")

    monkeypatch.setattr(tool, "build_model", fail)
    bad = {
        "schema": "system-spec/1",
        "blocks": [
            {"name": "A", "class": "Нет класса"},
            {"name": "A", "class": "Константа", "props": {"bad": 1}},
            {"name": "B", "class": "Константа",
             "ports": {"outputs": ["y"]}},
        ],
        "wires": [
            {"src": "B:out:0", "dst": "B:in:0"},
            {"src": "Z:out:0", "dst": "B:in:0"},
        ],
        "invariants": [{"kind": "bad"}],
    }

    text = await _error("build_and_verify", {"spec": bad})

    for code in ("unknown_class", "duplicate_name", "unknown_prop",
                 "self_loop", "unknown_block", "unknown_kind"):
        assert code in text, text


@pytest.mark.anyio
async def test_unknown_contract_is_partial(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    unknown_xml = """<Header><project><page>
  <object><name>`U`</name>
    <class_name>`Arduino - I2C - Ведомый I2C`</class_name>
    <obj_type>`100`</obj_type></object>
</page></project></Header>"""
    _patch_read(monkeypatch, unknown_xml)
    spec = {
        "schema": "system-spec/1",
        "name": "unknown",
        "blocks": [{"name": "U", "class": "Arduino - I2C - Ведомый I2C"}],
    }

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: partial" in text, text
    assert "port_unknown" in text, text


@pytest.mark.anyio
async def test_contract_ports_match_uses_registry(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    _patch_read(monkeypatch, INTEGRATOR_XML)
    spec = {
        "schema": "system-spec/1",
        "name": "contracts",
        "blocks": [{"name": "Int", "class": "Интегратор"}],
        "invariants": [{"id": "ports", "kind": "contract_ports_match"}],
    }

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: passed" in text, text
    assert "Контракты: проверено 1, ошибок 0, не проверено 0" in text, text
    assert "[passed] ports (invariant)" in text, text


@pytest.mark.anyio
async def test_portnames_prop_moves_to_record_field(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    _patch_read(monkeypatch, MEMORY_XML)
    spec = {
        "schema": "system-spec/1",
        "name": "memory",
        "blocks": [
            {"name": "To", "class": "В память",
             "ports": {"inputs": ["u"]}, "props": {"PortNames": "VAR"}},
            {"name": "From", "class": "Из памяти",
             "ports": {"outputs": ["y"]}, "props": {"PortNames": "VAR"}},
        ],
        "invariants": [{"id": "mem", "kind": "memory_pair_complete"}],
    }

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: passed" in text, text
    blocks = calls[0]["spec"].blocks
    assert [block.portnames for block in blocks] == ["VAR", "VAR"]
    assert all("PortNames" not in block.props for block in blocks)
    assert 'portnames = "VAR"' in render_model_text(calls[0]["spec"])


@pytest.mark.anyio
async def test_portnames_without_hints_is_partial(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    _patch_read(monkeypatch, MEMORY_XML)
    spec = {
        "schema": "system-spec/1",
        "name": "memory-no-hints",
        "blocks": [
            {"name": "To", "class": "В память", "portnames": "VAR"},
            {"name": "From", "class": "Из памяти", "portnames": "VAR"},
        ],
    }

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: partial" in text, text
    assert "port_unknown" in text, text
    assert "port_mismatch" not in text, text


@pytest.mark.anyio
async def test_contract_ports_match_on_portnames_is_partial(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    _patch_read(monkeypatch, MEMORY_XML)
    spec = {
        "schema": "system-spec/1",
        "name": "memory-contracts",
        "blocks": [
            {"name": "To", "class": "В память",
             "ports": {"inputs": ["u"]}, "props": {"PortNames": "VAR"}},
            {"name": "From", "class": "Из памяти",
             "ports": {"outputs": ["y"]}, "props": {"PortNames": "VAR"}},
        ],
        "invariants": [{"id": "ports", "kind": "contract_ports_match"}],
    }

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: partial" in text, text
    assert "portnames_instance_defined" in text, text
    assert "Вердикт: failed" not in text, text


@pytest.mark.anyio
async def test_portnames_on_plain_class_is_rejected(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("сборка не должна начинаться")

    monkeypatch.setattr(tool, "build_model", fail)
    spec = {
        "schema": "system-spec/1",
        "blocks": [{"name": "K", "class": "Константа",
                    "props": {"PortNames": "X"}}],
    }

    text = await _error("build_and_verify", {"spec": spec})

    assert "unknown_prop" in text, text


@pytest.mark.anyio
async def test_bad_invariant_max_is_rejected_before_build(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("сборка не должна начинаться")

    monkeypatch.setattr(tool, "build_model", fail)
    spec = dict(CHAIN_SPEC)
    spec["invariants"] = [
        {"id": "fan", "kind": "max_fanout", "max": "many"}]

    text = await _error("build_and_verify", {"spec": spec})

    assert "bad_max" in text, text


@pytest.mark.anyio
async def test_duplicate_invariant_ids_are_rejected(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("сборка не должна начинаться")

    monkeypatch.setattr(tool, "build_model", fail)
    spec = dict(CHAIN_SPEC)
    spec["invariants"] = [
        {"id": "same", "kind": "contract_ports_match"},
        {"id": "same", "kind": "unique_object_names"},
    ]

    text = await _error("build_and_verify", {"spec": spec})

    assert "duplicate_invariant_id" in text, text


@pytest.mark.anyio
async def test_bad_name_is_collected_with_compile_errors(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("сборка не должна начинаться")

    monkeypatch.setattr(tool, "build_model", fail)
    spec = {
        "schema": "system-spec/1",
        "blocks": [{"name": "Плохое имя", "class": "Нет класса"}],
    }

    text = await _error("build_and_verify", {"spec": spec})

    assert "bad_name" in text and "unknown_class" in text, text


def test_profile_and_port_hints_are_strict():
    with pytest.raises(ValidationError):
        SystemSpec.model_validate({
            "schema": "system-spec/1",
            "verification_profile": {"readback": None},
        })
    with pytest.raises(ValidationError):
        SystemSpec.model_validate({
            "schema": "system-spec/1",
            "verification_profile": {"build_strategy": "text"},
        })
    with pytest.raises(ValidationError):
        SystemSpec.model_validate({
            "schema": "system-spec/1",
            "blocks": [{"name": "A", "class": "Константа",
                        "ports": {"ouputs": ["y"]}}],
        })


@pytest.mark.anyio
async def test_port_block_without_hints_is_partial(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    xml = """<Header><project><page>
      <object><name>`In`</name><class_name>`Порт входа`</class_name>
        <obj_type>`103`</obj_type>
        <ports><port><name>`x`</name><mode>`1`</mode></port></ports>
      </object>
    </page></project></Header>"""
    _patch_read(monkeypatch, xml)
    spec = {
        "schema": "system-spec/1",
        "blocks": [{"name": "In", "class": "Порт входа"}],
    }

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: partial" in text, text
    assert "port_unknown" in text, text
    assert "port_mismatch" not in text, text


@pytest.mark.anyio
async def test_max_items_limits_the_list(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    broken = CHAIN_XML.replace("`Усилитель`", "`Другой`").replace(
        "`Временной график`", "`Третий`")
    _patch_read(monkeypatch, broken)
    text = _text(await mcp.call_tool(
        "build_and_verify", {"spec": CHAIN_SPEC, "max_items": 1}))

    assert "показано 1" in text, text


@pytest.mark.anyio
async def test_new_project_false_is_passed_to_build(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    _patch_read(monkeypatch, CHAIN_XML)

    _text(await mcp.call_tool(
        "build_and_verify", {"spec": CHAIN_SPEC, "new_project": False}))

    assert calls[0]["new_project"] is False


@pytest.mark.anyio
async def test_subsystem_is_built_and_verified(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    _patch_read(monkeypatch, SUBMODEL_XML)

    text = _text(await mcp.call_tool(
        "build_and_verify", {"spec": SUBMODEL_SPEC}))

    assert "Вердикт: passed" in text, text
    subsystem = calls[0]["spec"].blocks[0].subsystem
    assert subsystem is not None
    assert [block.name for block in subsystem.blocks] == ["In", "K", "Out"]
    assert len(subsystem.wires) == 2


@pytest.mark.anyio
async def test_unsupported_profile_is_not_passed(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    _patch_read(monkeypatch, CHAIN_XML)
    spec = dict(CHAIN_SPEC)
    spec["verification_profile"] = {
        "readback": ["xprt", "model_text"], "simulate": True}

    text = _text(await mcp.call_tool("build_and_verify", {"spec": spec}))

    assert "Вердикт: partial" in text, text
    assert "model_text" in text and "simulate" in text, text


@pytest.mark.anyio
async def test_readback_failure_names_the_build(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)

    def fail(path, sources, strict):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool, "read_model", fail)

    text = await _error("build_and_verify", {"spec": CHAIN_SPEC})
    assert "модель собрана" in text.lower(), text
    assert "boom" in text


@pytest.mark.anyio
async def test_verification_failure_names_the_build(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    _patch_read(monkeypatch, CHAIN_XML)

    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool, "verify_model", fail)

    text = await _error("build_and_verify", {"spec": CHAIN_SPEC})
    assert "модель собрана" in text.lower(), text
    assert "проверка не выполнена" in text, text
    assert "boom" in text


@pytest.mark.anyio
async def test_layout_warning_does_not_claim_missing_wires(monkeypatch):
    calls = []
    _patch_build(
        monkeypatch, calls,
        "Модель собрана.\n\nСоединено проводов: 2 из 2\n\n"
        "WARNING: раскладка не выполнена: контур не отработал")
    _patch_read(monkeypatch, CHAIN_XML)

    text = _text(await mcp.call_tool("build_and_verify", {"spec": CHAIN_SPEC}))

    assert "Вердикт: passed" in text, text
    assert "часть проводов не создана" not in text, text
    assert "сборка завершилась с предупреждениями" in text, text


@pytest.mark.anyio
async def test_readback_uses_production_parser(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)

    class FakeProject:
        id = 7

        def save_xml(self, path: str) -> None:
            Path(path).write_text(CHAIN_XML, encoding="utf-8")

    monkeypatch.setattr(session, "ensure_project", lambda: FakeProject())

    text = _text(await mcp.call_tool("build_and_verify", {"spec": CHAIN_SPEC}))

    assert "Вердикт: passed" in text, text
    assert "Покрытие: ok" in text, text


@pytest.mark.anyio
async def test_report_truncation_names_full_counts(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    _patch_read(monkeypatch, wrong_ports(300))

    text = _text(await mcp.call_tool(
        "build_and_verify", {"spec": blocks_spec(300)}))

    assert "failed 300" in text, text
    assert "всего 300" in text, text
    assert "показано 50" in text, text
    assert "отчёт верификации усечён" in text, text
    assert "failed 200" not in text, text


@pytest.mark.anyio
async def test_max_items_is_clamped_to_one_and_two_hundred(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls,
                 "Модель собрана.\n\nСоединено проводов: 0 из 0")
    _patch_read(monkeypatch, wrong_ports(300))

    low = _text(await mcp.call_tool(
        "build_and_verify", {"spec": blocks_spec(300), "max_items": 0}))
    high = _text(await mcp.call_tool(
        "build_and_verify", {"spec": blocks_spec(300), "max_items": 1000}))

    assert "показано 1" in low, low
    assert "всего 300" in low, low
    assert "показано 200" in high, high
    assert "всего 300" in high, high
