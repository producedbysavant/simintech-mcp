"""Межпроцессный замок COM-сессии: имя, занятость, WAIT_ABANDONED."""

from __future__ import annotations

import os

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp import com_mutex


class _Kernel32:
    """Kernel32 с управляемым результатом ожидания и учётом вызовов."""

    def __init__(self, wait: int = 0x0):
        self.wait = wait
        self.created: list[str] = []
        self.timeouts: list[int] = []
        self.released: list[int] = []
        self.closed: list[int] = []

    def CreateMutexW(self, attrs, initial, name):
        self.created.append(name)
        return 123

    def WaitForSingleObject(self, handle, timeout):
        self.timeouts.append(timeout)
        return self.wait

    def ReleaseMutex(self, handle):
        self.released.append(handle)

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.fixture
def mutex(monkeypatch, tmp_path):
    """Windows-ветка с подменённым kernel32 и своим файлом владельца."""
    monkeypatch.setattr(com_mutex, "_is_windows", lambda: True)
    monkeypatch.setattr(com_mutex, "OWNER_FILE",
                        str(tmp_path / "sitECRT-com-session.owner"))
    monkeypatch.setattr(com_mutex, "_handle", None, raising=False)


def _kernel(monkeypatch, wait: int = 0x0) -> _Kernel32:
    k32 = _Kernel32(wait)
    monkeypatch.setattr(com_mutex, "_kernel32", lambda: k32)
    return k32


def test_acquire_uses_shared_name_and_writes_owner(mutex, monkeypatch):
    """Имя замка ровно как у раннера; PID владельца записан."""
    k32 = _kernel(monkeypatch)
    com_mutex.acquire()
    try:
        assert k32.created == ["Local\\sitECRT-com-session"]
        assert k32.timeouts == [0]
        assert com_mutex.holder_pid() == os.getpid()
    finally:
        com_mutex.release()


def test_busy_reports_holder_pid(mutex, monkeypatch):
    """Занято: отказ с PID держателя, чужой процесс не трогается."""
    _kernel(monkeypatch, wait=0x102)
    with open(com_mutex.OWNER_FILE, "w", encoding="ascii") as fh:
        fh.write("4242")
    with pytest.raises(ToolError) as exc:
        com_mutex.acquire(timeout_ms=0)
    assert "занята другим клиентом" in str(exc.value)
    assert "4242" in str(exc.value)
    assert com_mutex._handle is None


def test_busy_without_owner_file_names_unknown(mutex, monkeypatch):
    """Файл владельца не читается: занятость всё равно отказ."""
    _kernel(monkeypatch, wait=0x102)
    with pytest.raises(ToolError) as exc:
        com_mutex.acquire()
    assert "PID держателя неизвестен" in str(exc.value)


def test_abandoned_lock_is_acquired(mutex, monkeypatch):
    """WAIT_ABANDONED: прежний держатель умер, замок наш."""
    k32 = _kernel(monkeypatch, wait=0x80)
    com_mutex.acquire()
    try:
        assert k32.released == []
        assert com_mutex.holder_pid() == os.getpid()
    finally:
        com_mutex.release()


def test_release_is_idempotent_and_removes_owner(mutex, monkeypatch):
    """Повторный release безопасен, файл владельца убран."""
    k32 = _kernel(monkeypatch)
    com_mutex.acquire()
    com_mutex.release()
    com_mutex.release()
    assert k32.released == [123] and k32.closed == [123]
    assert not os.path.exists(com_mutex.OWNER_FILE)


def test_acquire_twice_keeps_one_handle(mutex, monkeypatch):
    """Повторный acquire в процессе ничего не берёт заново."""
    k32 = _kernel(monkeypatch)
    com_mutex.acquire()
    try:
        com_mutex.acquire()
        assert len(k32.created) == 1
    finally:
        com_mutex.release()


def test_non_windows_is_noop(monkeypatch):
    """Linux: замок не трогает kernel32."""
    monkeypatch.setattr(com_mutex, "_is_windows", lambda: False)
    monkeypatch.setattr(com_mutex, "_handle", None, raising=False)
    com_mutex.acquire()
    com_mutex.release()
    assert com_mutex._handle is None
