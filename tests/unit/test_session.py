"""Состояние сессии: смена проекта, закрытие, отключение."""

from __future__ import annotations

import pytest

from simintech_mcp.server import mcp

from simintech_mcp import session

from _support import (
    _ClosableProject,
    _ConnectingBlock,
    _FakeWire,
    _WireProject,
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
