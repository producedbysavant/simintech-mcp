"""Инструмент расстановки блоков: координаты, размеры, выравнивание проводов.

Порядок операций существенен: переместить блоки → `RepaintEditor` →
`NormalizeWire`. Нормализация до перерисовки не видит новых координат портов,
и провода остаются диагональными.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page
from simintech_api.constants import BLOCK_GAP

from .. import runtime, session
from ..app import mcp
from .layout_geometry import (
    GRID_STEP,
    LABEL_CLASSES,
    MARGIN,
    MAX_REPORTED_OVERLAPS,
    first_point,
    model_frame,
    overlap_report,
    overlap_report_impl as _overlap_report,
    rect_overlaps as _overlaps,
    page_rects as _page_rects,
    rect_of as _rect_of,
    routing_metrics as _routing_metrics,
    shift_to_margin as _shift_to_margin,
    snap_centers as _snap_centers,
    wires_word as _wires_word,
)
from .layout_fit import (
    CANVAS_H,
    CANVAS_W,
    FIT_PADDING,
    apply_fit_view,
    fit_geometry,
)

#: Реэкспорт помощников геометрии и подгонки кадра (они живут в
#: `layout_geometry` и `layout_fit`): прежние точки импорта и атрибуты модуля
#: остаются на месте. Имена в `__all__` pyright и pyflakes не считают
#: неиспользованным импортом.
__all__ = [
    "CANVAS_H",
    "CANVAS_W",
    "FIT_PADDING",
    "GRID_STEP",
    "LABEL_CLASSES",
    "MARGIN",
    "MAX_REPORTED_OVERLAPS",
    "_overlap_report",
    "_overlaps",
    "_page_rects",
    "_rect_of",
    "_routing_metrics",
    "_shift_to_margin",
    "_snap_centers",
    "_wires_word",
    "apply_fit_view",
    "first_point",
    "fit_geometry",
    "model_frame",
    "overlap_report",
]

#: Предел пачки `set_block_center(moves=...)`: каждый токен — до трёх
#: COM-вызовов на единственном COM-потоке (`set_center`, `get_points`,
#: `get_name`), а COM-вызов таймаутом не прерывается (CLAUDE.md, инвариант 3:
#: у параметра, задающего число COM-вызовов, есть явный предел; прецедент —
#: `MAX_BLOCK_PROPS`). Боевой лист — десятки блоков (запрос fdd002); 128 —
#: запас к этому, но не к «тысячам токенов в одном вызове».
MAX_MOVES = 128

#: Классы, стыкующиеся стопкой вплотную (стандарт: «входные порты единой
#: колонкой без зазоров»).
PORT_STACK_CLASSES = ("Порт входа", "Порт выхода")


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

    Пустые маршруты (линии `connect`) нормализация заполняет, а маршруты,
    запечённые средой при импорте текста с проводами, — **не чинит**:
    заполненный маршрут она не перестраивает, и предел её вмешательства
    измерен — конец подтягивается к колонке приёмника, мусорные звенья
    остаются (живые замеры 10.10.2026: звенья вида `(-160,-992)` пережили и
    `normalize_only`, и `save_project` + `reload_project`). Ответ говорит об
    этом прямо, чтобы клиент не искал причину в инструменте.
    """
    wires = page.get_wires()
    for wire in wires:
        wire.normalize()
    if not wires:
        return "\nЛиний связи на странице нет — трассировать нечего"
    return (f"\nЛинии связи: нормализовано {len(wires)} "
            f"{_wires_word(len(wires))} страницы (NormalizeWire) — "
            "сверяйте снимком (`save_screenshot`): у готовых маршрутов "
            "выгрузка покажет прежние точки (их нормализация не "
            "пересчитывает — запечённые импортом текста так и не чинятся), "
            "пустые нормализация дописывает в данные")


def _place_value_labels_after_layout(page: Page,
                                     available: "Dict[str, Block]",
                                     text: str,
                                     lines: "List[str]") -> int:
    """Вернуть подписи значений к их блокам после сдвига блоков.

    Раскладка подписи не двигает (они не блоки, см. `layout_place`), поэтому
    после расстановки подпись остаётся на прежней точке, а её блок уехал.
    Здесь она ставится к родителю тем же правилом, что у `fit_value_labels`.

    `text` - уже снятая выгрузка страницы (её берёт граф открытого проекта):
    карта `parentblock` читается из неё, и второй контурный прогон не нужен.
    Если подписей на странице нет, выгрузка не снимается вовсе: проверка
    идёт по классам уже собранных блоков.

    Сбой подгонки не роняет раскладку: подписи - оформление, а блоки уже
    расставлены. Причина называется строкой в ответе, а не молчанием.
    """
    has_labels = False
    for block in available.values():
        try:
            if block.class_name == "constLabel":
                has_labels = True
                break
        except Exception:                                     # noqa: BLE001
            continue
    if not has_labels:
        return 0
    # Локальный импорт: `fits` импортирует `layout` (first_point,
    # overlap_report), и модульный импорт дал бы цикл.
    from .fits import fit_value_labels_page

    try:
        return fit_value_labels_page(page, "раскладка", lines,
                                     text=text or None)
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"раскладка: подписи значений не поправлены "
                     f"({type(exc).__name__}: {exc}).")
        return 0


@mcp.tool(title="Расставить блоки и трассировать", annotations={"readOnlyHint": False})
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
    У линий с корректным заполненным маршрутом выгрузка (`export_model_text`,
    `savemodeltofile`) после нормализации даёт те же точки бит-в-бит (живой
    замер 03.10.2026). Линиям без маршрута (`points = []` — так приходит
    часть проводов после импорта текста; живой замер 04.10.2026: 18 из 132)
    нормализация маршрут **заполняет** — в выгрузке после неё лежат полные
    ортогональные точки; результат переживает save+reload. Испорченный
    маршрут она при этом **не перестраивает**: замер 10.10.2026 —
    вмешательство ограничилось подтягиванием конца к колонке приёмника, а
    мусорные звенья остались; этим не чинятся и маршруты, испорченные
    импортом текста с проводами, — запечённые до перерисовки звенья
    переживают `normalize_only`, `layout_place` и `save_project` +
    `reload_project` (живые замеры 10.10.2026); такие линии лечит только
    пересоздание. Поэтому проверять нормализацию одной выгрузкой нельзя —
    смотрите снимок (`save_screenshot`).

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

    **Подписи не расставляются, но едут за своими блоками.** Объекты
    оформления (`constLabel`, `TextLabel`, «Комментарий» и прочий
    `NON_BLOCK_CLASSES` библиотеки) не блоки: их `Points` - якорь текста,
    `size` - типовая карточка, и «расставленная» подпись уезжает от своего
    блока (живой случай 02.10.2026). Они исключаются из расстановки и, как
    следствие, из метрики наложений. Но после сдвига блоков подпись
    возвращается к своему родителю (`constLabel` по карте `parentblock`):
    иначе у импортированных моделей подписи остаются в одной точке
    `createmodel` (248, 192) и сходятся «кучей». Карта читается выгрузкой
    страницы: если её уже снимали для графа, второй прогон не платится, а
    на странице без подписей выгрузка не снимается вовсе. Режим
    `normalize_only` блоки не двигает, поэтому подписи в нём не трогаются.

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
    #: Выгрузка страницы, снятая ради графа открытого проекта. После
    #: расстановки она же отдаётся подгонке подписей: карта `parentblock`
    #: живёт в том же тексте, и второй контурный прогон ни к чему.
    graph_text = ""
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
    # Подписи значений едут вместе со своими блоками. Раскладка их не
    # расставляет (см. докстринг), но после сдвига блока подпись остаётся на
    # прежней точке: у импортированных моделей все они стоят в одной точке
    # `createmodel` (248, 192) и сходятся «кучей», а после любой расстановки
    # куча переезжает вместе с моделью. Возврат к родителям: то же правило,
    # что у `fit_value_labels` (якорь на 18 px выше левого верхнего угла).
    label_lines: List[str] = []
    moved_labels = _place_value_labels_after_layout(page, available,
                                                    graph_text, label_lines)
    if moved_labels:
        project.repaint()
        routes += (f"\nПодписи значений подтянуты к блокам: {moved_labels}.")
    if label_lines:
        routes += "\n" + "\n".join(label_lines)
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


@mcp.tool(title="Двинуть блоки", annotations={"readOnlyHint": False})
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


@mcp.tool(title="Показать схему целиком", annotations={"readOnlyHint": False})
@runtime.com_threaded(mutates_project=True)
def fit_view() -> str:
    """Показать схему целиком: посчитать рамку модели и выставить кадр страницы.

    Обёртка над `apply_fit_view` — тот же шаг `save_screenshot` делает сам перед
    снимком. Рамка считается по габаритам блоков (`Points` плюс `get_size`),
    центр и масштаб переводятся в свойства страницы; подробности и замеры — в
    докстринге `apply_fit_view`.
    """
    return apply_fit_view()
