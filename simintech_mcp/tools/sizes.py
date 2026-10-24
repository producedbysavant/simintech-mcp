"""Габариты блоков: set_block_size, правило высоты и запись размера.

Ядро габаритных правок: запись размера в «Значение» (языковой парой
`setpropformula` + `setprop`) и правило «16 px на входной контакт» для
порт-блоков и nport-классов. Фит-подгонки живут в `fits.py`. Выделено из
`blocks.py` (issue #121).
"""

from __future__ import annotations

from typing import Optional

from fastmcp.exceptions import ToolError
from simintech_api import Block

from .. import runtime, session
from ..app import mcp
from ..rules import (NPORT_SIZE_CLASSES, PORT_HEIGHT_CLASSES,
                     PORT_MARGIN, PORT_ROW_HEIGHT, required_height)
from .block_size import size_value, write_block_size
from .blocks import missing_block, read_port_names, resolve_block


#: Предел размера блока в пикселях. Это не предел числа COM-вызовов, а
#: отсечка очевидных ошибок единиц изменения («360» против «360 мм» или
#: «3.6»): настоящий размер блока — десятки-сотни пикселей.
MAX_BLOCK_SIZE = 10000


def _size_text(sizes: tuple[float, float]) -> str:
    """Размер для ответов: `360x120` — формат среды (`GetBlockPropAsString`)."""
    width, height = sizes
    return f"{size_value(width)}x{size_value(height)}"


#: Константы правила (16 px на входной контакт, 8 px поля, штатная ширина
#: 32) живут в `rules.py`: их читают подгонка, проверка и ресурс-руководство.
#: Запись размера в «Значение» вынесена в `block_size.py`, чтобы не было
#: цикла `blocks` <-> `sizes`.


def _port_signal_count(target: Block) -> Optional[int]:
    """Число сигналов порт-блока по `PortNames`; `None` — прочитать не удалось.

    Пустой список — тот же `None`, а не ноль сигналов: у порт-блока имя есть
    всегда (замер: `'in\r\n'` сразу после создания), поэтому «пусто» здесь
    означает отказ чтения, а не измеренное состояние.
    """
    names = read_port_names(target)
    if names is None or not names:
        return None
    return len(names)


def _class_name(name: str, target: Block) -> str:
    """Имя класса блока или отказ, если прочитать нечем.

    Проверка высоты не имеет права на fail-open: нечитаемый класс нельзя
    отнести ни к правилу, ни вне его, а запись наугад могла бы испортить
    отображение (находка ревью).
    """
    try:
        class_name = str(target.class_name).strip()
    except Exception as exc:                                      # noqa: BLE001
        raise ToolError(
            f"Высоту блока '{name}' задать нельзя: класс блока не читается "
            f"({type(exc).__name__}: {exc}), а высота блоков с входными "
            f"контактами подчиняется правилу {PORT_ROW_HEIGHT} px на вход - "
            f"к правилу ли этот блок, проверить нечем."
        ) from exc
    if not class_name:
        raise ToolError(
            f"Высоту блока '{name}' задать нельзя: имя класса прочиталось "
            f"пустым, а высота блоков с входными контактами подчиняется "
            f"правилу {PORT_ROW_HEIGHT} px на вход - к правилу ли этот блок, "
            f"проверить нечем.")
    return class_name


def _required_height(name: str, target: Block,
                     class_name: Optional[str] = None) -> Optional[int]:
    """Обязательная высота блока или `None`, если правило к классу не относится.

    Два правила:

    * порт-блоки («Порт входа»/«Порт выхода») - 16 px на строку сигнала
      `PortNames` (правило владельца 01.10.2026);
    * блоки с собственным числом входов (`NPORT_SIZE_CLASSES`) - 16 px на
      **входной контакт** (живой замер 23.10.2026: при 4 входах высота 64
      даёт шаг портов 16 и поля 8 / 8). Субмодель правилом подгонки
      покрыта, но `set_block_size` её высоту не запирает: у неё бывает
      собственная ширина и оформление.

    Нечитаемый класс или число сигналов/входов - отказ (`ToolError`), а не
    пропуск проверки: иначе запись ломающей высоты вернулась бы успехом.
    """
    class_name = class_name or _class_name(name, target)
    if class_name in PORT_HEIGHT_CLASSES:
        count = _port_signal_count(target)
        if count is None:
            raise ToolError(
                f"Высоту порт-блока '{name}' задать нельзя: у класса "
                f"«{class_name}» высота подчиняется правилу {PORT_ROW_HEIGHT} "
                f"px на строку сигнала, а список сигналов (`PortNames`) не "
                f"читается - проверить высоту нечем, и запись наугад могла "
                f"бы испортить отображение.")
        return required_height(class_name, count)
    if class_name in NPORT_SIZE_CLASSES:
        try:
            in_ports = int(target.get_in_port_count())
        except Exception as exc:                                  # noqa: BLE001
            raise ToolError(
                f"Высоту блока '{name}' задать нельзя: у класса "
                f"«{class_name}» высота подчиняется правилу {PORT_ROW_HEIGHT} "
                f"px на входной контакт, а число входов не читается "
                f"({type(exc).__name__}: {exc}) - проверить высоту нечем."
            ) from exc
        return required_height(class_name, in_ports)
    return None


@mcp.tool(title="Размер блока", annotations={"readOnlyHint": False})
@runtime.com_threaded(mutates_project=True)
def set_block_size(block: str, width: float, height: float) -> str:
    """Задать размер блока в пикселях схемы.

    Размер — **графическое** свойство; пишется языковой парой
    `setpropformula(…, "")` + `setprop(…)` — в «Значение», как ввод в GUI
    без формулы (живые замеры 06.10.2026: после пары `getprop`/`get_size`
    читают новое, `.xprt` несёт `value=N`, `textvalue=''`). Скрипт ставится в
    **текущую** страницу, поэтому страница блока перед записью делается
    активной; не удалось — отказ, а не запись вслепую (находка ревью PR #94:
    прежний COM-путь от активной страницы не зависел). Прежний путь
    `SetGraphBlockProp` отвергнут: он писал габарит в «Формулу», и диалог
    свойств показывал расхождение (у владельца: Формула=352, Значение=64).
    Обычный `SetBlockProp` (и `set_block_param`) эти имена молча игнорирует —
    в каталоге параметров их поэтому нет. Замер 01.10.2026 (поставка
    2.26.6.23): «Усилитель» 32×32 → 140×80; у «Порта входа» через
    `SetGraphBlockProp` гналась и высота (64×16 → 360×120) — ровно тот
    случай, который теперь отвергается правилом ниже: у однозначного порта
    высота 16, и 120 ломает строки. Нечётные и дробные значения принимаются
    (141×79 применились и удержались) — сетки 2 px у этого пути нет.

    Заданный размер **переживает инициализацию**: после `ProjectStart`
    прочитанные габариты не изменились (замер там же) — заново среда блок не
    ужимает. После записи схема перерисовывается, и линии страницы
    перетрассировуются (`NormalizeWire`): координаты портов после смены
    размера сместились, как при перемещении блока.

    **Порты блока «Язык программирования» разводит среда.** После смены
    габаритов они пересчитываются сами, ровно по новому размеру: живой замер
    06.10.2026 (2.26.9.29; блок с входами u/a/s и выходом y) — 48×32 → 64×96
    дал входы на шаг 32 (84/116/148), 64×96 → 64×160 — на шаг 160/3, и это с
    уже подключённой линией. Отдельный `findportspositions` через
    `run_page_script` на разведённых портах идемпотентен (координаты не
    меняются) и конвейеру не нужен; прежнее наблюдение «порты остаются
    сжатыми» (замер 02.10.2026, 2.26.6.23) на актуальной поставке не
    воспроизводится.

    **У порт-блоков высота не свободна.** Классы «Порт входа»/«Порт выхода»
    подчиняются жёсткому правилу: `PORT_ROW_HEIGHT` px на строку сигнала — у
    блока из двух сигналов высота 32. Число строк читается из `PortNames`
    (COM отдаёт имена построчно: `'a1\r\na2\r\n'`; задаются они скриптом
    или импортом — замер 01.10.2026), а среда габарит сама не подгоняет:
    порт с двумя именами остаётся 64×16, и строки ломаются. Поэтому другая
    высота — отказ с названным правильным значением, а нечитаемый список
    сигналов — тоже отказ (проверить правило нечем). Принятую средой высоту
    вне правила инструмент тоже отвергает, а не выдаёт за успех с
    примечанием. Ширина задаётся свободно.

    Ответ печатает **перечитанный** размер. Принятое средой значение с
    отличием от запрошенного — примечание; вовсе не изменившийся размер —
    отказ: размера, которого нет, — не успех.

    **Другие блоки не двигаются**: размер задаётся только названному блоку —
    координаты и габариты остальных не меняются, перетрассировка касается лишь
    линий (контракт #24 п.6); ручная раскладка переживает повторные правки.

    Args:
        block: имя блока (автоимя из `list_blocks`) или его числовой id.
        width: ширина в пикселях (> 0, не больше `MAX_BLOCK_SIZE`).
        height: высота в пикселях (> 0, не больше `MAX_BLOCK_SIZE`).
    """
    if not 0 < width <= MAX_BLOCK_SIZE:
        raise ToolError(
            f"Ширина {size_value(width)} вне пределов: ширина — в пикселях, "
            f"больше 0 и не больше {MAX_BLOCK_SIZE} (отсечка ошибок единиц "
            f"измерения: настоящий размер блока — десятки-сотни пикселей).")
    project = session.ensure_project()
    page = project.get_main_page()
    target = resolve_block(page, block)
    if target is None:
        return missing_block(block)
    class_name = _class_name(block, target)
    required = _required_height(block, target, class_name=class_name)
    if required is not None and float(height) != float(required):
        height_text = (f"{height:g}" if float(height).is_integer()
                       else repr(float(height)))
        unsat = ""
        if required > MAX_BLOCK_SIZE:
            unsat = (f" Правило даёт {required} px, а предел размера — "
                     f"{MAX_BLOCK_SIZE}: этому блоку высоту этим инструментом "
                     f"задать нельзя вовсе.")
        if class_name in PORT_HEIGHT_CLASSES:
            raise ToolError(
                f"Высоту порт-блока '{block}' задать нельзя: у «Порта входа»/"
                f"«Порта выхода» высота подчиняется правилу {PORT_ROW_HEIGHT} px "
                f"на строку сигнала - у этого блока строк "
                f"{required // PORT_ROW_HEIGHT} (`PortNames`), значит высота - "
                f"{required} px, а запрошено {height_text}. Среда габарит сама "
                f"не подгоняет (замер 01.10.2026: порт с двумя именами "
                f"остаётся 64×16), и другая высота ломает отображение строк; "
                f"ширина при этом свободна." + unsat)
        raise ToolError(
            f"Высоту блока '{block}' задать нельзя: у класса «{class_name}» "
            f"высота подчиняется правилу {PORT_ROW_HEIGHT} px на входной "
            f"контакт (порт посередине, по {PORT_MARGIN} px сверху и снизу) - "
            f"входов {required // PORT_ROW_HEIGHT}, значит высота {required} "
            f"px, а запрошено {height_text}. Ширина свободна." + unsat)
    if not 0 < height <= MAX_BLOCK_SIZE:
        # Предельная проверка высоты стоит ПОСЛЕ правила: запрос ровно
        # правила (11200 при 700 строках) иначе упирался бы в предел, не
        # услышав, что тупик неразрешим (находка ревью).
        unsat = ""
        if required is not None and required > MAX_BLOCK_SIZE:
            unsat = (f" Учтите: правило {PORT_ROW_HEIGHT} px требует "
                     f"для этого блока {required} px — этим инструментом "
                     f"высоту ему задать нельзя вовсе.")
        raise ToolError(
            f"Высота {size_value(height)} вне пределов: высота — в пикселях, "
            f"больше 0 и не больше {MAX_BLOCK_SIZE} (отсечка ошибок единиц "
            f"измерения: настоящий размер блока — десятки-сотни пикселей)."
            + unsat)
    requested = (float(width), float(height))
    # Порядок как у расстановки: изменённая геометрия → перерисовка →
    # трассировка линий (контракт `layout_place`). Хвост идёт ПОСЛЕ
    # состоявшейся записи: его сбой не должен выглядеть отказом правки
    # (форма та же, что у `remove_block`; находка ревью PR #122 — ручной
    # цикл по линиям не был защищён и ронял уже записанный размер).
    result = write_block_size(page, target, width, height,
                              action="размер не записан")
    before, after = result.before, result.after
    if required is not None and float(after[1]) != float(required):
        # Среда могла преобразовать запись (как `_EvenOnlyBlock` в тестах):
        # «успех с примечанием» здесь оставил бы порт со сломанным
        # отображением и отчитался успехом (находка ревью).
        if class_name in PORT_HEIGHT_CLASSES:
            raise ToolError(
                f"Размер блока '{block}' записан, но среда приняла высоту "
                f"{_size_text(after)}: у порт-блока она обязана быть "
                f"{required} px ({PORT_ROW_HEIGHT} на строку, строк "
                f"{required // PORT_ROW_HEIGHT}) - отображение строк "
                f"испорчено. Это отступление от замера 01.10.2026; повторите "
                f"вызов, а если повтор не помогает - сообщите среду и версию.")
        raise ToolError(
            f"Размер блока '{block}' записан, но среда приняла высоту "
            f"{_size_text(after)}: у класса «{class_name}» она обязана быть "
            f"{required} px ({PORT_ROW_HEIGHT} px на вход, входов "
            f"{required // PORT_ROW_HEIGHT}) - отображение входов испорчено. "
            f"Повторите вызов; если повтор не помогает - сообщите среду и "
            f"версию.")
    if after == before and after != requested:
        raise ToolError(
            f"Размер блока '{block}' не изменился: было и осталось "
            f"{_size_text(before)}, запрошено {_size_text(requested)}. Среда "
            f"запись не применила. Повторите вызов; если размер не меняется "
            f"и дальше, проверьте, что имя из `list_blocks` — и сообщите "
            f"среду и версию: это отступление от замера 01.10.2026, где "
            f"`SetGraphBlockProp` применялся к тем же блокам.")
    note = ""
    if after != requested:
        note = (f" (среда приняла {_size_text(after)}, а запрошено "
                f"{_size_text(requested)})")
    return (f"Размер блока '{block}': {_size_text(before)} → "
            f"{_size_text(after)}{note}{result.tail_note}{result.overlaps}")
