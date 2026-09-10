"""Тесты COMClient с фейковым сервером (без реального COM).

comtypes на Linux падает при импорте (COM доступен только на Windows),
поэтому модуль comtypes и comtypes.client подменяются фейками в sys.modules.
Проверяется логика клиента: connect, open_project, find_signal, обработка
ошибок, диспетчеризация Read/Write по DataType.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from simintech_api.exceptions import ComCallError, ComConnectionError
from simintech_api.model import TDataDescriptor


class FakeServer:
    """Фейковый IMVTU_Server с минимальным набором методов."""

    def __init__(self):
        self.calls = []
        self._project_id = 42
        self._next_signal_id = 1000

    def SetNoCloseAppFlag(self, v):
        self.calls.append(("SetNoCloseAppFlag", v))

    def SetSilentMode(self, v):
        self.calls.append(("SetSilentMode", v))

    def GetProcessID(self):
        self.calls.append(("GetProcessID",))
        return 12345

    def OpenProject(self, path):
        self.calls.append(("OpenProject", path))
        return self._project_id

    def NewProject(self):
        self.calls.append(("NewProject",))
        return self._project_id

    def FindSignalData(self, name, project_id):
        self.calls.append(("FindSignalData", name, project_id))
        desc = TDataDescriptor()
        desc.DataId = self._next_signal_id
        desc.DataType = 0  # double
        return desc

    def ReadAsFloat(self, desc):
        self.calls.append(("ReadAsFloat", desc.DataId, desc.DataType))
        return 7.5

    def WriteAsFloat(self, desc, value):
        self.calls.append(("WriteAsFloat", desc.DataId, desc.DataType, value))

    def CloseProject(self, project_id):
        self.calls.append(("CloseProject", project_id))


def test_connect_initializes_com_for_current_thread(monkeypatch):
    """connect() инициализирует COM в текущем потоке.

    COM привязан к потоку. Серверы, выполняющие обработчики в пуле потоков
    (MCP/FastMCP), без этого падают с CO_E_NOTINITIALIZED («Не был произведён
    вызов CoInitialize»). Проверено на реальном SimInTech.
    """
    fake = FakeServer()
    calls = []
    client = _make_client(monkeypatch, fake)
    fake_comtypes = sys.modules["comtypes"]
    fake_comtypes.CoInitializeEx = lambda mode: calls.append(mode)

    client.connect()

    assert calls == [fake_comtypes.COINIT_APARTMENTTHREADED]


def test_connect_tolerates_changed_apartment_mode(monkeypatch):
    """RPC_E_CHANGED_MODE не считается ошибкой: поток уже инициализирован."""
    fake = FakeServer()
    _install_fake_comtypes(monkeypatch, fake)

    def raise_changed_mode(mode):
        exc = OSError("changed mode")
        exc.winerror = -2147417850
        raise exc

    sys.modules["comtypes"].CoInitializeEx = raise_changed_mode

    client = _make_client(monkeypatch, fake)
    client.connect()  # не должно бросить

    assert client.connected


def _install_fake_comtypes(monkeypatch, fake: FakeServer):
    """Подменить comtypes и comtypes.client в sys.modules фейками."""
    fake_comtypes = types.ModuleType("comtypes")
    fake_client = types.ModuleType("comtypes.client")
    fake_client.CreateObject = lambda progid: fake
    fake_comtypes.client = fake_client
    # Структуры для TDataDescriptor (model.py импортирует их из comtypes)
    from simintech_api import model as _model

    class FakeStructure:
        _fields_ = [("DataId", "int"), ("DataType", "int")]

        def __init__(self, data_id=0, data_type=0):
            self.DataId = data_id
            self.DataType = data_type

    fake_comtypes.Structure = FakeStructure
    fake_comtypes.c_int64 = "int"
    fake_comtypes.c_long = "int"
    # COM инициализируется по потокам; connect() вызывает CoInitializeEx.
    fake_comtypes.COINIT_APARTMENTTHREADED = 2
    fake_comtypes.COINIT_MULTITHREADED = 0
    fake_comtypes.CoInitializeEx = lambda mode: None

    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", fake_client)

    # Пересоздаём TDataDescriptor с фейковой структурой
    _model.TDataDescriptor = type("TDataDescriptor", (FakeStructure,), {})


def _make_client(monkeypatch, fake: FakeServer):
    from simintech_api.core import com_client as cc

    _install_fake_comtypes(monkeypatch, fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return cc.COMClient(silent_mode=True)


# ─── Тесты ─────────────────────────────────────────────────────────


def test_connect_success(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    assert client.connected
    assert ("SetNoCloseAppFlag", 1) in fake.calls
    assert ("SetSilentMode", 1) in fake.calls


def test_connect_platform_restricted(monkeypatch):
    from simintech_api.core.com_client import COMClient

    monkeypatch.setattr(sys, "platform", "linux")
    client = COMClient()
    with pytest.raises(ComConnectionError):
        client.connect()


def test_open_project(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    pid = client.open_project("model.prt")
    assert pid == 42
    assert ("OpenProject", "model.prt") in fake.calls


def test_new_project(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    assert client.new_project() == 42


def test_get_process_id(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    assert client.get_process_id() == 12345


def test_find_signal_returns_descriptor(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    desc = client.find_signal("sig", 42)
    assert isinstance(desc, TDataDescriptor)
    assert desc.DataId == 1000
    assert desc.DataType == 0
    assert desc.is_valid


def test_call_before_connect_raises(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    with pytest.raises(ComConnectionError):
        client.call("OpenProject", "x.prt")


def test_call_missing_method_raises(monkeypatch):
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    # Метода нет в фейковом сервере
    fake.__class__.__delattr__  # noqa — гарантируем отсутствие метода
    with pytest.raises(ComCallError):
        client.call("MissingMethod")


def test_typed_read_write_via_signal(monkeypatch):
    """Диспетчеризация Read/Write по DataType через Signal."""
    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()

    from simintech_api import Project, Signal
    prj = Project(client, 42)
    desc = client.find_signal("sig", 42)
    sig = Signal(prj, desc, "sig")
    assert sig.read() == 7.5
    sig.write(3.0)
    assert ("ReadAsFloat", 1000, 0) in fake.calls
    assert ("WriteAsFloat", 1000, 0, 3.0) in fake.calls


def test_owned_pid_records_client_pid(monkeypatch):
    """_owned_pid запоминает PID процесса, к которому подключился клиент."""
    from simintech_api.core import com_client as cc

    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()
    # FakeServer.GetProcessID возвращает 12345
    assert client._owned_pid == 12345


def test_shutdown_no_kill_by_default(monkeypatch):
    """shutdown() по умолчанию НЕ завершает процессы (только disconnect)."""
    from simintech_api.core import com_client as cc
    from simintech_api.utils import processes as proc

    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()

    killed = []
    monkeypatch.setattr(proc, "_pids_wmic", lambda: set())
    monkeypatch.setattr(proc, "_pids_tasklist", lambda: set())
    monkeypatch.setattr(proc, "_pids_powershell", lambda: set())
    monkeypatch.setattr(proc.subprocess, "run",
                        lambda *a, **k: killed.append(a[0]) or None)
    client.shutdown()          # без kill_pids — не убивать
    assert killed == []


def test_shutdown_kills_explicit_pids(monkeypatch):
    """shutdown(kill_pids=...) завершает ТОЛЬКО переданные PID'ы."""
    from simintech_api.core import com_client as cc
    from simintech_api.utils import processes as proc

    fake = FakeServer()
    client = _make_client(monkeypatch, fake)
    client.connect()

    killed = []
    monkeypatch.setattr(proc, "_pids_wmic", lambda: set())
    monkeypatch.setattr(proc, "_pids_tasklist", lambda: set())
    monkeypatch.setattr(proc, "_pids_powershell", lambda: set())
    monkeypatch.setattr(proc.subprocess, "run",
                        lambda *a, **k: killed.append(a[0]) or None)
    client.shutdown(kill_pids=[999, 888])
    # Каждый PID убивается отдельным taskkill
    assert killed
    assert all(args and args[0] == "taskkill" for args in killed)
    assert ["taskkill", "/F", "/PID", "999"] in killed
    assert ["taskkill", "/F", "/PID", "888"] in killed
