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
async def test_layout_place_rejects_pair_without_arrow(monkeypatch):
    """Токен без `->` — отказ, а не молчаливый пропуск связи.

    Раньше такая пара тихо выпадала из расстановки: клиент получал
    подтверждение успеха, а связь не учитывалась — блоки вставали в один слой.
    """
    _install_fake_project(monkeypatch, {
        "k_0": _PlacedBlock("k_0", 1),
        "kx_0": _PlacedBlock("kx_0", 2),
    })

    text = await _error("layout_place",
                        {"block_ids": "k_0,kx_0", "connections": "k_0→kx_0"})

    assert "k_0→kx_0" in text, "отказ обязан называть неразобранный токен"
    assert "->" in text, "отказ обязан показывать ожидаемую форму пары"


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
    assert session._WIRES == [], "отказ по нулевому id, а линия запомнена"


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
async def test_layout_place_aligns_ports_addressed_by_id(monkeypatch):
    """Блоки адресованы id, а `connect` запомнил имена — выравнивание всё равно.

    `block_ids` принимает и числовые id, но `_WIRES` хранит имена блоков: без
    перевода имён в токены `centers` совпадений не находил, и выравнивание
    молча пропускалось (ни счётчика, ни строки ВНИМАНИЕ).
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    dst.in_port_offset = 8.0
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "1,2", "connections": "1->2"}))

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
async def test_layout_place_routes_wires_of_opened_project(monkeypatch):
    """Линии открытого проекта тоже трассируются: их перечисляет `get_wires`.

    Раньше нормализовались только линии, созданные `connect` в этой сессии, а
    описание инструмента объясняло это тем, что «COM не умеет перечислять линии
    страницы» — тогда как `list_wires` в этом же сервере перечисляет их через
    `Page.get_wires`.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    events = _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    wire = src.connect(dst)          # линия из файла: `connect` не вызывался
    assert session._WIRES == []

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert wire.normalized == 1, "линия открытого проекта не трассирована"
    assert ("normalize", wire.id) in events
    assert "линий страницы" in text


@pytest.mark.anyio
async def test_layout_place_reports_no_wires(monkeypatch):
    """Если линий на странице нет, инструмент об этом говорит, а не молчит."""
    _install_wire_project(monkeypatch, {
        "k_0": _ConnectingBlock("k_0", 1),
        "kx_0": _ConnectingBlock("kx_0", 2),
    })

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert "Линий связи на странице нет" in text


@pytest.mark.anyio
async def test_layout_place_without_arguments_places_every_block(monkeypatch):
    """Пустой вызов — «все блоки страницы и все связи сессии».

    Частичный список оставлял неперечисленные блоки в (0,0) друг на друге:
    модель собиралась «кучей», а инструмент при этом подтверждал расстановку
    (живой случай 02.10.2026).
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    lone = _PlacedBlock("k_1", 3)
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst, "k_1": lone})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "Расставлено блоков: 3" in text, "не все блоки страницы расставлены"
    assert lone.center is not None, "блок без связей остался в куче"
    assert dst.center[0] > src.center[0], \
        "связь сессии не учтена: блоки встали в один слой"
    assert "Наложений блоков нет" in text


@pytest.mark.anyio
async def test_layout_place_bare_ignores_stale_wires(monkeypatch):
    """Связь с блоком вне страницы не срывает вызов «расставь всё».

    В реестре сессии могли остаться концы прежней страницы; строгий отказ по
    ним («вне block_ids») сорвал бы честный вызов без аргументов.
    """
    src = _ConnectingBlock("k_0", 1)
    _install_wire_project(monkeypatch, {"k_0": src})
    session._WIRES.append((None, "k_0", 0, "исчез_0", 0))

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "Расставлено блоков: 1" in text


@pytest.mark.anyio
async def test_layout_place_reports_overlap_with_foreign_block(monkeypatch):
    """Наложение расставленного блока на чужой названо в ответе.

    Контракт «без наложений» обязан быть виден фактом: габариты читаются из
    `Points`, и пересечение попадает в ответ, а не в глаза пользователя.
    """
    placed = _PlacedBlock("k_0", 1)
    obstacle = _PlacedBlock("t_0", 2)
    obstacle.SIZE = (400.0, 300.0)
    _install_fake_project(monkeypatch, {"k_0": placed, "t_0": obstacle})

    text = _text(await mcp.call_tool(
        "layout_place", {"block_ids": "k_0", "connections": ""}))

    assert "ВНИМАНИЕ: наложения блоков" in text
    assert "k_0" in text and "t_0" in text, "пара наложения названа не полностью"
