"""Состояние сессии: смена проекта, закрытие, отключение, живучесть COM."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from simintech_api import ComConnectionError

from simintech_mcp.server import mcp

from simintech_mcp import session

from _support import (
    _ClosableProject,
    _ConnectingBlock,
    _FakeWire,
    _WireProject,
    _error,
    _install_wire_project,
    _text,
)


def test_replace_project_clears_wire_registry(monkeypatch):
    """Смена проекта обнуляет реестр: чужие WireId указывают в никуда."""

    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    session._WIRES.append(_FakeWire(99))  # запись целиком не важна

    session._replace_project(_WireProject({}))

    assert session._WIRES == []


@pytest.mark.anyio
async def test_close_project_clears_wire_registry(monkeypatch):
    """close_project обнуляет реестр линий вместе с проектом."""
    project = _WireProject({})
    _install_wire_project(monkeypatch, {})
    monkeypatch.setattr(session, "_project", project)
    session._WIRES.append(_FakeWire(1))

    text = _text(await mcp.call_tool("close_project", {}))

    assert session._WIRES == []
    assert "закрыт" in text


def test_replace_project_closes_previous(monkeypatch):
    """Смена проекта закрывает предыдущий.

    Иначе create_project/open_project копили бы открытые проекты внутри
    mmain.exe, а инструменты молча работали бы с последним.
    """

    previous, fresh = _ClosableProject(), _ClosableProject()
    monkeypatch.setattr(session, "_project", previous)

    session._replace_project(fresh)

    assert previous.closed, "предыдущий проект не закрыт"
    assert session._project is fresh


def test_replace_project_tolerates_already_closed(monkeypatch):
    """Уже закрытый средой проект не должен ломать смену."""

    monkeypatch.setattr(session, "_project", _ClosableProject(raises=True))
    fresh = _ClosableProject()

    session._replace_project(fresh)

    assert session._project is fresh


def test_replace_project_without_previous(monkeypatch):
    """Первый проект в сессии — закрывать нечего."""

    monkeypatch.setattr(session, "_project", None)
    fresh = _ClosableProject()

    session._replace_project(fresh)

    assert session._project is fresh
    assert not fresh.closed


@pytest.mark.anyio
async def test_disconnect_resets_project_and_client(monkeypatch):
    """disconnect() сбрасывает и проект: иначе остаётся мёртвый ProjectId."""

    project = _ClosableProject()

    class _Client:
        connected = True

        def __init__(self):
            self.disconnected = False

        def disconnect(self):
            self.disconnected = True

    client = _Client()
    monkeypatch.setattr(session, "_project", project)
    monkeypatch.setattr(session, "_client", client)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert project.closed
    assert client.disconnected
    assert session._project is None
    assert session._client is None
    assert "Сессия завершена" in text


@pytest.mark.anyio
async def test_disconnect_without_session_is_noop(monkeypatch):
    """Нечего сбрасывать — сообщение «без изменений», а не вид действия."""

    monkeypatch.setattr(session, "_project", None)
    monkeypatch.setattr(session, "_client", None)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert "Без изменений" in text


@pytest.mark.anyio
async def test_close_project_without_project_is_noop(monkeypatch):
    """Закрытие без проекта — тоже «без изменений»."""

    monkeypatch.setattr(session, "_project", None)

    text = _text(await mcp.call_tool("close_project", {}))

    assert "Без изменений" in text


def test_replace_project_reports_failed_close(monkeypatch):
    """Неудачное закрытие предыдущего проекта не выдаётся за успех."""

    monkeypatch.setattr(session, "_project",
                        _ClosableProject(raises=True))
    fresh = _ClosableProject()

    note = session._replace_project(fresh)

    assert "ВНИМАНИЕ" in note
    assert session._project is fresh


@pytest.mark.anyio
async def test_disconnect_reports_failed_close(monkeypatch):
    """disconnect сбрасывает состояние, но сообщает о неудачном закрытии."""

    class _Client:
        def disconnect(self):
            pass

    monkeypatch.setattr(session, "_project",
                        _ClosableProject(raises=True))
    monkeypatch.setattr(session, "_client", _Client())

    text = _text(await mcp.call_tool("disconnect", {}))

    assert session._project is None
    assert "ВНИМАНИЕ" in text


def test_set_project_clears_wires():
    """Смена проекта сбрасывает линии: их COM-идентификаторы мертвы.

    Инвариант держится одним местом (`_set_project`), поэтому проверяется
    здесь, а не через `create_project`/`disconnect`.
    """

    saved_project = session._project
    session._WIRES.append(("wire", "k_0", 0, "kx_0", 0))
    try:
        session._set_project(None)

        assert session._WIRES == []
        assert session._project is None
    finally:
        session._WIRES.clear()
        session._project = saved_project


# ─── Живучесть COM ────────────────────────────────────────────────


class _DeadClient:
    """Клиент, у которого `connected` остался True, а COM не отвечает.

    Ровно это состояние наступает после перезапуска `mmain.exe`: флаг отражает
    только состоявшийся `connect()`, а прокси уже мёртв.
    """

    connected = True

    def call(self, method, *args):
        raise ComConnectionError("COM-сервер не отвечает")

    def get_process_id(self):
        return self.call("GetProcessID")


class _FreshClient:
    connected = True

    def __init__(self):
        self.probed = 0

    def connect(self):
        return self

    def get_process_id(self):
        self.probed += 1
        return 4242


def _install_client_factory(monkeypatch):
    """Подменить конструктор клиента, записав созданные экземпляры."""
    made = []

    def factory(silent_mode=True):
        client = _FreshClient()
        made.append(client)
        return client

    monkeypatch.setattr(session, "COMClient", factory)
    return made


def test_ensure_client_reconnects_dead_com(monkeypatch):
    """Мёртвый клиент не отдаётся повторно: `_ensure_client` переподключается.

    Без этого рецепт «перезапустите mmain.exe и повторите» неисполним:
    `connected` остаётся True, `_ensure_client` возвращал бы тот же мёртвый
    прокси, и каждый следующий COM-инструмент падал бы, пока не вызван
    `disconnect`. Проект сбрасывается вместе с клиентом: он держит собственный
    `_client`, и его COM-идентификаторы после смены сервера недействительны.
    """

    dead = _DeadClient()
    monkeypatch.setattr(session, "_client", dead)
    monkeypatch.setattr(session, "_project", _ClosableProject())
    session._WIRES.append(_FakeWire(1))
    made = _install_client_factory(monkeypatch)

    client = session._ensure_client()

    assert client is not dead, "старый мёртвый клиент не должен возвращаться"
    assert session._client is client
    assert made == [client], "новый клиент создаётся ровно один"
    assert session._WIRES == [], "линии мертвого проекта сбрасываются с ним"
    with pytest.raises(ToolError, match="Нет открытого проекта"):
        session._ensure_project()


def test_ensure_client_reuses_live_client(monkeypatch):
    """Живой клиент проверяется пробным вызовом, но не пересоздаётся.

    Без проверки клиент считается живым по флагу — а он не отражает живучесть
    COM; пересоздание же живого клиента рвало бы работающую сессию.
    """

    alive = _FreshClient()
    monkeypatch.setattr(session, "_client", alive)
    made = _install_client_factory(monkeypatch)

    assert session._ensure_client() is alive
    assert alive.probed == 1, "живой клиент должен быть проверен вызовом"
    assert made == [], "живой клиент пересоздавать нельзя"


class _DeadProject:
    """Проект, чьи COM-вызовы падают: сервер mmain.exe уже не отвечает."""

    def get_main_page(self):
        raise ComConnectionError("Сервер RPC недоступен")


@pytest.mark.anyio
async def test_ensure_project_reports_dead_com(monkeypatch):
    """Мёртвый COM виден и инструменту, который проект не создаёт.

    Смерть `mmain.exe` переподключали только `create_project`, `open_project` и
    `status`: остальные шли через `_ensure_project()` мимо пробника и отдавали
    сырой `ComCallError` из недр библиотеки. Теперь смерть видна и здесь: отказ
    называет причину и рецепт, а состояние сессии сброшено — иначе повтор
    подхватил бы тот же мёртвый прокси, и отказывали бы все COM-инструменты.
    """

    monkeypatch.setattr(session, "_client", _DeadClient())
    monkeypatch.setattr(session, "_project", _DeadProject())
    session._WIRES.append(_FakeWire(7))

    text = await _error("list_blocks", {})

    assert "потеряно" in text, "отказ обязан называть причину, а не молчать"
    assert "create_project" in text, "рецепт должен быть исполним"
    assert session._project is None
    assert session._client is None
    assert session._WIRES == [], "линии мёртвого проекта сбрасываются с ним"
    with pytest.raises(ToolError, match="Нет открытого проекта"):
        session._ensure_project()
