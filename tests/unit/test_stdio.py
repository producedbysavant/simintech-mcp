"""Изоляция stdout от транспорта JSON-RPC."""

from __future__ import annotations

import os
import sys

import pytest

from simintech_mcp import server, stdio

from _support import _install_fake_streams


def test_stdout_guard_routes_text_to_stderr(monkeypatch):
    """Посторонний вывод уходит в stderr, а .buffer остаётся настоящим."""
    from simintech_mcp.stdio import isolate_stdout

    real, err = _install_fake_streams(monkeypatch)
    isolate_stdout()

    # Транспорт MCP пишет протокол через sys.stdout.buffer.
    sys.stdout.buffer.write('{"jsonrpc": "2.0"}\n')
    # Посторонний вывод — print() из библиотеки, логов, предупреждений.
    print("диагностика")

    assert real.buffer.getvalue() == '{"jsonrpc": "2.0"}\n'
    assert "диагностика" not in real.buffer.getvalue()
    assert "диагностика" in err.getvalue()
    assert real.getvalue() == ""  # в текстовый stdout не попало ничего


def test_stdout_guard_misses_descriptor_writes(monkeypatch, capfd):
    """Запись прямо в дескриптор 1 гард не перехватывает — это его граница.

    `_StdoutGuard` подменяет `sys.stdout.write`, а нативный код (или библиотека,
    пишущая в fd 1) идёт мимо. Тест фиксирует границу явно, чтобы страховку не
    читали как «протокол защищён от любой записи»: такое попадание в stdout
    порвёт JSON-RPC молча.
    """
    from simintech_mcp.stdio import isolate_stdout

    _install_fake_streams(monkeypatch)
    isolate_stdout()

    os.write(1, b"mimo-guarda\n")

    assert "mimo-guarda" in capfd.readouterr().out


def test_stdout_guard_keeps_buffer_identity(monkeypatch):
    """`.buffer` прокси — тот же объект, что и до изоляции."""
    from simintech_mcp.stdio import isolate_stdout

    real, _ = _install_fake_streams(monkeypatch)
    buffer_before = sys.stdout.buffer

    isolate_stdout()

    assert sys.stdout.buffer is buffer_before


def test_isolate_stdout_idempotent(monkeypatch):
    """Повторный вызов не оборачивает прокси второй раз."""
    from simintech_mcp.stdio import isolate_stdout

    _install_fake_streams(monkeypatch)
    isolate_stdout()
    first = sys.stdout
    isolate_stdout()

    assert sys.stdout is first


def test_stdout_guard_survives_flush_and_isatty(monkeypatch):
    """Служебные методы потока не падают и не пишут в stdout."""
    from simintech_mcp.stdio import isolate_stdout

    real, _ = _install_fake_streams(monkeypatch)
    isolate_stdout()

    sys.stdout.flush()
    assert sys.stdout.isatty() is False
    assert real.getvalue() == ""


def test_stdout_guard_private_attr_raises_without_recursion(monkeypatch):
    """Приватные имена не делегируются — иначе возможна бесконечная рекурсия."""
    from simintech_mcp.stdio import isolate_stdout

    _install_fake_streams(monkeypatch)
    isolate_stdout()

    with pytest.raises(AttributeError):
        sys.stdout._nonexistent_attr


def test_main_isolates_stdout_before_run(monkeypatch):
    """main() включает изоляцию ДО запуска транспорта."""

    _install_fake_streams(monkeypatch)
    seen = {}

    def fake_run(**kwargs):
        seen["guard_installed"] = isinstance(sys.stdout, stdio._StdoutGuard)

    monkeypatch.setattr(server.mcp, "run", fake_run)
    server.main()

    assert seen["guard_installed"] is True
