"""Расчёт и живые сигналы."""

from __future__ import annotations

import pytest

from simintech_mcp import runtime
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


@pytest.mark.anyio
async def test_run_rejects_excessive_wait(monkeypatch):
    """Ожидание дольше таймаута COM-вызова отвергается.

    Инструмент выполняется в единственном выделенном потоке, а COM-вызов по
    таймауту не прерывается: с `wait_timeout` в миллиарды клиент получил бы
    отказ, но поток остался бы занят опросом до `deadline`, и все последующие
    COM-инструменты падали бы по таймауту до перезапуска mmain.exe. Это тот же
    класс, от которого защищает `MAX_STEP_COUNT`, — без проверки предел
    обходится параметрами `run`.
    """
    sim = _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("run", {"to_time": 10.0,
                                "wait_timeout": runtime.COM_CALL_TIMEOUT * 10})

    assert "больше предела" in text
    assert sim.run_to_calls == [], "до COM-вызовов дело доходить не должно"


@pytest.mark.anyio
async def test_run_rejects_non_positive_waits(monkeypatch):
    """Нулевое ожидание — отказ, а не мгновенный опрос в цикле."""
    sim = _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("run", {"to_time": 10.0, "wait_timeout": 0})

    assert "положительными" in text
    assert sim.run_to_calls == []


@pytest.mark.anyio
async def test_step_rejects_absurd_count(monkeypatch):
    """Абсурдное число шагов отвергается до первого COM-вызова.

    Каждый шаг — отдельный COM-вызов, а вызов в своём потоке по таймауту не
    прерывается: неограниченный `count` занял бы выделенный поток надолго, и
    сервер остался бы пригоден только до перезапуска mmain.exe.
    """
    sim = _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("step", {"count": simulation_tools.MAX_STEP_COUNT + 1})

    assert "больше предела" in text
    assert str(simulation_tools.MAX_STEP_COUNT) in text
    assert sim.stepped == 0, "до COM-вызовов дело доходить не должно"
