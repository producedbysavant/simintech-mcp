"""Снимок схемы: `savescreenshot` в контуре — файл в каталоге результатов.

Мост подделывается целиком (`ScriptBridge` в контурном ядре `page_script`):
настоящий требует Windows и живого `mmain.exe`, а проверяем мы контракт
инструмента — какой тип формата уходит в тело, куда пишется файл и что ответ.

Подделка моделирует **переход**: файл снимка пишется по пути **из тела**
(иначе тест не заметил бы, что путь потеряли), а исход задаётся тестом — так
проверяются и «не собрался», и «модель не считает».
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simintech_api.core.script_bridge import PageRunResult
from simintech_api.script_probe import (
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_NOT_COMPILED,
    OUTCOME_OK,
    ContourOutcome,
)

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import page_script

from _support import _error, _text


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _FakeProject:
    """Открытый проект: мосту нужен идентификатор."""

    def __init__(self, project_id: int = 7):
        self.id = project_id


def _shot_path_from_body(body: str) -> str:
    """Путь снимка — первый строковый литерал тела."""
    start = body.index('"') + 1
    end = body.index('"', start)
    return body[start:end]


class _BridgeShoots:
    """Мост-подделка: пишет файл снимка по пути из тела и отдаёт исход."""

    #: Магия PNG — то, что клиент увидит в файле по умолчанию.
    payload = b"\x89PNG\r\n\x1a\nfake"
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    restored = ""
    body = ""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        ran = (OUTCOME_OK, OUTCOME_MODEL_NOT_RUNNING)
        if type(self).outcome.kind in ran and "savescreenshot(" in body:
            Path(_shot_path_from_body(body)).write_bytes(type(self).payload)
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


class _BridgeSilent(_BridgeShoots):
    """Мост-подделка: тело «отработало», а файл не создан."""

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


def _install(monkeypatch, tmp_path: Path, bridge) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _FakeProject())
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_save_screenshot_writes_png_by_default(monkeypatch, tmp_path):
    """PNG — формат по умолчанию: тип 2 в теле, файл внутри каталога результатов."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = _text(await mcp.call_tool("save_screenshot", {}))

    path = _shot_path_from_body(_BridgeShoots.body)
    assert path.endswith(".png"), f"имя без формата png: {path}"
    assert f'"{path}", 2);' in _BridgeShoots.body, "в тело ушёл не тип 2 (PNG)"
    assert Path(path).is_file(), "файл снимка не создан"
    assert Path(path).read_bytes() == _BridgeShoots.payload
    assert str(tmp_path) in path, "снимок вне каталога результатов"
    assert "Снимок схемы (png)" in text
    assert path in text, "ответ обязан называть путь файла"


@pytest.mark.anyio
@pytest.mark.parametrize("fmt,code", [("bmp", 1), ("svg", 3)])
async def test_save_screenshot_maps_formats_to_types(
        monkeypatch, tmp_path, fmt, code):
    """Форматы отображаются на коды типов функции: BMP — 1, SVG — 3."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    await mcp.call_tool("save_screenshot", {"format": fmt})

    path = _shot_path_from_body(_BridgeShoots.body)
    assert path.endswith(f".{fmt}")
    assert f", {code});" in _BridgeShoots.body, \
        f"формат {fmt} ушёл не как тип {code}"


@pytest.mark.anyio
async def test_save_screenshot_rejects_unknown_format(monkeypatch, tmp_path):
    """Неизвестный формат — отказ со списком известных, а не молчание."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = await _error("save_screenshot", {"format": "tiff"})

    assert "не поддерживается" in text
    assert "png" in text and "bmp" in text and "svg" in text


@pytest.mark.anyio
async def test_save_screenshot_refuses_when_file_missing(monkeypatch, tmp_path):
    """Тело отработало, а файла нет — отказ, а не «снимок: путь» без файла.

    Так выглядит неподходящий тип: savescreenshot молча ничего не создаёт
    (живой замер 03.10.2026: тип 0).
    """
    _install(monkeypatch, tmp_path, _BridgeSilent)

    text = await _error("save_screenshot", {})

    assert "файла снимка нет" in text


@pytest.mark.anyio
async def test_save_screenshot_names_non_running_model(monkeypatch, tmp_path):
    """На несчитающей модели снимок есть — исход называет это, не подменяя успех."""
    class _Stuck(_BridgeShoots):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    _install(monkeypatch, tmp_path, _Stuck)

    text = _text(await mcp.call_tool("save_screenshot", {}))

    assert Path(_shot_path_from_body(_Stuck.body)).is_file()
    assert "initialization" in text, \
        "ответ обязан объяснить, почему снимок есть на стоящем расчёте"


@pytest.mark.anyio
async def test_save_screenshot_refuses_when_script_did_not_compile(
        monkeypatch, tmp_path):
    """`not-compiled` — отказ: снимка нет, и это не «модель не считает»."""
    class _Broken(_BridgeShoots):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED,
                                 lines=["savescreenshot(...)"])

    _install(monkeypatch, tmp_path, _Broken)

    text = await _error("save_screenshot", {})

    assert "не собралось" in text
    assert "окне сообщений" in text, "отказ не говорит, где искать причину"


@pytest.mark.anyio
async def test_save_screenshot_uses_fresh_names(monkeypatch, tmp_path):
    """Имя уникально на вызов: серия «до/после» не затирает предыдущие снимки."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    first_text = _text(await mcp.call_tool("save_screenshot", {}))
    first_path = _shot_path_from_body(_BridgeShoots.body)
    second_text = _text(await mcp.call_tool("save_screenshot", {}))
    second_path = _shot_path_from_body(_BridgeShoots.body)

    assert first_path != second_path, f"имена снимков не уникальны: {first_path}"
    assert Path(first_path).is_file() and Path(second_path).is_file()
    assert first_path in first_text and second_path in second_text


def test_png_size_reads_ihdr_and_rejects_other_formats(tmp_path):
    """Размер полотна читается из заголовка PNG; не-PNG честно отвергается.

    Полотно у среды не постоянно (живой замер 05.10.2026: 1026x580 у части
    снимков и 1026x659 у другой), поэтому `save_screenshot` берёт размер из
    самого снимка. Ошибка чтения не должна выглядеть как «размер есть»:
    у BMP и SVG заголовок другой, и пересчитывать по нему кадр нельзя.
    """
    from simintech_mcp.tools.screenshot import _png_size

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                    + (1026).to_bytes(4, "big") + (659).to_bytes(4, "big"))
    assert _png_size(str(png)) == (1026, 659)

    other = tmp_path / "shot.bmp"
    other.write_bytes(b"BM" + b"\x00" * 30)
    assert _png_size(str(other)) is None
    assert _png_size(str(tmp_path / "missing.png")) is None
