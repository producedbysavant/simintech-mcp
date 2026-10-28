"""M2: контрактные проверки в query_model и ресурс контрактов."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_mcp import model_store  # noqa: E402
from simintech_mcp.contracts import check_model_contracts  # noqa: E402
from simintech_mcp.resources import (  # noqa: E402
    resource_block_contract,
    resource_block_contracts,
)
from simintech_mcp.server import mcp  # noqa: E402
from simintech_api.model_ir import parse_xprt  # noqa: E402

from _support import _text  # noqa: E402

MODEL = """<Header><project><page>
  <object>
    <name>`M`</name>
    <class_name>`Перемножитель`</class_name>
    <obj_type>`100`</obj_type>
    <custom_props>
      <data><name>`nport`</name><value>`2`</value></data>
    </custom_props>
    <ports>
      <port><name>`Port_0`</name><mode>`1`</mode></port>
      <port><name>`PORT1`</name><mode>`0`</mode></port>
      <port><name>`PORT2`</name><mode>`0`</mode></port>
    </ports>
  </object>
</page></project></Header>"""

UNKNOWN = """<Header><project><page>
  <object>
    <name>`U`</name>
    <class_name>`Arduino - I2C - Ведомый I2C`</class_name>
    <obj_type>`100`</obj_type>
  </object>
</page></project></Header>"""


MEMORY = """<Header><project><page>
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


def setup_function():
    model_store.clear()


def test_contract_report_passes_and_fails():
    passed = check_model_contracts(parse_xprt(MODEL))
    assert passed.status == "passed"
    assert passed.passed == 1

    broken = check_model_contracts(parse_xprt(
        MODEL.replace("<value>`2`</value>", "<value>`3`</value>")))
    assert broken.status == "failed"
    assert broken.items[0]["code"] == "contract_ports_mismatch"

    unknown = check_model_contracts(parse_xprt(UNKNOWN))
    assert unknown.status == "not_evaluated"
    assert unknown.items[0]["reason"] == "not_observed_in_corpus"


def test_portnames_ports_are_instance_defined():
    """Образец реестра у portnames не эталон: проверка не выдумывает счёт."""
    report = check_model_contracts(parse_xprt(MEMORY))
    assert report.status == "not_evaluated"
    assert report.failed == 0
    assert report.by_reason == {"portnames_instance_defined": 2}


@pytest.mark.anyio
async def test_query_model_attaches_contracts(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.xprt").write_text(MODEL, encoding="utf-8")
    loaded = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.xprt"})))
    ref = loaded["model_ref"]

    check = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": ref, "query": "check_connections"})))
    assert check["evidence"]["contracts"]["status"] == "passed"
    assert check["evidence"]["contracts"]["evaluated"] == 1

    invariants = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": ref, "query": "validate_invariants",
         "arguments": {"invariants": [
             {"id": "ports", "kind": "contract_ports_match"}]}})))
    item = next(item for item in invariants["items"]
                if item["kind"] == "contract_ports_match")
    assert item["status"] == "passed"


def test_contract_resources_are_compact():
    summary = resource_block_contracts()
    assert "классов: 958" in summary
    assert "Перемножитель" in summary
    assert "Полный дамп не выводится" in summary

    contract = resource_block_contract("Перемножитель")
    assert "# Перемножитель" in contract
    assert "ports: nport" in contract
    assert "constraint nport-input-count" in contract
    assert "property nport" in contract
    assert len(contract) < 4000
