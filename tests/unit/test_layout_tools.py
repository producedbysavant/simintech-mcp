"""Раскладка: `layout_place`, `set_block_center` и формула кадра.

Связевые тесты (`connect`, `disconnect_wire`, `remove_block`) вынесены в
`test_wires_tools.py`, а габаритные — в `test_sizes_tools.py`/`test_fits_tools.py`
(issue #124): файл называет модуль, который проверяет.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.server import mcp

from simintech_mcp import session

from _support import (
    _ConnectingBlock,
    _FakeWire,
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

    # Размер читают двое: расстановка (габариты для слоёв) и метрика
    # наложений (габарит = центр ± size/2, замер 02.10.2026) — «ровно один
    # раз» тут не контракт, контракт — что размер берётся у самих блоков.
    assert first.size_reads >= 1, "размер блока не запрошен"
    assert second.size_reads >= 1, "размер блока не запрошен"


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

    `block_ids` принимает и числовые id, но `WIRES` хранит имена блоков: без
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
async def test_layout_place_normalize_only_routes_without_moving_blocks(
        monkeypatch):
    """normalize_only — трассировка всех линий страницы без расстановки.

    Режим объявлен явно после живого замера 03.10.2026: проект из одних
    порт-блоков раскладывать нельзя (широкие порты накрывают соседние
    колонки), а линии должны быть ортогональны. Прежде то же делали трюком —
    вызовом по не-блочному объекту, — и он опирался на побочное свойство
    фильтра подписей.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    events = _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})
    wire = src.wires[0][0]

    text = _text(await mcp.call_tool("layout_place",
                                     {"normalize_only": True}))

    assert events == [("repaint", None), ("normalize", 1)], \
        "перерисовка должна идти до трассировки"
    assert wire.normalized == 1, "линия не трассирована"
    assert src.center is None and dst.center is None, \
        "normalize_only сдвинул блоки — расстановки в этом режиме быть не должно"
    assert "Блоки не двигались" in text
    assert "нормализовано 1" in text
    assert "сверяйте снимком" in text, \
        "ответ обязан звать к отрисовке: ортогональность инструмент не измеряет"


@pytest.mark.anyio
async def test_layout_place_normalize_only_rejects_explicit_scope(monkeypatch):
    """normalize_only с block_ids/connections — отказ, а не тихий выбор одного.

    Режим «только нормализация» и расстановка взаимоисключающие; молчаливое
    предпочтение одного из них вернуло бы класс ошибок «подтверждение не того,
    что просили».
    """
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = await _error("layout_place",
                        {"block_ids": "k_0", "normalize_only": True})

    assert "не сочетается" in text
    assert "block_ids" in text


@pytest.mark.anyio
async def test_layout_place_normalize_only_reports_no_wires(monkeypatch):
    """Страница без линий — состояние, а не отказ (как в обычном режиме)."""
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = _text(await mcp.call_tool("layout_place",
                                     {"normalize_only": True}))

    assert "Блоки не двигались" in text
    assert "Линий связи на странице нет" in text


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
    assert session.WIRES == []

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert wire.normalized == 1, "линия открытого проекта не трассирована"
    assert ("normalize", wire.id) in events
    assert "нормализовано 1 линия страницы" in text


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
    session.WIRES.append((None, "k_0", 0, "исчез_0", 0))

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


@pytest.mark.anyio
async def test_layout_place_stacks_port_blocks_flush(monkeypatch):
    """Порт-блоки одного класса стыкуются стопкой вплотную (#24, п.2).

    Стандарт оформления: «входные порты единой колонкой без зазоров»; шаг
    стопки — высота блока (16 px у порт-блока с одним сигналом).
    """
    first = _PlacedBlock("In_0", 1, class_name="Порт входа")
    second = _PlacedBlock("In_1", 2, class_name="Порт входа")
    third = _PlacedBlock("In_2", 3, class_name="Порт входа")
    for block in (first, second, third):
        block.SIZE = (64.0, 16.0)
    _install_wire_project(monkeypatch, {"In_0": first, "In_1": second,
                                        "In_2": third})

    text = _text(await mcp.call_tool("layout_place", {}))

    ys = sorted(block.center[1] for block in (first, second, third))
    assert ys[1] - ys[0] == 16.0 and ys[2] - ys[1] == 16.0, \
        "стопка не вплотную: зазор между порт-блоками"
    xs = {first.center[0], second.center[0], third.center[0]}
    assert len(xs) == 1, "порт-блоки не в одной колонке"
    assert "Стопки порт-блоков" in text


@pytest.mark.anyio
async def test_layout_place_skips_label_objects(monkeypatch):
    """Подписи (`constLabel`) — не блоки: не двигаются и не считаются.

    Их `Points` — якорь текста, а `size` — типовая карточка 60×40; живой
    случай 02.10.2026: расставленная «как блок» подпись уезжает от своего
    блока и даёт ложные наложения.
    """
    block = _PlacedBlock("k_0", 1)
    label = _PlacedBlock("TextLabel3", 2, class_name="constLabel")
    _install_wire_project(monkeypatch, {"k_0": block, "TextLabel3": label})

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "Расставлено блоков: 1" in text
    assert label.center is None, "подпись подвинули как блок"
    assert "Подписи (не блоки) не расставляются: 1" in text
    assert "k_0—TextLabel3" not in text, \
        "карточка подписи принята за наложение"


@pytest.mark.anyio
async def test_layout_place_snaps_centers_to_grid(monkeypatch):
    """Центры блоков — на разметку 8 px (стандарт: 1 квадратик = 8×8).

    Стек 28+80=108 px даёт координату, не кратную 8, — постановка на сетку
    обязана её поправить (порты при этом тоже на сетке: они в cx±16, cy).
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("k_1", 2)
    first.SIZE = (60.0, 28.0)
    second.SIZE = (60.0, 28.0)
    _install_wire_project(monkeypatch, {"k_0": first, "k_1": second})

    await mcp.call_tool("layout_place",
                        {"block_ids": "k_0,k_1", "connections": ""})

    for block in (first, second):
        cx, cy = block.center
        assert cx % 8 == 0 and cy % 8 == 0, \
            f"центр {block.get_name()} вне сетки 8: ({cx}, {cy})"


@pytest.mark.anyio
async def test_layout_place_reports_overlap_when_foreign_block_listed_first(
        monkeypatch):
    """Пара наложения видна и когда чужой блок перечислен раньше (находка ревью).

    Метрика пропускала пары, где первым в перечислении страницы идёт **чужой**
    блок (`if name_a not in placed: continue`), и расставленный поверх чужого
    не назывался: ответ читался как «наложений нет».
    """
    obstacle = _PlacedBlock("t_0", 2)
    obstacle.SIZE = (400.0, 300.0)
    placed = _PlacedBlock("k_0", 1)
    _install_fake_project(monkeypatch, {"t_0": obstacle, "k_0": placed})

    text = _text(await mcp.call_tool(
        "layout_place", {"block_ids": "k_0", "connections": ""}))

    assert "ВНИМАНИЕ: наложения блоков" in text, \
        "чужой блок раньше в перечислении скрыл пару наложения"
    assert "t_0" in text and "k_0" in text, "пара наложения названа не полностью"


@pytest.mark.anyio
async def test_layout_place_does_not_flush_ports_across_foreign_block(
        monkeypatch):
    """Стопка не собирается через чужой блок (находка ревью).

    Прежде в колонке смыкались все однотипные порт-блоки, даже если между
    ними стоял другой блок: стопка насаживала порты на него — инструмент сам
    создавал наложение, о котором тут же предупреждал.
    """
    blocks = {}
    ports_before = []
    for index in range(6):
        block = _PlacedBlock(f"In_{index}", index + 1, class_name="Порт входа")
        block.SIZE = (64.0, 16.0)
        ports_before.append(block)
        blocks[block.get_name()] = block
    middle = _PlacedBlock("Sum_0", 100, class_name="Сумматор")
    middle.SIZE = (32.0, 32.0)
    blocks["Sum_0"] = middle
    ports_after = []
    for index in range(6, 8):
        block = _PlacedBlock(f"In_{index}", index + 1, class_name="Порт входа")
        block.SIZE = (64.0, 16.0)
        ports_after.append(block)
        blocks[block.get_name()] = block
    _install_wire_project(monkeypatch, blocks)

    text = _text(await mcp.call_tool("layout_place", {}))

    ys = sorted(block.center[1] for block in ports_before)
    assert ys[1] - ys[0] == 16.0, "соседние порты до чужого блока не сомкнулись"
    gap = ports_after[0].center[1] - ports_before[-1].center[1]
    assert gap > 16.0, "порт после чужого блока втянут в стопку через него"
    assert "Наложений блоков нет" in text


@pytest.mark.anyio
async def test_layout_place_keeps_port_stack_over_alignment(monkeypatch):
    """Приёмник-порт-блок выравнивание не двигает: стопка остаётся (ревью).

    Прежде выравнивание тянуло порт-блок к строке источника, стопка
    разъезжалась — а ответ продолжал утверждать «сомкнуто вплотную».
    """
    class _PortIn(_ConnectingBlock):
        class_name = "Порт входа"

    src = _ConnectingBlock("k_0", 1)
    ports = [_PortIn("In_0", 2), _PortIn("In_1", 3), _PortIn("In_2", 4)]
    _install_wire_project(monkeypatch, {"k_0": src, "In_0": ports[0],
                                        "In_1": ports[1], "In_2": ports[2]})
    for index in range(3):
        await mcp.call_tool("connect",
                            {"src": "k_0", "dst": f"In_{index}"})

    text = _text(await mcp.call_tool("layout_place", {}))

    ys = sorted(block.center[1] for block in ports)
    assert ys[1] - ys[0] == 40.0 and ys[2] - ys[1] == 40.0, \
        "стопка порт-блоков разъехалась выравниванием"
    assert "Стопки порт-блоков" in text


@pytest.mark.anyio
async def test_layout_place_bare_reports_unknown_links(monkeypatch):
    """Пустой реестр связей — сказано в ответе, а не умолчано (находка ревью).

    У проекта, открытого из файла, концы линий через COM не читаются: без
    предупреждения «расставлено» читалось бы как «расставлено со связями».
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    _install_wire_project(monkeypatch, {"k_0": first, "kx_0": second})

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "реестр `connect` пуст" in text
    assert "связи не учтены" in text


@pytest.mark.anyio
async def test_layout_place_aligns_by_exported_pairs(monkeypatch):
    """Открытый проект: выравнивание работает и по связям из выгрузки (#33).

    Реестр `connect` пуст, концов чужих линий через COM нет; но адреса
    выгрузки (`src = "k_0:out:0"`, `dst = "kx_0:in:0"`) несут индексы портов —
    приёмник выравнивается по выходу источника так же, как для связи сессии.
    Пока такой пары не было, приёмники открытого проекта оставались не
    выровненными (ограничение `layout_place`).
    """
    from simintech_mcp.tools import model_text

    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    dst.in_port_offset = 8.0
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    # Линия на странице (для полного числа в метрике) — в реестр сессии она
    # не попадает: проект «открытый», концы даёт выгрузка.
    src.wires.append((_FakeWire(9), dst, 0, 0))
    monkeypatch.setattr(model_text, "page_export_text", lambda: (
        '  MBTYWire: (\n'
        '    type = "wire",\n'
        '    src = "k_0:out:0",\n'
        '    dst = "kx_0:in:0"\n'
        '  ),\n', False, None, None))

    text = _text(await mcp.call_tool("layout_place", {}))

    assert dst.center[1] == src.center[1] + 8.0, \
        "вход приёмника не выровнен по выходу источника из выгрузки"
    assert dst.center[0] > src.center[0], "связь из выгрузки не учтена"
    assert "учтены связи открытого проекта" in text
    # Метрика идёт по тем же парам, что выравнивание: строка есть и без
    # реестра `connect` (follow-up PR #92 после мержа #91).
    assert "Метрики маршрутов (известные связи — 1; линий на странице — 1)" in text


@pytest.mark.anyio
async def test_layout_place_reports_routing_metrics(monkeypatch):
    """Метрики маршрутов — по известным связям, остальные честно вне метрики.

    Числа считает `audit_routing_segments` (та же, что у `audit_routing`), но
    только по связям, чьи концы известны: у чужих линий концов через COM нет.
    Линии без известных концов названы числом, а не выданы за проверенные.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "Метрики маршрутов (известные связи — 1; линий на странице — 1)" in text
    assert "канал теснее разреза — 0" in text
    assert "Все линии страницы — `audit_routing`" in text


@pytest.mark.anyio
async def test_layout_place_bare_tolerates_comma_in_block_name(monkeypatch):
    """Имя блока с запятой не срывает вызов «расставь всё» (находка ревью).

    Прежде связи сериализовались в строку `src->dst` с обратным разбором по
    запятой: переименованный в GUI блок `kx,0` ломал рекомендуемый вызов.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx,0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "kx,0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx,0"})

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "Расставлено блоков: 2" in text
    assert dst.center[0] > src.center[0], "связь не учтена: блоки в один слой"


@pytest.mark.anyio
async def test_layout_place_skips_gui_text_label(monkeypatch):
    """`TextLabel` из GUI — тоже не блок: исключается из расстановки (ревью).

    Библиотечный набор `NON_BLOCK_CLASSES` шире замеренного `constLabel`:
    подпись, нарисованная в GUI, — это `TextLabel` (или «Комментарий»).
    """
    block = _PlacedBlock("k_0", 1)
    label = _PlacedBlock("TextLabel7", 2, class_name="TextLabel")
    _install_wire_project(monkeypatch, {"k_0": block, "TextLabel7": label})

    text = _text(await mcp.call_tool("layout_place", {}))

    assert "Расставлено блоков: 1" in text
    assert label.center is None, "подпись TextLabel подвинули как блок"
    assert "Подписи (не блоки) не расставляются: 1" in text


@pytest.mark.anyio
async def test_set_block_center_moves_block_and_reports(monkeypatch):
    """set_block_center двигает блок в заданный центр и называет переход.

    Инструмент точечный — «раскладка под пины» собирается из таких ходов
    (запрос fdd002 04.10.2026): центр читается из `Points`, страница не
    пересобирается.
    """
    block = _PlacedBlock("k_0", 1)
    _install_fake_project(monkeypatch, {"k_0": block})

    text = _text(await mcp.call_tool("set_block_center",
                                     {"block": "k_0", "x": 200, "y": 100}))

    assert block.center == (200.0, 100.0), "координаты не применены"
    assert "(0, 0) → (200, 100)" in text, "переход центра не назван"
    assert "Линий связи на странице нет" in text
    assert session.current_project().repaints == 1, \
        "перерисовка обязана идти после перемещения"


@pytest.mark.anyio
async def test_set_block_center_retraces_wires_after_move(monkeypatch):
    """После сдвига — перерисовка и трассировка: порядок как в layout_place.

    Без перерисовки среда проложила бы провода по прежним прямоугольникам
    блоков (живой замер 15.09.2026) — линии уезжали бы мимо новых портов.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    events = _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})
    wire = src.wires[0][0]

    text = _text(await mcp.call_tool("set_block_center",
                                     {"block": "kx_0", "x": 400, "y": 200}))

    assert dst.center == (400.0, 200.0)
    assert events == [("repaint", None), ("normalize", 1)], \
        "порядок обязателен: перерисовка до трассировки"
    assert wire.normalized == 1, "линия не перетрассирована"
    assert "нормализовано 1" in text


@pytest.mark.anyio
async def test_set_block_center_batch_moves_in_one_pass(monkeypatch):
    """Пачка двигает все блоки одним ходом: одна перерисовка на всех.

    Раскладка «под пины» из десятков блоков — один вызов (запрос fdd002
    04.10.2026); по одному вызову это были бы десятки промежуточных
    перетрассировок.
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    _install_fake_project(monkeypatch, {"k_0": first, "kx_0": second})

    text = _text(await mcp.call_tool(
        "set_block_center", {"moves": "k_0=200,100; kx_0=400,100"}))

    assert first.center == (200.0, 100.0)
    assert second.center == (400.0, 100.0)
    assert "Перемещено блоков: 2" in text
    assert session.current_project().repaints == 1, \
        "пачка — один ход, а не перерисовка на каждый блок"
    assert "Наложений блоков нет" in text


@pytest.mark.anyio
async def test_set_block_center_batch_reports_overlap(monkeypatch):
    """Наложение среди подвинутых видно в ответе: 60×40 при шаге 20 пересекутся."""
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    _install_fake_project(monkeypatch, {"k_0": first, "kx_0": second})

    text = _text(await mcp.call_tool(
        "set_block_center", {"moves": "k_0=100,100; kx_0=120,100"}))

    assert "ВНИМАНИЕ: наложения блоков" in text
    assert "k_0—kx_0" in text


@pytest.mark.anyio
async def test_set_block_center_batch_refuses_before_moving(monkeypatch):
    """Неизвестное имя — отказ ДО перемещений: ни один блок не двинут.

    Частичное применение оставило бы схему в состоянии, которого нет в
    ответе: часть блоков уехала бы, а ответ был бы отказом.
    """
    first = _PlacedBlock("k_0", 1)
    _install_fake_project(monkeypatch, {"k_0": first})

    text = await _error("set_block_center",
                        {"moves": "k_0=200,100; нет_такого=1,1"})

    assert "не найдены" in text and "нет_такого" in text
    assert first.center is None, "частичного перемещения быть не должно"


@pytest.mark.anyio
async def test_set_block_center_rejects_both_forms(monkeypatch):
    """Одиночная форма и пачка не сочетаются — отказ, а не тихий выбор одной."""
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = await _error("set_block_center",
                        {"block": "k_0", "x": 1, "y": 1,
                         "moves": "k_0=2,2"})

    assert "не сочетается" in text


@pytest.mark.anyio
async def test_set_block_center_refuses_ambiguous_name(monkeypatch):
    """Имя у нескольких блоков — отказ до перемещения, а не выбор одного.

    Пару «В память»/«Из памяти» называют одним именем ячейки (живой случай
    05.10.2026): словарь по имени молча оставил бы один блок, и ответ не
    сказал бы, какой именно сдвинут. Id различает блоки — по нему адресация
    работает.
    """
    first = _PlacedBlock("#m1", 12)
    second = _PlacedBlock("#m1", 13)
    _install_fake_project(monkeypatch, {"first": first, "second": second})

    text = await _error("set_block_center",
                        {"block": "#m1", "x": 200, "y": 100})

    assert "несколько блоков" in text and "12" in text and "13" in text
    assert first.center is None and second.center is None, \
        "отказ обязан быть до перемещения"

    _text(await mcp.call_tool("set_block_center",
                              {"block": "13", "x": 200, "y": 100}))
    assert second.center == (200.0, 100.0), "id обязан различать пару"
    assert first.center is None


def test_parse_moves_rejects_bad_tokens():
    """Разбор пачки строгий: битый токен и повтор имени — отказ, не пропуск."""
    from simintech_mcp.tools.layout import _parse_moves

    assert _parse_moves("k_0=200,100; kx_0=400,120") == \
        [("k_0", 200.0, 100.0), ("kx_0", 400.0, 120.0)]
    with pytest.raises(ToolError):
        _parse_moves("k_0=200")
    # Смешанная пачка — именно она ловит «битый токен молча пропущен»:
    # проверка на «пачка пуста» такую мутацию не замечает (все токены битые
    # дают тот же отказ, а валидный+битый — тихую потерю одного блока).
    with pytest.raises(ToolError):
        _parse_moves("k_0=1,1; kx_0=двести,100")
    with pytest.raises(ToolError):
        _parse_moves("k_0=1,1; k_0=2,2")
    with pytest.raises(ToolError):
        _parse_moves("k_0=nan,1")


def test_parse_moves_refuses_oversized_batch():
    """Пачка сверх предела отвергается до COM: каждый токен — вызовы.

    Предел обязателен у параметра, задающего число COM-вызовов (CLAUDE.md,
    инвариант о пределах): иначе тысячи токенов займут единственный COM-поток
    и вызов не прервётся таймаутом.
    """
    from simintech_mcp.tools.layout import MAX_MOVES, _parse_moves

    at_limit = "; ".join(f"k_{i}=0,0" for i in range(MAX_MOVES))
    assert len(_parse_moves(at_limit)) == MAX_MOVES
    with pytest.raises(ToolError):
        _parse_moves(at_limit + "; k_end=0,0")


def test_wires_word_agrees_with_count():
    """Слово при числе согласуется: «1 линия», «2 линии», «5 линий»."""
    from simintech_mcp.tools.layout import _wires_word

    assert _wires_word(1) == "линия"
    assert _wires_word(2) == "линии"
    assert _wires_word(5) == "линий"
    assert _wires_word(21) == "линия"
    assert _wires_word(112) == "линий"
    assert _wires_word(14) == "линий"


def test_fit_geometry_puts_frame_margins_on_both_sides():
    """Формула кадра: края рамки ложатся на поля, центр — в центр полотна.

    Живой замер 05.10.2026: габарит модели на снимке лёг в 62..965 из 1026 при
    полях 6% — то есть формула воспроизводит именно то, что видно глазами.
    """
    from simintech_mcp.tools.layout import (
        CANVAS_W, FIT_PADDING, fit_geometry,
    )

    frame = (48.0, 48.0, 560.0, 112.0)
    scale, view_x, _view_y = fit_geometry(frame)

    assert abs(48.0 * scale + view_x - CANVAS_W * FIT_PADDING) < 1e-6
    assert 560.0 * scale + view_x <= CANVAS_W * (1.0 - FIT_PADDING) + 1e-6
    assert abs(304.0 * scale + view_x - CANVAS_W / 2.0) < 1e-6


def test_fit_geometry_scale_is_limited_by_the_tighter_side():
    """Масштаб выбирает более тесная сторона, а не среднее: модель обязана войти.

    Широкая рамка упирается в ширину полотна, высокая — в высоту; растянуть
    модель нельзя, иначе стороны разъедутся.
    """
    from simintech_mcp.tools.layout import (
        CANVAS_H, CANVAS_W, FIT_PADDING, fit_geometry,
    )

    wide, _, _ = fit_geometry((0.0, 0.0, 1000.0, 10.0))
    tall, _, _ = fit_geometry((0.0, 0.0, 10.0, 1000.0))

    assert abs(wide - CANVAS_W * (1.0 - 2.0 * FIT_PADDING) / 1000.0) < 1e-9
    assert abs(tall - CANVAS_H * (1.0 - 2.0 * FIT_PADDING) / 1000.0) < 1e-9


def test_fit_geometry_survives_degenerate_frame():
    """Нулевая рамка не делит на ноль: масштаб остаётся конечным и положительным.

    Так выглядит страница с одним блоком без читаемого размера или пустая:
    подгонка не должна падать и не должна выдавать бесконечность.
    """
    import math

    from simintech_mcp.tools.layout import fit_geometry

    scale, view_x, view_y = fit_geometry((10.0, 10.0, 10.0, 10.0))

    assert math.isfinite(scale) and math.isfinite(view_x) and math.isfinite(view_y)
    assert scale > 0


def test_fit_geometry_uses_given_canvas_height():
    """Полотно — параметр подгонки: у среды встречается и 580, и 659.

    Живой замер 05.10.2026: ширина снимка всегда 1026, а высота гуляет — 580
    у тринадцати снимков и 659 у семи. Кадр обязан считаться по фактическому
    полотну, иначе центр рамки ложится не на середину и поля разъезжаются.
    """
    from simintech_mcp.tools.layout import fit_geometry

    scale, view_x, view_y = fit_geometry((0.0, 0.0, 512.0, 64.0),
                                         canvas_w=1026.0, canvas_h=659.0)

    assert abs(256.0 * scale + view_x - 513.0) < 1e-6
    assert abs(32.0 * scale + view_y - 659.0 / 2.0) < 1e-6
