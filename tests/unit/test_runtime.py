"""Исполнение инструментов: COM-поток и журнал вызовов."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.server import mcp

from simintech_mcp import runtime

from _support import _FakeBlock, _error, _install_fake_project


def test_com_threaded_uses_single_dedicated_thread():
    """Все COM-вызовы идут через один поток.

    COM-объект привязан к апартаменту создавшего его потока; использование
    из другого потока даёт «Объект не подключен к серверу». Проверено на
    реальном SimInTech.
    """
    from simintech_mcp.runtime import _com_threaded

    @_com_threaded
    def whoami():
        import threading
        return threading.get_ident()

    assert whoami() == whoami() == whoami()


def test_com_threaded_returns_result_and_wraps_errors():
    """Отказ доставляется как `ToolError` — по нему MCP выставляет `isError`.

    Раньше инструменты сообщали об ошибке строкой «ERROR: …», и клиент,
    доверяющий флагу `isError`, видел 100% успеха.
    """
    from simintech_mcp.runtime import _com_threaded

    @_com_threaded
    def add(a, b):
        return a + b

    @_com_threaded
    def boom():
        raise ValueError("нет проекта")

    @_com_threaded
    def prose_error():
        return "ERROR: блока нет"

    @_com_threaded
    def ok_message():
        return "всё хорошо"

    assert add(2, 3) == 5
    assert ok_message() == "всё хорошо", "обычный текст не должен стать ошибкой"
    with pytest.raises(ToolError, match="нет проекта"):
        boom()
    with pytest.raises(ToolError, match="блока нет"):
        prose_error()


def test_com_threaded_times_out_instead_of_hanging(monkeypatch):
    """Зависание COM даёт отказ, а не молчаливое подвисание сервера."""
    import time as _time

    monkeypatch.setattr(runtime, "COM_CALL_TIMEOUT", 0.05)

    @runtime._com_threaded
    def slow():
        _time.sleep(0.3)
        return "поздно"

    with pytest.raises(ToolError, match="не ответил"):
        slow()


def test_com_threaded_preserves_signature():
    """functools.wraps сохраняет сигнатуру — иначе FastMCP не увидит аргументы."""
    from simintech_mcp.runtime import _com_threaded

    @_com_threaded
    def sample(name: str, count: int = 1) -> str:
        return name * count

    import inspect
    assert list(inspect.signature(sample).parameters) == ["name", "count"]


def test_log_event_writes_json_lines(monkeypatch, tmp_path):
    """События журнала — по одной JSON-строке на событие."""

    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(runtime.LOG_ENV, str(log))

    runtime.log_event("проверка", ok=True, n=1)

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "проверка"
    assert record["ok"] is True
    assert record["n"] == 1
    assert "ts" in record


def test_log_event_is_off_without_env(monkeypatch, tmp_path):
    """Без переменной журнал не пишется — поведение по умолчанию."""

    monkeypatch.delenv(runtime.LOG_ENV, raising=False)
    log = tmp_path / "log.jsonl"

    runtime.log_event("не должно записаться")

    assert not log.exists()


def test_log_event_never_writes_to_stdout(monkeypatch, capsys):
    """Журнал идёт в stderr: в stdout живёт JSON-RPC, его засорять нельзя."""

    monkeypatch.setenv(runtime.LOG_ENV, "stderr")

    runtime.log_event("событие", ok=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "событие" in captured.err


def test_log_event_survives_unwritable_path(monkeypatch, tmp_path):
    """Недоступный файл журнала не роняет инструмент."""

    monkeypatch.setenv(runtime.LOG_ENV, str(tmp_path / "нет-каталога" / "l.log"))

    runtime.log_event("без падения", ok=True)      # не должно бросить


@pytest.mark.anyio
async def test_tool_call_is_logged(monkeypatch, tmp_path):
    """Вызов инструмента попадает в журнал с длительностью и исходом."""

    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(runtime.LOG_ENV, str(log))
    _install_fake_project(monkeypatch, {"Gain": _FakeBlock("Усилитель")})

    await mcp.call_tool("set_block_param",
                        {"block": "Gain", "param": "a", "value": "2"})

    records = [json.loads(line)
               for line in log.read_text(encoding="utf-8").splitlines()]
    call = [r for r in records if r.get("tool") == "set_block_param"]
    assert call and call[0]["ok"] is True
    assert "ms" in call[0]
    assert call[0]["args"]["param"] == "a"


@pytest.mark.anyio
async def test_failed_tool_call_is_logged(monkeypatch, tmp_path):
    """Отказ тоже попадает в журнал — иначе причина не видна."""

    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(runtime.LOG_ENV, str(log))
    _install_fake_project(monkeypatch, {})

    await _error("get_block_params", {"block": "Нет"})

    records = [json.loads(line)
               for line in log.read_text(encoding="utf-8").splitlines()]
    failed = [r for r in records
              if r.get("tool") == "get_block_params" and not r.get("ok")]
    assert failed and "не найден" in failed[0]["error"]


def test_log_env_off_values_do_not_switch_logging_on(monkeypatch, tmp_path):
    """Выключающие значения переменной журнала действительно выключают его."""

    monkeypatch.chdir(tmp_path)     # мусорный файл не попадёт в репозиторий
    log = tmp_path / "log.jsonl"

    for value in ("1", "true"):
        log.unlink(missing_ok=True)
        monkeypatch.setenv(runtime.LOG_ENV, str(log))
        runtime.log_event("включено", value=value)
        assert log.exists(), value

    log.unlink()
    for value in ("0", "false", "off", "no"):
        monkeypatch.setenv(runtime.LOG_ENV, value)
        runtime.log_event("выключено")
        assert not log.exists(), value


def test_log_stdout_value_goes_to_stderr(monkeypatch, tmp_path, capsys):
    """`SIMINTECH_MCP_LOG=stdout` — это журнал в stderr, а не файл `stdout`.

    В stdout писать нельзя: там JSON-RPC, и одна строка лога рвёт транспорт.
    """

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(runtime.LOG_ENV, "stdout")

    runtime.log_event("проверка", ok=True)

    captured = capsys.readouterr()
    assert "проверка" in captured.err
    assert captured.out == ""
    assert not (tmp_path / "stdout").exists()
