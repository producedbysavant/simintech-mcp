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

    @staticmethod
    def get_blocks() -> list:
        return []


class _FakeProject:
    """Открытый проект: мосту нужен идентификатор, отчёту — страница."""

    def __init__(self, project_id: int = 7):
        self.id = project_id

    def get_current_page(self):
        return _Page()


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
        if type(self).outcome.kind == OUTCOME_OK and "savemodeltofile(" in body:
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


def _install(monkeypatch, tmp_path: Path, bridge) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _FakeProject())
    monkeypatch.setattr(mt, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_export_model_text_returns_dump_from_results_dir(monkeypatch, tmp_path):
    """Успех: текст выгрузки возвращается, а файлы пишутся в каталог результатов."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeRunsContour.payload in result
    # Путь выгрузки и файл результата контура — внутри каталога результатов:
    # инструмент, пишущий наружу, обходил бы песочницу чтения.
    assert Path(_dump_path_from_body(_BridgeRunsContour.body)).parent == tmp_path
    assert _BridgeRunsContour.result_path is not None
    assert _BridgeRunsContour.result_path.parent == tmp_path
    assert (tmp_path / MODEL_TEXT_FILE).exists()


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
async def test_export_model_text_clears_stale_dump(monkeypatch, tmp_path):
    """Прежняя выгрузка удаляется до прогона: иначе оборвавшийся прогон отдал бы её.

    Агент правит модель по этому тексту, поэтому устаревший текст в ответе —
    ошибка дороже отказа.
    """
    _install(monkeypatch, tmp_path, _BridgeFails)
    stale = tmp_path / MODEL_TEXT_FILE
    stale.write_text("СТАРАЯ ВЫГРУЗКА", encoding="utf-8")

    message = await _error("export_model_text", {})

    assert "не удалась" in message
    assert not stale.exists(), "устаревшая выгрузка осталась на месте"


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
