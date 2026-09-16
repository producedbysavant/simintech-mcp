"""Расстановка блоков и трассировка связей."""

from __future__ import annotations

import pytest

from simintech_mcp.server import mcp

from simintech_mcp import session

from _support import (
    _ConnectingBlock,
    _PlacedBlock,
    _error,
    _install_fake_project,
    _install_wire_project,
    _text,
)


@pytest.mark.anyio
async def test_layout_place_applies_coordinates(monkeypatch):
    """layout_place не только считает координаты, но и применяет их.

    Раньше инструмент возвращал координаты текстом, а блоки не двигал: агент
    получал подтверждение расстановки, которой не было.
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    _install_fake_project(monkeypatch, {"k_0": first, "kx_0": second})

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert "Расставлено блоков: 2" in text
    assert first.center is not None, "координаты не применены к блоку"
    assert second.center is not None, "координаты не применены к блоку"
    assert first.center != second.center, "блоки не разнесены по слоям"


@pytest.mark.anyio
async def test_layout_place_uses_block_sizes(monkeypatch):
    """Расстановка считается по размерам самих блоков, а не по константам.

    Размер блока задан правилами разработки SimInTech: подставлять свой
    (60x40) нельзя — блок нестандартного размера считается нарушением.
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    first.SIZE = (120.0, 80.0)
    second.SIZE = (120.0, 80.0)
    _install_fake_project(monkeypatch, {"k_0": first, "kx_0": second})

    await mcp.call_tool("layout_place",
                        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"})

    assert first.size_reads == 1, "размер блока не запрошен"
    assert second.size_reads == 1, "размер блока не запрошен"


@pytest.mark.anyio
async def test_layout_place_rejects_unknown_block(monkeypatch):
    """Несуществующий блок — отказ, а не «успешная» расстановка."""
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = await _error("layout_place",
                        {"block_ids": "k_0,нет_такого", "connections": ""})

    assert "не найдены" in text


@pytest.mark.anyio
async def test_layout_place_rejects_connection_outside_block_ids(monkeypatch):
    """Связь ссылается на блок вне block_ids — расстановка невозможна."""
    _install_fake_project(monkeypatch, {
        "k_0": _PlacedBlock("k_0", 1),
        "kx_0": _PlacedBlock("kx_0", 2),
    })

    text = await _error("layout_place",
                        {"block_ids": "k_0", "connections": "k_0->kx_0"})

    assert "вне block_ids" in text


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
    assert session._WIRES == [(wire, "k_0", 0, "Integrator_0", 0)], \
        "линия не запомнена вместе с концами для выравнивания"


@pytest.mark.anyio
async def test_layout_place_aligns_port_heights(monkeypatch):
    """Блок сдвигается так, чтобы основной вход лёг на высоту выхода.

    Иначе линия идёт с лишним изломом: у «Сумматора» входы на четверти и трёх
    четвертях высоты, а выход «Усилителя» посередине.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    dst.in_port_offset = 8.0
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert dst.center[1] == src.center[1] + 8.0, "вход не выровнен с выходом"
    assert dst.center[0] > src.center[0], "блоки не разнесены по слоям"
    assert "ВНИМАНИЕ" not in text


@pytest.mark.anyio
async def test_layout_place_repaints_before_routing(monkeypatch):
    """Порядок: перемещение → перерисовка → трассировка.

    Без перерисовки SimInTech прокладывает провода по прежним прямоугольникам
    блоков (они ещё лежат в (0,0) друг на друге) и оставляет в геометрии точки
    вроде (-160,-1056), которые потом не пересчитываются. Проверено на
    SimInTech64 2026-09-15.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    events = _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})
    wire = src.wires[0][0]

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert events == [("repaint", None), ("normalize", 1)], \
        "перерисовка должна идти до трассировки"
    assert wire.normalized == 1, "после расстановки линия не трассирована"
    assert "нормализовано 1" in text


@pytest.mark.anyio
async def test_layout_place_reports_no_wires(monkeypatch):
    """Если линий не создавали, инструмент об этом говорит, а не молчит."""
    _install_wire_project(monkeypatch, {
        "k_0": _ConnectingBlock("k_0", 1),
        "kx_0": _ConnectingBlock("kx_0", 2),
    })

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert "Линий связи в этой сессии нет" in text
