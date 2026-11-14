"""Геометрия раскладки: габариты блоков, сетка и отчёт о наложениях.

Модуль выделен из `layout` без изменения поведения: здесь помощники,
которые читают `Points`/`get_size` и считают метрики по готовым габаритам.
Точки входа для соседних модулей: `first_point`, `model_frame`,
`overlap_report`.
"""

from __future__ import annotations

import re

from simintech_api import Block, Page
from simintech_api.catalog import NON_BLOCK_CLASSES


#: Длинное тире в текстах ответа: кодовая точка задана escape, чтобы файл не
#: содержал символа U+2014 (правило репозитория), а прежний вывод не менялся.
EM_DASH = "\N{EM DASH}"

#: Сколько пар наложений перечислять в ответе: список для человека, длинный
#: хвост обрезается (счётчик остаётся).
MAX_REPORTED_OVERLAPS = 10

#: Шаг разметки схемы: 1 квадратик = 8×8 px (стандарт оформления владельца:
#: блоки «сигнал»/«порт»/«константа» 16 высоты, «Ступенька» 32; порты
#: стоят в `cx±16, cy`, поэтому кратность 8 у центров даёт сетку и портам).
#: Не путать с `simintech_api.constants.GRID_SIZE = 20`: это шаг библиотечных
#: `Grid`/`Router`, которые MCP не использует. `LayeredPlacer` и
#: `snap_centers` держат шаг `WIRE_PITCH = 8`.
GRID_STEP = 8.0

#: Отступ раскладки от начала координат: блок с центром x=0 теряет левую
#: половину: она уходит в минус и на лист не попадает, и снимок показывает
#: обрезанные блоки (живой замер 04.10.2026: полотно 1026x580, схема прижата
#: к левому верхнему углу, два блока срезаны краем). Кратен шагу разметки,
#: чтобы сетка пережила сдвиг.
MARGIN = 48.0

#: Классы-«оформление»: это не блоки. Набор библиотечный
#: (`simintech_api.catalog.NON_BLOCK_CLASSES`: `TextLabel`, `RotatedText`,
#: «Комментарий», `Rectangle` и т. п.): живой замер 02.10.2026 вскрыл
#: `constLabel` (`Points` якорь текста, `size` типовая карточка 60×40,
#: портов нет), но подпись в GUI может быть и `TextLabel`, и прямоугольником:
#: своя константа из одного имени пропускала их в расстановку (находка ревью
#: 02.10.2026). Расстановке и метрике не подлежат: расставленный «как блок»
#: объект оформления уезжает от того, что помечает, и даёт ложные наложения.
LABEL_CLASSES = NON_BLOCK_CLASSES


def snap_centers(centers: dict[str, tuple[float, float]]) -> None:
    """Поставить центры блоков на разметку 8 px (и порты тоже на сетку).

    Выравнивание по портам после этого сетку сохраняет **не всегда**: смещение
    вход→выход разность координат портов; у блоков высотой 16/32 она кратна
    8 (порты в `cx±16, cy` и на четвертях высоты по 8 px), а у трёхвходового
    «Сумматора» (32×48) четверть 12 px, и выровненный приёмник сходит с
    сетки на 4 (находка ревью 02.10.2026). Выравнивание последний шаг: у
    неподвинутых блоков сетка соблюдена, а сдвинутые назовёт проверка разметки
    `check_model_layout`.
    """
    for token, (cx, cy) in centers.items():
        centers[token] = (round(cx / GRID_STEP) * GRID_STEP,
                          round(cy / GRID_STEP) * GRID_STEP)


def first_point(points_text: str) -> "tuple[float, float] | None":
    """Первая точка `Points` - центр блока (замер 02.10.2026).

    `None` - свойство пусто или не разбирается: вызывающий обязан назвать
    такой блок, а не счесть его стоящим в (0, 0).
    """
    pairs = re.findall(r"\(([-\d.]+)\s*,\s*([-\d.]+)\)", points_text or "")
    if not pairs:
        return None
    return float(pairs[0][0]), float(pairs[0][1])


def shift_to_margin(centers: dict[str, tuple[float, float]],
                    sizes: dict[str, tuple[float, float]]) -> None:
    """Отодвинуть раскладку от начала координат на `MARGIN`.

    Габарит считается по фактическим размерам блоков (центр плюс-минус
    половина размера), сдвиг - кратно `GRID_STEP`: `snap_centers` идёт
    следом и обязан оставить центры на сетке. Пустая раскладка не трогается.
    """
    if not centers:
        return
    left = min(centers[t][0] - sizes[t][0] / 2.0 for t in centers)
    top = min(centers[t][1] - sizes[t][1] / 2.0 for t in centers)
    dx = round((MARGIN - left) / GRID_STEP) * GRID_STEP
    dy = round((MARGIN - top) / GRID_STEP) * GRID_STEP
    if dx == 0 and dy == 0:
        return
    for token, (cx, cy) in centers.items():
        centers[token] = (cx + dx, cy + dy)


def rect_of(points_text: str, size: "tuple[float, float]") -> \
        "tuple[float, float, float, float] | None":
    """Габарит блока: центр из `Points` ± половина размера.

    Живой замер 02.10.2026: `Points` - **не контур блока**, и min/max его
    точек габаритом не является («Константа» 32×16 даёт полилинию 16×32).
    Первая точка полилинии - центр блока (совпадает с `set_center` до
    десятых), вторая - выходной порт. `None` - свойство пусто/не разбирается
    или размер недоступен: проверка обязана назвать такой блок, а не молча
    счесть его непересекающимся.
    """
    center = first_point(points_text)
    if center is None:
        return None
    cx, cy = center
    w, h = size
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def model_frame(page: Page) -> "tuple[float, float, float, float] | None":
    """Рамка модели: объединение габаритов блоков страницы.

    Считается по тем же данным, что и метрика наложений: центр из `Points`
    (первая точка - центр блока), размер - `get_size`. Подписи
    (`NON_BLOCK_CLASSES`) пропускаются: их карточка 60x40 накрывает блок и
    растянула бы рамку. `None` - ни одного блока с читаемыми габаритами.

    Имя без подчёркивания - точка входа и для соседнего модуля
    (`save_screenshot` считает по ней кадр режимов `hires`/`zoom`).
    """
    left = top = None
    right = bottom = None
    for block in page.get_blocks():
        try:
            if block.class_name in LABEL_CLASSES:
                continue
        except Exception:                                     # noqa: BLE001
            pass
        try:
            size = block.get_size()
            rect = rect_of(block.get_points(), size) if size else None
        except Exception:                                     # noqa: BLE001
            rect = None
        if rect is None:
            continue
        left = rect[0] if left is None else min(left, rect[0])
        top = rect[1] if top is None else min(top, rect[1])
        right = rect[2] if right is None else max(right, rect[2])
        bottom = rect[3] if bottom is None else max(bottom, rect[3])
    if left is None or top is None or right is None or bottom is None:
        return None
    return (left, top, right, bottom)


def rect_overlaps(rect_a: "tuple[float, float, float, float]",
                  rect_b: "tuple[float, float, float, float]") -> bool:
    """Пересекаются ли прямоугольники (строго, касание - не наложение)."""
    return (rect_a[0] < rect_b[2] and rect_b[0] < rect_a[2]
            and rect_a[1] < rect_b[3] and rect_b[1] < rect_a[3])


def page_rects(
        page: Page,
) -> "tuple[list[tuple[str, tuple[float, float, float, float]]], list[str]]":
    """Габариты блоков страницы: (список, имена без геометрии).

    Общий сбор для наложений и метрик маршрутов. Подписи (`LABEL_CLASSES`)
    пропускаются: карточка 60×40 накрывает край своего блока - это не
    наложение (замер 02.10.2026). Габарит - центр (`Points`) ± `size/2`;
    одни `Points` габаритом не являются (замер 02.10.2026).
    """
    rects: list[tuple[str, tuple[float, float, float, float]]] = []
    no_geometry: list[str] = []
    for block in page.get_blocks():
        try:
            name = block.get_name()
        except Exception:                                     # noqa: BLE001
            name = str(block.id)
        try:
            if block.class_name in LABEL_CLASSES:
                continue
        except Exception:                                     # noqa: BLE001
            pass
        try:
            size = block.get_size()
        except Exception:                                     # noqa: BLE001
            size = None
        try:
            rect = rect_of(block.get_points(), size) if size else None
        except Exception:                                     # noqa: BLE001
            rect = None
        if rect is None:
            no_geometry.append(name)
        else:
            rects.append((name, rect))
    return rects, no_geometry


def overlap_report_impl(
        rects: "list[tuple[str, tuple[float, float, float, float]]]",
        no_geometry: list[str],
        moved_names: set[str],
) -> str:
    """Строки о наложениях: пары с участием перемещённых блоков.

    Контракт «без наложений» проверяется фактом - по габаритам из `Points`
    всех блоков страницы, прочитанным **после** перерисовки. Пары считаются
    с участием перемещённых (любой из двух): чужой блок, перечисленный
    раньше, не должен прятать пару (находка ревью 02.10.2026), а чужие
    наложения - не наша правка, но подвинутый поверх чужого обязан быть
    виден. Подписи (`LABEL_CLASSES`) пропускаются: карточка 60×40 накрывает
    край своего блока - это не наложение (замер 02.10.2026).

    Габариты приходят снаружи (`page_rects`): у `layout_place` тем же
    чтением пользуются метрики маршрутов, и второй проход по всем блокам
    страницы (COM на блок) был бы лишним (находка ревью PR #92).
    """
    overlaps: list[tuple[str, str]] = []
    for index, (name_a, rect_a) in enumerate(rects):
        for name_b, rect_b in rects[index + 1:]:
            if (name_a in moved_names or name_b in moved_names) \
                    and rect_overlaps(rect_a, rect_b):
                overlaps.append((name_a, name_b))
    if overlaps:
        shown = ", ".join(f"{a}{EM_DASH}{b}"
                          for a, b in overlaps[:MAX_REPORTED_OVERLAPS])
        more = (f" (и ещё {len(overlaps) - MAX_REPORTED_OVERLAPS})"
                if len(overlaps) > MAX_REPORTED_OVERLAPS else "")
        text = (f"\nВНИМАНИЕ: наложения блоков (нет свободного буфера): "
                f"{shown}{more}")
    else:
        text = "\nНаложений блоков нет."
    if no_geometry:
        text += (f"\nГеометрию прочитать не удалось у {len(no_geometry)} "
                 f"блоков: {', '.join(no_geometry[:MAX_REPORTED_OVERLAPS])}")
    return text


def overlap_report(page: Page, names: set[str]) -> str:
    """Строки о наложениях блоков страницы.

    `names` - блоки, чьи габариты изменились: в ответ попадают пары с их
    участием (чужие наложения не наша правка). Полный список страницы
    `check_model_layout` считает сам, своим проходом по габаритам, поэтому
    режима «все пары» здесь нет. Подписи и прочие объекты оформления
    пропускаются: их карточка 60×40 накрывает блок и дала бы ложную пару.

    Один сбор габаритов на страницу: тем же чтением пользуется
    `layout_place`, а `fit_port_blocks`, `set_block_size` и
    `set_block_param(nport)` называют наложения сразу после правки, не
    дожидаясь отдельного вызова.
    """
    rects, no_geometry = page_rects(page)
    return overlap_report_impl(rects, no_geometry, names)


def routing_metrics(
        rects: "list[tuple[str, tuple[float, float, float, float]]]",
        pairs: "list[tuple[str, int, str, int]]",
        aliases: dict[str, str],
        available: dict[str, Block],
        page: Page,
) -> str:
    """Метрики маршрутов по известным связям - строкой в ответ `layout_place`.

    Числа считает та же чистая `audit_routing_segments`, что и `audit_routing`
    (пересечения, общий трек с перекрытием, попадание в чужой габарит,
    порядок входов, канал теснее разреза), но данные - только связи с
    известными концами: реестр `connect`, а у открытого проекта - прямые
    пары из выгрузки (`wire_pairs` - те же, по которым работает
    выравнивание). Линии, которых мы не знаем, в метрику не входят - их
    число сказано прямо, а полный аудит всех линий страницы (контур читает
    концы каждой) даёт `audit_routing`. Пустые порты сюда не входят по той
    же причине: `getportwireid` - функция языка, а не COM.
    """
    from .check_model import audit_routing_segments

    wires: "dict[int, tuple[tuple[float, float], tuple[float, float]]]" = {}
    for index, (src_name, out_index, dst_name, in_index) in enumerate(
            pairs, start=1):
        src_token = aliases.get(src_name, src_name)
        dst_token = aliases.get(dst_name, dst_name)
        src_block = available.get(src_token)
        dst_block = available.get(dst_token)
        if src_block is None or dst_block is None:
            continue
        try:
            wires[index] = (src_block.get_out_port(out_index).get_coords(),
                            dst_block.get_in_port(in_index).get_coords())
        except Exception:                                     # noqa: BLE001
            continue
    if not wires or not rects:
        return ""
    try:
        total = len(page.get_wires())
    except Exception:                                         # noqa: BLE001
        total = None
    problems = audit_routing_segments(rects, wires)
    # Без «из N»: `wires_word` согласует именительный («1 линия»), а «1 из 1
    # линии» требует родительного - союз «из» дал бы «1 из 1 линия» (находка
    # ревью PR #92: «1 из 1 линий» - та же ловушка с другой стороны).
    scope = (f"{len(wires)}; линий на странице {EM_DASH} {total}"
             if total is not None else f"{len(wires)}")
    return (f"\nМетрики маршрутов (известные связи {EM_DASH} {scope}): "
            f"пересечений {EM_DASH} {len(problems.crossings)}; "
            f"общий трек с перекрытием {EM_DASH} {len(problems.coincident)}; "
            f"в чужом габарите {EM_DASH} {len(problems.block_hits)}; "
            f"порядок входов нарушен {EM_DASH} {len(problems.port_order)}; "
            f"канал теснее разреза {EM_DASH} {len(problems.channel_overflow)}; "
            f"вне канона (обратные/внутриколоночные) {EM_DASH} "
            f"{len(problems.unchecked)}. Все линии страницы {EM_DASH} `audit_routing`.")


def wires_word(count: int) -> str:
    """Согласовать слово с числом: «1 линия», «2 линии», «5 линий».

    Ответ читает человек (и агент): «нормализовано 1 линий» живьём резало
    глаз (живой замер 04.10.2026), а форма «найдено N …» с правильной
    словоформой - обычай интерфейсов, согласовывать глагол не приходится.
    """
    if count % 10 == 1 and count % 100 != 11:
        return "линия"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "линии"
    return "линий"
