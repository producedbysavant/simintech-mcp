"""Чистые помощники аудита маршрутов линий, вынесенные из `check_model`.

Здесь живёт расчёт читаемости маршрутов: колонки слоистой укладки, каналы
связей, предсказанные полилинии, пересечения, общий трек, попадания в чужие
габариты, порядок входов и перегрузка узлов. Модуль не трогает COM и контур:
на вход идут габариты блоков и концы линий, на выходе `RoutingProblems`.
Инструменты `check_model_layout` и `audit_routing` остались в `check_model`;
имена отсюда реэкспортируются там, чтобы прежние точки импорта работали.

Вынос механический: тела функций перенесены без изменения поведения.
"""

from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from ..geometry import (STUB, WIRE_PITCH, channel_width, collinear_overlap,
                        cut_sizes, predicted_polyline, proper_crossing,
                        segment_hits_rect, segments_of)
from ..rules import MAX_NODE_LINES


#: Допуск «конец линии стоит на границе габарита»: вход блока стоит в `cx-16`,
#: а координату линии среда отдаёт округлённой.
PORT_BAND = 8.0

#: Допуск колонки: центры X блоков в пределах одного квадратика разметки -
#: одна колонка. Планов раскладки у аудита нет: колонки берутся из
#: фактической геометрии, а ручная доводка и среда дают доли пикселя -
#: точное равенство центров было бы слишком строгим.
COLUMN_TOLERANCE = WIRE_PITCH

#: Допуск «связь выровнена»: Y конца у источника и Y конца у приёмника
#: совпадают (ТЗ 4.1). Оба конца - фактические, из контура; полпикселя:
#: среда считает в целых пикселях.
ALIGN_TOLERANCE = 0.5


_POINT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([+-])\s*(\d+(?:\.\d+)?)i")


def parse_point(text: str) -> Optional[Tuple[float, float]]:
    """Разобрать точку в форме «x+yi» (так печатает её строка языка).

    Скобки и пробелы вокруг точки допускаются: живой формат печати ещё не
    замерялся, а «(16-56i)» и «16-56i» - одна и та же точка.
    """
    match = _POINT_RE.search(text)
    if not match:
        return None
    x = float(match.group(1))
    y = float(match.group(3))
    if match.group(2) == "-":
        y = -y
    return (x, y)


class RoutingProblems(NamedTuple):
    """Что нашёл аудит маршрутов - чистая часть, без COM и контура."""

    #: Пары линий, отрезки которых пересекаются внутренностями.
    crossings: List[Tuple[int, int]]
    #: Пары линий, отрезки которых едут по одному треку с перекрытием.
    coincident: List[Tuple[int, int]]
    #: Линии, проходящие через внутренность чужого габарита: (линия, блок).
    block_hits: List[Tuple[int, str]]
    #: Блоки, к входам которых источники приходят в обратном порядке.
    port_order: List[str]
    #: Линии, маршрут которых не предсказывается (обратные связи).
    unchecked: List[int]
    #: Почему линия не проверена: обратная / внутриколоночная / нет колонок /
    #: конец не привязан / приёмник не правее источника.
    unchecked_reasons: Dict[int, str]
    #: Зазоры между колонками, чей канал теснее разреза (ТЗ 4.2):
    #: (зазор, cut, связей, фактический зазор, нужный канал).
    channel_overflow: List[Tuple[int, int, int, float, float]]


def inside(rect: "tuple[float, float, float, float]",
           point: "tuple[float, float]") -> bool:
    """Точка внутри габарита (строго; на границе - нет)."""
    return rect[0] < point[0] < rect[2] and rect[1] < point[1] < rect[3]


def port_order_violations(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]"
) -> List[str]:
    """Блоки, к входам которых источники приходят в обратном порядке.

    Для каждого габарита берутся концы линий, стоящие на его левой границе,
    - это входные порты. Порядок портов читается по Y, порядок источников -
    по Y начал тех же линий. Монотонность значит, что линии войдут в порты
    без креста; инверсия - что они пересекутся у самой стены блока.
    """
    violations: List[str] = []
    for name, rect in rects:
        incoming: List[Tuple[float, float]] = []
        for _wire_id, (start, end) in wires.items():
            if abs(end[0] - rect[0]) > PORT_BAND:
                continue
            if not (rect[1] - PORT_BAND <= end[1] <= rect[3] + PORT_BAND):
                continue
            incoming.append((end[1], start[1]))
        if len(incoming) < 2:
            continue
        incoming.sort()
        sources = [source for _port, source in incoming]
        # Инверсия (нижний источник в верхний порт) - крест у стены:
        # порядок источников обязан идти по Y так же, как порядок входов.
        if sources != sorted(sources):
            violations.append(name)
    return violations


def node_overflow(
        nodes: "Dict[Tuple[int, int], List[int]]"
) -> "List[Tuple[int, int, List[int]]]":
    """Узлы, где сходится больше `MAX_NODE_LINES` линий: (родитель, узел, ветви).

    В узле сходятся родительская линия и её ветви; `nodes` держит только
    ветви, поэтому линий в узле - `1 + len(branches)`: три ветви от одной
    точки - это родитель и три ветви, четыре линии, больше трёх (живой замер
    08.10.2026 - три `connect_branch` от одного выхода).

    «Наклонные подводы» и «врезка ветви в середину сегмента» этим счётом не
    ловятся: маршрут родителя среда не отдаёт, а наклонные лучи «звезды» -
    свойство отрисовки перегруженного узла, не данных (замер 07.10.2026) -
    и текст вердикта этого не утверждает.
    """
    crowded: List[Tuple[int, int, List[int]]] = []
    for (parent, node), branches in sorted(nodes.items()):
        if 1 + len(branches) > MAX_NODE_LINES:
            crowded.append((parent, node, sorted(branches)))
    return crowded


def node_caption(parent: int, node: int, branches: List[int]) -> str:
    """Строка перегруженного узла для вердикта: родитель, точка, ветви."""
    shown = ", ".join(str(w) for w in branches[:5])
    more = f", и ещё {len(branches) - 5}" if len(branches) > 5 else ""
    return (f"узел линии {parent}, точка {node}: линий {1 + len(branches)} "
            f"(родитель и ветви {shown}{more})")


def columns(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]"
) -> "Tuple[Dict[str, int], Dict[int, float], Dict[int, float]]":
    """Колонки слоистой укладки - из фактической геометрии блоков.

    Колонка - группа центров X в пределах `COLUMN_TOLERANCE` (один
    квадратик разметки): планов раскладки у аудита нет, а равенство
    центров было бы строже самой среды. Меньше двух колонок - колонок
    нет: аудит откатывается на аварийный путь, а не выдумывает канал.
    """
    if not rects:
        return {}, {}, {}
    centers = {(rect[0] + rect[2]) / 2.0 for _name, rect in rects}
    anchors: List[float] = []
    for center in sorted(centers):
        if anchors and center - anchors[-1] <= COLUMN_TOLERANCE:
            continue
        anchors.append(center)
    if len(anchors) < 2:
        return {}, {}, {}
    index_of = {anchor: i for i, anchor in enumerate(anchors)}
    column_of: Dict[str, int] = {}
    lefts: Dict[int, float] = {}
    rights: Dict[int, float] = {}
    for name, rect in rects:
        center = (rect[0] + rect[2]) / 2.0
        anchor = min(anchors, key=lambda value: abs(value - center))
        column = index_of[anchor]
        column_of[name] = column
        lefts[column] = min(lefts.get(column, rect[0]), rect[0])
        rights[column] = max(rights.get(column, rect[2]), rect[2])
    return column_of, lefts, rights


def column_at(
        point: "Tuple[float, float]",
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        column_of: "Dict[str, int]"
) -> "Optional[int]":
    """Колонка блока, к которому точка ближе всего (в пределах полосы порта).

    Конец линии стоит на границе блока, поэтому «ближайший габарит» и есть
    владелец порта; дальше полосы `PORT_BAND` связь не привязывается -
    честнее пропустить её, чем приписать чужой колонке.
    """
    best: "Optional[str]" = None
    best_gap: "Optional[float]" = None
    for name, rect in rects:
        gap = (max(rect[0] - point[0], 0.0, point[0] - rect[2])
               + max(rect[1] - point[1], 0.0, point[1] - rect[3]))
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, name
    if best is None or best_gap is None or best_gap > PORT_BAND:
        return None
    return column_of[best]


def channel_overflow(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]"
) -> "List[Tuple[int, int, int, float, float]]":
    """Зазоры, чей канал теснее разреза (ТЗ 4.2).

    По зазору между колонками: `(номер, cut, связей, зазор, нужно)`. `cut` -
    мощность разреза (`cut_sizes`), `связей` - сколько линий фактически идёт
    через зазор, `зазор` - свободное место между колонками, `нужно` -
    `channel_width(cut)`. Тесно, если фактика меньше канона. Обратные связи в
    разрез не входят: по ТЗ 4.3 им место в отдельном нижнем канале.
    """
    column_of, lefts, rights = columns(rects)
    if not column_of:
        return []
    gaps = max(lefts) + 1
    nets: List[Tuple[int, int, bool]] = []
    actual: Dict[int, int] = {}
    for wire_id in sorted(wires):
        start, end = wires[wire_id]
        src = column_at(start, rects, column_of)
        dst = column_at(end, rects, column_of)
        if src is None or dst is None:
            continue
        nets.append((src, dst, abs(start[1] - end[1]) < ALIGN_TOLERANCE))
        for gap in range(src, dst):
            actual[gap] = actual.get(gap, 0) + 1
    cut = cut_sizes(nets, gaps)
    found: "List[Tuple[int, int, int, float, float]]" = []
    for gap in range(gaps - 1):
        free = lefts.get(gap + 1, 0.0) - rights.get(gap, 0.0)
        need = channel_width(cut[gap])
        if free < need - 0.5:
            found.append((gap, cut[gap], actual.get(gap, 0), free, need))
    return found


def wire_channels(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]",
        column_of: "Dict[str, int]", lefts: "Dict[int, float]",
        rights: "Dict[int, float]"
) -> "Tuple[Dict[int, float | None], Dict[int, str]]":
    """X канала каждой связи - её трек в зазоре между колонками (ТЗ 4.2).

    Порядок треков детерминирован: связи зазора, которым трек нужен (не
    выровненные в одну горизонталь), сортируются по Y приёмника, при равенстве
    - по Y источника, и получают X = правая граница левой колонки +
    `STUB + (k + 0.5) * WIRE_PITCH`. STUB - первым слагаемым канона
    (`channel_w = STUB + WIRE_PITCH * cut`, ТЗ 4.2): вылет из порта в
    `predicted_polyline` остаётся левее трека, и линия не делает петлю назад.
    Без этого слагаемого трек ложился **ближе** вылета и полилиния шла
    вбок-назад (числовая проверка 05.10.2026: треки 36/44 против вылета до 48).

    **Дальняя связь (через две и более колонок) - «два плеча» с вертикалью у
    середины пролёта.** Живые снимки 08.10.2026 (k0 (0,0) → kx1 (1200,400),
    блок-препятствие в средней колонке): среда ведёт вертикаль у
    `(x_src + x_dst) / 2` и препятствий не обходит - при сдвиге блока
    вертикаль оставалась на месте и по X (300 → 900), и по Y (кейс
    «капризной дальней связи», #94). Прежнее «трек первого зазора» на
    занятом створе давало ложный `readable`: вертикаль шла сквозь габарит,
    а предсказание - мимо него. Канон канала (ТЗ 4.2) к дальним связям не
    применяется: это **модель**, проверяемая снимком (`save_screenshot`).

    **Предсказание, не замер.** Как среда укладывает треки внутри канала, мы не
    мерили и померить не можем (промежуточные точки линии среда не отдаёт),
    поэтому порядок - модель. Проверка - снимком (`save_screenshot`):
    предсказанные треки обязаны визуально совпасть с тем, как линии лежат в
    канале; если снимок покажет другой порядок (например, среда кладёт от
    краёв к центру) - порядок уточняется по нему.

    `None` - канон канала не даёт: меньше двух колонок, конец не привязан к
    блоку, связь обратная или внутриколоночная. Такие линии уходят в «не
    проверено»: выдуманная середина между концами дала бы ложные метрики.
    """
    channels: "Dict[int, float | None]" = {wid: None for wid in wires}
    reasons: "Dict[int, str]" = {wid: "нет колонок" for wid in wires}
    if not column_of:
        return channels, reasons
    placement: "Dict[int, Tuple[int, int, float, float, bool]]" = {}
    for wire_id in sorted(wires):
        start, end = wires[wire_id]
        src = column_at(start, rects, column_of)
        dst = column_at(end, rects, column_of)
        if src is None or dst is None:
            reasons[wire_id] = "конец не привязан"
            continue
        if dst < src:
            reasons[wire_id] = "обратная"
            continue
        if dst == src:
            reasons[wire_id] = "внутриколоночная"
            continue
        aligned = abs(start[1] - end[1]) < ALIGN_TOLERANCE
        placement[wire_id] = (src, dst, end[1], start[1], aligned)
        reasons.pop(wire_id, None)
    for gap in range(max(lefts) if lefts else 0):
        members = [wid for wid, (src, dst, _ey, _sy, aligned)
                   in placement.items()
                   if not aligned and src <= gap < dst]
        members.sort(key=lambda wid: (placement[wid][2], placement[wid][3]))
        for index, wire_id in enumerate(members):
            if gap == placement[wire_id][0]:
                channels[wire_id] = (rights[gap] + STUB
                                     + (index + 0.5) * WIRE_PITCH)
    # Дальняя связь перекрывает трек первого зазора: вертикаль у середины
    # пролёта (замеры выше). Перезапись - после цикла зазоров, чтобы
    # одиночные связи канона не изменились; выровненные остаются прямыми
    # (ветка ниже их не трогает).
    for wire_id, (src_col, dst_col, _ey, _sy, aligned) in placement.items():
        if not aligned and dst_col - src_col >= 2:
            start, end = wires[wire_id]
            channels[wire_id] = (start[0] + end[0]) / 2.0
    # Выровненной связи трек не нужен: её форма - прямая, канал не
    # задействован. Но канон канал ей даёт, поэтому она проверяема, а не
    # «не проверена».
    for wire_id, (src_col, _dst, _ey, _sy, aligned) in placement.items():
        if aligned:
            channels[wire_id] = rights[src_col]
    return channels, reasons


def audit_routing_segments(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]"
) -> RoutingProblems:
    """Посчитать проблемы маршрутов по предсказанным полилиниям.

    Чистая функция: ни COM, ни контура - потому её и проверяют тесты. Канал
    каждой связи - её трек в зазоре между колонками (`wire_channels`, ТЗ 4.2);
    линии, которым канон канала не даёт, честно уходят в `unchecked`.

    Обратная связь (приёмник левее источника) не предсказывается - маршрут
    ведёт среда, - и такие линии честно попадают в `unchecked`, а не в чистые.
    """
    column_of, lefts, rights = columns(rects)
    channels, reasons = wire_channels(
        rects, wires, column_of, lefts, rights)
    segments: List[Tuple[int, Tuple[float, float], Tuple[float, float]]] = []
    unchecked: List[int] = []
    unchecked_reasons: Dict[int, str] = {}
    for wire_id in sorted(wires):
        start, end = wires[wire_id]
        channel = channels.get(wire_id)
        if channel is None:
            unchecked.append(wire_id)
            unchecked_reasons[wire_id] = reasons.get(wire_id, "нет колонок")
            continue
        polyline = predicted_polyline(start, end, channel)
        if polyline is None:
            unchecked.append(wire_id)
            unchecked_reasons[wire_id] = "приёмник не правее источника"
            continue
        for first, second in segments_of(polyline):
            segments.append((wire_id, first, second))

    crossings: List[Tuple[int, int]] = []
    coincident: List[Tuple[int, int]] = []
    for index, (wire_a, a1, a2) in enumerate(segments):
        for wire_b, b1, b2 in segments[index + 1:]:
            if wire_a == wire_b:
                continue
            if proper_crossing(a1, a2, b1, b2):
                crossings.append((wire_a, wire_b))
            elif collinear_overlap(a1, a2, b1, b2, pitch=WIRE_PITCH):
                coincident.append((wire_a, wire_b))

    block_hits: List[Tuple[int, str]] = []
    for wire_id, first, second in segments:
        start, end = wires[wire_id]
        for name, rect in rects:
            # Из своего блока линия выходит: габарит с её концом - не чужой.
            if inside(rect, start) or inside(rect, end):
                continue
            if segment_hits_rect(first, second, rect):
                block_hits.append((wire_id, name))

    # Пара линий может совпасть не одним отрезком, а несколькими (стык и
    # горизонталь одной прямой): в списке она обязана стоять один раз -
    # дубли вскрыл живой прогон 05.10.2026.
    return RoutingProblems(sorted(set(crossings)), sorted(set(coincident)),
                           sorted(set(block_hits)),
                           port_order_violations(rects, wires), unchecked,
                           unchecked_reasons,
                           channel_overflow(rects, wires))


def parse_wire_report(
        text: str
) -> "Optional[Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]]":
    """Разобрать отчёт контура: концы линий. `None` - отчёт оборван.

    `DONE` - признак полного отчёта (его пишет `_check_script` последней
    строкой): без него часть `W`-строк могла не записаться, и вердикт по
    такому отчёту не выдаётся - та же защита, что у `check_model_layout`
    (находка ревью 02.10.2026). Строки, которые не разобрались, пропускаются:
    `DONE` подтверждает, что отчёт пришёл целиком, а число прочитанных линий
    называет сам вердикт.
    """
    wires: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]] = {}
    complete = False
    for line in text.splitlines():
        parts = line.split("|")
        if parts[0] == "DONE":
            complete = True
            continue
        if parts[0] != "W" or len(parts) != 4:
            continue
        start = parse_point(parts[2])
        end = parse_point(parts[3])
        if start is None or end is None:
            continue
        try:
            wire_id = int(parts[1])
        except ValueError:
            continue
        wires[wire_id] = (start, end)
    return wires if complete else None


def parse_parents(text: str) -> "Dict[int, int]":
    """Родитель каждой линии из `P`-строк отчёта (`getparentwireid`).

    У обычной линии 0, у ветви - id родительской. Общий вход и для вердикта
    о подключении, и для проверки узлов: `P`-строка одна на линию, второго
    вызова `getparentwireid` на неё не делается (находка ревью 08.10.2026 -
    #136 и #138 дублировали вызов).
    """
    parents: "Dict[int, int]" = {}
    for line in text.splitlines():
        parts = line.split("|")
        if parts[0] != "P" or len(parts) != 3:
            continue
        try:
            parents[int(parts[1])] = int(parts[2])
        except ValueError:
            continue
    return parents


def parse_node_branches(
        text: str,
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]",
        parents: "Dict[int, int]"
) -> "Dict[Tuple[int, int], List[int]]":
    """Ветви по узлам: (родитель, узел) → id ветвей.

    Родитель - из `P`-строк (`parse_parents`), узел - из `N`-строк
    (`getparentwirenodeindex`, у среды с единицы; живой замер 08.10.2026:
    три `connect_branch` от одного выхода получили узел 1 одного родителя).
    Пустой сегмент - пара, которую среда рождает вместе с ветвью (конец
    `0+0i`, замер 08.10.2026) - пропускается: он не линия в узле, и без
    этого счёт «сколько линий в узле» был бы завышен на него навсегда.
    Линия, чей конец не разобран (`W`-строки нет), тоже пропускается:
    отличить её пустой сегмент нечем.
    """
    nodes: "Dict[Tuple[int, int], List[int]]" = {}
    for line in text.splitlines():
        parts = line.split("|")
        if parts[0] != "N" or len(parts) != 3:
            continue
        try:
            wire_id = int(parts[1])
            node = int(parts[2])
        except ValueError:
            continue
        parent = parents.get(wire_id, 0)
        if parent == 0 or node == 0:
            continue
        ends = wires.get(wire_id)
        if ends is None or ends[1] == (0.0, 0.0):
            continue
        nodes.setdefault((parent, node), []).append(wire_id)
    return nodes
