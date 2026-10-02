"""Инструмент расстановки блоков: координаты, размеры, выравнивание проводов.

Порядок операций существенен: переместить блоки → `RepaintEditor` →
`NormalizeWire`. Нормализация до перерисовки не видит новых координат портов,
и провода остаются диагональными.
"""

from __future__ import annotations

import re

from fastmcp.exceptions import ToolError

from .. import runtime, session
from ..app import mcp


#: Сколько пар наложений перечислять в ответе: список — для человека, длинный
#: хвост обрезается (счётчик остаётся).
MAX_REPORTED_OVERLAPS = 10

#: Шаг разметки схемы: 1 квадратик = 8×8 px (стандарт оформления владельца:
#: блоки «сигнал»/«порт»/«константа» — 16 высоты, «Ступенька» — 32; порты
#: стоят в `cx±16, cy`, поэтому кратность 8 у центров даёт сетку и портам).
GRID_STEP = 8.0

#: Классы, стыкующиеся стопкой вплотную (стандарт: «входные порты единой
#: колонкой без зазоров»).
PORT_STACK_CLASSES = ("Порт входа", "Порт выхода")


def _flush_port_stacks(tokens: list, centers: dict, sizes: dict,
                       available: dict) -> int:
    """Стыковать порт-блоки одного класса в слое вплотную.

    Стандарт оформления (#24, п.2): блоки одного вида — стопкой без зазоров,
    «входные порты единой колонкой». Работает по фактическим высотам:
    следующий центр — предыдущий + (h1 + h2)/2, стопка садится точно. Блоки
    без читаемого класса не трогаются; возвращается число сдвинутых.
    """
    layers: dict = {}
    for token in tokens:
        layers.setdefault(centers[token][0], []).append(token)
    moved = 0
    for column in layers.values():
        groups: dict = {}
        for token in column:
            try:
                cls = available[token].class_name
            except Exception:                                     # noqa: BLE001
                continue
            if cls in PORT_STACK_CLASSES:
                groups.setdefault(cls, []).append(token)
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda t: centers[t][1])
            for prev, token in zip(members, members[1:]):
                cy = centers[prev][1] + (sizes[prev][1] + sizes[token][1]) / 2
                centers[token] = (centers[token][0], cy)
                moved += 1
    return moved


def _snap_centers(centers: dict) -> None:
    """Поставить центры блоков на разметку 8 px (и порты — тоже на сетку).

    Выравнивание по портам после этого сетку сохраняет: смещение вход→выход
    — разность кратных 8 координат (порты в `cx±16, cy` и четверть-высоты).
    """
    for token, (cx, cy) in centers.items():
        centers[token] = (round(cx / GRID_STEP) * GRID_STEP,
                          round(cy / GRID_STEP) * GRID_STEP)


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
    pairs = re.findall(r"\(([-\d.]+)\s*,\s*([-\d.]+)\)", points_text or "")
    if not pairs:
        return None
    cx, cy = float(pairs[0][0]), float(pairs[0][1])
    w, h = size
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def _overlaps(rect_a: "tuple[float, float, float, float]",
              rect_b: "tuple[float, float, float, float]") -> bool:
    """Пересекаются ли прямоугольники (строго, касание — не наложение)."""
    return (rect_a[0] < rect_b[2] and rect_b[0] < rect_a[2]
            and rect_a[1] < rect_b[3] and rect_b[1] < rect_a[3])


@mcp.tool()
@runtime._com_threaded(mutates_project=True)
def layout_place(block_ids: str = "", connections: str = "") -> str:
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

    **Проверка наложений.** После расстановки габариты блоков читаются из
    свойства `Points`, и пересекающиеся пары называются в ответе: контракт
    «без наложений» виден фактом, а не предполагается. Пары считаются только
    с участием расставленных блоков — чужие наложения не наша правка.

    **Сетка 8 px и стопки порт-блоков** (стандарт оформления владельца,
    02.10.2026): 1 квадратик разметки — 8×8; центры ставятся на сетку,
    поэтому и порты на ней (порты — `cx±16, cy`); «Порт входа»/«Порт выхода»
    одного слоя стыкуются стопкой вплотную — единой колонкой без зазоров
    (вертикальные зазоры между прочими блоками в стандарте — 8…32 px).

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
    поэтому выравнивание по портам ниже применяется лишь к связям текущей
    сессии, чьи концы запомнил `connect`.

    **Уточнено 2026-09-28 (живой замер, поставка 2.26.6.23):** концы линий
    по-прежнему не читаются через COM — обходной путь (выгрузка контейнера
    встроенным языком) описан в README, «Ограничения»; выравнивание по-прежнему
    работает только по связям текущей сессии.

    Args:
        block_ids: блоки через запятую — имена (`k_0`, `kx_0`, `ToFile_0`; их
            даёт `list_blocks`) или числовые id. Пусто — все блоки главной
            страницы.
        connections: пары `src->dst` через запятую, напр. `k_0->kx_0`. Оба конца
            должны быть перечислены в `block_ids`. Пусто при пустых `block_ids`
            — все связи сессии (их запомнил `connect`); пусто при явных
            `block_ids` — расстановка без учёта связей.
    """
    from simintech_api.layout import LayeredPlacer

    project = session._ensure_project()
    page = project.get_main_page()
    available = {}
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

    # `connect` запоминает концы линий именами блоков (`_WIRES`), а блоки здесь
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

    if bare_call:
        # «Все связи сессии»: концы чужих линий через COM не читаются, поэтому
        # честный максимум — реестр `connect`. Пары, чьи блоки не попали в
        # расстановку, отбрасываются: в реестре могли остаться концы прежней
        # страницы, и строгий отказ по ним сорвал бы вызов «расставь всё».
        by_name: dict[str, str] = {}
        for token in tokens:
            try:
                by_name.setdefault(available[token].get_name(), token)
            except Exception:                                 # noqa: BLE001
                continue
        known_pairs = dict.fromkeys(
            (src, dst) for _wire, src, _out, dst, _in in session._WIRES)
        connections = ",".join(
            f"{by_name[src]}->{by_name[dst]}"
            for src, dst in known_pairs if src in by_name and dst in by_name)

    links = []
    for pair in connections.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "->" not in pair:
            # Молчаливый пропуск здесь — ложный успех: связь не учитывается,
            # блоки встают в один слой, а клиент видит подтверждение
            # расстановки. Отказ называет сам токен, поэтому опечатку
            # (например, типографскую стрелку) видно сразу.
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
    positions = LayeredPlacer().place(tokens, links, sizes=sizes)

    centers = {token: positions[token] for token in tokens}
    # Стопки и сетка — до `set_center` и до выравнивания: выравнивание по
    # портам потом сохраняет сетку (порты стоят в cx±16, cy, смещения
    # вход→выход кратны 8), а стопки успевают развести порт-блоки до того,
    # как источники начнут тянуть за собой приёмники.
    flushed = _flush_port_stacks(tokens, centers, sizes, available)
    _snap_centers(centers)
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
    unaligned = []
    # Смещения портов относительно центров читаем один раз: дальше блоки
    # двигаются, а COM отдаёт координаты портов только после перерисовки —
    # повторное чтение вернуло бы устаревшие значения.
    offsets = {}
    for _wire, src_name, out_index, dst_name, in_index in session._WIRES:
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
    for _wire, src_name, out_index, dst_name, in_index in session._WIRES:
        if in_index != 0:
            continue
        src_token = aliases.get(src_name, src_name)
        dst_token = aliases.get(dst_name, dst_name)
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
    # Трассируем все линии страницы, а не только созданные этой сессией:
    # `page.get_wires()` их перечисляет (так же поступает `list_wires`), а
    # `normalize()` безопасен и на линии открытого проекта. Выравнивание выше
    # остаётся только для связей текущей сессии: концы чужих линий через COM
    # не читаются.
    wires = page.get_wires()
    for wire in wires:
        wire.normalize()
    routes = (f"\nЛинии связи: нормализовано {len(wires)} линий страницы — "
              f"участки ортогональные" if wires
              else "\nЛиний связи на странице нет — трассировать нечего")
    if flushed:
        routes += (f"\nСтопки порт-блоков: сомкнуто вплотную {flushed} — "
                   f"единой колонкой без зазоров")
    if unaligned:
        routes += (f"\nВНИМАНИЕ: выровнять не удалось для {len(unaligned)} "
                   f"связей: {', '.join(unaligned)}")

    # Наложения: контракт «без наложений» проверяется фактом — по габаритам из
    # `Points` всех блоков страницы, прочитанным после перерисовки. Пары — с
    # участием расставленных блоков: чужие наложения не наша правка, но
    # расставленный поверх чужого обязан быть виден.
    placed = set()
    for token in tokens:
        try:
            placed.add(available[token].get_name())
        except Exception:                                     # noqa: BLE001
            placed.add(str(available[token].id))
    rects = []
    no_geometry = []
    for block in page.get_blocks():
        try:
            name = block.get_name()
        except Exception:                                     # noqa: BLE001
            name = str(block.id)
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
    overlaps = []
    for index, (name_a, rect_a) in enumerate(rects):
        if name_a not in placed:
            continue
        for name_b, rect_b in rects[index + 1:]:
            if _overlaps(rect_a, rect_b):
                overlaps.append((name_a, name_b))
    if overlaps:
        shown = ", ".join(f"{a}—{b}"
                          for a, b in overlaps[:MAX_REPORTED_OVERLAPS])
        more = (f" (и ещё {len(overlaps) - MAX_REPORTED_OVERLAPS})"
                if len(overlaps) > MAX_REPORTED_OVERLAPS else "")
        routes += (f"\nВНИМАНИЕ: наложения блоков (нет свободного буфера): "
                   f"{shown}{more}")
    else:
        routes += "\nНаложений блоков нет."
    if no_geometry:
        routes += (f"\nГеометрию прочитать не удалось у {len(no_geometry)} "
                   f"блоков: {', '.join(no_geometry[:MAX_REPORTED_OVERLAPS])}")
    return f"Расставлено блоков: {len(applied)}\n" + "\n".join(applied) + routes
