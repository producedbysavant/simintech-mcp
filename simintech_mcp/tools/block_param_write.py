"""Запись параметров блока: nport, PortNames и разбор значений.

Выделено из `blocks.py` при разборе монолитов: инструмент `set_block_param`
остаётся там, а тяжёлые ветки записи (`nport`, `PortNames`, `SignalNames`)
и разбор значений живут здесь. Правила размеров читаются из `rules`, запись
размера идёт общей парой `block_size` (`setpropformula` + `setprop`).
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Union

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page

from .. import session
from ..rules import (NPORT_BLOCK_WIDTH, NPORT_PARAM, PORT_ROW_HEIGHT,
                     height_kind, required_height, table_positions)
from .block_size import size_value, write_block_size

#: Предел числа входов в `in_ports`: `SetPortCount` - один COM-вызов, который
#: на таком числе портов займёт выделенный поток надолго.
MAX_BLOCK_IN_PORTS = 64


def nport_value(value: str) -> int:
    """Разобрать число входов для `nport`: целое 1…MAX_BLOCK_IN_PORTS.

    Параметр принимает строку, но `nport` - число контактов: дробное,
    массив, пустое и отрицательное отвергаются до COM, иначе `SetPortCount`
    получил бы мусор.
    """
    parsed = coerce_param_value(value)
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        raise ToolError(
            f"nport={value!r} не число: ожидается целое число входов "
            f"1…{MAX_BLOCK_IN_PORTS}.")
    if isinstance(parsed, float) and not parsed.is_integer():
        raise ToolError(
            f"nport={value!r} не целое: число входов - целое "
            f"1…{MAX_BLOCK_IN_PORTS}.")
    count = int(parsed)
    if not 1 <= count <= MAX_BLOCK_IN_PORTS:
        raise ToolError(
            f"nport={count} вне пределов: число входов - целое "
            f"1…{MAX_BLOCK_IN_PORTS}.")
    return count


def set_nport(page: Page, target: Block, class_name: str, token: str,
              value: str, notes: List[str]) -> str:
    """Сменить число входов nport-класса и сразу поставить размер.

    Порядок проверен живым замером 23.10.2026 (2.26.9.29): `SetBlockProp`
    пишет `nport`, но пины не пересобирает; `SetPortCount` (`N + 1` с
    выходом) добавляет контакты, координаты которых среда раскладывает по
    новой высоте. Поэтому сначала параметр и `InitBlock`, затем
    `SetPortCount`, затем запись высоты 16 × фактических входов и ширины не
    уже 32 в «Значение» (`write_block_size`). Пины сверяются с запросом:
    расхождение - отказ без записи размера, иначе блок выглядел бы
    настроенным при старом числе контактов.

    Проект должен быть остановлен: контурная запись размера на идущем
    расчёте отвергается средой (`ScriptBridgeUnsafeStateError`). Это тот же
    гейт, что у `add_block`.
    """
    count = nport_value(value)
    project = session.ensure_project()
    from .page_script import require_stopped

    require_stopped(project, token, "nport")
    try:
        name = target.get_name()
    except Exception:                                             # noqa: BLE001
        name = token
    try:
        before_in = int(target.get_in_port_count())
        before_size = target.get_size()
        before_wires = len(page.get_wires())
    except Exception as exc:                                      # noqa: BLE001
        raise ToolError(
            f"число входов не изменено: состояние блока '{token}' не "
            f"прочитано ({type(exc).__name__}: {exc})."
        ) from exc
    removed = [record for record in session.WIRES
               if record[3] == name and record[4] >= count]
    try:
        target.set_property(NPORT_PARAM, count)
    except Exception as exc:                                      # noqa: BLE001
        raise ToolError(
            f"nport блока '{token}' не записан ({type(exc).__name__}: {exc}); "
            f"число входов и размер не менялись."
        ) from exc
    try:
        target.init()
    except Exception as exc:                                      # noqa: BLE001
        raise ToolError(
            f"nport блока '{token}' записан, но InitBlock не выполнен "
            f"({type(exc).__name__}: {exc}); пины и размер не менялись. "
            f"Перечитайте параметр и пины (`get_block_params`, "
            f"`check_model_layout`) и повторите; если состояние расходится, "
            f"пересоберите блок импортом."
        ) from exc
    if before_in != count:
        try:
            target.set_in_port_count(count)
        except Exception as exc:                                  # noqa: BLE001
            raise ToolError(
                f"nport блока '{token}' записан, но пины не пересобраны "
                f"({type(exc).__name__}: {exc}); высота 16 × {count} не "
                f"записана. Пересоберите блок импортом текста с "
                f"nport = {count}."
            ) from exc
    try:
        after_in = int(target.get_in_port_count())
        actual = target.get_property(NPORT_PARAM)
    except Exception as exc:                                      # noqa: BLE001
        raise ToolError(
            f"nport блока '{token}' записан, но перечитать пины не удалось "
            f"({type(exc).__name__}: {exc}); высота не записана."
        ) from exc
    if after_in != count:
        raise ToolError(
            f"пины блока '{token}' не пересобрались: запрошено {count} "
            f"входов, фактически {after_in}. `SetBlockProp` nport пины не "
            f"пересобирает (замер 03.10.2026); высота 16 × {count} НЕ "
            f"записана. Пересоберите блок импортом текста с nport = {count}.")
    if after_in <= 0:
        raise ToolError(
            f"у блока '{token}' после записи nport = {count} нет входных "
            f"контактов; высота не записана.")
    # Пины пересобраны: записи реестра о линиях на удалённых входах больше
    # не описывают существующие порты. Держать их нельзя: layout_place
    # построил бы граф по связям, которых в модели уже нет.
    forgotten = 0
    for record in removed:
        try:
            session.forget_wire(record[0].id)
            forgotten += 1
        except Exception as exc:                                  # noqa: BLE001
            notes.append(f"реестр связей не очистить для линии "
                         f"{getattr(record[0], 'id', '?')}: {exc}")
    try:
        actual_num = int(float(str(actual).strip()))
    except Exception:                                             # noqa: BLE001
        notes.append(f"nport перечитать не удалось ({actual!r}); размер "
                     f"посчитан по фактическим входам {after_in}")
    else:
        if actual_num != count:
            raise ToolError(
                f"nport блока '{token}' = {actual_num}, а запрошено {count}; "
                f"параметр и пины разошлись, высота не записана.")
    width = max(float(before_size[0]), float(NPORT_BLOCK_WIDTH))
    height = float(after_in * PORT_ROW_HEIGHT)
    # Отказ записи размера случается уже после записи nport, пересборки
    # пинов и чистки реестра: «Проект не изменён» здесь было бы ложью и
    # увело бы восстановление (находка ревью).
    project_note = (f"Проект уже изменён: nport = {count}, входов "
                    f"{after_in}, высота {PORT_ROW_HEIGHT} × {after_in} не "
                    f"записана.")
    if removed:
        project_note += (f" Записи реестра связей удалённых входов сняты: "
                         f"{forgotten}.")
    result = write_block_size(page, target, width, height,
                              action="размер не записан",
                              project_note=project_note)
    if float(result.after[1]) != height:
        raise ToolError(
            f"nport блока '{token}' записан, но среда приняла высоту "
            f"{size_value(result.after[1])} вместо {size_value(height)} "
            f"({after_in} вход(ов) × {PORT_ROW_HEIGHT}); отображение входов "
            f"испорчено. Повторите вызов; если повтор не помогает - сообщите "
            f"среду и версию.")
    if float(result.after[0]) < float(NPORT_BLOCK_WIDTH):
        notes.append(f"ширина {size_value(result.after[0])} меньше штатной "
                     f"{NPORT_BLOCK_WIDTH}")
    if after_in > before_in:
        notes.append("соедините новые входы: висячий вход блока расчёта "
                     "молча останавливает расчёт")
        notes.append(
            "новые пины добавлены в конец списка; для смысловой группировки "
            "переставьте их в GUI и синхронизируйте списки сигналов")
    if removed:
        notes.append(f"ВНИМАНИЕ: к удалённым входам было подключено "
                     f"{len(removed)} линий; записи реестра связей сняты: "
                     f"{forgotten} - проверьте `export_model_text`")
    try:
        after_wires = len(page.get_wires())
    except Exception:                                             # noqa: BLE001
        after_wires = None
    if after_wires is not None and after_wires != before_wires:
        notes.append(f"число линий страницы изменилось: {before_wires} → "
                     f"{after_wires}; проверьте связи")
    head = (f"{token}.{NPORT_PARAM} = {actual}; пины: {before_in} → "
            f"{after_in}; размер: "
            f"{size_value(before_size[0])}x{size_value(before_size[1])} → "
            f"{size_value(result.after[0])}x{size_value(result.after[1])} "
            f"({after_in} вход(ов) × {PORT_ROW_HEIGHT}, ширина не уже "
            f"{NPORT_BLOCK_WIDTH}).")
    tail = (" " + "; ".join(notes) + ".") if notes else ""
    return head + tail + result.tail_note + result.overlaps


def set_portnames(page: Page, target: Block, class_name: str,
                  token: str, value: str) -> str:
    """Записать `PortNames` контуром и сразу выставить высоту по правилу.

    `SetBlockProp` для этого свойства принимается без ошибки и не
    применяется (живой замер 14.11.2026), поэтому запись идёт
    `setpropasstring` в контуре страницы. Высота по новому числу строк
    пишется тем же прогоном: контур стоит ~14 с, и второй ради размера был
    бы дороже правки. Правило считает `rules.required_height`, то же, что у
    скрипта саморазмера и подгонки, иначе блок спорил бы сам с собой.

    Проверка - перечитывание `PortNames` через COM: расхождение - отказ с
    названным прежним значением, а не «записано» на слово.
    """
    from .block_script import normalize_script, runtime_literal
    from .block_size import size_pair_lines
    from .page_script import (refuse_contour_failure, require_stopped,
                              run_contour)

    # Тот же гейт, что у `set_nport`: контурная запись на идущем расчёте
    # отвергается средой, и частично применённая правка выглядела бы отказом.
    project = session.ensure_project()
    require_stopped(project, token, "PortNames")
    normalized = normalize_script(value)
    lines = len([line for line in normalized.splitlines() if line.strip()])
    try:
        before_size = target.get_size()
    except Exception:                                          # noqa: BLE001
        before_size = None
    pn_token = "PN" + uuid.uuid4().hex[:12]
    body = (f"obj = {target.id};\n"
            f'setpropasstring(obj, "PortNames", {runtime_literal(normalized)});\n'
            f'writelnutf8(fid, "{pn_token}_BEGIN");\n'
            'writelnutf8(fid, getpropasstring(obj, "PortNames"));\n'
            f'writelnutf8(fid, "{pn_token}_END");\n')
    want = required_height(class_name, lines) if lines else None
    if (want is not None and before_size is not None
            and float(before_size[1]) != float(want)):
        body += ("\n".join(size_pair_lines(target.id, "Height",
                                           float(want))) + "\n")
    outcome, _restored = run_contour(
        body, failed=f"{token}: записать PortNames не удалось")
    refuse_contour_failure(
        outcome, failed="PortNames не записан",
        unsure="запись PortNames не подтверждена",
        project_note=(f"Проект уже изменён: {token}.PortNames мог записаться, "
                      f"высота не записана - сверьте значение."))
    # Значение читается **внутри** контура: COM-чтение сразу после контура
    # отдаёт прежнее значение до `InitBlock` (живой замер 14.11.2026),
    # и проверка объявила бы состоявшуюся запись отказом.
    written = _between(outcome.lines, f"{pn_token}_BEGIN", f"{pn_token}_END")
    if written is None:
        try:
            written = target.get_property("PortNames") or ""
        except Exception as exc:                               # noqa: BLE001
            raise ToolError(
                f"{token}.PortNames записан контуром, но перечитать не "
                f"удалось ({type(exc).__name__}: {exc}); сверьте блок в GUI."
            ) from exc
    try:
        after_size = target.get_size()
    except Exception:                                          # noqa: BLE001
        after_size = None
    if (normalize_script(written).rstrip("\r\n")
            != normalized.rstrip("\r\n")):
        raise ToolError(
            f"{token}.PortNames не записан: в блоке осталось {written!r}, а "
            f"запрошено {value!r}. Среда запись не приняла; повторите "
            f"вызов, а если не помогает - сообщите среду и версию.")
    after = written.strip()
    notes: List[str] = []
    if lines and before_size is not None and after_size is not None:
        if float(after_size[1]) == float(want or 0):
            notes.append(f"высота по правилу: {size_value(before_size[1])} → "
                         f"{size_value(after_size[1])} (сетка 8 px)")
        else:
            notes.append(
                f"высота {size_value(after_size[1])}, а по правилу "
                f"{want}: среда приняла не то значение")
    tail = f" {'; '.join(notes)}" if notes else ""
    return f"{token}.PortNames = {after}{tail}"


def _exclude_first(target: Block) -> Optional[bool]:
    """Прочитать `fExcludeFirst`: True - первая строка таблицы заголовок.

    `None` - свойство не прочитано. Тогда число позиций неизвестно: таблицу
    писать можно, а высоту по правилу - нельзя, иначе первая строка была бы
    посчитана вслепую.
    """
    try:
        raw = target.get_property("fExcludeFirst")
    except Exception:                                             # noqa: BLE001
        return None
    return str(raw or "1").strip().lower() not in ("0", "false")


def _auto_change_height(target: Block) -> bool:
    """Прочитать `AutoChangeHeight`: True - среда сама ведёт высоту таблицы."""
    try:
        raw = target.get_property("AutoChangeHeight")
    except Exception:                                             # noqa: BLE001
        return False
    return str(raw or "0").strip().lower() not in ("0", "false")


def set_signalnames(target: Block, token: str, value: str) -> str:
    """Записать `SignalNames` табличного блока алгоритма контуром.

    COM `SetProp` для этого свойства у «Входа алгоритма»/«Выхода алгоритма»
    и их векторных вариантов - no-op (живой замер 24.11.2026). Рабочий путь -
    `setpropasstring` в контуре страницы; тогда же таблица читается обратно:
    COM-чтение сразу после контура отдаёт прежнее значение, и сверка объявила
    бы состоявшуюся запись отказом (тот же замер, что у `PortNames`).

    Высота по канону (16 px на позицию таблицы, строка-заголовок при
    `fExcludeFirst=1` не считается) пишется тем же прогоном. Нечитаемый
    `fExcludeFirst` оставляет высоту прежней: число позиций неизвестно, а
    угадывать его - значит испортить габарит.

    Успех подтверждается только совпадением прочитанной таблицы с запросом:
    расхождение - отказ, а не «записано».
    """
    from .block_script import normalize_script, runtime_literal
    from .block_size import size_pair_lines, size_value
    from .page_script import (refuse_contour_failure, require_stopped,
                              run_contour)

    project = session.ensure_project()
    require_stopped(project, token, "SignalNames")
    normalized = normalize_script(value)
    header = _exclude_first(target)
    exclude_header = True if header is None else header
    positions = table_positions(normalized, exclude_header)
    try:
        before_size = target.get_size()
    except Exception:                                             # noqa: BLE001
        before_size = None
    # Пустая таблица высоты не задаёт: правило считает позиции, а их нет.
    want = (float(PORT_ROW_HEIGHT * positions)
            if header is not None and positions > 0 else None)
    sn_token = "SN" + uuid.uuid4().hex[:12]
    body = (f"obj = {target.id};\n"
            f'setpropasstring(obj, "SignalNames", '
            f'{runtime_literal(normalized)});\n'
            f'writelnutf8(fid, "{sn_token}_BEGIN");\n'
            'writelnutf8(fid, getpropasstring(obj, "SignalNames"));\n'
            f'writelnutf8(fid, "{sn_token}_END");\n')
    if (want is not None and before_size is not None
            and float(before_size[1]) != want):
        body += "\n".join(size_pair_lines(target.id, "Height", want)) + "\n"
    outcome, _restored = run_contour(
        body, failed=f"{token}: записать SignalNames не удалось")
    refuse_contour_failure(
        outcome, failed="SignalNames не записан",
        unsure="запись SignalNames не подтверждена",
        project_note=(f"Проект уже изменён: {token}.SignalNames мог "
                      f"записаться, высота не записана - сверьте значение."))
    written = _between(outcome.lines, f"{sn_token}_BEGIN",
                       f"{sn_token}_END")
    if written is None:
        try:
            written = target.get_property("SignalNames") or ""
        except Exception as exc:                                  # noqa: BLE001
            raise ToolError(
                f"{token}.SignalNames записан контуром, но перечитать не "
                f"удалось ({type(exc).__name__}: {exc}); сверьте таблицу "
                f"в GUI.") from exc
    if normalize_script(written).rstrip("\r\n") != normalized.rstrip("\r\n"):
        got = table_positions(written, exclude_header)
        raise ToolError(
            f"{token}.SignalNames не записан: контур вернул таблицу на "
            f"{got} позиций, а запрошено {positions}. Среда запись не "
            f"приняла; повторите вызов, а если не помогает - сообщите среду "
            f"и версию.")
    after_positions = table_positions(written, exclude_header)
    notes: List[str] = []
    if header is None:
        notes.append(
            "fExcludeFirst не прочитан: высота по позициям не записана, "
            "сверьте габарит вручную")
    elif header:
        notes.append("первая строка - заголовок (fExcludeFirst=1); позиций "
                     f"{after_positions}")
    else:
        notes.append("строка-заголовок не исключается (fExcludeFirst=0); "
                     f"позиций {after_positions}")
    try:
        after_size = target.get_size()
    except Exception:                                             # noqa: BLE001
        after_size = None
    if (want is not None and before_size is not None
            and after_size is not None):
        kind = height_kind(float(after_size[1]), want, after_positions,
                           bool(header), _auto_change_height(target))
        if kind == "rule":
            notes.append(f"высота по правилу: {size_value(before_size[1])} -> "
                         f"{size_value(after_size[1])} "
                         f"({after_positions} поз. x {PORT_ROW_HEIGHT} px)")
        elif kind == "auto":
            notes.append(
                f"высота {size_value(after_size[1])} - авторазмер среды с "
                f"заголовком; канон по позициям {size_value(want)}")
        else:
            notes.append(f"высота {size_value(after_size[1])}, а по правилу "
                         f"{size_value(want)}: среда приняла не то значение")
    elif want is None and header is not None:
        notes.append("таблица пуста: высота не менялась")
    elif want is not None:
        notes.append("габарит до или после записи не прочитан: высота "
                     "не сверена")
    tail = f" {'; '.join(notes)}" if notes else ""
    return f"{token}.SignalNames записан{tail}"


def _between(lines: List[str], begin: str, end: str) -> Optional[str]:
    """Строки между маркерами контура; `None` - маркеров нет."""
    try:
        first = lines.index(begin)
        last = lines.index(end)
    except ValueError:
        return None
    if last < first:
        return None
    return "\n".join(lines[first + 1:last])


#: Публичные имена для `signal_table`: рецепт таблиц общий, копий не заводим.
exclude_first = _exclude_first
auto_change_height = _auto_change_height
between_markers = _between


#: Значение параметра блока: скаляр или массив скаляров (стиль SimInTech).
ParamValue = Union[int, float, str, List[Union[int, float, str]]]


def split_props(text: str) -> List[str]:
    """Разделить `props` по запятым, не трогая запятые внутри `[...]`.

    Без этого документированный пример `a=[1, -1]` разваливался на `a=[1`
    и `-1]`: первая часть уходила в свойство как обрезанный массив, вторая
    молча отбрасывалась (в ней нет `=`).
    """
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def parse_val(text: str) -> Union[int, float, str]:
    text = text.strip()
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        return text


def coerce_param_value(text: str) -> ParamValue:
    """Разобрать значение параметра блока из строки.

    Массивы в стиле SimInTech ('[1, -1]') → list; иначе — число или строка.
    """
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        body = stripped[1:-1].strip()
        if not body:
            return []
        return [parse_val(p) for p in body.split(",") if p.strip()]
    return parse_val(stripped)
