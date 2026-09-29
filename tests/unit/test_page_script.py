"""Инструменты языкового слоя: исход контура, отчёт об изменениях, отказы.

Мост подделывается целиком: настоящий требует Windows и живого `mmain.exe`, а
проверяется здесь контракт инструмента — что он возвращает, что пишет в каталог
результатов и как отказывает.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import page_script

from _support import _text


# ─── Фейки: сессия и мост ────────────────────────────────────────────────────


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _Named:
    """Объект страницы: отчёту нужно только имя."""

    def __init__(self, name: str):
        self._name = name

    def get_name(self) -> str:
        return self._name


class _FakePage:
    def __init__(self, names: list[str]):
        self._names = list(names)

    def get_blocks(self) -> list[_Named]:
        return [_Named(name) for name in self._names]


class _FakeProject:
    """Проект: мосту нужен `id`, отчёту — объекты текущей страницы."""

    def __init__(self, names: list[str] | None = None):
        self.id = 7
        self._names = list(names or [])

    def get_current_page(self) -> _FakePage:
        return _FakePage(self._names)


class _BridgeReads:
    """Мост-подделка: чтение скрипта возвращает заданный текст."""

    script = "// прежний\nseterrorflag(0);\n"

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def read_page_script(self) -> str:
        return type(self).script


def _install(monkeypatch, tmp_path: Path, bridge, project=None) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", project or _FakeProject())
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)


# ─── Отчёт об изменениях ─────────────────────────────────────────────────────


def test_change_report_names_added_objects():
    """Отчёт: было/стало и имена добавленных объектов.

    Проверяется **чистая** функция: снимки «до» и «после» ей передаёт
    вызывающий, поэтому отчёт проверяется без моста и не подменяет собой
    проверку инструментов.
    """
    report = page_script._change_report(
        ["k_0"], ["k_0", "Субмодель_0"], "// прежний скрипт")

    assert "было 1" in report and "стало 2" in report
    assert "Субмодель_0" in report
    assert "Прежний скрипт страницы возвращён: да" in report


def test_change_report_does_not_call_replaced_object_new():
    """Переименование не выдаётся за добавление: сравнение по именам, не по числу.

    Среда сама переименовывает объекты (`kx_0`), поэтому «стало больше» и
    «добавлен объект X» — разные утверждения, и склеивать их нельзя.
    """
    report = page_script._change_report(["k_0", "kx_0"], ["k_0"], "")

    assert "стало 1" in report
    assert "добавленных объектов нет" in report
    assert "возвращён: нет" in report


def test_change_report_truncates_long_list():
    """Длинный список добавленных обрезается с честной пометкой о хвосте."""
    before = []
    after = [f"k_{index}" for index in range(page_script.MAX_REPORTED_OBJECTS + 5)]

    report = page_script._change_report(before, after, "x")

    assert "и ещё 5" in report, "хвост списка скрыт без предупреждения"


# ─── get_page_script ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_page_script_returns_text(monkeypatch, tmp_path):
    """Скрипт страницы возвращается клиенту как есть."""
    _install(monkeypatch, tmp_path, _BridgeReads)

    text = _text(await mcp.call_tool("get_page_script", {}))

    assert "// прежний" in text
    assert "seterrorflag(0);" in text


@pytest.mark.anyio
async def test_get_page_script_says_page_has_no_script(monkeypatch, tmp_path):
    """Пустой скрипт — сообщение, а не отказ и не пустая строка."""
    class _Empty(_BridgeReads):
        script = ""

    _install(monkeypatch, tmp_path, _Empty)

    text = _text(await mcp.call_tool("get_page_script", {}))

    assert "пуст" in text.lower()


@pytest.mark.anyio
async def test_get_page_script_refuses_with_reason(monkeypatch, tmp_path):
    """Отказ моста превращается в ToolError с объяснением, а не в текст-успех."""
    from simintech_api.exceptions import ScriptBridgeUnsafeStateError

    from _support import _error

    class _BrokenBridge(_BridgeReads):
        def read_page_script(self) -> str:
            raise ScriptBridgeUnsafeStateError(
                "метка моста осталась в записях: состояние неопределённо")

    _install(monkeypatch, tmp_path, _BrokenBridge)

    message = await _error("get_page_script", {})

    assert "состояние" in message
    assert "копией проекта" in message, "отказ не говорит, что делать"
