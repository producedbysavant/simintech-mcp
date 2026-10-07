"""Инструмент расстановки блоков: координаты, размеры, выравнивание проводов.

Порядок операций существенен: переместить блоки → `RepaintEditor` →
`NormalizeWire`. Нормализация до перерисовки не видит новых координат портов,
и провода остаются диагональными.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page
from simintech_api.constants import BLOCK_GAP
from simintech_api.catalog import NON_BLOCK_CLASSES

from .. import runtime, session
from ..app import mcp


#: Сколько пар наложений перечислять в ответе: список — для человека, длинный
#: хвост обрезается (счётчик остаётся).
MAX_REPORTED_OVERLAPS = 10

#: Предел пачки `set_block_center(moves=...)`: каждый токен — до трёх
#: COM-вызовов на единственном COM-потоке (`set_center`, `get_points`,
#: `get_name`), а COM-вызов таймаутом не прерывается (CLAUDE.md, инвариант 3:
#: у параметра, задающего число COM-вызовов, есть явный предел; прецедент —
#: `MAX_BLOCK_PROPS`). Боевой лист — десятки блоков (запрос fdd002); 128 —
#: запас к этому, но не к «тысячам токенов в одном вызове».
MAX_MOVES = 128

#: Шаг разметки схемы: 1 квадратик = 8×8 px (стандарт оформления владельца:
#: блоки «сигнал»/«порт»/«константа» — 16 высоты, «Ступенька» — 32; порты
#: стоят в `cx±16, cy`, поэтому кратность 8 у центров даёт сетку и портам).
GRID_STEP = 8.0

#: Полотно снимка схемы (живой замер 04.10.2026): `savescreenshot` отдаёт PNG
#: 1026x580. По нему считается масштаб подгонки кадра.
CANVAS_W = 1026.0
CANVAS_H = 580.0

#: Поля подгонки кадра: доля полотна, оставляемая по краям.
FIT_PADDING = 0.06

#: Классы, стыкующиеся стопкой вплотную (стандарт: «входные порты единой
#: колонкой без зазоров»).
PORT_STACK_CLASSES = ("Порт входа", "Порт выхода")

#: Классы-«оформление»: это не блоки. Набор — библиотечный
#: (`simintech_api.catalog.NON_BLOCK_CLASSES`: `TextLabel`, `RotatedText`,
#: «Комментарий», `Rectangle` и т. п.): живой замер 02.10.2026 вскрыл
#: `constLabel` (`Points` — якорь текста, `size` — типовая карточка 60×40,
#: портов нет), но подпись в GUI может быть и `TextLabel`, и прямоугольником —
#: своя константа из одного имени пропускала их в расстановку (находка ревью
#: 02.10.2026). Расстановке и метрике не подлежат: расставленный «как блок»
#: объект оформления уезжает от того, что помечает, и даёт ложные наложения.
LABEL_CLASSES = NON_BLOCK_CLASSES


def _flush_port_stacks(tokens: list[str],
                       centers: dict[str, tuple[float, float]],
                       sizes: dict[str, tuple[float, float]],
                       available: dict[str, Block]) -> int:
    """Стыковать порт-блоки одного класса в слое вплотную.

    Стандарт оформления (#24, п.2): блоки одного вида — стопкой без зазоров,
    «входные порты единой колонкой». Стыкуются только **соседние** в колонке
    блоки одного класса: если между порт-блоками стоит чужой блок, «стопку»
    через него собирать нельзя — сдвиг насадил бы порты на него (находка
    ревью 02.10.2026: вызов «расставь всё» печатал «сомкнуто вплотную» и тут
    же — наложение, которое сам создал). Считается по фактическим высотам:
    следующий центр — предыдущий + (h1 + h2)/2. Блоки без читаемого класса не
    трогаются; возвращается число сдвинутых.
    """
    layers: dict[float, list[str]] = {}
    for token in tokens:
        layers.setdefault(centers[token][0], []).append(token)
    moved = 0
    for column in layers.values():
        ordered = sorted(column, key=lambda t: centers[t][1])
        runs: list[tuple[str | None, list[str]]] = []
        for token in ordered:
            try:
                cls: str | None = available[token].class_name
            except Exception:                                     # noqa: BLE001
                cls = None
            if cls is not None and runs and runs[-1][0] == cls:
                runs[-1][1].append(token)
            else:
                runs.append((cls, [token]))
        for cls, members in runs:
            if cls not in PORT_STACK_CLASSES or len(members) < 2:
                continue
            for prev, token in zip(members, members[1:]):
                cy = centers[prev][1] + (sizes[prev][1] + sizes[token][1]) / 2
                centers[token] = (centers[token][0], cy)
                moved += 1
    return moved


def _snap_centers(centers: dict[str, tuple[float, float]]) -> None:
    """Поставить центры блоков на разметку 8 px (и порты — тоже на сетку).

    Выравнивание по портам после этого сетку сохраняет **не всегда**: смещение
    вход→выход — разность координат портов; у блоков высотой 16/32 она кратна
    8 (порты в `cx±16, cy` и на четвертях высоты — по 8 px), а у трёхвходового
    «Сумматора» (32×48) четверть — 12 px, и выровненный приёмник сходит с
    сетки на 4 (находка ревью 02.10.2026). Выравнивание — последний шаг: у
    неподвинутых блоков сетка соблюдена, а сдвинутые назовёт проверка разметки
    `check_model_layout`.
    """
    for token, (cx, cy) in centers.items():
        centers[token] = (round(cx / GRID_STEP) * GRID_STEP,
                          round(cy / GRID_STEP) * GRID_STEP)


def first_point(points_text: str) -> "tuple[float, float] | None":
    """Первая точка `Points` — центр блока (замер 02.10.2026).

    `None` — свойство пусто или не разбирается: вызывающий обязан назвать
    такой блок, а не счесть его стоящим в (0, 0).
    """
    pairs = re.findall(r"\(([-\d.]+)\s*,\s*([-\d.]+)\)", points_text or "")
    if not pairs:
        return None
    return float(pairs[0][0]), float(pairs[0][1])


#: Отступ раскладки от начала координат: блок с центром x=0 теряет левую
#: половину — она уходит в минус и на лист не попадает, и снимок показывает
#: обрезанные блоки (живой замер 04.10.2026: полотно 1026x580, схема прижата
#: к левому верхнему углу, два блока срезаны краем). Кратен шагу разметки,
#: чтобы сетка пережила сдвиг.
MARGIN = 48.0


def _shift_to_margin(centers: dict[str, tuple[float, float]],
                     sizes: dict[str, tuple[float, float]]) -> None:
    """Отодвинуть раскладку от начала координат на `MARGIN`.

    Габарит считается по фактическим размерам блоков (центр плюс-минус
    половина размера), сдвиг — кратно `GRID_STEP`: `_snap_centers` идёт
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


def _rect_of(points_text: str, size: "tuple[float, float]") -> \
        "tuple[float, float, float, float] | None":
    """Габарит блока: центр из `Points` ± половина размера.

    Живой замер 02.10.2026: `Points` — **не контур блока**, и min/max его
    точек габаритом не является («Константа» 32×16 даёт полилинию 16×32).
    Первая точка полилинии — центр блока (совпадает с `set_center` до
    десятых), вторая — выходной порт. `None` — свойство пусто/не разбирается
    или размер недоступен: проверка обязана назвать такой блок, а не молча
    счесть его непересекающимся.
    """
    center = first_point(points_text)
    if center is None:
        return None
    cx, cy = center
    w, h = size
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def _model_frame(page: Page) -> "tuple[float, float, float, float] | None":
    """Рамка модели: объединение габаритов блоков страницы.

    Считается по тем же данным, что и метрика наложений: центр из `Points`
    (первая точка — центр блока), размер — `get_size`. Подписи
    (`NON_BLOCK_CLASSES`) пропускаются: их карточка 60x40 накрывает блок и
    растянула бы рамку. `None` — ни одного блока с читаемыми габаритами.
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
            rect = _rect_of(block.get_points(), size) if size else None
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


def _overlaps(rect_a: "tuple[float, float, float, float]",
              rect_b: "tuple[float, float, float, float]") -> bool:
    """Пересекаются ли прямоугольники (строго, касание — не наложение)."""
    return (rect_a[0] < rect_b[2] and rect_b[0] < rect_a[2]
            and rect_a[1] < rect_b[3] and rect_b[1] < rect_a[3])


def _page_rects(
        page: Page,
) -> "tuple[list[tuple[str, tuple[float, float, float, float]]], list[str]]":
    """Габариты блоков страницы: (список, имена без геометрии).

    Общий сбор для наложений и метрик маршрутов. Подписи (`LABEL_CLASSES`)
    пропускаются: карточка 60×40 накрывает край своего блока — это не
    наложение (замер 02.10.2026). Габарит — центр (`Points`) ± `size/2`;
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
            rect = _rect_of(block.get_points(), size) if size else None
        except Exception:                                     # noqa: BLE001
            rect = None
        if rect is None:
            no_geometry.append(name)
        else:
            rects.append((name, rect))
    return rects, no_geometry


def _overlap_report(
        rects: "list[tuple[str, tuple[float, float, float, float]]]",
        no_geometry: list[str],
        moved_names: set[str],
) -> str:
    """Строки о наложениях: пары с участием перемещённых блоков.

    Контракт «без наложений» проверяется фактом — по габаритам из `Points`
    всех блоков страницы, прочитанным **после** перерисовки. Пары считаются
    с участием перемещённых (любой из двух): чужой блок, перечисленный
    раньше, не должен прятать пару (находка ревью 02.10.2026), а чужие
    наложения — не наша правка, но подвинутый поверх чужого обязан быть
    виден. Подписи (`LABEL_CLASSES`) пропускаются: карточка 60×40 накрывает
    край своего блока — это не наложение (замер 02.10.2026).

    Габариты приходят снаружи (`_page_rects`): у `layout_place` тем же
    чтением пользуются метрики маршрутов, и второй проход по всем блокам
    страницы (COM на блок) был бы лишним (находка ревью PR #92).
    """
    overlaps: list[tuple[str, str]] = []
    for index, (name_a, rect_a) in enumerate(rects):
        for name_b, rect_b in rects[index + 1:]:
            if (name_a in moved_names or name_b in moved_names) \
                    and _overlaps(rect_a, rect_b):
                overlaps.append((name_a, name_b))
    if overlaps:
        shown = ", ".join(f"{a}—{b}"
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


def _routing_metrics(
        rects: "list[tuple[str, tuple[float, float, float, float]]]",
        pairs: "list[tuple[str, int, str, int]]",
        aliases: dict[str, str],
        available: dict[str, Block],
        page: Page,
) -> str:
    """Метрики маршрутов по известным связям — строкой в ответ `layout_place`.

    Числа считает та же чистая `audit_routing_segments`, что и `audit_routing`
    (пересечения, общий трек с перекрытием, попадание в чужой габарит,
    порядок входов, канал теснее разреза), но данные — только связи с
    известными концами: реестр `connect`, а у открытого проекта — прямые
    пары из выгрузки (`wire_pairs` — те же, по которым работает
    выравнивание). Линии, которых мы не знаем, в метрику не входят — их
    число сказано прямо, а полный аудит всех линий страницы (контур читает
    концы каждой) даёт `audit_routing`. Пустые порты сюда не входят по той
    же причине: `getportwireid` — функция языка, а не COM.
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
    # Без «из N»: `_wires_word` согласует именительный («1 линия»), а «1 из 1
    # линии» требует родительного — союз «из» дал бы «1 из 1 линия» (находка
    # ревью PR #92: «1 из 1 линий» — та же ловушка с другой стороны).
    scope = (f"{len(wires)}; линий на странице — {total}"
             if total is not None else f"{len(wires)}")
    return (f"\nМетрики маршрутов (известные связи — {scope}): "
            f"пересечений — {len(problems.crossings)}; "
            f"общий трек с перекрытием — {len(problems.coincident)}; "
            f"в чужом габарите — {len(problems.block_hits)}; "
            f"порядок входов нарушен — {len(problems.port_order)}; "
            f"канал теснее разреза — {len(problems.channel_overflow)}; "
            f"вне канона (обратные/внутриколоночные) — "
            f"{len(problems.unchecked)}. Все линии страницы — `audit_routing`.")


def _wires_word(count: int) -> str:
    """Согласовать слово с числом: «1 линия», «2 линии», «5 линий».

    Ответ читает человек (и агент): «нормализовано 1 линий» живьём резало
    глаз (живой замер 04.10.2026), а форма «найдено N …» с правильной
    словоформой — обычай интерфейсов, согласовывать глагол не приходится.
    """
    if count % 10 == 1 and count % 100 != 11:
        return "линия"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "линии"
    return "линий"


def normalize_page_wires(page: Page) -> str:
    """Трассировать все линии страницы — общий шаг обоих режимов.

    Трассируем все линии страницы, а не только созданные этой сессией:
    `page.get_wires()` их перечисляет (так же поступает `list_wires`), а
    `normalize()` безопасен и на линии открытого проекта. Порядок
    «перерисовка → трассировка» — на вызывающих: у `normalize_only`
    перерисовка своя, в расстановке — условная (`if shifted`).

    Текст один на оба режима намеренно: копия разошлась бы молча (находка
    ревью 04.10.2026), а «участки ортогональные» — утверждение, которого
    инструмент не измеряет: `NormalizeWire` в библиотеке отказ проглатывает
    (`Wire.normalize`), и нормализация видна отрисовкой, а не выгрузкой
    (живой замер 03.10.2026) — потому ответ зовёт к снимку.
    """
    wires = page.get_wires()
    for wire in wires:
        wire.normalize()
    if not wires:
        return "\nЛиний связи на странице нет — трассировать нечего"
    return (f"\nЛинии связи: нормализовано {len(wires)} "
            f"{_wires_word(len(wires))} страницы (NormalizeWire) — "
            "сверяйте снимком (`save_screenshot`): у готовых маршрутов "
            "выгрузка покажет прежние точки, пустые нормализация "
            "дописывает в данные")


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def layout_place(block_ids: str = "", connections: str = "",
                 normalize_only: bool = False) -> str:
    """Расставить блоки по слоям без наложений — **с применением** координат.

    Координаты считает `LayeredPlacer`, и они тут же применяются к блокам
    (`set_center`). Раньше инструмент только возвращал координаты текстом, а
    блоки не двигал: агент получал подтверждение расстановки, которой не было.
    Позиция задаётся до расчёта — она влияет только на вид схемы.

    **Вызов без аргументов — «всё, что известно»**: берутся все блоки главной
    страницы, а связями становятся все пары, запомненные `connect` в этой
    сессии. Это рекомендуемая форма: частичный список оставляет неперечисленные
    блоки в (0,0) друг на друге — «кучей», которую инструмент при этом
    подтверждал как расставленную (живой случай 02.10.2026). Явные аргументы
    остаются для выборочной расстановки и разбираются строго, как раньше.
    Граф берётся из реестра `connect`, а когда он пуст (проект открыт из
    файла) — из выгрузки страницы: концы линий COM не отдаёт, их отдаёт
    только выгрузка, вместе с ветвями.

    **`normalize_only` — трассировка без расстановки.** Линии страницы
    нормализуются, блоки не двигаются; расстановка, связи и метрика наложений
    не участвуют. Прежде то же делали трюком — вызовом по не-блочному объекту
    (`TextLabel`): подписи отфильтровываются из списка блоков, расстановка
    становится пустой, а цикл трассировки выполняется. Потребность реальна
    (живой замер 03.10.2026: проект из одних порт-блоков раскладывать нельзя —
    широкие порты накрывают соседние колонки, — а линии должны быть
    ортогональны), поэтому режим объявлен явно; сочетание с
    `block_ids`/`connections` отвергается, чтобы не было двусмысленности
    «расставить или только нормализовать».

    **Нормализация видна отрисовкой; пустые маршруты она пишет и в данные.**
    У линий с заполненным маршрутом выгрузка (`export_model_text`,
    `savemodeltofile`) после нормализации даёт те же точки бит-в-бит (живой
    замер 03.10.2026). Линиям без маршрута (`points = []` — так приходит
    часть проводов после импорта текста; живой замер 04.10.2026: 18 из 132)
    нормализация маршрут **заполняет** — в выгрузке после неё лежат полные
    ортогональные точки; результат переживает save+reload. Поэтому проверять
    нормализацию одной выгрузкой нельзя — смотрите снимок
    (`save_screenshot`).

    **Проверка наложений.** После расстановки габариты блоков читаются из
    свойства `Points`, и пересекающиеся пары называются в ответе: контракт
    «без наложений» виден фактом, а не предполагается. Пары считаются только
    с участием расставленных блоков — чужие наложения не наша правка.

    **Метрики маршрутов.** По связям с известными концами (реестр `connect`,
    а у открытого проекта — прямые пары из выгрузки: те же, что у
    выравнивания) в ответе считаются пересечения, общий трек с перекрытием,
    попадание в чужой габарит, порядок входов и канал теснее разреза — той
    же чистой функцией, что у `audit_routing`. Линии без известных концов в
    метрику не входят, и это сказано её числом: полный аудит всех линий
    страницы (контур читает концы каждой) — `audit_routing`.

    **Сетка 8 px и стопки порт-блоков** (стандарт оформления владельца,
    02.10.2026): 1 квадратик разметки — 8×8; центры ставятся на сетку,
    поэтому и порты на ней (порты — `cx±16, cy`); «Порт входа»/«Порт выхода»
    одного класса, соседние в колонке, стыкуются стопкой вплотную — единой
    колонкой без зазоров (вертикальные зазоры между прочими блоками в
    стандарте — 8…32 px). Приёмники-порт-блоки выравнивание по портам не
    двигает: стопка важнее прямого участка к ним.

    **Порт-блоки — не для раскладки.** «Порт входа»/«Порт выхода» — интерфейс
    схемы, их место по краям, и в `block_ids` раскладки их включать не
    следует: шаг слоёв считается каноном (ТЗ 4.2: `col_width + channel_w`),
    а порт шириной
    360 px накрывает соседнюю колонку — живой замер 03.10.2026 дал 23 пары
    наложений в проекте из одних портов. Исключите порт-блоки из `block_ids`:
    остальные блоки расставятся без наложений, порты останутся на своих
    местах.

    **Подписи не расставляются.** Объекты оформления (`constLabel`, `TextLabel`,
    «Комментарий» и прочий `NON_BLOCK_CLASSES` библиотеки) — не блоки: их
    `Points` — якорь текста, `size` — типовая карточка, и «расставленная»
    подпись уезжает от своего блока (живой случай 02.10.2026). Они
    исключаются из расстановки и, как следствие, из метрики наложений.

    Размеры блоков не задаются: `set_center` сохраняет родной размер каждого
    блока (он задан правилами разработки SimInTech, и подменять его нельзя), а
    расстановка считается по фактическим габаритам из `get_size`.

    Связи разбираются строго: токен в `connections`, который не является парой
    `src->dst`, — **отказ**, а не молчаливый пропуск. Раньше такая пара тихо
    выпадала из расстановки, и клиент получал подтверждение успеха, в котором
    связи не учтены (блоки вставали в один слой).

    Здесь же трассируются линии страницы — и созданные `connect` в этой сессии,
    и пришедшие из открытого проекта: после сдвига блоков геометрия
    пересчитывается `NormalizeWire`, иначе провода остаются диагональными (по
    прямой между портами). Это единственное место, где трассировка возможна: до
    расстановки блоки лежат в (0,0) друг на друге, и маршрут получается в обход
    наложенных блоков. Линии перечисляет `Page.get_wires` — то же, что делает
    `list_wires`; через COM не читаются только их концы (какие блоки соединены),
    поэтому выравнивание по портам ниже применяется к связям текущей сессии,
    чьи концы запомнил `connect`, а у проекта, открытого из файла (реестр
    пуст), — к прямым парам из выгрузки страницы: её адреса `block:out:N` /
    `block:in:N` несут те же индексы портов (живой замер 06.10.2026 — провод
    с `in:0` сошёлся с первым входом), а ветви пропускаются: у ветви не порт
    источника, а точка съёма.

    **Уточнено 2026-09-28 (живой замер, поставка 2.26.6.23):** концы линий
    через COM не читаются — их отдаёт выгрузка контейнера встроенным языком
    (README, «Ограничения»). 06.10.2026 выравнивание по этим концам
    распространено и на открытые проекты (реестр `connect` пуст).

    Args:
        block_ids: блоки через запятую — имена (`k_0`, `kx_0`, `ToFile_0`; их
            даёт `list_blocks`) или числовые id. Пусто — все блоки главной
            страницы.
        connections: пары `src->dst` через запятую, напр. `k_0->kx_0`. Оба конца
            должны быть перечислены в `block_ids`. Пусто при пустых `block_ids`
            — все связи сессии (их запомнил `connect`); пусто при явных
            `block_ids` — расстановка без учёта связей.
        normalize_only: True — нормализовать линии страницы, ничего не
            расставляя; с `block_ids`/`connections` не сочетается.
    """
    from simintech_api.layout import LayeredPlacer
    from simintech_api.layout.repeat import collapse_repeat, detect_repeat

    project = session.ensure_project()
    page = project.get_main_page()

    if normalize_only:
        # Только трассировка: расстановки, связей и метрики наложений в этом
        # режиме нет намеренно — он для раскладок, которые двигать нельзя
        # (живой замер 03.10.2026: широкие порт-блоки накрывают соседние
        # колонки, а линии должны быть ортогональны). Порядок «перерисовка →
        # трассировка» сохранён как в проверенном трюке с не-блочным объектом.
        if block_ids.strip() or connections.strip():
            raise ToolError(
                "normalize_only не сочетается с block_ids/connections: режим "
                "ничего не расставляет и связи не учитывает. Либо "
                "нормализация без раскладки, либо расстановка со связями."
            )
        project.repaint()
        return ("Блоки не двигались: normalize_only."
                + normalize_page_wires(page))

    available: dict[str, Block] = {}
    for block in page.get_blocks():
        available[str(block.id)] = block
        try:
            available[block.get_name()] = block
        except Exception:
            continue

    tokens = [t.strip() for t in block_ids.split(",") if t.strip()]
    bare_call = not tokens and not connections.strip()
    if not tokens:
        # Пусто — не ошибка, а «все блоки страницы»: частичный список оставлял
        # неперечисленные блоки в (0,0) друг на друге, и «куча» выглядела
        # успешно расставленной. Имя, которого COM не отдал, заменяется
        # числовым id — блок всё равно должен быть расставлен.
        for block in page.get_blocks():
            try:
                tokens.append(block.get_name())
            except Exception:                                 # noqa: BLE001
                tokens.append(str(block.id))
    missing = [t for t in tokens if t not in available]
    if missing:
        raise ToolError(
            f"Блоки не найдены на странице: {', '.join(missing)}. "
            f"Актуальные имена и id даёт list_blocks."
        )

    # Подписи — не блоки: их «габарит» (якорь текста + типовая карточка)
    # расстановке не подлежит, иначе подпись уезжает от своего блока.
    dropped_labels: list[str] = []
    kept: list[str] = []
    for token in tokens:
        try:
            cls = available[token].class_name
        except Exception:                                     # noqa: BLE001
            kept.append(token)
            continue
        if cls in LABEL_CLASSES:
            dropped_labels.append(token)
        else:
            kept.append(token)
    tokens = kept

    # `connect` запоминает концы линий именами блоков (`WIRES`), а блоки здесь
    # разрешено адресовать числовыми id (`block_ids='1,2'`). Без перевода имён в
    # токены `centers` совпадений не находил, и выравнивание молча пропускалось.
    # `available` хранит оба ключа — и id, и имя.
    aliases: dict[str, str] = {}
    for token in tokens:
        block = available[token]
        aliases.setdefault(str(block.id), token)
        try:
            aliases.setdefault(block.get_name(), token)
        except Exception:                                     # noqa: BLE001
            continue

    #: Пары с индексами портов из выгрузки открытого проекта (реестр `connect`
    #: пуст): ими выравнивание приёмников по источникам распространяется и на
    #: чужие линии, концы которых COM не отдаёт (issue #33).
    exported_pairs: "list[tuple[str, int, str, int]]" = []
    if bare_call:
        # «Все связи сессии»: концы чужих линий через COM не читаются, поэтому
        # честный максимум — реестр `connect`. Пары, чьи блоки не попали в
        # расстановку, отбрасываются: в реестре могли остаться концы прежней
        # страницы, и строгий отказ по ним сорвал бы вызов «расставь всё».
        # Список собирается напрямую, а не строкой `src->dst` с обратным
        # разбором: имя, переименованное в GUI, могло содержать запятую или
        # «->», и разбор строки сломал бы рекомендуемый вызов (находка ревью
        # 02.10.2026).
        by_name: dict[str, str] = {}
        for token in tokens:
            try:
                by_name.setdefault(available[token].get_name(), token)
            except Exception:                                 # noqa: BLE001
                continue
        links: list[tuple[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for _wire, src, _out, dst, _in in session.WIRES:
            src_token = by_name.get(src)
            dst_token = by_name.get(dst)
            if src_token is None or dst_token is None:
                continue
            if (src_token, dst_token) in seen_pairs:
                continue
            seen_pairs.add((src_token, dst_token))
            links.append((src_token, dst_token))
        if not links and not session.WIRES:
            # Реестр `connect` пуст — проект открыт из файла. Граф берём из
            # выгрузки страницы (автограф графа): COM концов линий не отдаёт,
            # их отдаёт только выгрузка, и она же называет ветви. Без этого
            # блоки встают в одну колонку, а ветви теряются вовсе — каналы
            # выходят уже канона на 8 px за каждую.
            from .model_text import (
                graph_from_wires,
                page_export_text,
                pairs_from_wires,
                parse_page_wires,
            )
            try:
                graph_text, _trunc, _outcome, _path = page_export_text()
            except Exception:                            # noqa: BLE001
                # Выгрузка — best-effort: не удалась, значит расстановка
                # идёт без связей, и ответ об этом скажет, а не упадёт.
                graph_text = ""
            # Разбор выгрузки один: из записей проводов выводятся и граф, и
            # пары — два `parse_page_wires` по одному тексту были бы лишней
            # работой на больших страницах (находка ревью PR #91).
            wires = parse_page_wires(graph_text)
            known = set(tokens)
            for src_block, dst_block in graph_from_wires(wires):
                if src_block not in known or dst_block not in known:
                    continue
                if (src_block, dst_block) in seen_pairs:
                    continue
                seen_pairs.add((src_block, dst_block))
                links.append((src_block, dst_block))
            # Пары с индексами портов — для выравнивания приёмников по
            # источникам: тем же выравниванием, что и по связям сессии
            # (ниже), но у открытого проекта концы линий даёт выгрузка.
            # Ветви пропускает `pairs_from_wires`: у них не порт источника.
            for src_block, out_index, dst_block, in_index in \
                    pairs_from_wires(wires):
                if src_block not in known or dst_block not in known:
                    continue
                exported_pairs.append(
                    (src_block, out_index, dst_block, in_index))
    else:
        links = []
        for pair in connections.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "->" not in pair:
                # Молчаливый пропуск здесь — ложный успех: связь не
                # учитывается, блоки встают в один слой, а клиент видит
                # подтверждение расстановки. Отказ называет сам токен, поэтому
                # опечатку (например, типографскую стрелку) видно сразу.
                raise ToolError(
                    f"Не разобрана связь «{pair}»: ожидается пара "
                    f"«источник->приёмник», например `k_0->kx_0`"
                )
            src, _, dst = pair.partition("->")
            src, dst = src.strip(), dst.strip()
            unknown = [t for t in (src, dst) if t not in tokens]
            if unknown:
                raise ToolError(
                    f"В connections упомянуты блоки вне block_ids: "
                    f"{', '.join(unknown)} — расстановка невозможна"
                )
            links.append((src, dst))

    # Размеры берём у самих блоков, а не подставляем свои: размер задан
    # правилами разработки SimInTech, и `set_center` не должен его менять.
    sizes = {token: available[token].get_size() for token in tokens}
    # 4.4) Повторяющаяся ячейка — дорожка, а не плоский граф: позиция паттерна
    # становится одним узлом раскладки, строки разворачиваются на его месте.
    # Так ячейка садится по потоку — после своих предшественников и до внешних
    # потребителей, — а не по жёсткому «слева». Не выделилась — канальная
    # раскладка и repeated_cell: no: схему не раздуваем.
    classes: dict[str, str] = {}
    for token in tokens:
        try:
            classes[token] = available[token].class_name
        except Exception:                                     # noqa: BLE001
            continue
    cell = detect_repeat(classes, links)
    repeated_note = "repeated_cell: no"
    if cell is None:
        positions = LayeredPlacer().place(tokens, links, sizes=sizes)
    else:
        nodes, edges, rows_of = collapse_repeat(cell, tokens, links)
        node_sizes: dict[object, tuple[float, float]] = {}
        for node, items in rows_of.items():
            widths = [sizes[item][0] for item in items if item in sizes]
            heights = [sizes[item][1] for item in items if item in sizes]
            node_sizes[node] = (
                max(widths, default=60.0),
                sum(heights) + BLOCK_GAP * max(0, len(heights) - 1))
        grown = LayeredPlacer().place(
            [node for node in nodes if node in node_sizes] +
            [node for node in nodes if node not in node_sizes],
            edges, sizes=node_sizes)
        # В `positions` остаются только блоки: супер-узлы после разворота не
        # нужны, а тип ключа держится единым (`str`) — этого требует mypy:
        # `dict(grown)` давал `dict[object, …]` и падал на сверке с веткой
        # канальной раскладки (CI-шаг «Типы»).
        positions = {}
        for node, items in rows_of.items():
            x, y = grown[node]
            step = (max((sizes[item][1] for item in items), default=40.0)
                    + BLOCK_GAP)
            base = y - (len(items) - 1) / 2.0 * step
            base -= base % 8.0        # база стопки — на разметке, шаг кратен ей
            for index, item in enumerate(items):
                positions[item] = (x, base + index * step)
        repeated_note = ("repeated_cell: yes (паттерн: " +
                         ", ".join(cell.pattern) +
                         f"; строк {len(cell.rows)})")

    centers = {token: positions[token] for token in tokens}
    # Порядок обязателен: **сначала сетка, затем стопки**. Обратный порядок
    # ломал стопку: `_snap_centers` округляет каждый центр сам, и соседи
    # разъезжались (20/60/100 → 16/64/96 — зазор и наложение вместо смыкания;
    # находка при разборе ревью 02.10.2026). Шаг стопки при высотах
    # порт-блоков 16×N кратен 8 ((16k₁+16k₂)/2 = 8(k₁+k₂)), поэтому от центра,
    # стоящего на сетке, вся стопка остаётся на сетке. Выравнивание по портам
    # идёт последним и порт-блоки не трогает (см. ниже); у остальных
    # приёмников смещение может увести центр с сетки (четверти трёхвходового
    # «Сумматора» — 12 px) — это назовёт проверка разметки.
    _shift_to_margin(centers, sizes)
    _snap_centers(centers)
    flushed = _flush_port_stacks(tokens, centers, sizes, available)
    for token in tokens:
        available[token].set_center(*centers[token])

    # Перерисовка ДО чтения портов: пока проект не перерисован, порты отдают
    # координаты блоков на прежних местах, и выравнивание посчиталось бы по
    # устаревшей геометрии (проверено на SimInTech64 2026-09-15: без этого
    # шага «Сумматор» уехал на тысячу пикселей вниз).
    project.repaint()

    # Выравнивание по вертикали: основной вход блока (in_index=0) ставим на
    # одну высоту с выходом источника. Иначе линия идёт с лишним изломом — у
    # «Сумматора» входы на четверти и трёх четвертях высоты, а выход
    # «Усилителя» посередине, и прямой участок не получается. Координаты
    # берём у самих портов, поэтому считаем по фактической геометрии.
    unaligned: list[str] = []
    # Связи для выравнивания — реестр `connect`, а у открытого проекта (реестр
    # пуст) ещё и прямые пары из выгрузки: индексы портов в адресах
    # `:out:N`/`:in:N` совпадают с COM-адресацией, и выравнивание работает и
    # для чужих линий, которых COM не отдаёт (issue #33).
    wire_pairs: "list[tuple[str, int, str, int]]" = [
        (src_name, out_index, dst_name, in_index)
        for _wire, src_name, out_index, dst_name, in_index in session.WIRES
    ] + exported_pairs
    # Смещения портов относительно центров читаем один раз: дальше блоки
    # двигаются, а COM отдаёт координаты портов только после перерисовки —
    # повторное чтение вернуло бы устаревшие значения.
    offsets: dict[tuple[str, int, bool], float] = {}
    for src_name, out_index, dst_name, in_index in wire_pairs:
        src_token = aliases.get(src_name, src_name)
        dst_token = aliases.get(dst_name, dst_name)
        for token, index, is_output in ((src_token, out_index, True),
                                        (dst_token, in_index, False)):
            key = (token, index, is_output)
            if key in offsets or token not in centers:
                continue
            owner = available.get(token)
            if owner is None:
                continue
            try:
                port = (owner.get_out_port(index) if is_output
                        else owner.get_in_port(index))
                offsets[key] = port.get_coords()[1] - centers[token][1]
            except Exception as exc:                          # noqa: BLE001
                unaligned.append(f"{token}[{index}] ({type(exc).__name__})")

    shifted = False
    for src_name, out_index, dst_name, in_index in wire_pairs:
        if in_index != 0:
            continue
        src_token = aliases.get(src_name, src_name)
        dst_token = aliases.get(dst_name, dst_name)
        # Приёмник-порт-блок не двигается: стопка вплотную — стандарт
        # оформления, и выравнивание вытянуло бы его из стопки, а ответ
        # продолжал бы утверждать «сомкнуто вплотную» (находка ревью
        # 02.10.2026: стопка 72/88 разъезжалась на 16/144). Линия к нему
        # может пойти с изломом — это плата за стопку.
        try:
            dst_class = available[dst_token].class_name
        except Exception:                                     # noqa: BLE001
            dst_class = None
        if dst_class in PORT_STACK_CLASSES:
            continue
        out_key = (src_token, out_index, True)
        in_key = (dst_token, in_index, False)
        if out_key not in offsets or in_key not in offsets:
            continue
        dy = ((centers[src_token][1] + offsets[out_key])
              - (centers[dst_token][1] + offsets[in_key]))
        if abs(dy) < 0.5:
            continue
        cx, cy = centers[dst_token]
        centers[dst_token] = (cx, cy + dy)
        available[dst_token].set_center(cx, cy + dy)
        shifted = True

    applied = [f"  {token}: ({centers[token][0]:.1f}, {centers[token][1]:.1f})"
               for token in tokens]

    # Порядок обязателен и проверен на SimInTech64: перемещение блоков →
    # перерисовка → трассировка. Без перерисовки SimInTech прокладывает
    # провода по прежним прямоугольникам блоков (они ещё лежат в (0,0) друг на
    # друге) и оставляет в геометрии точки вида (-160,-1056), которые потом не
    # пересчитываются. Если блоки сдвинулись на выравнивании — перерисовка
    # нужна ещё раз, уже перед трассировкой.
    if shifted:
        project.repaint()
    routes = normalize_page_wires(page)
    if bare_call and not links:
        routes += ("\nСвязи: ни одной пары не известно (реестр `connect` пуст)"
                   " — связи не учтены, блоки встали одной колонкой; концы"
                   " линий открытого проекта через COM не читаются.")
    if flushed:
        routes += (f"\nСтопки порт-блоков: сомкнуто вплотную {flushed} — "
                   f"единой колонкой без зазоров")
    if dropped_labels:
        routes += (f"\nПодписи (не блоки) не расставляются: "
                   f"{len(dropped_labels)} шт.")
    if unaligned:
        routes += (f"\nВНИМАНИЕ: выровнять не удалось для {len(unaligned)} "
                   f"связей: {', '.join(unaligned)}")
    if exported_pairs:
        routes += (f"\nВыравнивание по портам: учтены связи открытого проекта "
                   f"({len(exported_pairs)} пар с индексами — из выгрузки, "
                   f"реестр `connect` пуст)")

    # Наложения: контракт «без наложений» виден фактом — пары с участием
    # расставленных блоков считает `_overlap_report` (в нём же — почему
    # участие, а подписи пропускаются).
    placed: set[str] = set()
    for token in tokens:
        try:
            placed.add(available[token].get_name())
        except Exception:                                     # noqa: BLE001
            placed.add(str(available[token].id))
    # Габариты читаются один раз на весь ответ: наложения и метрики маршрутов
    # пользуются одним и тем же сбором (`_page_rects`) — второй проход по
    # всем блокам страницы был бы лишними COM-вызовами (находка ревью PR #92).
    rects, no_geometry = _page_rects(page)
    routes += _overlap_report(rects, no_geometry, placed)
    # Метрики — по тем же парам, что и выравнивание: реестр `connect`, а у
    # открытого проекта — прямые пары из выгрузки (`wire_pairs`). Так строка
    # метрик появляется и там, где связей сессии нет вовсе (follow-up к PR
    # #92 после мержа выравнивания по выгрузке).
    if wire_pairs:
        routes += _routing_metrics(rects, wire_pairs, aliases, available, page)
    return (repeated_note + "\n" +
            f"Расставлено блоков: {len(applied)}\n" + "\n".join(applied) + routes)


def _parse_moves(moves: str) -> "list[tuple[str, float, float]]":
    """Разобрать пачку «блок=x,y; блок=x,y» — строго, без догадок.

    Токен без `=`, с пустым именем, не парой чисел или нечисловой парой —
    отказ с указанием токена: молчаливый пропуск оставил бы блок на старом
    месте, а ответ утверждал бы переезд. Повтор имени — тоже отказ: какая из
    точек победила, решал бы порядок разбора, а не запрос. Пачка длиннее
    `MAX_MOVES` отвергается до COM-вызовов: у параметра, задающего число
    вызовов, предел обязателен (CLAUDE.md, инвариант 3).
    """
    order: list[tuple[str, float, float]] = []
    seen: set[str] = set()
    for raw in moves.split(";"):
        token = raw.strip()
        if not token:
            continue
        name, eq, coords = token.partition("=")
        name = name.strip()
        parts = [part.strip() for part in coords.split(",")] if eq else []
        numbers = None
        if name and len(parts) == 2:
            try:
                numbers = (float(parts[0]), float(parts[1]))
            except ValueError:
                numbers = None
        if numbers is None or not all(map(math.isfinite, numbers)):
            raise ToolError(
                f"токен пачки не разобран: «{token}» — формат «блок=x,y», "
                f"координаты — конечные числа.")
        if name in seen:
            raise ToolError(
                f"блок {name} назван в пачке дважды — оставьте одну точку.")
        seen.add(name)
        order.append((name, numbers[0], numbers[1]))
    if not order:
        raise ToolError(
            "пачка пуста: формат «блок=x,y; блок=x,y», например "
            "«k_0=200,100; kx_0=400,120».")
    if len(order) > MAX_MOVES:
        raise ToolError(
            f"в пачке {len(order)} блоков — предел {MAX_MOVES}: каждый токен "
            f"это COM-вызовы, разбейте перемещение на несколько вызовов и "
            f"повторите.")
    return order


def fit_geometry(frame: "tuple[float, float, float, float]",
                 canvas_w: float = CANVAS_W,
                 canvas_h: float = CANVAS_H,
                 padding: float = FIT_PADDING) -> "tuple[float, float, float]":
    """Кадр по рамке модели: (масштаб, смещение_x, смещение_y).

    Семантика свойств кадра по живому замеру 04.10.2026 (разбор пикселей
    снимков): **экран = модель * масштаб + смещение**. То есть
    `x_center`/`y_center` — смещение В ПИКСЕЛЯХ, а не координата модели: при
    0/0 и масштабе 1 снимок совпал с координатами модели точка в точку
    (габарит тёмных точек 48..561 при модели 48..560), при масштабе 4 левый
    край встал на 192 = 48*4.

    Масштаб берётся по меньшей из сторон: модель обязана войти целиком, и
    растянуть её под полотно нельзя — иначе стороны разъедутся. Поля
    `padding` (доля полотна) остаются с обоих краёв.

    Функция чистая — ни COM, ни состояния, — поэтому её и проверяют тесты:
    `tests/unit/test_layout_tools.py`, класс `test_fit_geometry_*`.
    """
    left, top, right, bottom = frame
    width = max(right - left, 1.0)
    height = max(bottom - top, 1.0)
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    scale = min(canvas_w * (1.0 - 2.0 * padding) / width,
                canvas_h * (1.0 - 2.0 * padding) / height)
    return (scale,
            canvas_w / 2.0 - cx * scale,
            canvas_h / 2.0 - cy * scale)


def apply_fit_view(canvas_w: float = CANVAS_W,
                   canvas_h: float = CANVAS_H) -> str:
    """Посчитать рамку модели и выставить кадр страницы — общий шаг подгонки.

    Нужен обоим: и инструменту `fit_view`, и `save_screenshot` — снимок обязан
    показывать модель целиком, а не её угол. Возвращает краткую сводку; если
    габариты прочитать не удалось, бросает `ToolError` (вызывающий сам решает,
    отказ это или примечание к снимку).

    Показать схему целиком: посчитать рамку модели и выставить кадр страницы.

    Готовой функции «показать целиком» в языке среды нет — проверено по справке
    04.10.2026: `changeprojectzoom` это загрузка проекта из файла, а раздел «слои
    схемного окна» про видимость слоёв. Поэтому рамку считает сам инструмент,
    а кадр пишется в свойства страницы.

    **Как считается.** Габариты всех блоков страницы объединяются в одну рамку
    (центр из `Points` плюс `get_size`; подписи пропускаются), её центр
    становится `x_center`/`y_center`, а масштаб — отношением полотна снимка к
    рамке с полями `FIT_PADDING`. Полотно 1026x580 — живой замер PNG от
    `savescreenshot`. Фактический размер узнаёт `save_screenshot` по самому
    снимку (он у среды не постоянен: живой замер 05.10.2026 — 1026x580 у части
    снимков и 1026x659 у другой) и передаёт его сюда.

    **Как пишется.** Через блок свойств в `createmodel` — тот же путь, что у
    `import_model_text`: `x_center`, `y_center`, `x_scale`, `y_scale` это
    свойства страницы. Объекты не добавляются, прежний скрипт страницы
    возвращается на место.

    Проверено живьём 04.10.2026: после записи `x_scale = 4` снимок той же
    модели стал другим (4473 байта до, 7923 после) — свойства кадра управляют
    отрисовкой.
    """
    project = session.ensure_project()
    page = project.get_main_page()
    frame = _model_frame(page)
    if frame is None:
        raise ToolError(
            "На странице нет блоков с читаемыми габаритами — подгонять кадр "
            "не по чему: габариты читаются из `Points` и `get_size`."
        )
    left, top, right, bottom = frame
    width = max(right - left, 1.0)
    height = max(bottom - top, 1.0)
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    scale, view_x, view_y = fit_geometry(frame, canvas_w, canvas_h)

    from simintech_api.model_operations import build_import_model_text_body

    from .page_script import refuse_contour_failure, run_contour

    props = (
        "(\n"
        f"  x_center = {view_x:g},\n"
        f"  y_center = {view_y:g},\n"
        f"  x_scale = {scale:g},\n"
        f"  y_scale = {scale:g}\n"
        ")\n"
    )
    outcome, _restored = run_contour(
        build_import_model_text_body(props),
        failed="выставить кадр страницы не удалось")
    refuse_contour_failure(outcome, failed="подгонка кадра")
    return (
        f"Кадр выставлен по рамке модели: {width:.0f}x{height:.0f} px "
        f"на полотне {canvas_w:.0f}x{canvas_h:.0f}.\n"
        f"  центр рамки (координаты модели): ({cx:.1f}, {cy:.1f})\n"
        f"  записано: x_center = {view_x:.1f}, y_center = {view_y:.1f} "
        f"— это смещение в пикселях, экран = модель * масштаб + смещение\n"
        f"  масштаб: {scale:.3f}\n"
        f"Проверьте снимком `save_screenshot`: в кадр обязана попасть вся "
        f"модель целиком, без обрезки по краям."
    )


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def set_block_center(block: str = "", x: Optional[float] = None,
                     y: Optional[float] = None, moves: str = "") -> str:
    """Двинуть блоки центрами в заданные точки — линии перетрассируются.

    Две формы, ровно одна за вызов:

    * одиночная: `block` + `x`, `y` — центр одного блока;
    * пачка: `moves` — строкой «блок=x,y; блок=x,y» (до `MAX_MOVES` токенов);
      все точки применяются одним ходом: одна перерисовка и одна трассировка
      на всю пачку (раскладка «под пины» из десятков блоков — один вызов,
      а не десятки).

    Координаты — **центр блока**: первая точка `Points` (то же значение
    хранит среда; его читают `layout_place` и проверка разметки).

    **Проверка до перемещения.** Все имена сверяются со страницей заранее:
    неизвестное имя — отказ, и тогда не двинут ни один блок — частичное
    применение оставило бы схему в состоянии, которого нет в ответе (сбой
    COM в середине пачки — исключение: откатить его нечем, уцелевшие
    перемещения назовёт следующая сверка). Имя, которое носят несколько
    блоков, тоже отвергается: так адресуют пару «В память»/«Из памяти», и
    какой из двух блоков сдвинется, решил бы порядок обхода страницы, а не
    запрос; такой блок адресуют числовым id (`list_blocks` печатает id).

    После перемещения — перерисовка и трассировка (`NormalizeWire`): порядок
    обязателен и тот же, что в `layout_place`; без перерисовки среда
    прокладывает провода по прежним прямоугольникам блоков (живой замер
    15.09.2026). Нормализация: у готовых маршрутов выгрузка покажет прежние
    точки, пустые она дописывает в данные (замер 04.10.2026) — проверка
    снимком (`save_screenshot`). Ответ называет переход
    центра каждого блока, число обработанных линий и наложения среди
    подвинутых.

    Инструмент **точечный**: размеры блоков не меняются (`set_center`
    сохраняет родной размер класса), стопки порт-блоков не смыкаются —
    расстановку целиком делает `layout_place`.

    Args:
        block: имя блока (автоимя из `list_blocks`) или числовой id —
            одиночная форма.
        x, y: координаты центра — одиночная форма (задаются вместе с
            `block`).
        moves: пачка «блок=x,y; блок=x,y» (до `MAX_MOVES` токенов) — не
            сочетается с `block`.
    """
    project = session.ensure_project()
    page = project.get_main_page()

    # Форма выверяется до чтения страницы и до первого `set_center`: отказной
    # запрос не тратит COM-вызовы, а предел пачки (`MAX_MOVES`) защищает сам
    # COM-поток (CLAUDE.md, инвариант 3).
    single = bool(block.strip())
    if single and moves.strip():
        raise ToolError(
            "не сочетается: либо block с x, y, либо moves — назовите одну "
            "форму.")
    if single:
        if x is None or y is None:
            raise ToolError(
                "для одиночной формы задайте оба числа: block, x, y (или "
                "передайте пачку `moves`).")
        if not math.isfinite(x) or not math.isfinite(y):
            raise ToolError(
                f"координаты должны быть конечными числами: ({x}, {y}).")
        order: list[tuple[str, float, float]] = [(block.strip(), x, y)]
    elif moves.strip():
        order = _parse_moves(moves)
    else:
        raise ToolError(
            "нечего двигать: назовите блок (block, x, y) или пачку "
            "(moves = «блок=200,100; kx_0=400,120»).")

    available: dict[str, Block] = {}
    name_counts: dict[str, int] = {}
    name_ids: dict[str, list[str]] = {}
    for current in page.get_blocks():
        block_id = str(current.id)
        available[block_id] = current
        try:
            current_name = current.get_name()
        except Exception:                                     # noqa: BLE001
            continue
        name_counts[current_name] = name_counts.get(current_name, 0) + 1
        name_ids.setdefault(current_name, []).append(block_id)
        available[current_name] = current

    missing = [name for name, _x, _y in order if name not in available]
    if missing:
        raise ToolError(
            f"блоки не найдены на странице: {', '.join(missing)} — ничего не "
            f"перемещено. Актуальные имена и id даёт `list_blocks`.")

    # Имя, которое носят несколько блоков (пара «В память»/«Из памяти» —
    # один и тот же Name ячейки, живой случай 05.10.2026), не различает их:
    # словарь по имени молча оставил бы один блок, и ответ не сказал бы,
    # какой именно сдвинут. Ид — различает; его печатает `list_blocks`.
    ambiguous = [name for name, _x, _y in order
                 if name_counts.get(name, 0) > 1]
    if ambiguous:
        details = "; ".join(f"«{name}» — id: {', '.join(name_ids[name])}"
                            for name in ambiguous)
        raise ToolError(
            f"имя называют несколько блоков: {details} — адресуйте нужный "
            f"числовым id: имя не различает их, ничего не перемещено.")

    lines = [f"Перемещено блоков: {len(order)}"]
    moved_names: set[str] = set()
    for name, cx, cy in order:
        target = available[name]
        before = None
        try:
            before = first_point(target.get_points())
        except Exception:                                     # noqa: BLE001
            before = None
        target.set_center(cx, cy)
        try:
            moved_names.add(target.get_name())
        except Exception:                                     # noqa: BLE001
            moved_names.add(str(target.id))
        move = (f"({before[0]:g}, {before[1]:g}) → ({cx:g}, {cy:g})"
                if before is not None else
                f"(центр до не прочитан) → ({cx:g}, {cy:g})")
        lines.append(f"  {name}: центр {move}")
    project.repaint()
    rects, no_geometry = _page_rects(page)
    routes = (normalize_page_wires(page)
              + _overlap_report(rects, no_geometry, moved_names))
    return "\n".join(lines) + routes


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def fit_view() -> str:
    """Показать схему целиком: посчитать рамку модели и выставить кадр страницы.

    Обёртка над `apply_fit_view` — тот же шаг `save_screenshot` делает сам перед
    снимком. Рамка считается по габаритам блоков (`Points` плюс `get_size`),
    центр и масштаб переводятся в свойства страницы; подробности и замеры — в
    докстринге `apply_fit_view`.
    """
    return apply_fit_view()
