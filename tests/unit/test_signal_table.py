"""Вставка сигналов в таблицу: чистые функции и сквозной путь с контуром."""

from __future__ import annotations

import re

import pytest

from simintech_mcp import session
from simintech_mcp import surface
from simintech_mcp.server import mcp
from simintech_mcp.tools import signal_table

from _support import _error, _tool_text, _FakeProject


class _Outcome:
    kind = "ok"

    def __init__(self, lines):
        self.lines = lines


class _TableBlock:
    """Блок с пятью колонками таблицы; COM-свойства хранятся в словаре."""

    id = 78
    class_name = "Вход алгоритма"

    def __init__(self, props, *, size=(64.0, 800.0), ports=2,
                 vector=False):
        self.props = dict(props)
        self.props.setdefault("fExcludeFirst", "1")
        self.props.setdefault("AutoChangeHeight", "0")
        self._size = list(size)
        self.ports = ports
        self.vector = vector

    def get_name(self):
        return "Signal_Block_0"

    def get_property(self, name):
        return self.props[name]

    def get_size(self):
        return tuple(self._size)

    def positions(self):
        rows = signal_table.split_rows(self.props["SignalNames"])
        header = str(self.props.get("fExcludeFirst") or "1").lower() not in (
            "0", "false")
        return max(0, len(rows) - (1 if header and rows else 0))


def _block(signals="Имя сигнала\r\nA\r\nB\r\n", **kwargs):
    props = {
        "SignalNames": signals,
        "Captions": "Название\r\nCa\r\nCb\r\n",
        "SystemNames": "Имя\r\nSa\r\nSb\r\n",
        "ParamNames": "Параметр\r\nPa\r\nPb\r\n",
        "Numbers": "ID\r\n\r\n\r\n",
    }
    props.update(kwargs.pop("props", {}))
    return _TableBlock(props, **kwargs)


def _eval_literal(expr: str) -> str:
    parts = [part.strip() for part in expr.split(" + ")]
    out = []
    for part in parts:
        if part.startswith('"') and part.endswith('"'):
            out.append(part[1:-1])
        elif part == "chr(34)":
            out.append('"')
        elif part == "chr(13)":
            out.append("\r")
        elif part == "chr(10)":
            out.append("\n")
        else:
            raise AssertionError(f"не разобрали литерал: {part!r}")
    return "".join(out)


def _install_contour(monkeypatch, block, *, ignore=(), vector=False):
    """Подделка контура: читает свойства, применяет запись, ведёт порты."""
    calls = []

    def fake(body, *, failed):
        calls.append(body)
        token = re.search(r'"(IT[0-9a-f]+)_READ_', body)
        if token:
            marker = token.group(1)
            lines = []
            for column in signal_table.TABLE_COLUMNS:
                if f"{marker}_READ_{column}_BEGIN" not in body:
                    continue
                lines += [f"{marker}_READ_{column}_BEGIN",
                          block.props[column],
                          f"{marker}_READ_{column}_END"]
            lines.append(f"{marker}_PORTS={block.ports}")
            return _Outcome(lines), ""
        marker = re.search(r'"(IT[0-9a-f]+)_WRITE_', body).group(1)
        for line in body.splitlines():
            match = re.match(
                r'setpropasstring\(obj, "([^"]+)", (.*)\);$', line)
            if not match:
                continue
            column, expr = match.groups()
            if column not in ignore:
                block.props[column] = _eval_literal(expr)
        if "initobject(obj);" in body:
            block.ports = 1 if vector else block.positions()
        height = re.search(r'setprop\(blk, "Height", ([0-9.]+)\)', body)
        if height:
            block._size[1] = float(height.group(1))
        lines = []
        for column in signal_table.TABLE_COLUMNS:
            if f"{marker}_WRITE_{column}_BEGIN" not in body:
                continue
            lines += [f"{marker}_WRITE_{column}_BEGIN",
                      block.props[column],
                      f"{marker}_WRITE_{column}_END"]
        lines.append(f"{marker}_PORTS={block.ports}")
        return _Outcome(lines), ""

    monkeypatch.setattr(signal_table, "run_contour", fake)
    return calls


def _install(monkeypatch, block):
    monkeypatch.setattr(session, "_project",
                        _FakeProject({"Signal_Block_0": block}))


def test_split_join_roundtrip_and_breaks():
    assert signal_table.split_rows("") == []
    assert signal_table.split_rows("A\r\nB\r\n") == ["A", "B"]
    assert signal_table.split_rows("A\r\nB\r\n\r\n") == ["A", "B", ""]
    assert signal_table.split_rows("A" + chr(0x2028) + "B") == [
        "A" + chr(0x2028) + "B"]
    assert signal_table.validate_signals("A" + chr(0x2028) + "B") == [
        "A", "B"]
    assert signal_table.join_rows(["A", "B"]) == "A\r\nB\r\n"
    assert signal_table.split_rows(signal_table.join_rows(
        ["H", "A", ""])) == ["H", "A", ""]


def test_validate_signals_rejects_bad_batches():
    assert signal_table.validate_signals("A\nB") == ["A", "B"]
    for text in ("", "\n", "A\n\nB", " A", "A ", "A\nA"):
        with pytest.raises(Exception):
            signal_table.validate_signals(text)
    with pytest.raises(Exception):
        signal_table.validate_signals("\n".join(
            f"S{i}" for i in range(signal_table.MAX_INSERT_SIGNALS + 1)))


def test_resolve_position_modes_and_fallback():
    rows = ["A", "B", "C"]
    assert signal_table.resolve_position(
        rows, before="B", after="", index=None).position == 1
    assert signal_table.resolve_position(
        rows, before="", after="B", index=None).position == 2
    assert signal_table.resolve_position(
        rows, before="", after="", index=3).position == 3
    assert signal_table.resolve_position(
        rows, before="", after="", index=None).mode == "append"
    fallback = signal_table.resolve_position(
        rows, before="нет", after="", index=1)
    assert fallback.position == 1 and fallback.fallback
    with pytest.raises(Exception):
        signal_table.resolve_position(
            rows, before="нет", after="", index=None)
    with pytest.raises(Exception):
        signal_table.resolve_position(
            ["A", "A"], before="A", after="", index=None)
    with pytest.raises(Exception):
        signal_table.resolve_position(
            rows, before="A", after="B", index=None)
    with pytest.raises(Exception):
        signal_table.resolve_position(
            rows, before="", after="", index=4)


def test_splice_pads_short_column():
    assert signal_table.splice_rows(["H", "A"], 4, ["X", "Y"]) == [
        "H", "A", "", "", "X", "Y"]
    assert signal_table.splice_rows(["H", "A"], 1, ["X"]) == [
        "H", "X", "A"]


@pytest.mark.anyio
async def test_insert_before_writes_all_columns_and_height(monkeypatch):
    block = _block()
    _install(monkeypatch, block)
    calls = _install_contour(monkeypatch, block)

    text = _tool_text(await mcp.call_tool(
        "insert_signals",
        {"block": "Signal_Block_0", "signals": "X\nY", "before": "B"}))

    assert len(calls) == 2, "должны быть чтение и запись"
    write = calls[1]
    for column in signal_table.TABLE_COLUMNS:
        assert f'setpropasstring(obj, "{column}"' in write
    assert "initobject(obj);" in write
    assert signal_table.split_rows(block.props["SignalNames"]) == [
        "Имя сигнала", "A", "X", "Y", "B"]
    assert signal_table.split_rows(block.props["Captions"]) == [
        "Название", "Ca", "", "", "Cb"]
    assert block.ports == 4
    assert block._size[1] == 64.0
    assert "позиций 2 -> 4" in text
    assert "вставлено 2" in text


@pytest.mark.anyio
async def test_index_fallback_is_named(monkeypatch):
    block = _block()
    _install(monkeypatch, block)
    _install_contour(monkeypatch, block)

    text = _tool_text(await mcp.call_tool(
        "insert_signals",
        {"block": "Signal_Block_0", "signals": "X", "before": "нет",
         "index": 0}))

    assert signal_table.split_rows(block.props["SignalNames"])[1] == "X"
    assert "использован index" in text


@pytest.mark.anyio
async def test_ambiguous_anchor_refused_before_write(monkeypatch):
    block = _block(signals="Имя сигнала\r\nA\r\nA\r\n")
    _install(monkeypatch, block)
    calls = _install_contour(monkeypatch, block)

    text = await _error("insert_signals",
                        {"block": "Signal_Block_0", "signals": "X",
                         "before": "A"})

    assert "встречается" in text
    assert len(calls) == 1, "после чтения запись не должна начинаться"


@pytest.mark.anyio
async def test_running_project_refused_before_contour(monkeypatch):
    block = _block()
    project = _FakeProject({"Signal_Block_0": block})
    project._simulation._state = 3
    monkeypatch.setattr(session, "_project", project)
    calls = _install_contour(monkeypatch, block)

    text = await _error("insert_signals",
                        {"block": "Signal_Block_0", "signals": "X",
                         "before": "B"})

    assert "не в остановленном состоянии" in text
    assert calls == []


@pytest.mark.anyio
async def test_verification_mismatch_names_column(monkeypatch):
    block = _block()
    _install(monkeypatch, block)
    _install_contour(monkeypatch, block, ignore=("Captions",))

    text = await _error("insert_signals",
                        {"block": "Signal_Block_0", "signals": "X",
                         "before": "B"})

    assert "колонка Captions" in text
    assert "повторная вставка" in text


@pytest.mark.anyio
async def test_non_algorithm_class_keeps_height(monkeypatch):
    block = _block()
    block.class_name = "Усилитель"
    _install(monkeypatch, block)
    write = _install_contour(monkeypatch, block)

    text = _tool_text(await mcp.call_tool(
        "insert_signals",
        {"block": "Signal_Block_0", "signals": "X", "before": "B"}))

    assert 'setprop(blk, "Height"' not in write[1]
    assert block._size[1] == 800.0
    assert "класс вне правила высоты" in text


@pytest.mark.anyio
async def test_vector_class_reports_ports_not_positions(monkeypatch):
    block = _block(vector=True, ports=1)
    block.class_name = "Вход алгоритма (векторный)"
    _install(monkeypatch, block)
    _install_contour(monkeypatch, block, vector=True)

    text = _tool_text(await mcp.call_tool(
        "insert_signals",
        {"block": "Signal_Block_0", "signals": "X", "before": "B"}))

    assert "векторный класс" in text
    assert block.ports == 1


@pytest.mark.anyio
async def test_tool_is_registered_and_searchable():
    tool = await mcp.get_tool("insert_signals")
    assert tool is not None
    short = surface.SHORT_DESCRIPTIONS["insert_signals"]
    assert "SignalNames" in short


def _remove_block():
    block = _block(signals="H\r\nA\r\nB\r\nA\r\nC\r\nB\r\n", ports=5)
    block.props.update({
        "Captions": "CH\r\nCa\r\nCb\r\nCa2\r\nCc\r\nCb2\r\n",
        "SystemNames": "SH\r\nSa\r\nSb\r\nSa2\r\nSc\r\nSb2\r\n",
        "ParamNames": "PH\r\nPa\r\nPb\r\nPa2\r\nPc\r\nPb2\r\n",
        "Numbers": "ID\r\n\r\n\r\n\r\n\r\n\r\n",
    })
    return block


def test_select_removals_modes():
    body = ["A", "B", "A", "C", "B"]
    assert signal_table.select_removals(
        body, [], True, None, 1) == [2, 4]
    assert signal_table.select_removals(
        body, ["A"], False, None, 1) == [0, 2]
    assert signal_table.select_removals(
        body, [], False, 1, 2) == [1, 2]
    assert signal_table.remove_rows(["H", "A", "B"], [1]) == ["H", "B"]
    assert signal_table.parse_remove_names("A\nB\nA") == ["A", "B"]


@pytest.mark.anyio
async def test_remove_duplicates_keeps_first_in_all_columns(monkeypatch):
    block = _remove_block()
    _install(monkeypatch, block)
    calls = _install_contour(monkeypatch, block)

    text = _tool_text(await mcp.call_tool(
        "remove_signals", {"block": "Signal_Block_0", "duplicates": True}))

    assert signal_table.split_rows(block.props["SignalNames"]) == [
        "H", "A", "B", "C"]
    assert signal_table.split_rows(block.props["Captions"]) == [
        "CH", "Ca", "Cb", "Cc"]
    assert block.ports == 3
    assert block._size[1] == 48.0
    assert "удалено 2" in text
    assert "позиций 5 -> 3" in text
    assert len(calls) == 2


@pytest.mark.anyio
async def test_remove_by_names_and_range(monkeypatch):
    block = _remove_block()
    _install(monkeypatch, block)
    _install_contour(monkeypatch, block)

    text = _tool_text(await mcp.call_tool(
        "remove_signals",
        {"block": "Signal_Block_0", "signals": "A"}))

    assert signal_table.split_rows(block.props["SignalNames"]) == [
        "H", "B", "C", "B"]
    assert "удалено 2" in text

    block2 = _remove_block()
    _install(monkeypatch, block2)
    _install_contour(monkeypatch, block2)
    text2 = _tool_text(await mcp.call_tool(
        "remove_signals",
        {"block": "Signal_Block_0", "index": 1, "count": 2}))

    assert signal_table.split_rows(block2.props["SignalNames"]) == [
        "H", "A", "C", "B"]
    assert "удалено 2" in text2


@pytest.mark.anyio
async def test_remove_noop_is_error(monkeypatch):
    block = _block(signals="H\r\nA\r\nB\r\n", ports=2)
    _install(monkeypatch, block)
    calls = _install_contour(monkeypatch, block)

    text = await _error("remove_signals",
                        {"block": "Signal_Block_0", "duplicates": True})

    assert "нечего удалять" in text
    assert len(calls) == 1, "после чтения запись не начинается"


@pytest.mark.anyio
async def test_remove_mode_conflict_and_range(monkeypatch):
    block = _remove_block()
    _install(monkeypatch, block)
    _install_contour(monkeypatch, block)

    text = await _error("remove_signals",
                        {"block": "Signal_Block_0", "signals": "A",
                         "duplicates": True})
    assert "ровно один режим" in text

    text2 = await _error("remove_signals",
                         {"block": "Signal_Block_0", "index": 4,
                          "count": 2})
    assert "выходит за" in text2


@pytest.mark.anyio
async def test_remove_wired_middle_refused(monkeypatch):
    block = _remove_block()
    project = _FakeProject({"Signal_Block_0": block})
    monkeypatch.setattr(session, "_project", project)
    page = project.get_main_page()
    monkeypatch.setattr(page, "get_wires", lambda: [object()])
    calls = _install_contour(monkeypatch, block)

    text = await _error("remove_signals",
                        {"block": "Signal_Block_0", "duplicates": True})

    assert "провода остаются на прежних индексах" in text
    assert "allow_shift=True" in text
    assert len(calls) == 1, "после чтения запись не начинается"


@pytest.mark.anyio
async def test_remove_wired_middle_allows_with_flag(monkeypatch):
    block = _remove_block()
    project = _FakeProject({"Signal_Block_0": block})
    monkeypatch.setattr(session, "_project", project)
    page = project.get_main_page()
    monkeypatch.setattr(page, "get_wires", lambda: [object()])
    _install_contour(monkeypatch, block)

    text = _tool_text(await mcp.call_tool(
        "remove_signals",
        {"block": "Signal_Block_0", "duplicates": True,
         "allow_shift": True}))

    assert signal_table.split_rows(block.props["SignalNames"]) == [
        "H", "A", "B", "C"]
    assert "переразведите" in text


@pytest.mark.anyio
async def test_remove_verification_mismatch_names_column(monkeypatch):
    block = _remove_block()
    _install(monkeypatch, block)
    _install_contour(monkeypatch, block, ignore=("Captions",))

    text = await _error("remove_signals",
                        {"block": "Signal_Block_0", "duplicates": True})

    assert "колонка Captions" in text
    assert "повторное удаление" in text
