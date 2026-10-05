"""Чистая геометрия: габариты блоков, их пересечения и сегменты линий.

Живёт отдельным модулем, а не внутри `tools/layout.py`: этими функциями
пользуются и расстановка (`layout_place` — метрика наложений), и проверка
оформления (`check_model_layout`), и аудит маршрутов (`audit_routing`).
Разбор строки `Points` здесь один: формат — свойство SimInTech, и второй его
разбор рано или поздно разошёлся бы с первым.

Сегментная часть — язык проверки читаемости: среда промежуточные точки
линий не отдаёт (замер 02.10.2026), поэтому линия проверяется **по
предсказанию** той формы, которой её рисует `NormalizeWire`
(`predicted_polyline`), а счётчики (`segment_hits_rect`,
`collinear_overlap`, `proper_crossing`) отвечают на вопросы «бьёт ли линия
блок», «делят ли две линии один трек», «пересекаются ли линии».
"""

from __future__ import annotations

import re

#: Точка в строке `Points` — «(x , y)» (пробелы вокруг запятой ставит среда).
_POINT_RE = re.compile(r"\(([-\d.]+)\s*,\s*([-\d.]+)\)")

#: Допуск «та же координата»: среда считает в целых пикселях, а язык печатает
#: точки без дробной части; полпикселя отделяют «лежит на прямой» от «рядом».
_EPS = 0.5

#: Шаг трека — квадратик разметки (стандарт оформления 02.10.2026): треки
#: линий кладутся кратно ему. Мера «сколько связей влезает в канал» и порог
#: «два сегмента делят трек».
WIRE_PITCH = 8.0

#: Вылет из порта: линия не должна начинаться в габарите блока. Те же 16 px,
#: что у портов при стандартной разметке (порт стоит в `cx±16`).
STUB = 16.0


def _ortho_orientation(p: "tuple[float, float]", q: "tuple[float, float]") -> \
        "str | None":
    """Ориентация сегмента: «h» горизонтальный, «v» вертикальный, иначе None."""
    if abs(p[1] - q[1]) < _EPS:
        return "h"
    if abs(p[0] - q[0]) < _EPS:
        return "v"
    return None


def _axis_interval(p0: float, p1: float, lo: float, hi: float) -> \
        "tuple[float, float] | None":
    """Интервал параметра t∈[0,1], где координата строго внутри (lo, hi).

    None — координата внутри интервала не бывает. Открытость важна: линия
    по самой границе габарита не считается попаданием внутрь.
    """
    delta = p1 - p0
    if delta == 0:
        return (0.0, 1.0) if lo < p0 < hi else None
    t_lo, t_hi = (lo - p0) / delta, (hi - p0) / delta
    if t_lo > t_hi:
        t_lo, t_hi = t_hi, t_lo
    t_lo, t_hi = max(0.0, t_lo), min(1.0, t_hi)
    if t_lo >= t_hi:
        return None
    return (t_lo, t_hi)


def segment_hits_rect(a: "tuple[float, float]", b: "tuple[float, float]",
                      rect: "tuple[float, float, float, float]") -> bool:
    """Проходит ли отрезок через внутренность прямоугольника.

    Касание грани и проход по самой границе — не попадание: порт-блок стоит
    вплотную к соседям, и «линия по краю» — обычная разметка, а не дефект.
    """
    left, top, right, bottom = rect
    tx = _axis_interval(a[0], b[0], left, right)
    if tx is None:
        return False
    ty = _axis_interval(a[1], b[1], top, bottom)
    if ty is None:
        return False
    return max(tx[0], ty[0]) < min(tx[1], ty[1])


def collinear_overlap(a1: "tuple[float, float]", a2: "tuple[float, float]",
                      b1: "tuple[float, float]", b2: "tuple[float, float]",
                      pitch: float = WIRE_PITCH) -> bool:
    """Лежат ли два отрезка на одном треке с перекрытием длиннее `pitch`.

    Трек — общая горизонталь или вертикаль. Короткое наложение (до шага
    трека) — стык в углу, он неизбежен; длинное — две линии едут по одной
    колее и на схеме неразличимы.
    """
    for axis in (0, 1):
        other = 1 - axis
        if abs(a1[other] - a2[other]) >= _EPS \
                or abs(b1[other] - b2[other]) >= _EPS \
                or abs(a1[other] - b1[other]) >= _EPS:
            continue
        lo = max(min(a1[axis], a2[axis]), min(b1[axis], b2[axis]))
        hi = min(max(a1[axis], a2[axis]), max(b1[axis], b2[axis]))
        if hi - lo > pitch:
            return True
    return False


def proper_crossing(a1: "tuple[float, float]", a2: "tuple[float, float]",
                    b1: "tuple[float, float]", b2: "tuple[float, float]") -> bool:
    """Пересекаются ли отрезки внутренностями (T-стык и общий конец — нет).

    Считаются только ортогональные пары (горизонталь × вертикаль): аудит
    работает по предсказанным ортогоналям.
    """
    oa, ob = _ortho_orientation(a1, a2), _ortho_orientation(b1, b2)
    if (oa, ob) == ("h", "v"):
        horizontal, vertical = (a1, a2), (b1, b2)
    elif (oa, ob) == ("v", "h"):
        horizontal, vertical = (b1, b2), (a1, a2)
    else:
        return False
    hy = horizontal[0][1]
    hx_lo, hx_hi = sorted((horizontal[0][0], horizontal[1][0]))
    vx = vertical[0][0]
    vy_lo, vy_hi = sorted((vertical[0][1], vertical[1][1]))
    return hx_lo < vx < hx_hi and vy_lo < hy < vy_hi


def segments_of(points: "list[tuple[float, float]]") -> \
        "list[tuple[tuple[float, float], tuple[float, float]]]":
    """Отрезки полилинии — парами соседних вершин (точки-дубли пропускаются)."""
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for first, second in zip(points, points[1:]):
        if first != second:
            segments.append((first, second))
    return segments


def predicted_polyline(src: "tuple[float, float]",
                       dst: "tuple[float, float]", channel_x: float,
                       *, stub: float = STUB) -> \
        "list[tuple[float, float]] | None":
    """Ортогональ, которой среда соединит два порта (предсказание).

    Среда промежуточных точек линии не отдаёт (замер 02.10.2026), поэтому
    «ударит ли линия в блок» проверяется по предсказанию той же формы,
    которой её рисует `NormalizeWire` (ортогонализатор среды): вылет из
    порта на `stub`, горизонталь до середины канала, вертикаль до Y
    приёмника, заход в порт. У портов на одной горизонтали линия прямая.

    `src` — выход источника (сторона RIGHT), `dst` — вход приёмника (LEFT).
    При `dst` левее `src` (обратная связь) форма **не предсказывается** —
    `None`: обратные связи среда ведёт своим маршрутом, и подставлять догадку
    вместо него нельзя. `channel_x` — середина свободного зазора между
    габаритами (её считает вызывающий, знающий габариты концов).
    """
    (x0, y0), (x1, y1) = src, dst
    if x1 <= x0:
        return None
    if abs(y0 - y1) < _EPS:
        return [(x0, y0), (x1, y1)]
    return [(x0, y0), (x0 + stub, y0), (channel_x, y0),
            (channel_x, y1), (x1, y1)]


def rect_of(points_text: str,
            size: "tuple[float, float]") -> "tuple[float, float, float, float] | None":
    """Габарит блока: центр из `Points` ± половина размера.

    Живой замер 02.10.2026: `Points` — **не контур блока**, и min/max его
    точек габаритом не является («Константа» 32×16 даёт полилинию 16×32).
    Первая точка полилинии — центр блока (совпадает с `set_center` до
    десятых, а при создании — с «левый верхний + половина размера»), вторая —
    выходной порт (центр + (16, 0)). Поэтому габарит строится из центра и
    `get_size`, а не из размаха точек. `None` — свойство пусто/не разбирается
    или размер недоступен: вызывающий обязан назвать такой блок.
    """
    pairs = _POINT_RE.findall(points_text or "")
    if not pairs:
        return None
    cx, cy = float(pairs[0][0]), float(pairs[0][1])
    w, h = size
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def overlaps(rect_a: "tuple[float, float, float, float]",
             rect_b: "tuple[float, float, float, float]") -> bool:
    """Пересекаются ли прямоугольники (строго; касание — не наложение)."""
    return (rect_a[0] < rect_b[2] and rect_b[0] < rect_a[2]
            and rect_a[1] < rect_b[3] and rect_b[1] < rect_a[3])


#: Канал между колонками считается от мощности разреза (ТЗ 4.2, «канал шире
#: разреза»): `channel_w = STUB + WIRE_PITCH * max(1, cut)`. Имени `cut_size`
#: в поставке SimInTech нет (сплошной поиск 05.10.2026) — это наша метрика
#: слоистой укладки, а не вендорское свойство.
def channel_width(cut: int, *, stub: float = STUB,
                  pitch: float = WIRE_PITCH) -> float:
    """Ширина канала между колонками (ТЗ 4.2).

    `cut` — мощность разреза между парой колонок. Даже при нуле связей канал
    не схлопывается: в нём остаётся место хотя бы под один трек.
    """
    return stub + pitch * max(1, cut)


def cut_sizes(nets: "list[tuple[int, int, bool]]", gaps: int) -> "list[int]":
    """Мощность разреза для каждого зазора между колонками (ТЗ 4.2).

    `nets` — по связи: (колонка источника, колонка приёмника, выровнена).
    Связь, выровненная в одну горизонталь, трека на разрезе не занимает и в
    мощность не входит. Для зазора `i` считаются связи с источником в колонке
    `<= i` и приёмником в колонке `>= i + 1` — те, что вынуждены идти через
    зазор. Обратные связи (приёмник не правее источника) сюда не попадают: по
    ТЗ 4.3 им место в отдельном нижнем канале, а не в слое.
    """
    return [
        sum(1 for src, dst, aligned in nets
            if not aligned and src <= gap < dst)
        for gap in range(gaps)
    ]
