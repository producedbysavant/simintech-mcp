"""Подгонки по правилам: fit_port_blocks и fit_value_labels.

Ширина порт-блоков и блоков памяти по подписям, высота субмоделей и
мультиплексоров по входным контактам, возврат пина компенсацией центра;
подписи значений — к их блокам-родителям. Обход страниц — порциями по
бюджету COM-вызова с курсором продолжения. Выделено из `blocks.py`
(issue #121).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page

from .. import runtime, session
from ..app import mcp
from ..geometry import CHAR_WIDTH_ESTIMATE
from .blocks import resolve_block
from .check_model import read_port_names
from .layout import first_point
from .page_script import (
    activate_or_refuse,
    refuse_contour_failure,
    return_main_active,
    run_contour,
)
from .model_text import page_export_text
from .sizes import (
    PORT_ROW_HEIGHT,
    PORT_HEIGHT_CLASSES,
    size_pair_lines,
    size_value,
)


#: Классы, которым `fit_port_blocks` подгоняет **ширину** по подписям
#: `PortNames`. Кроме порт-блоков это блоки памяти: замер 07.10.2026 —
#: импорт ставит и «В память», и «Из памяти» 64×16 при любой длине имени
#: (длинная надпись вылезает за рамку), высоту фит у них не трогает —
#: правило высоты у этих классов не измерено, к `PORT_HEIGHT_CLASSES`
#: они поэтому не отнесены (у тех высота жёсткая, и `set_block_size` её
#: проверяет).
_FIT_WIDTH_CLASSES = (*PORT_HEIGHT_CLASSES, "В память", "Из памяти")


#: Классы, чья высота считается построчно (`PORT_ROW_HEIGHT` на **входной**
#: контакт): субмодель (правило владельца 07.10.2026) и мультиплексор
#: (замер 08.10.2026: импорт ставит 32×32 при любом `nport`, входы ложатся с
#: шагом `32 / nport` — при `nport = 8` это 4 px; входов ровно `nport`,
#: выход один, поэтому высота `nport × 16` даёт шаг пинов 16). Демультиплексор
#: сюда не отнесён: его канон (один вход слева, выходы справа) не замерен.
_PORT_ROW_CLASSES = ("Субмодель", "Мультиплексор")


#: Оценка времени одного контурного прогона страницы в фитах: живой замер
#: 06–07.10.2026 — ~20 с (контур перезапускает расчёт), с запасом на
#: перерисовку и трассировку. По ней фиты решают, влезает ли следующая
#: страница в остаток COM_CALL_TIMEOUT.
_PAGE_CONTOUR_ESTIMATE_SECONDS = 25.0


#: Запас бюджета обхода страниц: после последнего прогона фита остаётся
#: перерисовка, NormalizeWire и возврат активной страницы.
_CONTOUR_BUDGET_MARGIN_SECONDS = 30.0


#: Предел обхода субмоделей при подгонках: дерево страниц конечно, но цикл
#: в данных не должен вешать инструмент.
MAX_SUBMODEL_DEPTH = 8


#: Отступ подписи значения от родителя: якорь — у левого верхнего угла
#: блока, на 18 px выше. Образец — боевой проект evs360: `constLabel` с
#: точкой (440, 46) у блока с центром (456, 72) и размером 32×16, то есть
#: (cx − w/2, cy − h/2 − 18) — совпало точно (замер 06.10.2026).
VALUE_LABEL_GAP = 18.0


#: Отложенная правка габарита: (блок, имя, свойство, было, станет, чем
#: обосновано). Правки страницы собираются до общего контурного прогона
#: (см. `_apply_size_plan`), а подтверждение идёт после него перечитыванием.
class _SizeFix(NamedTuple):
    block: Any
    name: str
    prop: str
    before: float
    want: float
    note: str
    #: «Приведение к «Значению»»: число не меняется, снимается формула.
    #: Владелец (07.10.2026): тронутый габаритами блок приводится к
    #: «Значению» по ОБЕИМ осям — у субмоделей ширина оставалась с формулой
    #: (`textvalue=120`), и он снимал её вручную.
    touch: bool = False
    #: Порт-«пин» и его координаты до правки: при смене ширины среда двигает
    #: порты, и центр компенсируется так, чтобы пин остался на месте
    #: (владелец держит пин на фиксированном X и меняет ширину «влево от
    #: пина» — иначе связи изламывались, 26/86 прямых).
    pin: Any = None
    pin_before: Any = None


def _port_pin(block: Block) -> Tuple[Any, Any]:
    """Порт-«пин» блока и его координаты: `(порт, (x, y) | None)`.

    Хватает **любого** порта: при смене ширины среда двигает все порты
    одинаково по X, и по одному видно дельту, которую надо вернуть центру.
    Координаты не читаются — `(порт, None)`: компенсации не будет, и это
    назовётся примечанием, а не молчанием.
    """
    for name in ("get_in_port", "get_out_port"):
        getter = getattr(block, name, None)
        if getter is None:
            continue
        try:
            port = getter(0)
        except Exception:                                     # noqa: BLE001
            continue
        try:
            return port, port.get_coords()
        except Exception:                                     # noqa: BLE001
            return port, None
    return None, None


def _plan_signal_width(block: Block, where: str,
                       lines: List[str], plan: List[_SizeFix]) -> None:
    """Запланировать расширение блока под самую длинную подпись сигнала.

    Для порт-блоков и блоков памяти (`_FIT_WIDTH_CLASSES`). Ширина — по
    той же оценке, что ловит `check_model_layout` (`CHAR_WIDTH_ESTIMATE`
    px на символ): правка и проверка обязаны сходиться, иначе проверка
    продолжила бы ругаться на исправленное. Высота не меняется, но
    **приводится к «Значению»** (владелец 07.10.2026: габариты тронутого
    блока — без формулы по обеим осям). Пин запоминается для компенсации
    центра. Записи здесь нет: саму правку применяет общий прогон страницы.
    """
    try:
        name = block.get_name()
    except Exception:                                         # noqa: BLE001
        name = f"id={block.id}"
    names = read_port_names(block)
    if names is None:
        lines.append(f"{where}: {name}: PortNames не читается — ширина не "
                     f"проверена.")
        return
    if not names:
        return
    try:
        width = float(block.get_size()[0])
        height = float(block.get_size()[1])
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {name}: ширина не читается "
                     f"({type(exc).__name__}: {exc}) — пропущен.")
        return
    longest = max(names, key=len)
    estimate = len(longest) * CHAR_WIDTH_ESTIMATE
    if estimate <= width:
        return
    pin, pin_before = _port_pin(block)
    plan.append(_SizeFix(
        block=block, name=name, prop="Width", before=width,
        want=float(estimate),
        note=(f"«{longest}» ~{estimate:g} px, оценка "
              f"×{CHAR_WIDTH_ESTIMATE:g}"),
        pin=pin, pin_before=pin_before))
    plan.append(_SizeFix(
        block=block, name=name, prop="Height", before=height, want=height,
        note="", touch=True))


def _plan_in_rows_height(block: Block, where: str,
                         lines: List[str], plan: List[_SizeFix]) -> None:
    """Запланировать высоту блока с построчными входами — 16 px на **вход**.

    Правило владельца (07.10.2026): «16 px (2 квадратика) на 1 порт» —
    считаются входные контакты; у субмодели с единственным выходом это его
    формула «(число портов − 1) × 16» (выход уходит на правую сторону рамки
    и строки не занимает). Прежняя формула «× все порты» давала +16 всем
    девяти субмоделям боевой модели — владелец откатил их вручную.

    Тот же счёт — **мультиплексору** (`_PORT_ROW_CLASSES`): его параметр
    `nport` и есть число входов (выход один), поэтому высота `nport × 16`
    задаёт шаг пинов 16 px — как у соседних: импорт ставит 32×32 при любом
    `nport`, и входы ложатся с шагом `32 / nport` (замер 08.10.2026:
    `nport = 8` — шаг 4 px; `32×128` — шаг 16). Ширина не меняется, но
    **приводится к «Значению»** (у субмоделей она оставалась с формулой).
    Записи здесь нет: саму правку применяет общий прогон страницы.
    """
    try:
        name = block.get_name()
    except Exception:                                         # noqa: BLE001
        name = f"id={block.id}"
    try:
        in_ports = int(block.get_in_port_count())
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {name}: порты не читаются "
                     f"({type(exc).__name__}: {exc}) — высота не проверена.")
        return
    if in_ports <= 0:
        return
    want = float(in_ports * PORT_ROW_HEIGHT)
    try:
        height = float(block.get_size()[1])
        width = float(block.get_size()[0])
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {name}: высота не читается "
                     f"({type(exc).__name__}: {exc}) — пропущена.")
        return
    if height == want:
        return
    plan.append(_SizeFix(
        block=block, name=name, prop="Height", before=height, want=want,
        note=f"{in_ports} вход(ов) × {PORT_ROW_HEIGHT:g}"))
    plan.append(_SizeFix(
        block=block, name=name, prop="Width", before=width, want=width,
        note="", touch=True))


def _size_fixes_body(plan: List[_SizeFix]) -> str:
    """Тело контура для плана правок — пары «снять формулу + записать значение».

    Тот же путь, что у `set_block_size` (живые замеры 06.10.2026): COM-метод
    `SetGraphBlockProp` кладёт число в «Формулу», а языковая пара — в
    «Значение». Очистка формулы обязательна и стоит первой: при живой формуле
    `setprop` маскируется (сохранение берёт формулу).
    """
    lines: List[str] = []
    for fix in plan:
        lines += size_pair_lines(fix.block.id, fix.prop, fix.want)
    return "\n".join(lines) + "\n"


def _apply_size_plan(page: Page, where: str, lines: List[str],
                     plan: List[_SizeFix]) -> Tuple[int, bool]:
    """Применить план правок одним контурным прогоном; вернуть число сбывшихся.

    Одним прогоном — не оптимизация, а условие: контур перезапускает расчёт,
    и прогон на каждый блок стоил бы его N раз (у порт-блоков N — десятки).
    Страница перед прогоном активируется: скрипт ставится в текущую страницу,
    и блоки ищутся по id на ней; не удалось активировать — **отказ**, а не
    прогон вслепую: промах записи выглядел бы как «среда не приняла запись»
    и увёл бы диагноз в сторону среды (находка ревью PR #102). Подтверждение
    — перечитывание `get_size`: «среда не приняла запись» остаётся
    примечанием, а не успехом.

    Возврат — `(число изменённых блоков, была ли отвергнутая запись)`:
    у тронутого блока правок две (рабочая ось и приведение второй к
    «Значению»), и заголовок ответа обязан знать про «среда не приняла
    запись», иначе «Габариты подогнаны» противоречило бы собственной строке
    (находка ревью PR #122).
    """
    activate_or_refuse(page, action="габариты не записаны", where=where)
    outcome, _restored = run_contour(_size_fixes_body(plan),
                                     failed="записать габариты не удалось")
    refuse_contour_failure(
        outcome, failed="габариты не записаны",
        unsure="габариты не подтверждены",
        aborted_hint=("Часть правок могла примениться — проверьте габариты "
                      "(`get_block_params` размер не читает; смотрите снимок "
                      "или повторите `fit_port_blocks`)."))
    changed_blocks: set[int] = set()
    rejected = False
    # Сначала — правки рабочих осей (`touch=False`), затем приведения
    # (`touch=True`). Порядок важен: «приведена к „Значению"» говорится
    # только у блока, чья рабочая ось действительно прошла; иначе строка
    # выдавала бы приведение за состоявшееся (находка ревью PR #122:
    # touch-ветка добавляла блок в изменённые даже при отвергнутой записи,
    # и заголовок «Габариты подогнаны» был ложью).
    ordered = ([fix for fix in plan if not fix.touch]
               + [fix for fix in plan if fix.touch])
    for fix in ordered:
        word = "ширина" if fix.prop == "Width" else "высота"
        index = 0 if fix.prop == "Width" else 1
        try:
            after = float(fix.block.get_size()[index])
        except Exception:                                     # noqa: BLE001
            lines.append(f"{where}: {fix.name}: {word} записана, но "
                         f"перечитать не удалось — проверьте снимком.")
            changed_blocks.add(id(fix.block))
            continue
        if after == fix.before:
            if fix.touch:
                if id(fix.block) in changed_blocks:
                    lines.append(
                        f"{where}: {fix.name}: {word} {size_value(fix.before)}"
                        f" — приведена к «Значению» (число не менялось).")
                # Блок с отвергнутой рабочей осью уже назван — приведение
                # здесь не утверждается.
            else:
                rejected = True
                lines.append(f"{where}: {fix.name}: {word} не изменилась "
                             f"({size_value(fix.before)}) — среда не приняла "
                             f"запись.")
            continue
        lines.append(
            f"{where}: {fix.name}: {word} {size_value(fix.before)} → "
            f"{size_value(after)} ({fix.note}).")
        changed_blocks.add(id(fix.block))
        if fix.pin is not None:
            _restore_pin(fix, where, lines)
    return len(changed_blocks), rejected


def _restore_pin(fix: _SizeFix, where: str, lines: List[str]) -> None:
    """Вернуть пин блока на прежний X — сдвигом центра после смены ширины.

    Владелец (07.10.2026): ширину порт-блока он меняет «влево от пина» —
    тогда связи остаются прямыми; фит же расширял от центра, пины съезжали,
    и после него прямых связей стало 26/86 против 69/86 до. Смена ширины
    средой двигает порты на ΔX — центр компенсируется на
    `pin_before.x − pin_after.x`; сторона пина при этом не угадывается:
    дельта берётся фактом.
    """
    if fix.pin_before is None:
        lines.append(f"{where}: {fix.name}: координаты пина не прочитались "
                     f"до правки — центр не сдвинут (проверьте линии).")
        return
    try:
        pin_after = fix.pin.get_coords()
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {fix.name}: пин после правки не читается "
                     f"({type(exc).__name__}: {exc}) — центр не сдвинут "
                     f"(проверьте линии).")
        return
    dx = float(fix.pin_before[0]) - float(pin_after[0])
    if abs(dx) < 0.01:
        return
    center = first_point(fix.block.get_points())
    if center is None:
        lines.append(f"{where}: {fix.name}: центр не читается — пин не "
                     f"возвращён (проверьте линии).")
        return
    fix.block.set_center(center[0] + dx, center[1])
    lines.append(
        f"{where}: {fix.name}: пин возвращён на место — центр сдвинут на "
        f"{size_value(dx)} px (ширина меняется «влево от пина»).")


def _walk_pages(project: Any, main: Page,
                lines: List[str]) -> List[Any]:
    """Страницы модели: [(страница, как назвать)] — главная и субмодели.

    Единый обход подгонок: ширина портов ВНУТРИ субмоделей импортом тоже не
    задаётся, и высота блоков-субмоделей видна только на их страницах.
    Повторный вход в страницу не допускается, глубже `MAX_SUBMODEL_DEPTH` —
    не обходим (с примечанием в `lines`).
    """
    found: List[Any] = [(main, "главная")]
    seen = {main.id}

    def walk(page: Page, label: str, depth: int) -> None:
        if depth > MAX_SUBMODEL_DEPTH:
            lines.append(f"{label}: глубже {MAX_SUBMODEL_DEPTH} уровней не "
                         f"обхожу.")
            return
        try:
            blocks = page.get_blocks()
        except Exception as exc:                              # noqa: BLE001
            lines.append(f"{label}: блоки не перечислить "
                         f"({type(exc).__name__}: {exc}).")
            return
        for block in blocks:
            try:
                class_name = str(block.class_name).strip()
            except Exception:                                 # noqa: BLE001
                continue
            if class_name != "Субмодель":
                continue
            try:
                sub = project.submodel_page(block.id)
            except Exception as exc:                          # noqa: BLE001
                lines.append(
                    f"{label}: страница субмодели блока id={block.id} не "
                    f"читается ({type(exc).__name__}: {exc}) — пропущена.")
                continue
            if sub.id in seen:
                continue
            seen.add(sub.id)
            try:
                sub_name = block.get_name()
            except Exception:                                 # noqa: BLE001
                sub_name = str(block.id)
            sub_label = f"{label} → субмодель '{sub_name}'"
            found.append((sub, sub_label))
            walk(sub, sub_label, depth + 1)

    walk(main, "главная", 1)
    return found


def _foreach_page_within_budget(
        pages: List[Any],
        run: Callable[[Any, str], int],
        resume_key: str) -> Tuple[int, List[Any], Optional[str]]:
    """Обойти страницы порцией, укладывающейся в `COM_CALL_TIMEOUT`.

    Каждая страница фитов стоит один контурный прогон (~20 с, живой замер
    06–07.10.2026: контур перезапускает расчёт). Лимит `COM_CALL_TIMEOUT`
    (120 с) — на ВЕСЬ вызов: на многопстраничной модели обход одной порцией
    его перекрывал, клиент получал отказ «Перезапустите mmain.exe» (неверный
    — COM не завис), а правки доигрывали в фоне (issue #117, находка
    07.10.2026). Здесь обход останавливается **заранее** и называет
    отложенные страницы.

    **Продолжение — с отложенной, а не с первой.** Первая отложенная
    страница запоминается курсором (`session.set_fit_resume`), и следующая
    порция начинается с неё: без этого хвост не обошёлся бы никогда —
    каждая порция снова платила бы за те же начальные страницы (у
    `fit_value_labels` экспорт-разведка платна всегда; находка ревью
    PR #118). Курсор сбрасывается при полном проходе и при смене проекта.

    Первая страница порции берётся **всегда**, даже если по оценке не
    влезает в бюджет: «не обойти ни одной» на большой модели означало бы
    вечный отказ на любом вызове. Если и одна эта страница длится дольше
    лимита, отказ `com_threaded` остаётся — для одиночной страницы бюджет
    гарантий не даёт, и это цена безусловного прогресса (находка ревью
    PR #118; разбить один прогон страницы нельзя — контур исполняется
    целиком).

    Возврат — `(суммарный результат run, отложенные страницы,
    where продолженной страницы | None)`; у отложенных сохраняется их
    `(page, where)`.
    """
    budget = runtime.COM_CALL_TIMEOUT - _CONTOUR_BUDGET_MARGIN_SECONDS
    started = time.monotonic()
    total = 0
    start = 0
    resumed_where: Optional[str] = None
    resume_id = session.fit_resume(resume_key)
    if resume_id is not None:
        for index, (page, _where) in enumerate(pages):
            if getattr(page, "id", None) == resume_id:
                start = index
                resumed_where = pages[index][1]
                break
        # Страницы-курсора нет (модель изменилась): обход с начала — курсор
        # снимается, чтобы не сбивать и следующие порции.
        if resumed_where is None:
            session.clear_fit_resume(resume_key)
    for index in range(start, len(pages)):
        if index > start and (time.monotonic() - started
                              + _PAGE_CONTOUR_ESTIMATE_SECONDS > budget):
            next_id = getattr(pages[index][0], "id", None)
            if next_id is not None:
                session.set_fit_resume(resume_key, next_id)
            return total, list(pages[index:]), resumed_where
        total += run(*pages[index])
    session.clear_fit_resume(resume_key)
    return total, [], resumed_where


def _coverage_notes(pages: List[Any], deferred: List[Any],
                    tool: str) -> Tuple[str, str]:
    """Строки охвата для ответов обоих фитов: (счётчик, хвост отложенных).

    Одна точка сборки — формулировки не могут разойтись между
    `fit_port_blocks` и `fit_value_labels` (находка ревью PR #118).
    """
    walked = (f"{len(pages) - len(deferred)} из {len(pages)}" if deferred
              else f"{len(pages)}")
    tail = ("\n" + _deferred_pages_note(deferred, tool) if deferred else "")
    return walked, tail


def _deferred_pages_note(deferred: List[Any], tool: str) -> str:
    """Строка ответа про отложенные страницы — «повторите, он идемпотентен».

    Не «ВНИМАНИЕ» и не отказ: порция — штатный режим инструмента на большой
    модели, и адресат сообщения — не поломка, а неполный охват.
    """
    names = "; ".join(str(where) for _page, where in deferred)
    return (f"Обошёл не все страницы: {len(deferred)} отложено — вызов "
            f"ограничен временем одного COM-вызова "
            f"({runtime.COM_CALL_TIMEOUT:.0f} с; каждая страница — контурный "
            f"прогон ~{_PAGE_CONTOUR_ESTIMATE_SECONDS:.0f} с). Повторите "
            f"`{tool}`: обход продолжится с отложенной страницы (уже "
            f"сделанное не трогается). Отложены: {names}.")


def _run_fits_over_pages(pages: List[Any], run_page: Callable[[Any, str], int],
                         tool: str, main: Any,
                         lines: List[str]) -> Tuple[int, List[Any], str, str]:
    """Оркестровка порционного обхода — одна на оба фита.

    Обход с курсором, возврат главной активной, строка продолжения и охват
    (`_coverage_notes`) собираются здесь; в фитах остаётся только суть
    страницы (`run_page`). Копия расходилась бы при правке курьера,
    активации или формулировок (находка ревью PR #122).
    """
    try:
        total, deferred, resumed = _foreach_page_within_budget(
            pages, run_page, tool)
    finally:
        # Активной возвращается главная — и при отказе посередине обхода:
        # `_apply_size_plan` мог отказать на странице субмодели, оставив её
        # текущей, а следующая контурная операция (выгрузка, снимок) снимает
        # ИМЕННО активную страницу (находка ревью PR #102). Провал возврата —
        # примечанием: молчаливый `pass` выдавал бы успех с чужой активной
        # страницей (находка ревью PR #122).
        return_main_active(main, lines)
    if resumed:
        lines.append(f"Обход продолжен с отложенной страницы: {resumed}.")
    walked, tail = _coverage_notes(pages, deferred, tool)
    return total, deferred, walked, tail


def _fit_page_sizes(page: Page, where: str, affected: List[Any],
                    lines: List[str]) -> Tuple[int, bool]:
    """Поправить габариты одной страницы; вернуть (число, был ли отказ записи).

    Порт-блоки — ширина по подписям (та же оценка, что у
    `check_model_layout`); блоки-субмодели — высота по числу **входных**
    контактов. Правки собираются в план и применяются **одним** контурным
    прогоном (`_apply_size_plan`): запись идёт в «Значение», а не в
    «Формулу», и обе оси тронутого блока приводятся к «Значению».
    """
    try:
        blocks = page.get_blocks()
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: блоки не перечислить "
                     f"({type(exc).__name__}: {exc}).")
        return 0, False
    plan: List[_SizeFix] = []
    for block in blocks:
        try:
            class_name = str(block.class_name).strip()
        except Exception:                                     # noqa: BLE001
            continue
        if class_name in _FIT_WIDTH_CLASSES:
            _plan_signal_width(block, where, lines, plan)
        elif class_name in _PORT_ROW_CLASSES:
            _plan_in_rows_height(block, where, lines, plan)
    if not plan:
        return 0, False
    changed, rejected = _apply_size_plan(page, where, lines, plan)
    if changed and page not in affected:
        affected.append(page)
    return changed, rejected


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def fit_port_blocks() -> str:
    """Подогнать габариты блоков, связанных с портами, по правилам.

    Три правила (наблюдения владельца: замеры 06.10 и боевой прогон
    с ручной доводкой 07.10.2026; правило мультиплексора — замер 08.10.2026):

    * **ширина порт-блоков и блоков памяти** — по длиннейшей подписи
      `PortNames` (импорт нормализует рамку ~32 px независимо от поданных
      `points`, и длинные имена вылезают; у «В память»/«Из памяти» замер
      07.10.2026 — 64×16 при любой длине имени). Оценка та же, что у
      `check_model_layout` (`CHAR_WIDTH_ESTIMATE` px/символ): после
      подгонки его примечание «подписи шире рамки» снимается (само
      примечание — не дефект: свисание текста владелец допускает —
      замечание 07.10.2026);
    * **высота субмодели и мультиплексора** — `PORT_ROW_HEIGHT` px на
      **входной** контакт: считаются входы, не все порты (у субмодели с
      единственным выходом — «все порты − 1»; выход уходит на правую сторону
      рамки и строки не занимает — правило владельца 07.10.2026). Импорт
      ставит субмоделям 48×32, мультиплексору — 32×32 независимо от
      `nport` (его `nport` и есть число входов; входы ложатся с шагом
      `32 / nport`: при `nport = 8` это 4 px), и фит даёт шаг 16, как у
      соседей (замер 08.10.2026; ширина не меняется);
    * **пин на месте** — при смене ширины порт-блока центр компенсируется
      так, чтобы координата порта не сдвинулась: владелец держит пин на
      фиксированном X и меняет ширину «влево от пина» — иначе связи
      изламываются (после прежней подгонки от центра прямых связей стало
      26/86 против 69/86 до).

    **Обе оси — в «Значение».** Блок, тронутый правкой, приводится к
    «Значению» и по ширине, и по высоте, даже если число не меняется:
    прежняя версия трогала одну ось, и у субмоделей ширина оставалась с
    формулой (`textvalue` = числу) — владелец снимал её вручную. Обычная
    правка идёт языковой парой `setpropformula(…)` + `setprop(…)` (как у
    `set_block_size`): COM-путь `SetGraphBlockProp` кладёт число в
    «Формулу».

    **Субмодели обходятся рекурсивно** (`Project.submodel_page`, предел
    `MAX_SUBMODEL_DEPTH`, защита от повторного входа): ширина портов ВНУТРИ
    субмоделей тем же импортом тоже не задаётся, а `check_model_layout`
    смотрит только текущую страницу. Активной в конце снова становится
    главная страница; план правок страницы применяется **одним** контурным
    прогоном — контур перезапускает расчёт, и прогон на каждый блок стоил
    бы его N раз; страница перед прогоном активируется.

    **Обход — порциями по бюджету.** Каждая страница стоит один контурный
    прогон (~20 с), а COM-вызов ограничен `COM_CALL_TIMEOUT` (120 с): на
    многопстраничной модели одна порция перекрывала лимит — клиент получал
    отказ «Перезапустите mmain.exe» (неверный: COM не завис), а правки
    доигрывали в фоне (issue #117). Теперь обход останавливается заранее и
    называет отложенные страницы; курсор сессии
    (`session.set_fit_resume`) запоминает первую отложенную, и повторный
    вызов **продолжает с неё** — без курсора порция снова платила бы за
    начальные страницы, и хвост не обошёлся бы никогда (находка ревью
    PR #118). Уже подогнанное не трогается (идемпотентность замерена
    07.10.2026).

    После правок — перерисовка и трассировка линий затронутых страниц
    (`NormalizeWire`), как у `set_block_size`.

    **Только по правилам.** Блок, уже им соответствующий, не трогается:
    ширину, выставленную руками с запасом, инструмент не «оптимизирует»
    (сужает), высоту — не подгоняет под иные представления.
    """
    project = session.ensure_project()
    main = project.get_main_page()
    lines: List[str] = []
    pages = _walk_pages(project, main, lines)
    affected: List[Any] = []
    rejected_any = False

    def run_page(page: Any, where: str) -> int:
        nonlocal rejected_any
        changed_count, rejected = _fit_page_sizes(page, where, affected,
                                                  lines)
        rejected_any = rejected_any or rejected
        return changed_count

    changed, deferred, walked, tail = _run_fits_over_pages(
        pages, run_page, "fit_port_blocks", main, lines)
    if not changed and rejected_any:
        # Запись не прошла: «менять нечего» тут — ложь (план был, среда его
        # не приняла) — находка ревью PR #122.
        head = (f"Габариты не изменились: среда не приняла запись (оценка "
                f"×{CHAR_WIDTH_ESTIMATE:g} px/символ; страниц обойдено: "
                f"{walked}).")
        result = head + ("\n" + "\n".join(lines) if lines else "") + tail
    elif not changed:
        # «Менять нечего» не должно звучать как «всё осмотрено», если часть
        # страниц отложена бюджетом: вывод говорится только о виденном.
        what = ("менять нечего" if not deferred else
                "менять нечего на обойдённых страницах")
        head = (f"Габариты по правилам: {what} (оценка "
                f"×{CHAR_WIDTH_ESTIMATE:g} px/символ; страниц обойдено: "
                f"{walked}).")
        result = head + ("\n" + "\n".join(lines) if lines else "") + tail
    else:
        project.repaint()
        for page in affected:
            try:
                for wire in page.get_wires():
                    wire.normalize()
            except Exception as exc:                          # noqa: BLE001
                lines.append(f"линии страницы не трассированы "
                             f"({type(exc).__name__}: {exc}).")
        caution = ("; часть записей среда не приняла — см. ниже"
                   if rejected_any else "")
        result = (f"Габариты подогнаны: {changed}{caution} (страниц обойдено:"
                  f" {walked}).\n" + "\n".join(lines)) + tail
    return result


#: Кавычечные литералы выгрузки: двойные с `\`-экранированием и одинарные
#: с удвоением (строки языка). Содержимое вырезается перед подсчётом скобок.
_QUOTED_LITERALS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\'\'|[^\'])*\'')


def _unquoted(line: str) -> str:
    """Строка без содержимого кавычечных литералов — для счёта скобок.

    Выгрузка несёт `script` страницы **одной строкой** с экранированными
    `\\n` (живой пример в `test_language_contour_live`), а в тексте скрипта
    бывают скобки (`writelnutf8(fid, "тест (1")`): одиночная `(` уводила
    счётчик глубины навсегда, `top_level` перестал совпадать с записями, и
    карта подписей возвращалась пустой — `fit_value_labels` молча отвечал
    «подписи на месте» (находка ревью PR #96, воспроизведено).
    """
    return _QUOTED_LITERALS.sub("", line)


def _constlabel_parents(text: str) -> Dict[str, str]:
    """Карта «подпись значения → блок-родитель» из выгрузки страницы.

    `parentblock` через COM не читается (замер 06.10.2026: `getpropasstring`
    по обоим написаниям пуст), поэтому связь берётся из текста
    `savemodeltofile` — того же источника, что у автографа `layout_place`.

    **Только верхний уровень.** Выгрузка несёт вложенные страницы на всю
    глубину — `subsystem:` и скобочный блок (замер 06.10.2026) — и без
    фильтра пары субмоделей попадали бы в карту страницы-владельца: на
    страницах с совпавшими автоименами (`TextLabel7` у главной и субмодели
    в живой пробе) подпись могла быть «подтянута» по чужой паре. Глубина
    считается по скобкам построчно (скобки `points=[…]` сбалансированы в
    строке); содержимое кавычечных литералов в счёт не идёт (`_unquoted`) —
    скрипт страницы и строковые значения иначе сбивают баланс.
    """
    result: Dict[str, str] = {}
    name = ""
    is_label = False
    parent = ""
    depth = 0
    for line in text.splitlines():
        raw = line.strip()
        stripped = _unquoted(raw)
        top_level = depth == 1
        depth += stripped.count("(") - stripped.count(")")
        match = re.match(r"^([A-Za-z_][\w]*):\s*\(\s*$", stripped)
        if match:
            if is_label and parent and name:
                result[name] = parent
            name = match.group(1) if top_level else ""
            is_label = False
            parent = ""
            continue
        if not name:
            continue
        # Разбор значений — по исходной строке: `_unquoted` вырезает их
        # содержимое, и по очищенной `"constLabel"` уже не найти.
        if raw.startswith("type ="):
            is_label = "constLabel" in raw
        elif "parentblock" in raw and "=" in raw:
            parent = raw.split("=", 1)[1].strip().strip(",").strip('"')
    if is_label and parent and name:
        result[name] = parent
    return result


def _fit_value_labels_page(page: Page, where: str,
                           lines: List[str]) -> int:
    """Подтянуть подписи значений одной страницы к их блокам.

    Выгрузка идёт **текущей страницей** (контур ставит скрипт туда), поэтому
    страница активируется здесь же — иначе снялась бы та, что осталась
    активной от предыдущего обхода (живой случай 06.10.2026: после
    `fit_port_blocks` активной была субмодель, и выгрузка принесла её текст
    без единой подписи). Положение — с образца боевого проекта: якорь
    подписи = `(cx − w/2, cy − h/2 − VALUE_LABEL_GAP)` у родителя.
    `set_center` у подписи ставит её карточку (60×40) центром — поэтому к
    цели добавляется полуразмер карточки (живой замер 06.10.2026: центру
    (100, 100) отвечает якорь (70, 80)).
    """
    page.activate()
    try:
        text, _truncated, _outcome, _path = page_export_text()
    except ToolError as exc:
        lines.append(f"{where}: выгрузка для чтения parentblock не удалась "
                     f"({exc}) — подписи не тронуты.")
        return 0
    pairs = _constlabel_parents(text)
    moved = 0
    for label_name, parent_name in pairs.items():
        label = resolve_block(page, label_name)
        parent = resolve_block(page, parent_name)
        if label is None or parent is None:
            lines.append(f"{where}: подпись «{label_name}» или её блок "
                         f"«{parent_name}» не найдены — не тронута.")
            continue
        try:
            center = first_point(parent.get_points())
            width, height = parent.get_size()
            anchor = first_point(label.get_points())
            label_w, label_h = label.get_size()
        except Exception as exc:                              # noqa: BLE001
            lines.append(f"{where}: {label_name}: геометрия не читается "
                         f"({type(exc).__name__}: {exc}) — пропущена.")
            continue
        if center is None or anchor is None:
            lines.append(f"{where}: {label_name}: точки не разбираются — "
                         f"пропущена.")
            continue
        want = (center[0] - float(width) / 2.0,
                center[1] - float(height) / 2.0 - VALUE_LABEL_GAP)
        if abs(anchor[0] - want[0]) < 0.5 and abs(anchor[1] - want[1]) < 0.5:
            continue
        label.set_center(want[0] + float(label_w) / 2.0,
                         want[1] + float(label_h) / 2.0)
        try:
            after = first_point(label.get_points())
        except Exception:                                     # noqa: BLE001
            after = None
        if after is None:
            lines.append(f"{where}: {label_name}: подпись сдвинута, но точку "
                         f"перечитать не удалось — проверьте снимком.")
            moved += 1
            continue
        lines.append(
            f"{where}: {label_name}: подпись ({anchor[0]:g}, {anchor[1]:g}) "
            f"→ ({after[0]:g}, {after[1]:g}) — к блоку «{parent_name}».")
        moved += 1
    return moved


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def fit_value_labels() -> str:
    """Вернуть подписи значений (`constLabel`) к их блокам-родителям.

    Импорт создаёт подпись значения (например, «1» у усилителя) **всегда в
    одной точке (248, 192)** — независимо от блока и от поданных `points`
    (замер 06.10.2026: две подписи разных блоков получили одну и ту же
    точку), и в GUI подпись «слетает» далеко от своего блока. Инструмент
    ставит её к левому верхнему углу родителя, на `VALUE_LABEL_GAP` выше —
    положение снято с боевого проекта evs360 (совпало точно).

    Связь «подпись → родитель» читается **выгрузкой страницы**
    (`parentblock` через COM не отдаётся — замер 06.10.2026): подпись без
    родителя или уже стоящая на месте не трогается.

    **Субмодели обходятся рекурсивно** (`Project.submodel_page`, предел
    `MAX_SUBMODEL_DEPTH`, защита от повторного входа) — тем же обходом, что у
    `fit_port_blocks`: подписи значений бывают и на внутренних страницах, а
    выгрузка снимается активной страницей, поэтому каждая страница
    активируется перед съёмкой. Активной в конце снова становится главная.

    **Обход — порциями по бюджету** — как у `fit_port_blocks`: страница
    стоит один контурный прогон (выгрузка), и на многопстраничной модели
    одна порция перекрывала `COM_CALL_TIMEOUT`; отложенные страницы
    называются в ответе, а курсор сессии запоминает первую из них — именно
    **с неё** начнётся повторный вызов. Без курсора экспорт-разведка
    (платная на каждой странице) заставляла бы каждую порцию заново
    оплачивать начальные страницы, и хвост не обошёлся бы никогда
    (находка ревью PR #118). Уже подтянутое не трогается (идемпотентность
    замерена 07.10.2026).
    """
    project = session.ensure_project()
    main = project.get_main_page()
    lines: List[str] = []
    pages = _walk_pages(project, main, lines)
    moved, deferred, walked, tail = _run_fits_over_pages(
        pages,
        lambda page, where: _fit_value_labels_page(page, where, lines),
        "fit_value_labels", main, lines)
    if not moved:
        # «На месте» не должно звучать как «всё обойдено», если часть
        # страниц отложена бюджетом: охват называется и здесь.
        head = ("Подписи значений на месте." if not deferred else
                f"Подписи значений на месте на обойдённых страницах "
                f"(страниц обойдено: {walked}).")
        return head + ("\n" + "\n".join(lines) if lines else "") + tail
    project.repaint()
    return (f"Подписи значений подтянуты к блокам: {moved} "
            f"(страниц обойдено: {walked}).\n" + "\n".join(lines)) + tail
