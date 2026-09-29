"""Инструменты языкового слоя: исход контура, отчёт об изменениях, отказы.

Мост подделывается целиком: настоящий требует Windows и живого `mmain.exe`, а
проверяется здесь контракт инструмента — что он возвращает, что пишет в каталог
результатов и как отказывает.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simintech_api.script_probe import ContourOutcome

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


# ─── set_page_script ─────────────────────────────────────────────────────────


class _BridgeRuns:
    """Мост-подделка контура: возвращает заданный исход и «прежний скрипт».

    Подделка моделирует **переход**, а не подставляет удобный ответ: тело
    запоминается, исход задаётся тестом, а `restored` меняется так же, как в
    жизни (мост возвращает на место то, что было до установки).
    """

    outcome = None
    restored = ""
    body = ""
    result_path: Path | None = None

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path):
        from simintech_api.core.script_bridge import PageRunResult
        type(self).body = body
        type(self).result_path = Path(result_path)
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)

    def install_script(self, script: str) -> None:
        """Оставленный скрипт: инструмент зовёт его, только если проверка прошла."""
        type(self).installed = script


class _BridgeRefuses(_BridgeRuns):
    """Мост-подделка: расчёт не сдвинул время — так выглядит отказ моста."""

    def run_page_script(self, body: str, result_path: Path):
        from simintech_api.exceptions import ScriptBridgeError
        raise ScriptBridgeError("расчёт не подтвердил рост модельного времени")


def _outcome(kind: str):
    from simintech_api.script_probe import ContourOutcome
    return ContourOutcome(kind=kind, lines=[])


@pytest.mark.anyio
async def test_set_page_script_keeps_script_when_it_compiles(monkeypatch, tmp_path):
    """Скрипт собрался — он остаётся в странице, а прежний приходит в ответе."""
    from simintech_api.script_probe import OUTCOME_OK

    class _Keeps(_BridgeRuns):
        outcome = _outcome(OUTCOME_OK)
        restored = "// прежний"
        installed = ""

    _install(monkeypatch, tmp_path, _Keeps, project=_FakeProject(["k_0"]))

    text = _text(await mcp.call_tool(
        "set_page_script", {"script": "seterrorflag(0);"}))

    assert _Keeps.installed == "seterrorflag(0);", "скрипт не оставлен в странице"
    assert "// прежний" in text, "прежний скрипт не отдан клиенту"
    assert "Отчёт об изменениях" in text


@pytest.mark.anyio
async def test_set_page_script_refuses_when_it_does_not_compile(
        monkeypatch, tmp_path):
    """Не собрался — отказ, и скрипт в странице **не** оставляется."""
    from simintech_api.script_probe import OUTCOME_NOT_COMPILED

    from _support import _error

    class _Broken(_BridgeRuns):
        outcome = _outcome(OUTCOME_NOT_COMPILED)
        installed = ""

    _install(monkeypatch, tmp_path, _Broken, project=_FakeProject())

    message = await _error("set_page_script", {"script": "x("})

    assert "не собрался" in message or "не компилир" in message
    assert _Broken.installed == "", "скрипт оставлен, хотя не собрался"
    assert "окне сообщений" in message, "отказ не говорит, где искать причину"


@pytest.mark.anyio
async def test_set_page_script_refuses_empty_script(monkeypatch, tmp_path):
    """Пустой текст — отказ: `SetPageScript` с пустой строкой **стирает** скрипт."""
    from _support import _error

    _install(monkeypatch, tmp_path, _BridgeRuns, project=_FakeProject())

    message = await _error("set_page_script", {"script": "   \n"})

    assert "пуст" in message.lower()


# ─── run_page_script (клапан) ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_page_script_returns_outcome_and_body_lines(
        monkeypatch, tmp_path):
    """Клапан отдаёт исход как есть — включая «модель не считает»."""
    from simintech_api.script_probe import OUTCOME_MODEL_NOT_RUNNING

    class _Stuck(_BridgeRuns):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING,
                                 lines=["СТРОКА ТЕЛА"])
        restored = "// прежний"

    _install(monkeypatch, tmp_path, _Stuck, project=_FakeProject(["k_0"]))

    text = _text(await mcp.call_tool("run_page_script", {"script": "x();"}))

    assert "не считает" in text
    assert "СТРОКА ТЕЛА" in text, "строки тела не показаны"
    # Полный текст прежнего скрипта клапан не печатает: он возвращён в проект,
    # и прочитать его можно `get_page_script`. В ответе — факт возврата.
    assert "Прежний скрипт страницы возвращён: да" in text


@pytest.mark.anyio
async def test_run_page_script_reports_objects_added(monkeypatch, tmp_path):
    """Отчёт об изменениях собирается инструментом: снимки до и после различаются.

    Подделка проекта моделирует **переход**: первый снимок отдаёт один объект,
    второй — два. Подделка, возвращающая одно и то же, кодировала бы допущение
    кода и не поймала бы отчёт, который всегда пишет «добавленных нет».
    """
    from simintech_api.script_probe import OUTCOME_OK

    class _Growing(_FakeProject):
        seen = 0

        def get_current_page(self):
            type(self).seen += 1
            names = (["k_0"] if type(self).seen == 1
                     else ["k_0", "Субмодель_0"])
            return _FakePage(names)

    class _Ok(_BridgeRuns):
        outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])

    _install(monkeypatch, tmp_path, _Ok,
             project=_Growing(["k_0"]))

    text = _text(await mcp.call_tool("run_page_script", {"script": "x();"}))

    assert "было 1" in text and "стало 2" in text
    assert "Субмодель_0" in text


@pytest.mark.anyio
async def test_run_page_script_refuses_abort_with_last_line(monkeypatch, tmp_path):
    """Обрыв — не отказ инструмента, а исход: он показывается, а не прячется."""
    from simintech_api.script_probe import OUTCOME_ABORTED

    class _Aborted(_BridgeRuns):
        outcome = ContourOutcome(kind=OUTCOME_ABORTED, lines=["УСПЕЛО"])

    _install(monkeypatch, tmp_path, _Aborted, project=_FakeProject())

    text = _text(await mcp.call_tool("run_page_script", {"script": "x();"}))

    assert "aborted" in text
    assert "УСПЕЛО" in text, "строка, до которой дошло тело, не показана"
