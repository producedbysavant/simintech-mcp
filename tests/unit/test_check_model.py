"""Проверка оформления модели: наложения, подписи, порты и концы линий.

Мост подделывается целиком: настоящий требует Windows и живого `mmain.exe`.
Подделка моста моделирует **переход**: тело контура открывает файл отчёта
само (дескриптор моста телу недоступен по имени — он назван случайной частью
метки), поэтому подделка читает путь из тела и пишет отчёт туда же, а не
«куда-нибудь».
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

import simintech_mcp.tools.check_model as cm
from simintech_mcp import session
from simintech_mcp.server import mcp

from _support import _error, _text


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _CheckBlock:
    """Блок с габаритом, подписями и числом портов — как их читает проверка.

    `Points` повторяет живую форму (замер 02.10.2026): первая точка — **центр**
    блока, вторая — выходной порт (центр + (16, 0)), дальше точки полилинии.
    Габарит проверка строит из центра и размера — min/max подделки был бы
    неверен ровно так же, как был неверен код.
    """

    def __init__(self, name, center=None, portnames="", ports=2,
                 width=32.0, height=16.0, class_name=""):
        self._name = name
        self._center = center
        self._portnames = portnames
        self._ports = ports
        self._width = width
        self._height = height
        self.class_name = class_name

    @property
    def id(self):
        return abs(hash(self._name)) % 1000

    def get_name(self):
        return self._name

    def get_size(self):
        return (self._width, self._height)

    def get_points(self):
        if self._center is None:
            return ""
        cx, cy = self._center
        return (f"[({cx:g} , {cy:g}), ({cx + 16:g} , {cy:g}), "
                f"({cx:g} , {cy - self._height / 2:g}), "
                f"({cx:g} , {cy + 24:g})]")

    def get_property(self, name):
        assert name == "PortNames"
        return self._portnames

    def get_port_count(self):
        return self._ports


class _FakeWire:
    def __init__(self, wire_id):
        self.id = wire_id


class _FakePage:
    def __init__(self, blocks, wires):
        self._blocks = blocks
        self._wires = wires

    def get_blocks(self):
        return list(self._blocks)

    def get_wires(self):
        return list(self._wires)


class _FakeProject:
    id = 7

    def __init__(self, page):
        self._page = page

    def get_main_page(self):
        return self._page


class _BridgeWritesReport:
    """Мост-подделка: пишет отчёт по пути из тела и отдаёт заданный исход."""

    payload = ""
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    body = ""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        start = body.index('"') + 1
        end = body.index('"', start)
        Path(body[start:end]).write_text(type(self).payload, encoding="utf-8")
        return PageRunResult(outcome=type(self).outcome, restored_script="")


class _BridgeFails(_BridgeWritesReport):
    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        raise ScriptBridgeError("модельное время не сдвинулось")


def _install(monkeypatch, tmp_path, blocks, wires=(),
             bridge=_BridgeWritesReport, payload=""):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(
        session, "_project", _FakeProject(_FakePage(blocks, wires)))
    monkeypatch.setattr(cm, "ScriptBridge", bridge)
    monkeypatch.setattr(bridge, "payload", payload, raising=False)


@pytest.mark.anyio
async def test_check_reports_clean_model(monkeypatch, tmp_path):
    """Чистая модель: без наложений, подписи в рамках, порты не пусты."""
    blocks = [
        _CheckBlock("k_0", center=(0, 0)),
        _CheckBlock("kx_0", center=(200, 0)),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "Наложения габаритов: нет" in text
    assert "Пустые порты: нет" in text
    assert "Связи: линий на странице нет" in text
    assert "Разметка 8 px: центры на сетке" in text


@pytest.mark.anyio
async def test_check_reports_off_grid_centers(monkeypatch, tmp_path):
    """Центр блока вне разметки 8 px — предупреждение с именем.

    Стандарт 02.10.2026: 1 квадратик = 8×8; координаты центра (3, 5) на неё
    не ложатся.
    """
    blocks = [_CheckBlock("k_0", center=(3, 5))]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "ВНИМАНИЕ: центры вне разметки 8 px: k_0" in text


@pytest.mark.anyio
async def test_check_ignores_label_objects(monkeypatch, tmp_path):
    """Подписи (`constLabel`) — не блоки: в габаритах и разметке не участвуют.

    Их `Points` — якорь текста, `size` — типовая карточка: без исключения
    они дают ложные «наложения» и «вне сетки» (живой случай 02.10.2026).
    """
    blocks = [
        _CheckBlock("k_0", center=(0, 0)),
        _CheckBlock("TextLabel3", center=(3, 5), class_name="constLabel"),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "Разметка 8 px: центры на сетке" in text
    assert "TextLabel3" not in text, "подпись попала в проверку габаритов"


@pytest.mark.anyio
async def test_check_reports_overlaps(monkeypatch, tmp_path):
    """Наложение габаритов названо парой — метрика из #24, п.4.

    Габарит — центр ± размер: центры (0,0) и (8,8) при 32×16 пересекаются.
    """
    blocks = [
        _CheckBlock("k_0", center=(0, 0)),
        _CheckBlock("kx_0", center=(8, 8)),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "ВНИМАНИЕ: наложения габаритов: k_0—kx_0" in text


@pytest.mark.anyio
async def test_check_uses_center_based_bounds(monkeypatch, tmp_path):
    """Габарит — центр(Points) ± size, а не min/max полилинии.

    Живой замер 02.10.2026: у «Константы» 32×16 полилиния даёт размах 16×32 —
    min/max габаритом не является. Здесь блоки стоят так, что min/max-габарит
    дал бы ложное наложение, а центр-габарит — нет.
    """
    blocks = [
        _CheckBlock("k_0", center=(0, 0)),
        _CheckBlock("k_1", center=(40, 0)),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "Наложения габаритов: нет" in text


@pytest.mark.anyio
async def test_check_reports_empty_ports_from_contour(monkeypatch, tmp_path):
    """Пустой порт приходит из отчёта контура и попадает в вердикт."""
    blocks = [_CheckBlock("k_0", center=(0, 0))]
    _install(monkeypatch, tmp_path, blocks, payload="EMPTY|k_0|1\n")

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "ВНИМАНИЕ: пустые порты: k_0[1]" in text


@pytest.mark.anyio
async def test_check_classifies_wires(monkeypatch, tmp_path):
    """Связь по концам: совпал один ряд — прямая, разошлись оба — с изломом."""
    blocks = [_CheckBlock("k_0", center=(0, 0))]
    wires = [_FakeWire(1), _FakeWire(2)]
    payload = ("W|1|(0+0i)|(100+0i)\n"
               "W|2|(0-56i)|(100+8i)\n")
    _install(monkeypatch, tmp_path, blocks, wires=wires, payload=payload)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "прямых 1" in text
    assert "с изломом: 2" in text


@pytest.mark.anyio
async def test_check_reports_wide_label(monkeypatch, tmp_path):
    """Длинная подпись порт-блока шире рамки — предупреждение с числами."""
    blocks = [
        _CheckBlock("t_0", center=(0, 0),
                    portnames="CoolTT_C_CoolSt_WorkSt\n"),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "ВНИМАНИЕ: подписи шире рамки" in text
    assert "t_0" in text and "CoolTT_C_CoolSt_WorkSt" in text


@pytest.mark.anyio
async def test_check_script_queries_ports_and_wire_ends(monkeypatch, tmp_path):
    """Тело контура: пустота портов — `getportwireid`, концы — `getwire*coord`."""
    blocks = [_CheckBlock("k_0", center=(0, 0), ports=2)]
    wires = [_FakeWire(3)]
    _install(monkeypatch, tmp_path, blocks, wires=wires)

    _text(await mcp.call_tool("check_model_layout", {}))

    body = _BridgeWritesReport.body
    assert "getportwireid(getblockportid(k_0, 0)) = 0" in body
    assert "getportwireid(getblockportid(k_0, 1)) = 0" in body
    assert "getwirestartpointcoord(3)" in body
    assert "getwireendpointcoord(3)" in body


@pytest.mark.anyio
async def test_check_names_not_compiled_contour(monkeypatch, tmp_path):
    """Не собравшийся контур: причина названа, а не «всё хорошо»."""
    class _Broken(_BridgeWritesReport):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED, lines=[])

        def run_page_script(self, body, result_path):
            type(self).body = body
            return PageRunResult(outcome=type(self).outcome,
                                 restored_script="")

    blocks = [_CheckBlock("k_0", center=(0, 0))]
    _install(monkeypatch, tmp_path, blocks, bridge=_Broken)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "скрипт не собрался" in text
    assert "не проверены" in text


@pytest.mark.anyio
async def test_check_reads_report_on_model_not_running(monkeypatch, tmp_path):
    """«Модель не считает» — не отказ: отчёт контура всё равно читается."""
    class _Stuck(_BridgeWritesReport):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    blocks = [_CheckBlock("k_0", center=(0, 0))]
    _install(monkeypatch, tmp_path, blocks, bridge=_Stuck,
             payload="EMPTY|k_0|0\n")

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "модель не считает" in text
    assert "пустые порты: k_0[0]" in text


@pytest.mark.anyio
async def test_check_refuses_when_bridge_fails(monkeypatch, tmp_path):
    """Отказ моста — отказ инструмента: состояние проекта неопределённо."""
    blocks = [_CheckBlock("k_0", center=(0, 0))]
    _install(monkeypatch, tmp_path, blocks, bridge=_BridgeFails)

    message = await _error("check_model_layout", {})

    assert "контур проверки не отработал" in message


@pytest.mark.anyio
async def test_check_names_unreadable_geometry(monkeypatch, tmp_path):
    """Габарит не прочитался — блок назван, а не молча пропущен."""
    blocks = [
        _CheckBlock("k_0", center=None),
        _CheckBlock("kx_0", center=(200, 0)),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "Геометрию прочитать не удалось у: k_0" in text


@pytest.mark.anyio
async def test_check_skips_unsafe_block_name(monkeypatch, tmp_path):
    """Имя вне ASCII-идентификатора в скрипт не вставляется.

    Имя идёт в тело скрипта без кавычек (язык адресует блоки
    идентификаторами): имя вида `k); чужой вызов; (` — это инъекция в
    исполняемый текст. Такой блок пропускается с названной причиной, а в
    теле не должно остаться ни одной его части.
    """
    blocks = [
        _CheckBlock('k_0); bad(); (', center=(0, 0)),
        _CheckBlock("k_1", center=(100, 0)),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    body = _BridgeWritesReport.body
    assert "bad()" not in body, "подозрительное имя попало в тело скрипта"
    assert "getblockportid(k_1, 0)" in body, "безопасный блок пропал"
    assert "Порты пропущены" in text
