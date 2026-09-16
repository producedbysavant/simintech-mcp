"""Жизненный цикл проекта: создание, расчёт, сохранение."""

from __future__ import annotations

import sys

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.server import mcp

from simintech_mcp import session
from simintech_mcp.tools import project as project_tools

from _support import (
    _TemplateProject,
    _error,
    _install_fake_template,
    _install_savable,
    _text,
)


@pytest.mark.anyio
async def test_status_on_linux():
    """status() на Linux сообщает о несовместимости платформы."""
    if sys.platform == "win32":
        pytest.skip("Тест для не-Windows окружения")
    result = await mcp.call_tool("status", {})
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "Windows" in text


@pytest.mark.anyio
async def test_create_project_uses_template_and_sets_end_time(monkeypatch):
    """create_project идёт через шаблон и применяет end_time."""

    project, opened = _install_fake_template(monkeypatch)

    text = _text(await mcp.call_tool("create_project", {"end_time": 2.5}))

    assert opened == [project], "проект должен создаваться из шаблона"
    assert project.end_time == 2.5
    assert "2.5 с" in text
    assert session._project is project


@pytest.mark.anyio
async def test_create_project_rejects_non_positive_end_time(monkeypatch):
    """Неверное время отвергается ДО открытия шаблона.

    Иначе созданный проект остался бы висеть в mmain.exe: он не попал бы ни в
    `_project`, ни в закрытие.
    """
    _project_obj, opened = _install_fake_template(monkeypatch)

    text = await _error("create_project", {"end_time": 0})

    assert "положительным" in text
    assert opened == [], "шаблон открывать было нельзя"


@pytest.mark.anyio
async def test_set_calc_time_delegates_to_project(monkeypatch):
    """set_calc_time передаёт значение в проект."""

    project = _TemplateProject()
    monkeypatch.setattr(session, "_project", project)

    text = _text(await mcp.call_tool("set_calc_time", {"seconds": 7.0}))

    assert project.end_time == 7.0
    assert "7.0 с" in text


@pytest.mark.anyio
async def test_save_project_defaults_to_xml(monkeypatch):
    """По умолчанию сохраняется XML, и перед записью показывается форма.

    Без показа формы в файл уходит признак «окно скрыто», и GUI открывает
    проект, не показывая окно модели.
    """
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool("save_project", {"path": r"C:\m.xprt"}))

    assert project.calls == [("show_form", None), ("xml", r"C:\m.xprt")]
    assert "XML" in text
    assert "Форма проекта показана" in text


@pytest.mark.anyio
async def test_save_project_binary_flag_writes_prt(monkeypatch):
    """binary=True пишет нативный .prt — его открывает GUI SimInTech."""
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": r"C:\m.prt", "binary": True}))

    assert project.calls == [("show_form", None), ("binary", r"C:\m.prt")]
    assert ".prt" in text


@pytest.mark.anyio
async def test_save_project_can_skip_showing_form(monkeypatch):
    """show_form=False — безоконное сохранение: форму не показываем."""
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": r"C:\m.prt", "binary": True,
                         "show_form": False}))

    assert project.calls == [("binary", r"C:\m.prt")]
    assert "Форму не показывали" in text


@pytest.mark.anyio
async def test_save_project_failure_is_error(monkeypatch):
    """Неудачная запись — отказ, а не ответ «проект сохранён»."""
    _install_savable(monkeypatch, raises=True)

    text = await _error("save_project", {"path": r"C:\m.prt", "binary": True})

    assert "диск переполнен" in text


def test_status_refuses_when_com_unavailable(monkeypatch):
    """`status` отказывает, если подключиться не удалось.

    Текстом это выглядело бы успехом для клиента, доверяющего `isError`.
    """

    monkeypatch.setattr(sys, "platform", "win32")

    def unavailable():
        raise RuntimeError("COM не зарегистрирован")

    monkeypatch.setattr(session, "_ensure_client", unavailable)

    with pytest.raises(ToolError, match="недоступен"):
        project_tools.status()
