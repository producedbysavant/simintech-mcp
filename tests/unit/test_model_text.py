"""Текст модели: выгрузка и сборка — путь внутри каталога результатов и отказы.

Мост подделывается целиком (`ScriptBridge` в модуле инструмента): настоящий
требует Windows и живого `mmain.exe`, а проверяем мы не COM, а контракт
инструмента — куда он пишет, что возвращает и как отказывает.

Подделка моделирует **переход**: артефакт пишется по пути **из тела** (иначе
тест не заметил бы, что путь потеряли), а исход задаётся тестом — так
проверяются и «не собрался», и «модель не считает».
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simintech_api.core.script_bridge import PageRunResult
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.script_probe import (
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_NOT_COMPILED,
    OUTCOME_OK,
    ContourOutcome,
)

import simintech_mcp.tools.model_text as mt
from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools.model_text import MODEL_TEXT_FILE, PROBE_RESULT_FILE

from _support import _error, _text


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _Page:
    """Страница без объектов: отчёту об изменениях нужен её список."""

    def __init__(self, wires=None):
        #: Линии страницы: подсказка о трассировке считает их число.
        self._wires = list(wires or [])

    def get_blocks(self) -> list:
        return []

    def get_wires(self) -> list:
        return list(self._wires)


class _FakeProject:
    """Открытый проект: мосту нужен идентификатор, отчёту — страница."""

    def __init__(self, project_id: int = 7, wires=None):
        self.id = project_id
        self._wires = list(wires or [])

    def get_current_page(self):
        return _Page(self._wires)


def _dump_path_from_body(body: str) -> str:
    """Путь выгрузки — первый строковый литерал тела."""
    start = body.index('"') + 1
    end = body.index('"', start)
    return body[start:end]


class _BridgeRunsContour:
    """Мост-подделка контура: пишет артефакт по пути из тела и отдаёт исход.

    Пишет с BOM — так делает `savemodeltofile` (замер 2026-09-29), и именно
    поэтому инструмент BOM снимает: с ним текст не вклеивается обратно.
    """

    payload = 'A: (type = "Константа", points=[(0, 0)])'
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    restored = ""
    body = ""
    result_path: Path | None = None

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        type(self).result_path = Path(result_path)
        # Артефакт пишет только тело выгрузки. У тела сборки пути нет, а первый
        # строковый литерал — имя блока («Ступенька»): запись по нему создавала
        # файл в текущем каталоге, и так он однажды попал в коммит ветки.
        # «Тело исполнилось» — это оба исхода, `ok` и `model-not-running`:
        # `savemodeltofile` идёт из `initialization`, и файл есть даже на
        # стоящем расчёте (живое наблюдение 01.10.2026) — раньше подделка
        # писала файл только при `ok` и тем повторяла допущение кода.
        ran = (OUTCOME_OK, OUTCOME_MODEL_NOT_RUNNING)
        if type(self).outcome.kind in ran and "savemodeltofile(" in body:
            Path(_dump_path_from_body(body)).write_text(
                "﻿" + type(self).payload, encoding="utf-8")
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


class _BridgeFails(_BridgeRunsContour):
    """Мост-подделка: расчёт не сдвинул время — так выглядит отказ моста."""

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        raise ScriptBridgeError("модельное время не сдвинулось")


class _BridgeSilent(_BridgeRunsContour):
    """Мост-подделка: тело «отработало», а файла нет."""

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


def _install(monkeypatch, tmp_path: Path, bridge, wires=None) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _FakeProject(wires=wires))
    monkeypatch.setattr(mt, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_export_model_text_returns_dump_from_results_dir(monkeypatch, tmp_path):
    """Успех: текст выгрузки возвращается, а файлы пишутся в каталог результатов."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeRunsContour.payload in result
    dump_path = Path(_dump_path_from_body(_BridgeRunsContour.body))
    # Путь выгрузки и файл результата контура — внутри каталога результатов:
    # инструмент, пишущий наружу, обходил бы песочницу чтения.
    assert dump_path.parent == tmp_path
    assert _BridgeRunsContour.result_path is not None
    assert _BridgeRunsContour.result_path.parent == tmp_path
    assert dump_path.exists(), "выгрузка не записана"
    assert dump_path.name != MODEL_TEXT_FILE, \
        "выгрузка пишется под общим именем — прошлый прогон виден под тем же"


@pytest.mark.anyio
async def test_export_model_text_strips_bom(monkeypatch, tmp_path):
    """BOM снимается: с ним текст не вклеивается обратно через `eval`.

    `savemodeltofile` пишет выгрузку с BOM (замер 2026-09-29), а `eval` файла с
    BOM не принимает — возвращённый агенту текст должен быть пригоден для
    обратного пути без правки.
    """
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert "﻿" not in result
    assert _BridgeRunsContour.payload in result


@pytest.mark.anyio
async def test_export_model_text_does_not_serve_stale_dump(monkeypatch, tmp_path):
    """Старая выгрузка не выдаётся за новую — за счёт уникального имени.

    Прежде от этого защищались удалением файла прошлого прогона, но запертый
    файл удалить не даёт (WinError 32, живое наблюдение 02.10.2026), и защита
    молча отказывала. Теперь имя уникально на вызов: путь, который читает
    инструмент, создаёт только этот прогон — «тело отработало, файла нет»
    остаётся отказом, даже когда под прежним именем лежит старая выгрузка.
    """
    stale = tmp_path / MODEL_TEXT_FILE
    stale.write_text("СТАРАЯ ВЫГРУЗКА", encoding="utf-8")
    _install(monkeypatch, tmp_path, _BridgeSilent)

    message = await _error("export_model_text", {})

    assert "файла нет" in message
    assert "СТАРАЯ ВЫГРУЗКА" not in message
    assert stale.read_text(encoding="utf-8") == "СТАРАЯ ВЫГРУЗКА", \
        "файл с прежним именем тронут — запертый снять всё равно нельзя"


@pytest.mark.anyio
async def test_export_model_text_uses_fresh_names_per_call(monkeypatch, tmp_path):
    """У каждого вызова свои имена выгрузки и результата контура.

    Общее имя — тот же класс отказа, что WinError 32: обрыв оставляет файл
    запертым, и следующий вызов упирается в него. Уникальные имена заодно
    сохраняют прошлые артефакты читаемыми — их пути названы в ответах.
    """
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    await mcp.call_tool("export_model_text", {})
    first = _dump_path_from_body(_BridgeRunsContour.body)
    first_probe = _BridgeRunsContour.result_path
    await mcp.call_tool("export_model_text", {})
    second = _dump_path_from_body(_BridgeRunsContour.body)
    second_probe = _BridgeRunsContour.result_path

    assert first != second, "имя выгрузки переиспользовано между вызовами"
    assert first_probe != second_probe, "имя результата контура переиспользовано"
    assert first_probe.name != PROBE_RESULT_FILE
    assert second_probe.name != PROBE_RESULT_FILE
    assert Path(first).exists(), "прошлая выгрузка затёрта новым прогоном"


@pytest.mark.anyio
async def test_export_model_text_names_non_running_model(monkeypatch, tmp_path):
    """Несчитающая модель: выгрузка есть, и состояние названо (живое 01.10.2026).

    `savemodeltofile` исполняется в секции `initialization`, поэтому текст
    приходит полным и расчёт при этом не идёт. Молчать нельзя: агент решает
    по ответу, что делать дальше, а отказ здесь — только у тела, которое не
    отработало вовсе.
    """

    class _BridgeStuck(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    _install(monkeypatch, tmp_path, _BridgeStuck)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeRunsContour.payload in result
    assert "модель не считает" in result
    assert "initialization" in result, "причина полноты текста не названа"


@pytest.mark.anyio
async def test_export_model_text_names_the_reason(monkeypatch, tmp_path):
    """Отказ моста называет причину и что проверить: модель должна считать."""
    _install(monkeypatch, tmp_path, _BridgeFails)

    message = await _error("export_model_text", {})

    assert "модельное время не сдвинулось" in message
    assert "неподключённый вход" in message


@pytest.mark.anyio
async def test_export_model_text_refuses_when_file_is_missing(monkeypatch, tmp_path):
    """Прогон прошёл, а файла нет — это отказ, а не пустой успех."""
    _install(monkeypatch, tmp_path, _BridgeSilent)

    message = await _error("export_model_text", {})

    assert "файла нет" in message

    # И файл результата контура — своё имя, не пересекающееся с выгрузкой.
    assert PROBE_RESULT_FILE != MODEL_TEXT_FILE


@pytest.mark.anyio
async def test_export_model_text_refuses_when_script_did_not_compile(
        monkeypatch, tmp_path):
    """«Не собрался» — отказ с указанием, где искать текст ошибки."""
    class _Broken(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED, lines=[])

    _install(monkeypatch, tmp_path, _Broken)

    message = await _error("export_model_text", {})

    assert "not-compiled" in message
    assert "окне сообщений" in message, "отказ не говорит, где искать причину"


@pytest.mark.anyio
async def test_import_model_text_refuses_empty_text(monkeypatch, tmp_path):
    """Пустой текст модели — отказ до COM-вызова."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    message = await _error("import_model_text", {"model_text": "   \n"})

    assert "пуст" in message.lower()


@pytest.mark.anyio
async def test_import_model_text_builds_body_and_reports_changes(
        monkeypatch, tmp_path):
    """Тело сборки собирается из текста, а ответ называет изменения."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "createmodel(getcurrentprojectid, model);" in _BridgeRunsContour.body
    assert 'block0: (type = "Ступенька")' in _BridgeRunsContour.body
    assert "Отчёт об изменениях" in result


@pytest.mark.anyio
async def test_import_model_text_reports_model_stuck(monkeypatch, tmp_path):
    """«Модель не считает» — не отказ сборки: текст принят, а расчёт стоит."""
    class _Stuck(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    _install(monkeypatch, tmp_path, _Stuck)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "не считает" in result


class _BridgeAddsWires(_BridgeRunsContour):
    """Мост-подделка: тело сборки добавило линии.

    Подделка моделирует **переход**: счётчик линий в подсказке — это прирост
    (до/после контура), и подделка, возвращающая одно и то же число, не могла
    бы его проверить.
    """

    def run_page_script(self, body, result_path):
        if "createmodel(" in body:
            session.ensure_project()._wires.extend([object(), object()])
        return super().run_page_script(body, result_path)


@pytest.mark.anyio
async def test_import_model_text_warns_lines_not_traced(monkeypatch, tmp_path):
    """Импорт добавил линии — ответ напоминает: не трассированы, нужен `layout_place`.

    В кейсе #24 сразу после импорта провода шли диагоналями через всю схему,
    и модель выглядела нечитаемой; подсказка в ответе снимает лишний круг
    «почему косо» (issue #24, п.3).
    """
    _install(monkeypatch, tmp_path, _BridgeAddsWires)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "Линии связи: +2" in result
    assert "не пересчитана" in result
    assert "layout_place" in result


@pytest.mark.anyio
async def test_import_model_text_does_not_blame_existing_wires(
        monkeypatch, tmp_path):
    """Без прироста линии импорту не приписываются (находка ревью).

    Прежде по одному счётчику ответ утверждал «после импорта они не
    трассированы» про все линии страницы — в том числе уже проложенные.
    """
    _install(monkeypatch, tmp_path, _BridgeRunsContour,
             wires=[object(), object()])

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "новых импорт не добавил" in result
    assert "не пересчитана" not in result


@pytest.mark.anyio
async def test_import_model_text_reports_no_lines(monkeypatch, tmp_path):
    """Линий нет — ответ говорит и это, а не молчит неопределённо."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "Линий связи на странице нет" in result
