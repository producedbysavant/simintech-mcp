"""Перехват лога SimInTech через именованный канал (Named Pipe).

SimInTech выступает КЛИЕНТОМ pipe: после SetPipeName(name) и
SetJournalSavePeriod(period) он с заданной периодичностью пишет накопленные
сообщения в канал. Наша сторона — сервер канала: создаёт pipe, ждёт
подключения и читает лог в фоновом потоке.

Ограничение: работает только на Windows (pywin32 CreateNamedPipe). На Linux
запускается, но активность не ведёт (логика недоступна).
"""

from __future__ import annotations

import sys
import threading
from typing import Callable, Optional


class PipeLogger:
    """Фоновый перехватчик лога SimInTech.

    Args:
        pipe_name: имя канала (без префикса '\\\\.\\pipe\\').
        callback: вызывается с каждой строкой лога.
        journal_period_ms: период записи лога (SetJournalSavePeriod).
    """

    def __init__(
        self,
        pipe_name: str = "SITDebugPipe",
        callback: Optional[Callable[[str], None]] = None,
        journal_period_ms: int = 500,
    ):
        self.pipe_name = pipe_name
        self.journal_period_ms = journal_period_ms
        self.callback = callback or (lambda line: print(line))
        self._thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def start(self, client) -> None:
        """Настроить сервер и запустить фоновый поток чтения лога."""
        if not self.available:
            return
        try:
            client._safe_call("SetPipeName", self.pipe_name)
            client._safe_call("SetJournalSavePeriod", self.journal_period_ms)
        except Exception:
            pass

        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def stop(self, client) -> None:
        """Остановить перехват и сбросить настройки лога."""
        if self._thread and self._running:
            self._running = False
            self._thread.join(timeout=2.0)
        try:
            client._safe_call("SetJournalSavePeriod", 60000)
            client._safe_call("SetPipeName", "")
        except Exception:
            pass

    # ─── Внутреннее (только Windows) ────────────────────────────────

    def _reader(self) -> None:  # pragma: no cover — Windows-only
        try:
            import win32file
            import win32pipe
            import pywintypes
        except ImportError:
            return

        pipe_path = rf"\\.\pipe\{self.pipe_name}"
        try:
            handle = win32pipe.CreateNamedPipe(
                pipe_path,
                win32pipe.PIPE_ACCESS_INBOUND,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE,
                1, 65536, 65536, 0, None,
            )
        except pywintypes.error:
            return  # канал уже существует / не удалось создать

        while self._running:
            try:
                win32pipe.ConnectNamedPipe(handle, None)
                buf = b""
                while self._running:
                    hr, data = win32file.ReadFile(handle, 4096)
                    if data:
                        buf += data
                        while b"\r\n" in buf or b"\n" in buf:
                            line, _, buf = buf.partition(b"\r\n")
                            if not line:
                                line, _, buf = buf.partition(b"\n")
                            self._emit(line)
            except Exception:
                break
        win32file.CloseHandle(handle)

    def _emit(self, raw: bytes) -> None:
        try:
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            text = ""
        if text:
            try:
                self.callback(text)
            except Exception:
                pass
