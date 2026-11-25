"""Межпроцессный замок COM-сессии SimInTech.

Контракт согласован с раннером sitECRT (19.11.2026). Профиль
`%LOCALAPPDATA%\\DCAD` общий у всех экземпляров, поэтому два клиента без
замка поднимают два `mmain` и мешают друг другу. Замок берётся **до**
`CreateObject` и держится до `disconnect` и снятия своего процесса;
между открытием и закрытием проекта не отпускается. Если `disconnect`
идёт спасательным путём на зависшем COM-вызове, замок остаётся за
зависшим потоком: Windows отпускает мьютекс только из него, и система
снимет замок при выходе процесса.

Имя `Local\\sitECRT-com-session` действует в пределах сеанса пользователя.
Если сервер когда-нибудь пойдёт службой или в другом сеансе, понадобится
`Global\\` и согласование прав.

Занято: ждать `timeout_ms` (по умолчанию 0), затем отказ с PID держателя
из `%TEMP%\\sitECRT-com-session.owner`. Новый `mmain` не поднимается,
чужой процесс не трогается. `WAIT_ABANDONED` считается взятым замком:
предыдущий держатель умер.

Не Windows: no-op, как у раннера.
"""

from __future__ import annotations

import atexit
import os
import sys
import tempfile
import threading
from typing import Any, Optional

from fastmcp.exceptions import ToolError

#: Имя замка: ровно эта строка, общая с раннером sitECRT.
MUTEX_NAME = "Local\\sitECRT-com-session"

#: Файл с PID держателя: его читает и раннер, и наш отказ.
OWNER_FILE = os.path.join(tempfile.gettempdir(),
                          "sitECRT-com-session.owner")

_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102

_handle: Optional[int] = None

#: Поток, который взял замок. Windows отпускает мьютекс только из него:
#: `ReleaseMutex` из чужого потока даёт `ERROR_NOT_OWNER`, а ctypes молчит.
_owner_thread: Optional[int] = None


def _is_windows() -> bool:
    """Проверка платформы вынесена ради тестов."""
    return sys.platform == "win32"


def _kernel32() -> Any:
    """Kernel32 через ctypes; отдельная точка подмены в тестах."""
    import ctypes

    # `windll` есть только на Windows; getattr не даёт статическому
    # анализатору на Linux ругаться на отсутствующий атрибут.
    return getattr(ctypes, "windll").kernel32


def acquire(timeout_ms: int = 0) -> None:
    """Взять замок COM-сессии; занято - отказ с PID держателя.

    Повторный вызов в том же процессе ничего не делает: замок уже наш.
    """
    global _handle, _owner_thread
    if not _is_windows() or _handle is not None:
        return
    k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ToolError(
            f"COM-замок «{MUTEX_NAME}» создать не удалось: система не дала "
            f"дескриптор. Соединение не открывается, чтобы не поднять "
            f"второй SimInTech рядом с чужим.")
    wait = int(k32.WaitForSingleObject(handle, max(0, int(timeout_ms))))
    if wait not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        k32.CloseHandle(handle)
        pid = holder_pid()
        where = (f" (PID держателя {pid})" if pid is not None else
                 " (PID держателя неизвестен: файл владельца не читается)")
        raise ToolError(
            f"COM-сессия занята другим клиентом{where}: замок «{MUTEX_NAME}» "
            f"держит другой процесс, работающий с SimInTech. Профиль "
            f"%LOCALAPPDATA%\\DCAD общий, поэтому второй экземпляр мешал бы "
            f"ему. Дождитесь завершения того клиента или закройте его; "
            f"свой SimInTech этим отказом не поднимался, чужой процесс не "
            f"тронут.")
    _handle = handle
    _owner_thread = threading.get_ident()
    try:
        with open(OWNER_FILE, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        # Диагностика не должна ломать соединение: без файла отказ на
        # занятый замок просто не назовёт PID.
        pass


def release() -> str:
    """Отпустить замок и убрать файл владельца; идемпотентно.

    Windows привязывает владение мьютексом к потоку: `ReleaseMutex` из
    чужого потока возвращает 0 (`ERROR_NOT_OWNER`), и закрытие дескриптора
    владение не снимает. Поэтому из чужого потока замок **не трогается**:
    дескриптор и файл владельца остаются, а вызывающий получает
    `"not-owner"` и обязан назвать это состояние, а не «сессия сброшена».

    Возвращает исход: `"released"`, `"no-handle"`, `"not-owner"` или
    `"release-failed"` (сбой `ReleaseMutex` на своём потоке: дескриптор и
    файл владельца сохранены для повтора).
    """
    global _handle, _owner_thread
    if _handle is None:
        return "no-handle"
    if _is_windows() and _owner_thread != threading.get_ident():
        return "not-owner"
    handle = _handle
    if _is_windows():
        try:
            k32 = _kernel32()
            if not k32.ReleaseMutex(handle):
                return "release-failed"
            k32.CloseHandle(handle)
        except Exception:                                      # noqa: BLE001
            return "release-failed"
    _handle = None
    _owner_thread = None
    _remove_owner_file()
    return "released"


def held_by_other_thread() -> bool:
    """Замок держит живой поток, отличный от текущего.

    Так спасательный путь узнаёт, что отпустить замок из него нельзя:
    владелец - зависший COM-поток, и честный ответ называет это, а не
    выдаёт сброс за освобождение.
    """
    return (_is_windows() and _handle is not None
            and _owner_thread != threading.get_ident())


def _remove_owner_file() -> None:
    try:
        os.remove(OWNER_FILE)
    except OSError:
        pass


def holder_pid() -> Optional[int]:
    """PID держателя из файла владельца; `None` - не читается.

    Файл может быть устаревшим (держатель умер, замок отпущен системой):
    значение называется как прочитанное, а не как гарантированное.
    """
    try:
        with open(OWNER_FILE, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _at_exit() -> None:
    """На выходе процесса убрать файл владельца, даже если поток чужой.

    Замок снимет сама система вместе с завершением потока; файл владельца
    не должен пережить процесс и назвать чужому клиенту несуществующего
    держателя.
    """
    if release() in ("not-owner", "release-failed"):
        _remove_owner_file()


atexit.register(_at_exit)
