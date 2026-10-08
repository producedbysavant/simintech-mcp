"""Связи: connect, connect_branch, disconnect_wire, remove_block, list_wires.

Разложено из `test_blocks_tools.py` и `test_layout_tools.py` (issue #124):
связевые тесты жили в файлах, которые называют раскладку и блоки; здесь —
файл по модулю `tools/wires.py`. Общие фейки — в `_support.py`.
"""

from __future__ import annotations

import pytest
from simintech_api import ComCallError

from simintech_mcp import session
from simintech_mcp.server import mcp

from _support import (
    _ConnectingBlock,
    _FakeProject,
    _FakeWire,
    _PlacedBlock,
    _error,
    _install_contour,
    _install_fake_project,
    _install_wire_project,
    _text,
    _tool_text,
)


# ─── connect: запоминание линии и адресация ─────────────────────────────────


@pytest.mark.anyio
async def test_connect_only_remembers_wire(monkeypatch):
    """connect запоминает линию, но НЕ трассирует её.

    Трассировать в этот момент нельзя: блоки стоят в (0,0) друг на друге, и
    NormalizeWire оставляет в геометрии точки вроде (-160,-1056), которые
    потом не пересчитываются. Проверено на SimInTech64 2026-09-15.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "Integrator_0": dst})

    text = _text(await mcp.call_tool("connect",
                                     {"src": "k_0", "dst": "Integrator_0"}))

    assert "Соединено" in text
    wire = src.wires[0][0]
    assert wire.normalized == 0, "линия трассирована до расстановки блоков"
    assert session.WIRES == [(wire, "k_0", 0, "Integrator_0", 0)], \
        "линия не запомнена вместе с концами для выравнивания"


@pytest.mark.anyio
async def test_connect_refuses_zero_wire_id(monkeypatch):
    """Нулевой id линии — отказ, а не «Соединено … (wire=0)».

    Ноль у соседнего вызова (`create_block` бросает `BlockError` на нулевом
    id) значит «ничего не создано». Линия с id 0 прошла бы дальше как успех:
    агент счёл бы вход подключённым, а отказ не всплыл бы нигде — ни в
    `layout_place` (`NormalizeWire` на неверном WireId молча возвращает 0),
    ни в `run`, который отчитается о расчёте модели без этой связи.
    """
    src = _ConnectingBlock("k_0", 1, wire_id=0)
    dst = _ConnectingBlock("Integrator_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "Integrator_0": dst})

    text = await _error("connect", {"src": "k_0", "dst": "Integrator_0"})

    assert "id=0" in text
    assert session.WIRES == [], "отказ по нулевому id, а линия запомнена"


def test_resolve_block_prefers_name_and_tolerates_non_decimal_digits():
    """Имя выигрывает у id; «²» — не id, а честное «не найден» (ревью 05.10.2026).

    Блок с числовым именем («5») обязан остаться адресуемым по имени; а
    `isdigit()`-разбор подавал бы «²» в `int()` — отказ был бы про ValueError
    вместо дружелюбного «блок не найден».
    """
    from _support import _FakePage
    from simintech_mcp.tools.blocks import resolve_block

    named_five = _PlacedBlock("5", 99)
    by_id = _PlacedBlock("k_0", 7)
    page = _FakePage({"5": named_five, "k_0": by_id})

    assert resolve_block(page, "5") is named_five, "имя числового блока"
    assert resolve_block(page, "7") is by_id, "id — запасной путь"
    assert resolve_block(page, " k_0 ") is by_id, "пробелы отбрасываются"
    assert resolve_block(page, "²") is None, "«²» — не десятичное число"
    assert resolve_block(page, "нет_такого") is None


@pytest.mark.anyio
async def test_connect_addressed_by_id_remembers_names(monkeypatch):
    """connect принимает числовой id, а в реестр кладёт имена блоков.

    Сырой токен-идентификатор в `WIRES` молча выпал бы из графа
    `layout_place`, который строит его по именам блоков (находка ревью
    05.10.2026).
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})

    text = _text(await mcp.call_tool("connect",
                                     {"src": "1", "dst": " kx_0 "}))

    assert "Соединено k_0 -> kx_0" in text
    assert session.WIRES == [(src.wires[0][0], "k_0", 0, "kx_0", 0)], \
        "в реестре — имена, а не сырые токены"


@pytest.mark.anyio
async def test_connect_resolves_pair_with_shared_name_by_id(monkeypatch):
    """Половины пары с общим именем различаются по id — обе соединяемы.

    Имя ячейки (`#m1`) у «В память» и «Из памяти» одно: `find_block` вернул
    бы первую половину всегда, и вторая была бы недостижима (живой случай
    05.10.2026, issue #80).
    """
    first = _ConnectingBlock("#m1", 12)
    second = _ConnectingBlock("#m1", 13)
    _install_wire_project(monkeypatch, {"first": first, "second": second})

    text = _text(await mcp.call_tool("connect",
                                     {"src": "12", "dst": "13"}))

    assert "Соединено #m1 -> #m1" in text
    assert first.wires and first.wires[0][1] is second, \
        "соединена не та половина пары"


# ─── connect_branch: ветвление от существующей линии ───────────────


class _BranchBlock:
    """Блок с портами для connect_branch: id, имена и наличие портов."""

    def __init__(self, name, block_id, out_ports=1, in_ports=1):
        self._name = name
        self.id = block_id
        self._out = out_ports
        self._in = in_ports
        self.class_name = "Усилитель"

    def get_name(self):
        return self._name

    def get_out_port(self, index=0):
        from simintech_api import PortError
        if index >= self._out:
            raise PortError(f"блок {self.id}: выходной порт {index} не найден")
        return object()

    def get_in_port(self, index=0):
        from simintech_api import PortError
        if index >= self._in:
            raise PortError(f"блок {self.id}: входной порт {index} не найден")
        return object()


class _BranchBridge:
    """Мост-подделка тела ветвления: отвечает заданными строками.

    Тело идёт контуром (`createwire` и чтения) — подделка фиксирует тело и
    возвращает сценарий: успешный `created=… parent=… node=…` либо `err=…`.
    Так проверяются диагнозы инструмента на каждый `err`, а не язык.
    """

    payload: list = []
    kind = "ok"
    body = ""

    def __init__(self, client, project_id):
        self.project_id = project_id

    def run_page_script(self, body, result_path):
        from simintech_api.core.script_bridge import PageRunResult
        from simintech_api.script_probe import ContourOutcome

        type(self).body = body
        lines = [] if type(self).kind != "ok" else list(type(self).payload)
        return PageRunResult(
            outcome=ContourOutcome(kind=type(self).kind, lines=lines),
            restored_script="")


def _branch_bridge(payload=(), kind="ok"):
    """Мост с конфигурацией на тест (фабрика подкласса — урок #83)."""
    return type("_ConfiguredBranchBridge", (_BranchBridge,), {
        "payload": list(payload), "kind": kind, "body": ""})


def _branch_project():
    """Проект: источник с выходом, приёмник с входом."""
    src = _BranchBlock("k_0", 10)
    dst = _BranchBlock("kx_1", 11)
    return _FakeProject({"k_0": src, "kx_1": dst}), src, dst


@pytest.mark.anyio
async def test_connect_branch_creates_and_reports_node(monkeypatch, tmp_path):
    """Успех: ветвь создана, узел K+1 и родитель названы, реестр пополнен."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=1"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1"}))

        assert "Ветвь создана: k_0[0] → kx_1[0]" in text
        assert "wire=777" in text and "узел 1" in text
        assert "родитель 555" in text
        assert project.get_main_page().activations >= 1, (
            "контурный прогон без активной страницы")
        assert any(w[0].id == 777 for w in session.WIRES), \
            "ветвь не попала в реестр session.WIRES"
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_body_passes_point_index(monkeypatch, tmp_path):
    """Точка и порты доезжают до тела: createwire получает K как есть."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=3"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1",
                               "point_index": 2}))

        assert "createwire(prj, 0, parent, 2, 0, inp, 0)" in bridge.body
        assert "getpointcount(parent)" in bridge.body
        # Гарды портов — как у `_disconnect_wire_body`: нулевой порт иначе
        # даёт мусорную линию или ложный диагноз (находка ревью PR #110).
        assert 'if outport = 0 then writelnutf8(fid, "err=no-out-port");' \
            in bridge.body
        assert 'if inp = 0 then writelnutf8(fid, "err=no-in-port");' \
            in bridge.body
        # Узел 3 при K=2 — ожидание сходится, примечания нет.
        assert "ВНИМАНИЕ" not in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_notes_node_mismatch(monkeypatch, tmp_path):
    """K=0, узел не 1 — точка недостижима: строгое примечание."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=4"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1"}))

        assert "ВНИМАНИЕ" in text
        assert "узел 4 при K=0" in text
        assert "недостижима" in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_mismatch_on_k_gt0_hedges(monkeypatch, tmp_path):
    """K>0 и узел не K+1 — не утверждаем «недостижима»: соответствие не измерено.

    Соответствие «узел = K+1» замерено только на K=0 (находка ревью PR
    #110): на K>0 строгое примечание «точка недостижима» могло бы ругать
    корректную ветвь.
    """
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=1"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1",
                               "point_index": 2}))

        assert "ВНИМАНИЕ" in text
        assert "соответствие для K>0 не измерено" in text
        assert "недостижима" not in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_without_parent_wire(monkeypatch,
                                                          tmp_path):
    """У выхода нет линии — отказ с указанием `connect`."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=no-parent-wire"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1"})

        assert "линия не идёт" in text
        assert "`connect`" in text
        assert not any(w[0].id == 777 for w in session.WIRES)
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_point_out_of_range(monkeypatch,
                                                         tmp_path):
    """K вне точек данных — отказ ДО создания (иначе молчаливый фолбэк)."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=point-range cnt=1"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1",
                             "point_index": 1})

        assert "1 точек данных" in text
        assert "свернула бы ветвь к первой" in text
        assert "`K < 1`" in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_zero_points_advice(monkeypatch, tmp_path):
    """cnt=0: совет — только point_index=0, без невыполнимого «K < 0».

    Находка ревью PR #110: при нуле точек формула «K < cnt» давала совет,
    противоречащий собственному запрету отрицательного K.
    """
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=point-range cnt=0"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1",
                             "point_index": 1})

        assert "0 точек данных" in text
        assert "`point_index=0` (проверенный случай)" in text
        assert "K < 0" not in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_when_port_blind_in_contour(monkeypatch,
                                                                 tmp_path):
    """Порт виден COM, но не контуру — отказ, а не мусорная линия.

    Нулевой приёмник `createwire` превращает в реальную линию (находка
    ревью PR #110) — гард в теле обязан ловить это до создания.
    """
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=no-in-port"])
    _install_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1"})

        assert "не нашла входной порт" in text
        assert "пересоздаться" in text
        assert not any(w[0].id == 777 for w in session.WIRES)
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_on_not_compiled(monkeypatch, tmp_path):
    """Несобравшееся тело — отказ, а не «создано»."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(kind="not-compiled")
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("connect_branch", {"src": "k_0", "dst": "kx_1"})

    assert "не собралось" in text


@pytest.mark.anyio
async def test_connect_branch_rejects_bad_port_before_contour(monkeypatch,
                                                              tmp_path):
    """Несуществующий порт — отказ через COM ДО контура (диагноз, не фолбэк)."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=1 parent=1 node=1"])
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("connect_branch",
                        {"src": "k_0", "dst": "kx_1", "in_index": 5})

    assert "нет входного порта 5" in text
    assert bridge.body == "", "контур звали, хотя порт не существует"


# ─── disconnect_wire: снятие линии контуром ─────────────────────────────────


class _FakeClient:
    """COM-клиент: контуру достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _BridgeReplies:
    """Мост-подделка контура: исход задан, снятие линии — эффектом `on_run`.

    Эффект выполняется **во время прогона** — между чтениями числа линий «до»
    и «после», как у настоящего моста. Без этого счётчик не менялся бы, и
    проверка «минус одна» кодировала бы договорённость теста, а не поведение
    инструмента.
    """

    kind = "ok"
    lines: list = []
    on_run = None
    body = ""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body, result_path):
        from simintech_api.core.script_bridge import PageRunResult
        from simintech_api.script_probe import ContourOutcome

        type(self).body = body
        if type(self).on_run is not None:
            type(self).on_run()
        return PageRunResult(
            outcome=ContourOutcome(kind=type(self).kind,
                                   lines=list(type(self).lines)),
            restored_script="// прежний")


def _install_disconnect(monkeypatch, tmp_path, bridge, blocks):
    """Подменить проект, клиента и мост разом.

    Клиент обязателен: без него `ensure_client` ушёл бы в настоящий COM,
    которого на Linux нет.
    """
    from simintech_mcp.tools import page_script

    _install_wire_project(monkeypatch, blocks)
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)


def test_disconnect_wire_body_checks_the_source_before_removing():
    """Тело сверяет начало линии с выходом src ДО удаления.

    Концы по COM не читаются, поэтому «линия идёт от src» проверяет среда:
    `findstartport` — выходной порт начала линии во входе. Без этой проверки
    снялась бы чужая связь — то, от чего инструмент и защищает.
    """
    from simintech_mcp.tools.wires import _disconnect_wire_body

    body = _disconnect_wire_body(11, 0, 22, 1)

    assert "getinportid(22, 1)" in body, "вход приёмника адресуется не тем портом"
    assert "getoutportid(11, 0)" in body, "выход источника адресуется не тем портом"
    assert "findstartport(p_in)" in body
    assert "if startPort = p_out then begin" in body, (
        "сравнение начала линии с ожидаемым выходом пропало — "
        "удаление сняло бы любую линию во входе")
    assert body.index("findstartport") < body.index("removeprimitiv"), (
        "удаление стоит до проверки источника")
    for short in (" fs ", " w ", " p "):
        assert short not in body, (
            f"имя {short!r} из резерва кодогенератора (i/j/c) и однобуквенных"
            " — стандарт ЭВС360 требует имена от трёх знаков")


def test_parse_drop_reply_reads_body_lines():
    """Разбор ответа тела: снятие, несколько причин отказа, мусор — как unknown."""
    from simintech_mcp.tools.wires import _parse_drop_reply

    assert _parse_drop_reply(["removed=5 pw=0"]).wire_id == 5
    assert _parse_drop_reply(["removed=5 pw=8"]).port_left == 8
    assert _parse_drop_reply(["err=other-src blk=9"]).block_id == 9
    assert _parse_drop_reply(["err=not-connected"]).kind == "not-connected"
    assert _parse_drop_reply(["err=no-in-port"]).kind == "no-in-port"
    assert _parse_drop_reply(["err=no-out-port"]).kind == "no-out-port"
    assert _parse_drop_reply(["мусор вместо ответа"]).kind == "unknown"
    assert _parse_drop_reply([]).kind == "unknown"


@pytest.mark.anyio
async def test_disconnect_wire_removes_line_and_forgets_it(monkeypatch, tmp_path):
    """Снятие: линия уходит со страницы, реестр сессии её забывает."""
    src = _ConnectingBlock("k_0", 1)
    other = _ConnectingBlock("kx_0", 2)
    dst = _ConnectingBlock("Integrator_0", 3)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))
    spare = _FakeWire(78)
    other.wires.append((spare, dst, 0, 1))
    saved = list(session.WIRES)

    class _Drops(_BridgeReplies):
        lines = ["removed=77 pw=0"]
        on_run = staticmethod(lambda: src.wires.clear())

    try:
        _install_disconnect(monkeypatch, tmp_path, _Drops,
                            {"k_0": src, "kx_0": other, "Integrator_0": dst})
        # Записи кладутся после установки: `_install_wire_project` чистит
        # реестр (он общий для сессии).
        session.WIRES.append((wire, "k_0", 0, "Integrator_0", 0))
        session.WIRES.append((spare, "kx_0", 0, "Integrator_0", 1))

        text = _text(await mcp.call_tool(
            "disconnect_wire", {"src": "k_0", "dst": "Integrator_0"}))

        assert "снята (wire=77)" in text
        assert "Линий связи на странице: 2 → 1" in text
        assert [record[0] for record in session.WIRES] == [spare], (
            "запись о снятой линии осталась в реестре (или пропала чужая)")
        assert _Drops.body.count("findstartport") == 1
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_disconnect_wire_addressed_by_id(monkeypatch, tmp_path):
    """disconnect принимает id — как и connect: так адресуют половину пары.

    Имя у «В память»/«Из памяти» общее, и снятие по имени уходило бы либо в
    «блок не найден» (для id), либо в чужую половину (для имени) — связь
    становилась неснимаемой (находка ревью 05.10.2026).
    """
    src = _ConnectingBlock("#m1", 12)
    dst = _ConnectingBlock("k_0", 3)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))
    saved = list(session.WIRES)

    class _Drops(_BridgeReplies):
        lines = ["removed=77 pw=0"]
        on_run = staticmethod(lambda: src.wires.clear())

    try:
        _install_disconnect(monkeypatch, tmp_path, _Drops,
                            {"first": src, "k_0": dst})

        text = _text(await mcp.call_tool(
            "disconnect_wire", {"src": "12", "dst": "3"}))

        assert "снята (wire=77)" in text
    finally:
        session.WIRES[:] = saved


def test_remove_block_body_when_wires_not_touched():
    """Тело при with_wires=False линии только называет — не снимает."""
    from simintech_mcp.tools.wires import _remove_block_body

    body = _remove_block_body(10, False)

    assert "blk = 10;" in body
    assert "nports = getblockportcount(blk);" in body
    assert "getblockportid(blk, portIdx)" in body, "порты — общим индексом"
    assert "getinportid" not in body and "getoutportid" not in body, (
        "перебор по направлениям пропускал бы ненаправленные порты")
    assert "while portIdx < nports do begin" in body
    assert "for i :=" not in body, "`for` в контуре не компилируется"
    assert 'seen = "|";' in body, "нет набора линий: повтор не отсекается"
    assert 'pos("|" + inttostr(wireId) + "|", seen) = 0' in body
    assert 'writelnutf8(fid, "busy=" + inttostr(busy))' in body
    assert "removeprimitiv(blk)" in body
    assert "removeprimitiv(wireId)" not in body, "линии не должны сниматься"
    for short in (" i ", " p ", " w "):
        assert short not in body, (
            f"имя {short!r} из резерва кодогенератора (i/j/c) и однобуквенных"
            " — стандарт ЭВС360 требует имена от трёх знаков")


def test_remove_block_body_with_wires_cuts_lines():
    """Тело при with_wires=True снимает линии и блок, без ветки busy."""
    from simintech_mcp.tools.wires import _remove_block_body

    body = _remove_block_body(10, True)

    assert "removeprimitiv(wireId)" in body
    assert "busy" not in body
    assert "removeprimitiv(blk)" in body
    assert '"cut=" + inttostr(wireId)' in body


@pytest.mark.anyio
async def test_remove_block_removes_block(monkeypatch, tmp_path):
    """Удаление: тело сообщило removed=, блок исчез со страницы — успех."""
    block = _ConnectingBlock("k_0", 10)
    blocks = {"k_0": block}

    class _Removes(_BridgeReplies):
        lines = ["removed=10"]
        on_run = staticmethod(lambda: blocks.pop("k_0", None))

    _install_disconnect(monkeypatch, tmp_path, _Removes, blocks)

    text = _text(await mcp.call_tool("remove_block", {"block": "k_0"}))

    assert "удалён" in text
    assert "id=10" in text


@pytest.mark.anyio
async def test_remove_block_missing_block(monkeypatch, tmp_path):
    """Неизвестный блок — «не найден», до всякого прогона."""
    _install_disconnect(monkeypatch, tmp_path, _BridgeReplies,
                        {"k_0": _ConnectingBlock("k_0", 1)})

    message = await _error("remove_block", {"block": "нет_такого"})

    assert "не найден" in message


@pytest.mark.anyio
async def test_remove_block_refuses_when_wires_connected(monkeypatch, tmp_path):
    """Линии подключены — отказ с перечислением; блок и линии не тронуты."""
    block = _ConnectingBlock("k_0", 10)
    saved = list(session.WIRES)

    class _Busy(_BridgeReplies):
        lines = ["wire=77", "busy=1"]

    try:
        _install_disconnect(monkeypatch, tmp_path, _Busy, {"k_0": block})
        message = await _error("remove_block", {"block": "k_0"})

        assert "не удалён" in message and "77" in message
        assert "with_wires=True" in message
        assert "disconnect_wire" in message
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_remove_block_with_wires_forgets_lines(monkeypatch, tmp_path):
    """with_wires: линии сняты вместе с блоком, реестр сессии их забыл."""
    src = _ConnectingBlock("k_0", 10)
    dst = _ConnectingBlock("kx_0", 2)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))
    blocks = {"k_0": src, "kx_0": dst}
    saved = list(session.WIRES)

    def _effect():
        src.wires.clear()
        blocks.pop("k_0", None)

    class _Cuts(_BridgeReplies):
        lines = ["cut=77", "removed=10"]
        on_run = staticmethod(_effect)

    try:
        _install_disconnect(monkeypatch, tmp_path, _Cuts, blocks)
        session.WIRES.append((wire, "k_0", 0, "kx_0", 0))

        text = _text(await mcp.call_tool(
            "remove_block", {"block": "k_0", "with_wires": True}))

        assert "удалён вместе с линиями (1)" in text
        assert "Линий связи на странице: 1 → 0" in text
        assert session.WIRES == [], "снятая линия осталась в реестре"
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_remove_block_refuses_when_block_still_found(monkeypatch, tmp_path):
    """Тело сказало «removed=», а блок находится — отказ: среда не подтвердила."""
    class _Lies(_BridgeReplies):
        lines = ["removed=10"]

    _install_disconnect(monkeypatch, tmp_path, _Lies,
                        {"k_0": _ConnectingBlock("k_0", 10)})

    message = await _error("remove_block", {"block": "k_0"})

    assert "не подтверждено" in message
    assert "по-прежнему" in message


@pytest.mark.anyio
async def test_remove_block_confirms_by_id_for_shared_name(monkeypatch, tmp_path):
    """Пара с общим именем: подтверждение по id не спутает вторую половину.

    У «В память»/«Из памяти» имя одно (`#m1`); перерезолв токеном нашёл бы
    оставшуюся половину и отчитался «не подтверждено» после настоящего
    удаления (находка ревью 06.10.2026).
    """
    first = _ConnectingBlock("#m1", 10)
    second = _ConnectingBlock("#m1", 11)
    blocks = {"first": first, "second": second}

    class _Removes(_BridgeReplies):
        lines = ["removed=10"]
        on_run = staticmethod(lambda: blocks.pop("first", None))

    _install_disconnect(monkeypatch, tmp_path, _Removes, blocks)
    page = session.current_project().get_main_page()
    monkeypatch.setattr(
        page, "find_block",
        lambda name: next((b for b in page.get_blocks()
                           if b.get_name() == name), None))

    text = _text(await mcp.call_tool("remove_block", {"block": "#m1"}))

    assert "удалён" in text and "id=10" in text
    assert second is not None, "вторая половина пары не должна удаляться"


@pytest.mark.anyio
async def test_remove_block_warns_when_port_unread_but_removed(
        monkeypatch, tmp_path):
    """Порт не прочитался, а блок удалён: успех с предупреждением, не отказ.

    Отказ здесь врал бы «повторите» (блока уже нет), а «ничего не изменено» —
    о линиях, снятых до места отказа (находка ревью 06.10.2026).
    """
    blocks = {"k_0": _ConnectingBlock("k_0", 10)}

    class _Removes(_BridgeReplies):
        lines = ["err=no-port", "removed=10"]
        on_run = staticmethod(lambda: blocks.pop("k_0", None))

    _install_disconnect(monkeypatch, tmp_path, _Removes, blocks)

    text = _text(await mcp.call_tool("remove_block", {"block": "k_0"}))

    assert "удалён" in text
    assert "не читался" in text


@pytest.mark.anyio
async def test_remove_block_refuses_when_section_not_run(monkeypatch, tmp_path):
    """Секция не выполнилась — честный отказ, а не «тело отработало»."""
    class _NotRun(_BridgeReplies):
        kind = "section-not-run"
        lines = []

    _install_disconnect(monkeypatch, tmp_path, _NotRun,
                        {"k_0": _ConnectingBlock("k_0", 10)})

    message = await _error("remove_block", {"block": "k_0"})

    assert "не выполнилась" in message


@pytest.mark.anyio
async def test_remove_block_refuses_when_body_aborts(monkeypatch, tmp_path):
    """Обрыв тела — «не подтверждено»: успело ли удаление, не определить."""
    class _Aborted(_BridgeReplies):
        kind = "aborted"
        lines = ["busy=0"]

    _install_disconnect(monkeypatch, tmp_path, _Aborted,
                        {"k_0": _ConnectingBlock("k_0", 10)})

    message = await _error("remove_block", {"block": "k_0"})

    assert "не подтверждено" in message
    assert "оборвалось" in message
    assert "list_blocks" in message


@pytest.mark.anyio
async def test_disconnect_wire_refuses_when_input_has_no_line(monkeypatch, tmp_path):
    """Вход без линии — отказ: снимать нечего, реестр не тронут."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)

    class _Empty(_BridgeReplies):
        lines = ["err=not-connected"]

    _install_disconnect(monkeypatch, tmp_path, _Empty,
                        {"k_0": src, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не приходит ни одной линии" in message
    assert "не изменён" in message
    assert session.WIRES == []


@pytest.mark.anyio
async def test_disconnect_wire_refuses_foreign_source(monkeypatch, tmp_path):
    """Линия во входе идёт от другого блока — отказ, линия не снята.

    Это главная защита: концы линий по COM не читаются, и без проверки
    «снялось бы то, что оказалось во входе».
    """
    src = _ConnectingBlock("k_0", 1)
    foreign = _ConnectingBlock("kx_0", 2)
    dst = _ConnectingBlock("Integrator_0", 3)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))

    class _Foreign(_BridgeReplies):
        lines = ["err=other-src blk=2"]

    _install_disconnect(monkeypatch, tmp_path, _Foreign,
                        {"k_0": src, "kx_0": foreign, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "'kx_0' (id=2)" in message, "отказ не назвал фактический источник"
    assert "не изменён" in message
    assert src.wires, "линия снята, хотя источник — не тот, что назван"


@pytest.mark.anyio
async def test_disconnect_wire_reports_leftover_line(monkeypatch, tmp_path):
    """После снятия во входе осталась ещё линия — сказано в ответе."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))

    class _Leftover(_BridgeReplies):
        lines = ["removed=77 pw=88"]
        on_run = staticmethod(lambda: src.wires.clear())

    _install_disconnect(monkeypatch, tmp_path, _Leftover,
                        {"k_0": src, "Integrator_0": dst})

    text = _text(await mcp.call_tool(
        "disconnect_wire", {"src": "k_0", "dst": "Integrator_0"}))

    assert "ещё видна линия (id=88)" in text


@pytest.mark.anyio
async def test_disconnect_wire_forgets_every_vanished_line(monkeypatch, tmp_path):
    """Ушло больше одной линии — предупреждение, и реестр чистится по всем.

    Среда допускает линии с несколькими концами: снятие может унести не одну
    запись. Подделка моделирует переход «обе линии исчезли»; ответ обязан
    предупредить, а реестр — потерять обе записи (запись о сестре иначе
    осталась бы опорой `layout_place` для мёртвой связи).
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    first, second = _FakeWire(77), _FakeWire(78)
    src.wires.append((first, dst, 0, 0))
    dst.wires.append((second, src, 0, 0))
    saved = list(session.WIRES)

    class _Bundle(_BridgeReplies):
        lines = ["removed=77 pw=0"]

        @staticmethod
        def bundle() -> None:
            src.wires.clear()
            dst.wires.clear()

    _Bundle.on_run = _Bundle.bundle

    try:
        _install_disconnect(monkeypatch, tmp_path, _Bundle,
                            {"k_0": src, "Integrator_0": dst})
        session.WIRES.append((first, "k_0", 0, "Integrator_0", 0))
        session.WIRES.append((second, "Integrator_0", 0, "k_0", 0))

        text = _text(await mcp.call_tool(
            "disconnect_wire", {"src": "k_0", "dst": "Integrator_0"}))

        assert "Линий связи на странице: 2 → 0" in text
        assert "Исчезло больше одной линии" in text
        assert session.WIRES == [], "реестр держит записи исчезнувших линий"
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_disconnect_wire_refuses_unknown_block(monkeypatch, tmp_path):
    """Несуществующий блок — отказ до контура (расчёт не запускается)."""
    class _Unused(_BridgeReplies):
        lines = ["removed=77 pw=0"]

    _install_disconnect(monkeypatch, tmp_path, _Unused,
                        {"k_0": _ConnectingBlock("k_0", 1)})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "нет_такого"})

    assert "не найден на странице" in message
    assert _Unused.body == "", "контур запущен, хотя блок не найден"


@pytest.mark.anyio
async def test_disconnect_wire_refuses_missing_port(monkeypatch, tmp_path):
    """Номера портов проверяются ДО контура: порт за диапазоном — отказ."""
    from simintech_api import PortError

    class _NoPort(_ConnectingBlock):
        def get_in_port(self, index=0):
            raise PortError("Блок 2: входной порт 5 не найден")

    class _Unused(_BridgeReplies):
        lines = ["removed=77 pw=0"]

    _install_disconnect(monkeypatch, tmp_path, _Unused,
                        {"k_0": _ConnectingBlock("k_0", 1),
                         "Integrator_0": _NoPort("Integrator_0", 2)})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0",
                            "in_index": 5})

    assert "нет входного порта 5" in message
    assert _Unused.body == "", "контур запущен, хотя порта нет"


@pytest.mark.anyio
async def test_disconnect_wire_refuses_when_body_does_not_compile(
        monkeypatch, tmp_path):
    """Тело не собралось — отказ, и сказано, где искать причину."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)

    class _Broken(_BridgeReplies):
        kind = "not-compiled"

    _install_disconnect(monkeypatch, tmp_path, _Broken,
                        {"k_0": src, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не собралось" in message
    assert "окне сообщений" in message


@pytest.mark.anyio
async def test_disconnect_wire_does_not_claim_success_after_abort(
        monkeypatch, tmp_path):
    """Обрыв тела — «снятие не подтверждено»; ушедшая линия из реестра уходит.

    Тело могло успеть удалить линию до обрыва записи: утверждать «не снята»
    нельзя, но и держать в реестре запись о реально исчезнувшей линии тоже —
    реестр приводится по факту перечисления страницы.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))
    saved = list(session.WIRES)

    class _Aborted(_BridgeReplies):
        kind = "aborted"
        lines = ["removed=77 pw=0"]
        on_run = staticmethod(lambda: src.wires.clear())

    try:
        _install_disconnect(monkeypatch, tmp_path, _Aborted,
                            {"k_0": src, "Integrator_0": dst})
        session.WIRES.append((wire, "k_0", 0, "Integrator_0", 0))

        message = await _error("disconnect_wire",
                               {"src": "k_0", "dst": "Integrator_0"})

        assert "не подтверждено" in message
        assert "оборвалось" in message
        assert "list_wires" in message, "отказ не говорит, чем проверить схему"
        assert session.WIRES == [], \
            "запись реально исчезнувшей линии осталась в реестре"
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_disconnect_wire_refuses_when_environment_still_sees_the_line(
        monkeypatch, tmp_path):
    """Та же линия на входе — «снятие не подтверждено», а не «снята».

    Тело сообщило «removed», но обратное чтение (`pw`) показывает ту же
    линию: удаление не прошло. Строке тела репозиторий не верит на слово —
    успех подтверждается фактом. Счётчик здесь **уменьшается** (уходит чужая
    линия), чтобы отказ ловился именно проверкой `pw`, а не «число не
    уменьшилось».
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    other = _ConnectingBlock("kx_0", 3)
    wire = _FakeWire(77)
    src.wires.append((wire, dst, 0, 0))
    spare = _FakeWire(99)
    other.wires.append((spare, dst, 0, 1))

    class _Stuck(_BridgeReplies):
        lines = ["removed=77 pw=77"]
        on_run = staticmethod(lambda: other.wires.clear())

    _install_disconnect(monkeypatch, tmp_path, _Stuck,
                        {"k_0": src, "kx_0": other, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не подтверждено" in message
    assert "77" in message
    assert src.wires, "линия исчезла, хотя среда её по-прежнему видит"


@pytest.mark.anyio
async def test_disconnect_wire_refuses_when_wire_count_did_not_drop(
        monkeypatch, tmp_path):
    """Число линий страницы не уменьшилось — «снятие не подтверждено»."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    src.wires.append((_FakeWire(77), dst, 0, 0))

    class _Still(_BridgeReplies):
        lines = ["removed=77 pw=0"]

    _install_disconnect(monkeypatch, tmp_path, _Still,
                        {"k_0": src, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не уменьшилось" in message
    assert session.WIRES == [], "реестр тронут без подтверждения"


@pytest.mark.anyio
async def test_disconnect_wire_says_when_wire_list_is_unreadable(
        monkeypatch, tmp_path):
    """Сбой перечисления линий назван, а не проглочен (находка ревью)."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)

    class _Ok(_BridgeReplies):
        lines = ["removed=77 pw=0"]

    _install_disconnect(monkeypatch, tmp_path, _Ok,
                        {"k_0": src, "Integrator_0": dst})

    def _boom():
        raise RuntimeError("COM: перечисление объектов не прошло")

    session.current_project().page.get_wires = _boom

    text = _text(await mcp.call_tool(
        "disconnect_wire", {"src": "k_0", "dst": "Integrator_0"}))

    assert "снята (wire=77)" in text
    assert "прочитать не удалось" in text, "сбой чтения числа линий умолчан"


@pytest.mark.anyio
async def test_disconnect_wire_refuses_when_body_reports_missing_port(
        monkeypatch, tmp_path):
    """Тело не нашло порт — отказ с редакцией «порт пересоздался»."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)

    class _NoPort(_BridgeReplies):
        lines = ["err=no-in-port"]

    _install_disconnect(monkeypatch, tmp_path, _NoPort,
                        {"k_0": src, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не нашла входной порт" in message


@pytest.mark.anyio
async def test_disconnect_wire_refuses_unreadable_body_reply(monkeypatch, tmp_path):
    """Неизвестный ответ тела — «снятие не подтверждено», не успех."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)

    class _Mute(_BridgeReplies):
        lines = ["нечто вместо ответа"]

    _install_disconnect(monkeypatch, tmp_path, _Mute,
                        {"k_0": src, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не оставило ответа" in message


@pytest.mark.anyio
async def test_disconnect_wire_refuses_when_line_start_unreadable(
        monkeypatch, tmp_path):
    """Начало линии не читается (`findstartport` → 0) — снимать нечем."""
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)

    class _NoStart(_BridgeReplies):
        lines = ["err=other-src blk=0"]

    _install_disconnect(monkeypatch, tmp_path, _NoStart,
                        {"k_0": src, "Integrator_0": dst})

    message = await _error("disconnect_wire",
                           {"src": "k_0", "dst": "Integrator_0"})

    assert "не назвала начало линии" in message


def test_vanished_wires_compares_sets():
    """Разница перечислений — чистая функция: пропавшие id и «не знаем»."""
    from simintech_mcp.tools.wires import _vanished_wires

    assert _vanished_wires([1, 2, 3], [3, 4]) == [1, 2]
    assert _vanished_wires([1], []) == [1]
    assert _vanished_wires([1], [1]) == []
    assert _vanished_wires(None, [1]) == [], "сбой чтения — не «ничего не ушло»"
    assert _vanished_wires([1], None) == []


# ─── list_wires: контракт ответа ──────────────────────────────────


class _BrokenWirePage:
    """Страница, у которой перечисление линий падает."""

    def __init__(self, error):
        self._error = error

    def get_wires(self):
        raise self._error


class _BrokenWireProject:
    def __init__(self, page):
        self._page = page

    def get_main_page(self):
        return self._page


def _page_with_wires(monkeypatch, wire_ids, extra_blocks=()):
    """Страница с заданными линиями (подделка собирает их из блоков)."""
    block = _PlacedBlock("k_0", 1)
    block.wires = [(_FakeWire(i), "kx_0", 0, 0) for i in wire_ids]
    blocks = {"k_0": block}
    for name, block_id in extra_blocks:
        blocks[name] = _PlacedBlock(name, block_id)
    _install_fake_project(monkeypatch, blocks)


@pytest.mark.anyio
async def test_list_wires_reports_empty_page(monkeypatch):
    """Пустая страница: названо отсутствие линий, а не счётчик с нулём."""
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = _text(await mcp.call_tool("list_wires", {}))

    assert "Линий связи на странице нет" in text


@pytest.mark.anyio
async def test_list_wires_reports_ids_and_says_what_it_cannot(monkeypatch):
    """Ответ несёт количество, идентификаторы и оговорку про концы линий.

    Оговорка — часть контракта, а не украшение: концы линий через COM не
    читаются, и клиент обязан узнать об этом из ответа, а не догадываться по
    отсутствию пары «откуда → куда».
    """
    _page_with_wires(monkeypatch, [11, 12, 13], extra_blocks=[("kx_0", 2)])

    text = _text(await mcp.call_tool("list_wires", {}))

    assert "Линий связи: 3" in text
    assert "блоков на странице: 2" in text
    assert "11, 12, 13" in text
    assert "через COM не читаются" in text


@pytest.mark.anyio
async def test_list_wires_truncates_a_long_list_with_a_marker(monkeypatch):
    """Больше 20 линий: печатаются первые 20, усечение помечено «…».

    Без пометки клиент счёл бы список полным и не заметил бы остальные линии.
    """
    _page_with_wires(monkeypatch, range(1, 26))

    text = _text(await mcp.call_tool("list_wires", {}))

    assert "Линий связи: 25" in text
    listed = text.split("Идентификаторы:")[1]
    assert "1, 2, 3" in listed
    assert "20 …" in listed, "усечение обязано быть помечено"
    assert ", 21" not in listed, "двадцать первая линия не печатается"


@pytest.mark.anyio
async def test_list_wires_refuses_when_enumeration_fails(monkeypatch):
    """Сбой перечисления — отказ, а не «линий нет».

    Подстановка пустого списка выдала бы сбой среды за честный ноль объектов:
    клиент, доверяющий `isError`, увидел бы успех и продолжил работу по
    недостоверной модели схемы.
    """
    page = _BrokenWirePage(ComCallError("GetPageObjectCount"))
    monkeypatch.setattr(session, "_project", _BrokenWireProject(page))

    text = await _error("list_wires", {})

    assert "ComCallError" in text
