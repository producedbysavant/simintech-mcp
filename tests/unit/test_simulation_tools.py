"""Расчёт и живые сигналы."""

from __future__ import annotations

import pytest

from simintech_mcp.server import mcp
from simintech_mcp.tools import simulation as simulation_tools

from _support import _error, _install_fake_simulation, _text


@pytest.mark.anyio
async def test_run_reports_failure_when_time_did_not_move(monkeypatch):
    """run отказывает, если расчёт не дошёл, — а не сообщает об успехе.

    Текстом это было бы неотличимо для клиента, доверяющего `isError`:
    недостижение отметки — неудача, и она обязана прийти отказом.
    """

    sim = _install_fake_simulation(monkeypatch, [0.0, 0.0])
    sim.run_to_result = False

    text = await _error("run", {"to_time": 1.0})

    assert "не дошёл" in text
    assert "0.000" in text
    assert sim.run_to_calls == [(1.0, simulation_tools.CALC_WAIT_SECONDS,
                                 simulation_tools.CALC_STALL_SECONDS)]


@pytest.mark.anyio
async def test_run_reports_success_with_actual_time(monkeypatch):
    """Успешный расчёт подтверждается фактическим временем."""
    _install_fake_simulation(monkeypatch, [1.0])

    text = _text(await mcp.call_tool("run", {"to_time": 1.0}))

    assert "завершён" in text
    assert "1.000" in text


@pytest.mark.anyio
async def test_run_passes_waits_to_library(monkeypatch):
    """Параметры ожидания уходят в библиотечный run_to, а не игнорируются."""
    sim = _install_fake_simulation(monkeypatch, [1.0])

    await mcp.call_tool("run", {"to_time": 1.0, "wait_timeout": 5.0,
                                "stall_seconds": 0.5})

    assert sim.run_to_calls == [(1.0, 5.0, 0.5)]


@pytest.mark.anyio
async def test_run_without_target_warns_no_confirmation(monkeypatch):
    """Неблокирующий запуск не выдаётся за подтверждённый результат."""
    _install_fake_simulation(monkeypatch, [0.0])

    text = _text(await mcp.call_tool("run", {}))

    assert "неблокирующий" in text


@pytest.mark.anyio
async def test_step_reports_stalled_time(monkeypatch):
    """step — тот же класс, что run: время не сдвинулось, значит расчёт стоит.

    И это отказ: шаги, которых не было, не должны выглядеть успехом.
    """
    sim = _install_fake_simulation(monkeypatch, [0.0, 0.0])

    text = await _error("step", {"count": 3})

    assert "не сдвинулось" in text
    assert sim.stepped == 3


@pytest.mark.anyio
async def test_step_reports_time_delta(monkeypatch):
    """Успешные шаги подтверждаются дельтой времени."""
    _install_fake_simulation(monkeypatch, [0.0, 0.003])

    text = _text(await mcp.call_tool("step", {"count": 3}))

    assert "0.000 → 0.003" in text


@pytest.mark.anyio
async def test_step_rejects_non_positive_count(monkeypatch):
    _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("step", {"count": 0})

    assert "положительным" in text
