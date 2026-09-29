"""Выгрузка текста модели: путь внутри каталога результатов, отказы и чистка.

Мост подделывается целиком (`ScriptBridge` в модуле инструмента): настоящий
мост требует Windows и живого `mmain.exe`, а проверяем мы не COM, а контракт
инструмента — куда он пишет, что возвращает и как отказывает.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simintech_api.exceptions import ScriptBridgeError

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import model_text
from simintech_mcp.tools.model_text import MODEL_TEXT_FILE, PROBE_RESULT_FILE

from _support import _error, _text


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _FakeProject:
    """Открытый проект: мосту нужен только его идентификатор."""

    def __init__(self, project_id: int = 7):
        self.id = project_id


class _Probe:
    def __init__(self) -> None:
        self.complete = True
        self.lines: list[str] = []


def _dump_path_from_body(body: str) -> str:
    """Путь выгрузки — первый строковый литерал тела."""
    start = body.index('"') + 1
    end = body.index('"', start)
    return body[start:end]


class _BridgeWritesDump:
    """Мост-подделка: пишет выгрузку по пути **из тела**, как это делает скрипт.

    Подделка, возвращающая успех без записи файла, кодировала бы то же
    допущение, что и код: тест перестал бы замечать, что путь в теле потеряли.
    """

    payload = 'A: (type = "Константа", points=[(0 , 0)])'
    body = ""
    result: Path | None = None

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_probe(self, body: str, result_path: Path) -> _Probe:
        type(self).body = body
        type(self).result = Path(result_path)
        Path(_dump_path_from_body(body)).write_text(
            type(self).payload, encoding="utf-8")
        return _Probe()


class _BridgeFails:
    """Мост-подделка: расчёт не сдвинул время — так выглядит отказ моста."""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_probe(self, body: str, result_path: Path) -> _Probe:
        raise ScriptBridgeError("модельное время не сдвинулось")


class _BridgeSilent:
    """Мост-подделка: проба «завершена», а файла нет."""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_probe(self, body: str, result_path: Path) -> _Probe:
        return _Probe()


class _BridgeWritesBom(_BridgeWritesDump):
    """Мост-подделка: выгрузка с BOM — так её пишет `savemodeltofile`."""

    def run_probe(self, body: str, result_path: Path) -> _Probe:
        Path(_dump_path_from_body(body)).write_bytes(
            ("\ufeff" + type(self).payload).encode("utf-8"))
        return _Probe()


def _install(monkeypatch, tmp_path: Path, bridge) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _FakeProject())
    monkeypatch.setattr(model_text, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_export_model_text_returns_dump_from_results_dir(monkeypatch, tmp_path):
    """Успех: текст выгрузки возвращается, а файлы пишутся в каталог результатов."""
    _install(monkeypatch, tmp_path, _BridgeWritesDump)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeWritesDump.payload in result
    # Путь выгрузки и файл результата моста — внутри каталога результатов:
    # инструмент, пишущий наружу, обходил бы песочницу чтения.
    assert Path(_dump_path_from_body(_BridgeWritesDump.body)).parent == tmp_path
    assert _BridgeWritesDump.result is not None
    assert _BridgeWritesDump.result.parent == tmp_path
    assert (tmp_path / MODEL_TEXT_FILE).exists()


@pytest.mark.anyio
async def test_export_model_text_strips_bom(monkeypatch, tmp_path):
    """BOM снимается: с ним текст не вклеивается обратно через `eval`.

    `savemodeltofile` пишет выгрузку с BOM (замер 2026-09-29), а `eval` файла с
    BOM не принимает — возвращённый агенту текст должен быть пригоден для
    обратного пути без правки.
    """
    _install(monkeypatch, tmp_path, _BridgeWritesBom)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert "﻿" not in result
    assert _BridgeWritesDump.payload in result


@pytest.mark.anyio
async def test_export_model_text_clears_stale_dump(
        monkeypatch, tmp_path):
    """Прежняя выгрузка удаляется до прогона: иначе оборвавшаяся проба отдала бы её.

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
async def test_export_model_text_names_the_reason(
        monkeypatch, tmp_path):
    """Отказ моста называет причину и что проверить: модель должна считать."""
    _install(monkeypatch, tmp_path, _BridgeFails)

    message = await _error("export_model_text", {})

    assert "модельное время не сдвинулось" in message
    assert "неподключённый вход" in message


@pytest.mark.anyio
async def test_export_model_text_refuses_when_file_is_missing(monkeypatch, tmp_path):
    """Проба прошла, а файла нет — это отказ, а не пустой успех."""
    _install(monkeypatch, tmp_path, _BridgeSilent)

    message = await _error("export_model_text", {})

    assert "файла нет" in message

    # И файл результата моста инструмент тоже убирает за собой: имя
    # закреплено, чтобы не пересечься с результатами расчёта.
    assert PROBE_RESULT_FILE != MODEL_TEXT_FILE
