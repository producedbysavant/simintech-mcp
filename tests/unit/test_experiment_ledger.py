"""Ledger экспериментальных свидетельств: ключ по хешам, лимиты и копия."""

from __future__ import annotations

import pytest
from simintech_api.model_ir import parse_experiment_record, parse_xprt
from simintech_api.model_ir.experiments import EXPERIMENT_SCHEMA

from simintech_mcp import experiment_ledger

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


def _model():
    return parse_xprt(MODEL)


def _record(model, **overrides):
    data = {
        "schema": EXPERIMENT_SCHEMA,
        "id": "exp-1",
        "structure_hash": model.meta.structure_hash,
        "content_hash": model.meta.content_hash,
        "scenario": {
            "changed": "main::K:0", "from": 0.0, "to": 1.0, "at": 0.1,
        },
        "run": {"step": 1e-3, "to_time": 0.5, "baseline": "base-1"},
        "observation": {
            "target": "main::G:0", "changed": True, "first_change_at": 0.1,
        },
        "status": "observed",
        "recorded_at": "2026-11-07",
        "limitations": [],
    }
    data.update(overrides)
    return parse_experiment_record(data)


def _attach(record, **overrides):
    data = {
        "goal": "проверить влияние K на G",
        "attached_ref": "mir1.ref",
        "project_id": 7,
        "bound": True,
        "generation": 1,
        "attached_at": "2026-11-07T12:00:00+00:00",
    }
    data.update(overrides)
    return experiment_ledger.attach(record, **data)


def _key(model):
    return (model.meta.structure_hash, model.meta.content_hash)


def test_is_negative_excludes_unknown():
    model = _model()
    positive = _record(model)
    negative = _record(model, id="exp-2")
    negative.observation["changed"] = False
    unknown = _record(model, id="exp-3", status="unknown")
    unknown.observation["changed"] = False

    assert experiment_ledger.is_negative(positive) is False
    assert experiment_ledger.is_negative(negative) is True
    assert experiment_ledger.is_negative(unknown) is False


def test_attach_and_read():
    model = _model()
    entry = _attach(_record(model))

    assert entry.goal == "проверить влияние K на G"
    entries = experiment_ledger.entries_for(*_key(model))
    assert len(entries) == 1
    assert entries[0].record.id == "exp-1"
    assert entries[0].record.target == "main::G:0"

    records = experiment_ledger.records_for(*_key(model))
    assert len(records) == 1
    assert records[0].source == "main::K:0"

    summary = experiment_ledger.summary(*_key(model))
    assert summary["stored"] == 1
    assert summary["limit"] == 100
    assert summary["other_content_versions"] == 0


def test_attach_keeps_canonical_copy():
    model = _model()
    record = _record(model)
    _attach(record)
    record.scenario["changed"] = "main::K:1"

    again = experiment_ledger.records_for(*_key(model))[0]
    assert again.source == "main::K:0"


def test_duplicate_id_is_rejected():
    model = _model()
    _attach(_record(model))

    with pytest.raises(experiment_ledger.LedgerError, match="повтор id"):
        _attach(_record(model))


def test_duplicate_id_across_models_is_rejected():
    model = _model()
    _attach(_record(model))
    other = _record(model, content_hash="b" * 64, id="exp-1")

    with pytest.raises(experiment_ledger.LedgerError, match="повтор id"):
        _attach(other)


def test_limit_is_one_hundred_records():
    model = _model()
    for number in range(experiment_ledger.MAX_RECORDS_PER_MODEL):
        _attach(_record(model, id=f"exp-{number}"))

    with pytest.raises(experiment_ledger.LedgerError, match="100"):
        _attach(_record(model, id="exp-101"))


def test_bucket_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(experiment_ledger, "MAX_BUCKETS", 1)
    model = _model()
    _attach(_record(model))

    with pytest.raises(experiment_ledger.LedgerError, match="полон"):
        _attach(_record(model, content_hash="b" * 64, id="exp-2"))


def test_oversized_record_is_rejected():
    model = _model()
    record = _record(model)
    record.scenario["note"] = "x" * experiment_ledger.MAX_RECORD_BYTES

    with pytest.raises(experiment_ledger.LedgerError, match="байт"):
        _attach(record)


def test_nan_is_rejected():
    model = _model()
    record = _record(model)
    record.scenario["from"] = float("nan")

    with pytest.raises(experiment_ledger.LedgerError, match="JSON"):
        _attach(record)


def test_goal_and_id_limits():
    model = _model()

    with pytest.raises(experiment_ledger.LedgerError, match="goal пуст"):
        _attach(_record(model), goal="  ")
    with pytest.raises(experiment_ledger.LedgerError, match="длиннее"):
        _attach(_record(model), goal="я" * 501)
    with pytest.raises(experiment_ledger.LedgerError, match="id длиннее"):
        _attach(_record(model, id="x" * 121))


def test_forget_by_id_and_reuse():
    model = _model()
    _attach(_record(model))

    assert experiment_ledger.forget("exp-1") == ("exp-1",)
    assert experiment_ledger.records_for(*_key(model)) == ()
    assert experiment_ledger.forget("exp-1") == ()

    _attach(_record(model))
    assert experiment_ledger.summary(*_key(model))["stored"] == 1


def test_forget_wrong_hash_is_refused():
    model = _model()
    _attach(_record(model))

    with pytest.raises(experiment_ledger.LedgerError, match="другой content_hash"):
        experiment_ledger.forget("exp-1", content_hash="b" * 64)
    assert experiment_ledger.records_for(*_key(model))


def test_forget_all_records():
    model = _model()
    _attach(_record(model))
    _attach(_record(model, id="exp-2"))

    removed = experiment_ledger.forget(
        "", structure_hash=model.meta.structure_hash,
        content_hash=model.meta.content_hash, all_records=True)

    assert set(removed) == {"exp-1", "exp-2"}
    assert experiment_ledger.summary(*_key(model))["stored"] == 0
    assert experiment_ledger.summary()["buckets"] == 0
    with pytest.raises(experiment_ledger.LedgerError, match="оба хеша"):
        experiment_ledger.forget("", all_records=True)


def test_summary_sees_other_content_versions():
    model = _model()
    _attach(_record(model))
    _attach(_record(model, content_hash="b" * 64, id="exp-2"))

    summary = experiment_ledger.summary(*_key(model))
    assert summary["stored"] == 1
    assert summary["other_content_versions"] == 1
    assert summary["buckets"] == 2


def test_entry_for_and_stale():
    model = _model()
    _attach(_record(model), bound=True, generation=1)
    entry = experiment_ledger.entry_for("exp-1")

    assert entry is not None
    assert entry.stale(1) is False
    assert entry.stale(2) is True
    data = entry.to_dict(2)
    assert data["stale"] is True
    assert data["goal"] == "проверить влияние K на G"
    assert data["attached_ref"] == "mir1.ref"

    _attach(_record(model, id="exp-2"), bound=False, generation=1)
    file_entry = experiment_ledger.entry_for("exp-2")
    assert file_entry is not None
    assert file_entry.stale(2) is False
    assert experiment_ledger.entry_for("unknown") is None


def test_clear():
    model = _model()
    _attach(_record(model))

    experiment_ledger.clear()

    assert experiment_ledger.summary()["buckets"] == 0
    assert experiment_ledger.records_for(*_key(model)) == ()
    assert experiment_ledger.entry_for("exp-1") is None
