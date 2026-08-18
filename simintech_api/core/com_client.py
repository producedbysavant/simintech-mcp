"""COM-клиент: низкоуровневый доступ к серверу SimInTech (IMVTU_Server).

Работает только на Windows (COM). Обёртка над comtypes; TDataDescriptor
передаётся структурой (VT_RECORD), поэтому pywin32 не подходит.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional

from ..exceptions import ComConnectionError, ComCallError
from ..model import TDataDescriptor


class COMClient:
    """Подключение к SimInTech через COM и выполнение методов IMVTU_Server.

    Args:
        silent_mode: запустить SimInTech в скрытом режиме (без UI).
        com_progid: ProgID COM-объекта. По умолчанию "MVTU.Server"
            (кокласс MVTU_Server, интерфейс IMVTU_Server).
    """

    PROGID = "MVTU.Server"

    def __init__(self, silent_mode: bool = True, com_progid: str = PROGID):
        self._server: Any = None
        self._connected = False
        self._silent_mode = silent_mode
        self._com_progid = com_progid

    # ─── Жизненный цикл ─────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """COM доступен только на Windows."""
        return sys.platform == "win32"

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> "COMClient":
        """Подключиться к COM-серверу SimInTech.

        Вызывает mmain.exe (out-of-proc). Требует зарегистрированного
        COM-объекта: `bin/mmain.exe /regserver`.
        """
        if not self.is_available:
            raise ComConnectionError(
                "COM API SimInTech работает только на Windows. "
                "Текущая платформа: " + sys.platform
            )
        try:
            import comtypes.client
            self._server = comtypes.client.CreateObject(self._com_progid)
        except Exception as exc:
            raise ComConnectionError(
                f"Не удалось создать COM-объект '{self._com_progid}': {exc}. "
                f"Убедитесь, что SimInTech установлен и выполнен mmain.exe /regserver"
            ) from exc

        # Защита от автозавершения сервера при отсоединении последнего клиента
        self._safe_call("SetNoCloseAppFlag", 1)
        if self._silent_mode:
            self._safe_call("SetSilentMode", 1)

        self._connected = True
        return self

    def disconnect(self) -> None:
        """Отсоединиться от сервера (не закрывая приложение)."""
        self._server = None
        self._connected = False

    # ─── Низкоуровневые вызовы ──────────────────────────────────────

    def call(self, method: str, *args: Any) -> Any:
        """Вызвать метод IMVTU_Server и вернуть результат.

        В comtypes [out]-параметры возвращаются в порядке объявления;
        [in]-параметры передаются как обычные аргументы.
        """
        if not self._connected or self._server is None:
            raise ComConnectionError(
                "COM-сервер не подключён. Вызовите connect() первым."
            )
        func = getattr(self._server, method, None)
        if func is None:
            raise ComCallError(method, message=f"метод не найден в интерфейсе IMVTU_Server")
        try:
            return func(*args)
        except Exception as exc:
            hr = getattr(exc, "hresult", None) or getattr(exc, "hr", None)
            raise ComCallError(method, hr=hr, message=str(exc)) from exc

    def _safe_call(self, method: str, *args: Any) -> Any:
        """Вызов без строгой обработки ошибок (для необязательных настроек)."""
        if self._server is None:
            return None
        func = getattr(self._server, method, None)
        if func is None:
            return None
        try:
            return func(*args)
        except Exception:
            return None

    # ─── Типизированные вспомогательные методы ──────────────────────

    def open_project(self, path: str) -> int:
        """Открыть проект (.prt/.xprt), вернуть ProjectId (i64)."""
        project_id = self.call("OpenProject", path)
        return _as_int(project_id)

    def new_project(self) -> int:
        """Создать новый проект, вернуть ProjectId."""
        project_id = self.call("NewProject")
        return _as_int(project_id)

    def get_process_id(self) -> int:
        """Вернуть PID процесса mmain.exe."""
        return _as_int(self.call("GetProcessID"))

    def find_signal(self, name: str, project_id: int) -> TDataDescriptor:
        """Найти сигнал по имени в проекте; вернуть TDataDescriptor."""
        desc = self.call("FindSignalData", name, project_id)
        if isinstance(desc, TDataDescriptor):
            return desc
        # comtypes может вернуть кортеж из [out]-структуры — восстановим
        return _to_descriptor(desc)


def _as_int(value: Any) -> int:
    """Привести результат COM-вызова к int (comtypes ctypes-значения)."""
    if value is None:
        return 0
    if hasattr(value, "value"):
        return int(value.value)
    return int(value)


def _to_descriptor(value: Any) -> TDataDescriptor:
    """Восстановить TDataDescriptor из результата comtypes-вызова.

    comtypes может возвращать структуру напрямую (объект TDataDescriptor),
    кортеж (DataId, DataType) или объект с полями .DataId/.DataType.
    """
    if isinstance(value, TDataDescriptor):
        return value
    if value is None:
        return TDataDescriptor()
    if isinstance(value, (tuple, list)):
        data_id = value[0] if len(value) > 0 else 0
        data_type = value[1] if len(value) > 1 else 0
        return TDataDescriptor(_as_int(data_id), _as_int(data_type))
    data_id = getattr(value, "DataId", getattr(value, "data_id", 0))
    data_type = getattr(value, "DataType", getattr(value, "data_type", 0))
    return TDataDescriptor(_as_int(data_id), _as_int(data_type))
