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
