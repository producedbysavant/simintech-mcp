"""Инструменты ledger и связь experiment evidence с influence."""

from __future__ import annotations

import json

import pytest

from simintech_mcp import experiment_ledger, model_store, session
from simintech_mcp.server import mcp

from _support import _error, _text

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

MODEL_B = MODEL.replace("`K`", "`K2`").replace("`G`", "`G2`")
MODEL_CONTENT = MODEL.replace(
    "<obj_type>`100`</obj_type>",
    "<obj_type>`100`</obj_type><custom_props><data><name>`a`</name>"
    "<value>`[2]`</value></data></custom_props>")


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


def _record_args(**overrides):
    data = {
        "goal": "проверить влияние K на G",
        "scenario": {
            "changed": "main::K:0", "from": 0.0, "to": 1.0, "at": 0.1,
        },
        "run": {"step": 1e-3, "to_time": 0.5, "baseline": "base-1"},
        "observation": {
            "target": "main::G:0", "changed": True, "first_change_at": 0.1,
        },
    }
    data.update(overrides)
    return data


async def _load(tmp_path, monkeypatch, name="m.xprt", text=MODEL):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / name).write_text(text, encoding="utf-8")
    result = await mcp.call_tool("load_model", {"path": name})
    return json.loads(_text(result))


async def _record(ref, **overrides):
    arguments = _record_args(**overrides)
    arguments["model_ref"] = ref
    result = await mcp.call_tool("record_experiment", arguments)
    return json.loads(_text(result))


async def _influence(ref, **args):
    arguments = {"target": "main::G:0"}
    arguments.update(args)
    result = await mcp.call_tool(
        "query_model", {"model_ref": ref, "query": "influence",
                        "arguments": arguments})
    return json.loads(_text(result))


async def _experiments(ref, **args):
    payload = {"model_ref": ref, "query": "experiments"}
    if args:
        payload["arguments"] = args
    result = await mcp.call_tool("query_model", payload)
    return json.loads(_text(result))


def _chain(result):
    return next(item for item in result["items"]
                if item["source"]["address"] == "main::K@property:a")


@pytest.mark.anyio
async def test_record_and_influence(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)

    response = await _record(data["model_ref"])

    assert response["recorded"] is True
    assert response["id"].startswith("exp-")
    assert response["stale"] is False
    assert response["experiment"]["goal"] == "проверить влияние K на G"
    assert response["experiment"]["structure_hash"] == data["model_hash"]
    assert response["experiment"]["content_hash"] == data["content_hash"]
    assert response["experiment"]["recorded_at"]

    result = await _influence(data["model_ref"])
    chain = _chain(result)
    assert chain["evidence"]["status"] == "experiment"
    assert chain["evidence"]["ref"] == f"experiment:{response['id']}"
    assert result["evidence"]["experiments"]["applied"] == 1
    assert result["evidence"]["ledger"]["stored"] == 1
    assert any(item["kind"] == "experiment_scope"
               for item in result["evidence"]["limitations"])


@pytest.mark.anyio
async def test_experiment_include_default_and_filter(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    await _record(data["model_ref"])

    default = await _influence(data["model_ref"])
    assert _chain(default)["evidence"]["status"] == "experiment"

    only_contract = await _influence(data["model_ref"], include=["contract"])
    assert only_contract["items"] == []
    assert only_contract["evidence"]["experiments"]["applied"] == 1
    assert any(item["kind"] == "experiment_filtered"
               for item in only_contract["evidence"]["limitations"])


@pytest.mark.anyio
async def test_experiment_survives_new_load_model(monkeypatch, tmp_path):
    first = await _load(tmp_path, monkeypatch, "a.xprt", MODEL)
    await _record(first["model_ref"])
    other = await _load(tmp_path, monkeypatch, "b.xprt", MODEL_B)
    again = await _load(tmp_path, monkeypatch, "a.xprt", MODEL)

    assert again["model_ref"] != first["model_ref"]
    assert again["ledger"]["stored"] == 1
    result = await _influence(again["model_ref"])
    assert _chain(result)["evidence"]["status"] == "experiment"
    assert (await _experiments(other["model_ref"]))["items"] == []

    model_store.clear()
    after_clear = await _load(tmp_path, monkeypatch, "a.xprt", MODEL)
    assert after_clear["ledger"]["stored"] == 1
    assert (await _experiments(after_clear["model_ref"]))["items"]


@pytest.mark.anyio
async def test_hash_mismatch_refused(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    arguments = _record_args()
    arguments["model_ref"] = data["model_ref"]

    text = await _error("record_experiment",
                        dict(arguments, structure_hash="0" * 64))
    assert "другой модели" in text
    text = await _error("record_experiment",
                        dict(arguments, content_hash="0" * 64))
    assert "другой модели" in text
    assert (await _experiments(data["model_ref"]))["items"] == []

    accepted = await _record(
        data["model_ref"], structure_hash=data["model_hash"],
        content_hash=data["content_hash"])
    assert accepted["recorded"] is True


@pytest.mark.anyio
async def test_negative_observation_is_not_absence(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    response = await _record(
        data["model_ref"],
        observation={"target": "main::G:0", "changed": False})

    assert response["negative"] is True
    assert any("не доказывает" in note for note in response["notes"])

    result = await _influence(data["model_ref"])
    assert _chain(result)["evidence"]["status"] == "not_established"
    assert result["evidence"]["known_absences"] == []
    assert result["evidence"]["experiments"]["negative"] == 1
    assert any(item["kind"] == "experiment_negative"
               for item in result["evidence"]["limitations"])

    listing = await _experiments(data["model_ref"])
    assert listing["evidence"]["negative"] == 1
    assert any("не доказывают" in note for note in listing["notes"])


@pytest.mark.anyio
async def test_stale_snapshot_is_accepted(monkeypatch):
    monkeypatch.setattr(session, "ensure_project", lambda: _FakeProject())
    data = json.loads(_text(await mcp.call_tool("load_model", {})))
    session.mark_mutated()

    response = await _record(data["model_ref"])

    assert response["stale"] is True
    assert any("устарел" in note for note in response["notes"])
    text = await _error(
        "query_model",
        {"model_ref": data["model_ref"], "query": "experiments"})
    assert "устарел" in text

    again = json.loads(_text(await mcp.call_tool("load_model", {})))
    assert again["ledger"]["stored"] == 1
    assert (await _experiments(again["model_ref"]))["items"]


@pytest.mark.anyio
async def test_forget_experiment(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    record = await _record(data["model_ref"])
    assert len((await _experiments(data["model_ref"]))["items"]) == 1

    text = await _error(
        "forget_experiment",
        {"experiment_id": record["id"], "model_ref": data["model_ref"],
         "content_hash": "0" * 64})
    assert text.startswith("forget_experiment:")

    result = await mcp.call_tool(
        "forget_experiment", {"experiment_id": record["id"]})
    payload = json.loads(_text(result))
    assert payload["removed"] == 1
    assert payload["forgotten"] == [record["id"]]
    assert (await _experiments(data["model_ref"]))["items"] == []
    assert (await _influence(data["model_ref"]))[
        "evidence"]["experiments"]["applied"] == 0

    text = await _error("forget_experiment",
                        {"experiment_id": record["id"]})
    assert "не найдена" in text


@pytest.mark.anyio
async def test_forget_all_records(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    await _record(data["model_ref"])
    await _record(data["model_ref"])

    result = await mcp.call_tool(
        "forget_experiment", {"all_records": True,
                              "model_ref": data["model_ref"]})
    payload = json.loads(_text(result))
    assert payload["removed"] == 2
    assert payload["cleared"] is True
    assert (await _experiments(data["model_ref"]))["items"] == []


@pytest.mark.anyio
async def test_forget_by_hashes_without_ref(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    record = await _record(data["model_ref"])
    model_store.clear()

    result = await mcp.call_tool(
        "forget_experiment",
        {"experiment_id": record["id"],
         "structure_hash": data["model_hash"],
         "content_hash": data["content_hash"]})
    payload = json.loads(_text(result))
    assert payload["removed"] == 1


@pytest.mark.anyio
async def test_query_experiments_summary_and_detail(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    first = await _record(data["model_ref"])
    await _record(data["model_ref"])

    listing = await _experiments(data["model_ref"])
    assert listing["evidence"]["records_total"] == 2
    assert listing["evidence"]["records_shown"] == 2
    assert listing["truncated"] is False
    assert "scenario" not in listing["items"][0]
    assert listing["items"][0]["goal"] == "проверить влияние K на G"

    detail = await _experiments(data["model_ref"], id=first["id"])
    assert len(detail["items"]) == 1
    assert detail["items"][0]["scenario"]["changed"] == "main::K:0"
    assert detail["items"][0]["goal"] == "проверить влияние K на G"

    limited = await _experiments(data["model_ref"], max_items=1)
    assert len(limited["items"]) == 1
    assert limited["truncated"] is True
    assert limited["evidence"]["records_total"] == 2

    text = await _error(
        "query_model",
        {"model_ref": data["model_ref"], "query": "experiments",
         "arguments": {"id": "missing"}})
    assert "не найдена" in text


@pytest.mark.anyio
async def test_record_does_not_bump_generation(monkeypatch):
    monkeypatch.setattr(session, "ensure_project", lambda: _FakeProject())
    data = json.loads(_text(await mcp.call_tool("load_model", {})))
    before = model_store.generation()

    response = await _record(data["model_ref"])

    assert model_store.generation() == before
    assert "Изменения внесены" not in json.dumps(response, ensure_ascii=False)


@pytest.mark.anyio
async def test_record_limit_is_mapped_to_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_ledger, "MAX_RECORDS_PER_MODEL", 1)
    data = await _load(tmp_path, monkeypatch)
    await _record(data["model_ref"])

    arguments = _record_args()
    arguments["model_ref"] = data["model_ref"]
    text = await _error("record_experiment", arguments)
    assert "уже 1" in text


@pytest.mark.anyio
async def test_record_validation_errors(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)

    arguments = _record_args(goal="")
    arguments["model_ref"] = data["model_ref"]
    assert "goal пуст" in await _error("record_experiment", arguments)

    arguments = _record_args(run={"step": 1e-3, "to_time": 0.5})
    arguments["model_ref"] = data["model_ref"]
    assert "baseline" in await _error("record_experiment", arguments)

    arguments = _record_args(status="bogus")
    arguments["model_ref"] = data["model_ref"]
    assert "status" in await _error("record_experiment", arguments)


@pytest.mark.anyio
async def test_status_unknown_is_inapplicable(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    response = await _record(data["model_ref"], status="unknown")

    assert any("unknown" in note for note in response["notes"])
    result = await _influence(data["model_ref"])
    assert result["evidence"]["experiments"]["inapplicable"] == 1
    assert result["evidence"]["experiments"]["applied"] == 0


@pytest.mark.anyio
async def test_unknown_negative_is_not_counted_negative(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    response = await _record(
        data["model_ref"], status="unknown",
        observation={"target": "main::G:0", "changed": False})

    assert response["negative"] is False
    listing = await _experiments(data["model_ref"])
    assert listing["evidence"]["negative"] == 0
    result = await _influence(data["model_ref"])
    assert result["evidence"]["experiments"]["negative"] == 0
    assert result["evidence"]["experiments"]["inapplicable"] == 1


@pytest.mark.anyio
async def test_query_experiments_reads_other_version_by_id(
        monkeypatch, tmp_path):
    first = await _load(tmp_path, monkeypatch, "a.xprt", MODEL)
    record = await _record(first["model_ref"])
    other = await _load(tmp_path, monkeypatch, "a2.xprt", MODEL_CONTENT)

    detail = await _experiments(other["model_ref"], id=record["id"])

    assert len(detail["items"]) == 1
    assert detail["items"][0]["structure_hash"] == first["model_hash"]
    assert detail["items"][0]["content_hash"] == first["content_hash"]
    assert detail["evidence"]["other_version"] is True
    assert any("другой версии" in note for note in detail["notes"])

    result = await mcp.call_tool(
        "forget_experiment", {"experiment_id": record["id"]})
    assert json.loads(_text(result))["removed"] == 1


@pytest.mark.anyio
async def test_experiments_id_race_stays_current(monkeypatch, tmp_path):
    data = await _load(tmp_path, monkeypatch)
    record = await _record(data["model_ref"])
    monkeypatch.setattr(experiment_ledger, "entries_for",
                        lambda *args: ())

    detail = await _experiments(data["model_ref"], id=record["id"])

    assert detail["evidence"]["other_version"] is False
    assert not any("другой версии" in note for note in detail["notes"])


@pytest.mark.anyio
async def test_forget_all_notes_other_versions(monkeypatch, tmp_path):
    first = await _load(tmp_path, monkeypatch, "a.xprt", MODEL)
    await _record(first["model_ref"])
    other = await _load(tmp_path, monkeypatch, "a2.xprt", MODEL_CONTENT)
    await _record(other["model_ref"])

    result = await mcp.call_tool(
        "forget_experiment", {"all_records": True,
                              "model_ref": first["model_ref"]})
    payload = json.loads(_text(result))

    assert payload["removed"] == 1
    assert payload["ledger"]["other_content_versions"] == 1
    assert any("других версий" in note for note in payload["notes"])
    assert (await _experiments(other["model_ref"]))["items"]
