"""Файл диагностики шага внешнего решателя: схема, проверки и предупреждения."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from simintech_mcp import sandbox, session
from simintech_mcp.solver_diagnostics import (
    SCHEMA,
    DiagnosticsError,
    parse_diagnostics,
    read_diagnostics,
    read_diagnostics_result,
)


PID = 4242


def _payload(**overrides):
    data = {
        "schema": SCHEMA,
        "pid": PID,
        "t": 1.25,
        "h": 1e-05,
        "status": "accepted",
        "reason": "",
        "Nbad": 2,
        "Ngood": 100,
    }
    data.update(overrides)
    return data


def _write(path: Path, **overrides) -> Path:
    path.write_text(json.dumps(_payload(**overrides)), encoding="utf-8")
    return path


def _write_log(path: Path, *records) -> Path:
    path.write_text(
        "\n".join(json.dumps(item) for item in records) + "\n",
        encoding="utf-8")
    return path


def _call(**overrides):
    data = {
        "schema": SCHEMA,
        "pid": PID,
        "t": 1.25,
        "status": "call",
        "reason": "call",
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def _output_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(sandbox.OUTPUT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        session, "current_client", lambda: SimpleNamespace(session_pid=PID))
    return tmp_path


def test_parse_accepts_rejected_step_with_reason():
    data = parse_diagnostics(json.dumps(_payload(
        status="rejected", reason="ner>0")).encode())

    assert data["status"] == "rejected"
    assert data["reason"] == "ner>0"


@pytest.mark.parametrize("overrides, message", [
    ({"schema": "other"}, "schema"),
    ({"status": "unknown"}, "status"),
    ({"pid": 0}, "pid"),
    ({"Nbad": -1}, "Nbad"),
    ({"Ngood": 1.5}, "Ngood"),
    ({"t": float("nan")}, "t"),
    ({"h": float("inf")}, "h"),
    ({"status": "rejected", "reason": ""}, "reason"),
])
def test_parse_rejects_bad_fields(overrides, message):
    with pytest.raises(DiagnosticsError, match=message):
        parse_diagnostics(json.dumps(_payload(**overrides)).encode())


def test_parse_rejects_non_object_and_bad_json():
    with pytest.raises(DiagnosticsError, match="объект"):
        parse_diagnostics(b"[]")
    with pytest.raises(DiagnosticsError, match="JSON"):
        parse_diagnostics(b"{")


def test_parse_requires_each_mandatory_field():
    data = _payload()
    del data["Ngood"]

    with pytest.raises(DiagnosticsError, match="Ngood"):
        parse_diagnostics(json.dumps(data).encode())


def test_read_returns_machine_line_for_fresh_same_process(tmp_path):
    _write(tmp_path / "diag.json")

    line = read_diagnostics("diag.json", run_started=time.time() - 1)

    assert line.startswith("solver: ")
    data = json.loads(line[len("solver: "):])
    assert data["pid"] == PID
    assert data["status"] == "accepted"
    assert data["Nbad"] == 2
    assert data["Ngood"] == 100


def test_read_rejects_foreign_process_without_values(tmp_path):
    _write(tmp_path / "diag.json", pid=1)

    line = read_diagnostics("diag.json", run_started=time.time() - 1)

    assert "другого процесса" in line
    assert "solver: " not in line


def test_read_rejects_stale_file_without_values(tmp_path):
    path = _write(tmp_path / "diag.json")
    old = time.time() - 100
    os.utime(path, (old, old))

    line = read_diagnostics("diag.json", run_started=time.time())

    assert "не обновлён" in line
    assert "solver: " not in line


def test_read_reports_missing_file():
    line = read_diagnostics("absent.json", run_started=time.time())

    assert "файла нет" in line
    assert "solver: " not in line


def test_read_reports_bad_json(tmp_path):
    (tmp_path / "diag.json").write_text("{", encoding="utf-8")

    line = read_diagnostics("diag.json", run_started=time.time())

    assert "недоступна" in line
    assert "solver: " not in line


def test_read_reports_oversized_file(tmp_path):
    (tmp_path / "diag.json").write_bytes(b"x" * (64 * 1024 + 1))

    line = read_diagnostics("diag.json", run_started=time.time())

    assert "больше" in line
    assert "solver: " not in line


def test_parse_accepts_call_without_counters():
    data = parse_diagnostics(json.dumps(_call()).encode())

    assert data["status"] == "call"
    assert "Nbad" not in data


def test_read_jsonl_returns_last_outcome(tmp_path):
    _write_log(tmp_path / "diag.jsonl",
               _payload(status="accepted", reason="accepted"),
               _payload(status="rejected", reason="ner>0", Nbad=5))

    result = read_diagnostics_result("diag.jsonl",
                                     run_started=time.time() - 1)

    assert result.status == "rejected"
    assert '"reason":"ner>0"' in result.text
    assert '"Nbad":5' in result.text


def test_read_jsonl_trailing_call_keeps_last_outcome(tmp_path):
    _write_log(tmp_path / "diag.jsonl",
               _payload(status="accepted", reason="accepted"),
               _call())

    result = read_diagnostics_result("diag.jsonl",
                                     run_started=time.time() - 1)

    assert result.status == "accepted"
    assert result.last_status == "call"
    assert result.pending_entry is True
    assert '"status":"accepted"' in result.text
    assert "вход в im_run" in result.text


def test_read_jsonl_only_call_has_no_outcome(tmp_path):
    _write_log(tmp_path / "diag.jsonl", _call())

    result = read_diagnostics_result("diag.jsonl",
                                     run_started=time.time() - 1)

    assert result.status == "call"
    assert result.warning is True
    assert "solver: " not in result.text
    assert "только записи входа" in result.text


def test_parse_accepts_integral_float_counters():
    data = parse_diagnostics(
        json.dumps(_payload(Nbad=4.0, Ngood=120.0)).encode())

    assert data["Nbad"] == 4
    assert data["Ngood"] == 120


def test_parse_rejects_fractional_counters():
    with pytest.raises(DiagnosticsError, match="Nbad"):
        parse_diagnostics(json.dumps(_payload(Nbad=4.5)).encode())


def test_parse_wraps_recursion_and_int_limit_errors():
    with pytest.raises(DiagnosticsError, match="JSON"):
        parse_diagnostics(b"[" * 3000)
    with pytest.raises(DiagnosticsError, match="JSON"):
        parse_diagnostics(b"9" * 5000)


def test_parse_accepts_entry_alias():
    data = parse_diagnostics(json.dumps(_call(status="entry")).encode())

    assert data["status"] == "entry"


def test_read_rejects_previous_run_within_old_tolerance(tmp_path):
    path = _write(tmp_path / "diag.jsonl")
    now = time.time()
    os.utime(path, (now - 1.0, now - 1.0))

    result = read_diagnostics_result("diag.jsonl", run_started=now - 0.5)

    assert "не обновлён" in result.text
    assert "solver: " not in result.text


def test_read_rejects_future_mtime(tmp_path):
    path = _write(tmp_path / "diag.jsonl")
    now = time.time()
    os.utime(path, (now + 60, now + 60))

    result = read_diagnostics_result("diag.jsonl", run_started=now)

    assert "будущем" in result.text
    assert "solver: " not in result.text


def test_read_rejects_mixed_process_log(tmp_path):
    _write_log(tmp_path / "diag.jsonl",
               _payload(status="accepted", reason="accepted"),
               _call(pid=1))

    result = read_diagnostics_result("diag.jsonl",
                                     run_started=time.time() - 1)

    assert "другого процесса" in result.text
    assert "solver: " not in result.text


def test_read_jsonl_large_log_uses_tail(tmp_path):
    path = tmp_path / "diag.jsonl"
    records = [
        _payload(status="accepted", reason="accepted", t=float(i))
        for i in range(2000)
    ]
    records.append(_payload(status="rejected", reason="err>1", Nbad=7))
    path.write_text(
        "\n".join(json.dumps(item) for item in records) + "\n",
        encoding="utf-8")
    assert path.stat().st_size > 64 * 1024

    result = read_diagnostics_result("diag.jsonl",
                                     run_started=time.time() - 1)

    assert result.status == "rejected"
    assert '"reason":"err>1"' in result.text
    assert '"Nbad":7' in result.text
