"""Проверка оформления модели: наложения, подписи, порты и концы линий.

Мост подделывается целиком: настоящий требует Windows и живого `mmain.exe`.
Подделка моста моделирует **переход**: тело контура открывает файл отчёта
само (дескриптор моста телу недоступен по имени — он назван случайной частью
метки), поэтому подделка читает путь из тела и пишет отчёт туда же, а не
«куда-нибудь».
"""

from __future__ import annotations

import os
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

    def get_current_page(self):
        return self._page


class _BridgeWritesReport:
    """Мост-подделка: пишет отчёт по пути из тела и отдаёт заданный исход."""

    payload = ""
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    body = ""
    #: Пути отчёта и маркера, которые видел мост: по ним проверяется
    #: уникальность имён на вызов (тот же приём, что в #40).
    seen_reports: list = []
    seen_markers: list = []

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        start = body.index('"') + 1
        end = body.index('"', start)
        report = body[start:end]
        type(self).seen_reports.append(report)
        type(self).seen_markers.append(str(result_path))
        Path(report).write_text(type(self).payload, encoding="utf-8")
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
    # `DONE` в отчёте — признак, что тело дошло до конца: «пустых портов нет»
    # проверка говорит только по полному отчёту.
    _install(monkeypatch, tmp_path, blocks, payload="DONE\n")

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


@pytest.mark.anyio
async def test_check_reads_the_current_page(monkeypatch, tmp_path):
    """Чтения идут по текущей странице — той же, куда контур ставит скрипт.

    Мост ставит скрипт в `GetCurentPage`; если COM-чтения идут по главной, на
    субмодели половины одного вердикта описывают разные страницы (находка
    ревью 02.10.2026).
    """

    class _TwoPages:
        id = 7

        def __init__(self):
            self.main = _FakePage([_CheckBlock("main_0", center=(0, 0))], [])
            self.current = _FakePage([_CheckBlock("cur_0", center=(3, 5))], [])

        def get_main_page(self):
            return self.main

        def get_current_page(self):
            return self.current

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _TwoPages())
    monkeypatch.setattr(cm, "ScriptBridge", _BridgeWritesReport)
    monkeypatch.setattr(_BridgeWritesReport, "payload", "DONE\n",
                        raising=False)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "cur_0" in text, "проверена не текущая страница"
    assert "main_0" not in text, "в вердикт попала главная страница"


@pytest.mark.anyio
async def test_check_skips_name_with_trailing_newline(monkeypatch, tmp_path):
    """Имя с хвостовым переводом строки в скрипт не попадает (находка ревью).

    `$` в Python совпадает и перед хвостовым переводом строки, поэтому имя
    `k_0` плюс перевод строки проходило охрану `_SAFE_NAME_RE` и легло бы в
    текст скрипта без кавычек.
    """
    blocks = [
        _CheckBlock("k_0\n", center=(0, 0)),
        _CheckBlock("kx_0", center=(100, 0)),
    ]
    _install(monkeypatch, tmp_path, blocks)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    body = _BridgeWritesReport.body
    assert "getblockportid(k_0" not in body, \
        "имя с переводом строки попало в тело скрипта"
    assert "getblockportid(kx_0, 0)" in body, "безопасный блок пропал"
    assert "Порты пропущены" in text


@pytest.mark.anyio
async def test_check_does_not_certify_incomplete_report(monkeypatch, tmp_path):
    """Оборванный отчёт не читается как чистый вердикт (находка ревью).

    Прежде при пустом списке изломов ответ печатал «все прямые», даже когда
    концы получены не у всех линий, — рядом с «Концы не разобраны…».
    """
    blocks = [_CheckBlock("k_0", center=(0, 0))]
    wires = [_FakeWire(1), _FakeWire(2)]
    # Тело успело записать только первую линию и оборвалось (нет DONE).
    payload = "W|1|(0+0i)|(100+0i)\n"
    _install(monkeypatch, tmp_path, blocks, wires=wires, payload=payload)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "все прямые" not in text, "оборванный отчёт выдан за чистый"
    assert "концы получены у 1 из 2" in text


@pytest.mark.anyio
async def test_check_counts_all_unparsed_ends(monkeypatch, tmp_path):
    """Счётчик неразобранных концов полный, список — с «и ещё» (находка ревью).

    Прежде `len(unparsed)` печатался по обрезанному списку: 11 неразобранных
    концов читались как 10.
    """
    blocks = [_CheckBlock("k_0", center=(0, 0))]
    wires = [_FakeWire(index) for index in range(1, 12)]
    payload = "".join(f"W|{index}|мусор|ещё мусор\n"
                      for index in range(1, 12)) + "DONE\n"
    _install(monkeypatch, tmp_path, blocks, wires=wires, payload=payload)

    text = _text(await mcp.call_tool("check_model_layout", {}))

    assert "Концы не разобраны у 11 линий" in text
    assert "(и ещё 1)" in text


@pytest.mark.anyio
async def test_check_uses_fresh_contour_files(monkeypatch, tmp_path):
    """Имена контурных файлов проверки уникальны на вызов (хвост #40).

    Запертый прошлым обрывом файл (WinError 32) новому вызову не мешает: ни
    отчёт, ни маркер не переиспользуют общее имя — тот же приём, что у
    `page_script`/`model_text` в #40. Файлы собственного прошлого вызова при
    этом убираются, чтобы песочница не копила по два на вызов (ревью #42).
    """
    blocks = [_CheckBlock("k_0", center=(0, 0))]
    _BridgeWritesReport.seen_reports = []
    _BridgeWritesReport.seen_markers = []
    _install(monkeypatch, tmp_path, blocks, payload="DONE\n")

    await mcp.call_tool("check_model_layout", {})
    await mcp.call_tool("check_model_layout", {})

    first, second = _BridgeWritesReport.seen_reports
    assert first != second, "имя отчёта переиспользовано между вызовами"
    assert Path(first).name != cm.REPORT_FILE
    # `sandbox.output_root()` — это realpath каталога результатов: сравнение с
    # `tmp_path` напрямую падало бы под симлинкованным корнем (macOS /tmp →
    # /private/tmp) — находка ревью #42.
    assert Path(first).parent == Path(os.path.realpath(str(tmp_path)))
    marker = _BridgeWritesReport.seen_markers[0]
    assert Path(marker).name != cm.MARKER_FILE, "маркер под общим именем"
    assert Path(marker).name != Path(first).name
    # Файлы прошлого вызова убираются: уникальные имена не должны копить
    # песочницу (находка ревью #42).
    assert not Path(first).exists(), "файл отчёта прошлого вызова остался"


def test_outside_sheet_flags_negative_edges():
    """Срез листом: отрицательный левый или верхний край габарита — дефект.

    Живой случай 04.10.2026: блоки стояли с центром x=0, и левая половина
    уходила за край листа — на снимке они выглядели обрезанными.
    """
    from simintech_mcp.tools.check_model import _outside_sheet

    assert _outside_sheet((-16.0, 0.0, 16.0, 16.0)) is True
    assert _outside_sheet((0.0, -8.0, 32.0, 8.0)) is True
    assert _outside_sheet((48.0, 48.0, 560.0, 112.0)) is False
    assert _outside_sheet((0.0, 0.0, 32.0, 16.0)) is False


# ── Аудит маршрутов (ТЗ п.1, инструмент audit_routing) ─────────────


def test_audit_routing_flags_crossing():
    """Внутреннее пересечение попадает в crossings."""
    problems = cm.audit_routing_segments(
        [],
        {1: ((0.0, 0.0), (200.0, 0.0)),
         2: ((50.0, -100.0), (150.0, 100.0))})
    assert (1, 2) in problems.crossings
    assert problems.coincident == []


def test_audit_routing_flags_shared_track():
    """Общий трек длиннее WIRE_PITCH — coincident, а не crossings."""
    problems = cm.audit_routing_segments(
        [],
        {1: ((0.0, 0.0), (200.0, 0.0)),
         2: ((0.0, 0.0), (100.0, 0.0))})
    assert (1, 2) in problems.coincident
    assert problems.crossings == []


def test_audit_routing_flags_wire_through_block():
    """Линия сквозь чужой габарит названа вместе с блоком."""
    problems = cm.audit_routing_segments(
        [("U1", (50.0, -50.0, 150.0, 50.0))],
        {1: ((0.0, 0.0), (200.0, 0.0))})
    assert problems.block_hits == [(1, "U1")]


def test_audit_routing_flags_inverted_port_order():
    """Инверсия источников у входов блока — крест у стены."""
    problems = cm.audit_routing_segments(
        [("U1", (100.0, 0.0, 200.0, 100.0))],
        {1: ((0.0, 0.0), (100.0, 80.0)),
         2: ((40.0, 100.0), (100.0, 20.0))})
    assert problems.port_order == ["U1"]


def test_audit_routing_marks_feedback_unchecked():
    """Обратная связь (приёмник левее источника) — не проверена, не «чисто»."""
    problems = cm.audit_routing_segments(
        [], {1: ((200.0, 0.0), (0.0, 0.0))})
    assert problems.unchecked == [1]
    assert problems.crossings == []


def test_audit_routing_clean_is_empty():
    """Прямая линия без соседей и габаритов — чистый вердикт."""
    problems = cm.audit_routing_segments([], {1: ((0.0, 0.0), (200.0, 0.0))})
    assert not (problems.crossings or problems.coincident
                or problems.block_hits or problems.port_order
                or problems.unchecked)


def test_audit_wire_report_requires_done():
    """Отчёт без `DONE` — оборванный: парсер отдаёт None, а не часть линий.

    `DONE` пишет `_check_script` последней строкой именно затем, чтобы полный
    отчёт отличался от оборванного (находка ревью 02.10.2026). Без этой
    проверки вердикт `readable` мог бы выйти по половине линий: оборванное
    тело не отличить от «линий меньше, чем есть».
    """
    assert cm._parse_wire_report("W|1|(0+0i)|(100+0i)\nDONE\n") == {
        1: ((0.0, 0.0), (100.0, 0.0))}
    assert cm._parse_wire_report("W|1|(0+0i)|(100+0i)\n") is None


def test_audit_routing_deduplicates_shared_pairs():
    """Пара, совпавшая двумя отрезками, стоит в ответе один раз."""
    problems = cm.audit_routing_segments(
        [],
        {1: ((0.0, 0.0), (100.0, 80.0)),
         2: ((0.0, 0.0), (80.0, 0.0))})
    assert problems.coincident == [(1, 2)]


def test_channel_overflow_flags_tight_gap():
    """Зазор между колонками у́же канала из ТЗ 4.2 — переполнение."""
    rects = [("U1", (80.0, 0.0, 120.0, 100.0)),
             ("U2", (140.0, 0.0, 180.0, 100.0))]

    found = cm.channel_overflow(rects, {1: ((120.0, 0.0), (140.0, 50.0))})

    assert found == [(0, 1, 1, 20.0, 24.0)]


def test_channel_overflow_silent_when_channel_fits():
    """Широкий зазор вмещает канал — переполнения нет."""
    rects = [("U1", (80.0, 0.0, 120.0, 100.0)),
             ("U2", (280.0, 0.0, 320.0, 100.0))]

    assert cm.channel_overflow(
        rects, {1: ((120.0, 0.0), (280.0, 50.0))}) == []


def test_channel_overflow_ignores_back_edge():
    """Обратная связь в разрез не входит (ТЗ 4.3), прямая — входит."""
    rects = [("U1", (80.0, 0.0, 120.0, 100.0)),
             ("U2", (146.0, 0.0, 186.0, 100.0))]

    assert cm.channel_overflow(
        rects, {1: ((146.0, 50.0), (120.0, 0.0))}) == []
    forward = {1: ((120.0, 0.0), (146.0, 50.0)),
               2: ((120.0, 20.0), (146.0, 70.0))}
    assert cm.channel_overflow(rects, forward) != []


def test_audit_routing_reports_channel_overflow():
    """Переполнение канала попадает в вердикт аудита."""
    rects = [("U1", (80.0, 0.0, 120.0, 100.0)),
             ("U2", (140.0, 0.0, 180.0, 100.0))]

    problems = cm.audit_routing_segments(
        rects, {1: ((120.0, 0.0), (140.0, 50.0))})

    assert problems.channel_overflow == [(0, 1, 1, 20.0, 24.0)]
