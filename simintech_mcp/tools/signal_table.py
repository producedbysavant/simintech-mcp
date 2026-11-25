"""Вставка сигналов в таблицу блока: SignalNames и параллельные колонки.

Таблица сигналов в GUI это несколько свойств-колонок: `SignalNames`,
`Captions`, `SystemNames`, `ParamNames`, `Numbers`. Строка таблицы задаёт
порт блока, поэтому вставка обязана добавить строку во все колонки на
одном номере и пересобрать порты (`initobject`). Живой замер 25.11.2026:
`Signal_Block_0` имел 459 портов и 459 позиций. Вставка одной строки перед
первой позицией дала 460 позиций и 460 портов. Дозапись строки в самый
конец, после концевого пустого заполнителя, добавляет две позиции (пустой
заполнитель и имя) и даёт 461.

Чтение и запись идут контуром страницы: COM `SetProp` для `SignalNames`
у блоков алгоритма не применяется, а COM-чтение сразу после контура
отдаёт прежнее значение. Поэтому таблица читается отдельным контуром, а
запись проверяется чтением внутри того же прогона.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page

from .. import runtime, session
from ..app import mcp
from ..rules import (ALGORITHM_TABLE_CLASSES, PORT_ROW_HEIGHT, height_kind,
                     table_positions)
from .block_param_write import (auto_change_height, between_markers,
                                exclude_first)
from .block_script import runtime_literal
from .block_size import size_pair_lines, size_value
from .blocks import missing_block, resolve_block
from .page_script import (refuse_contour_failure, require_stopped,
                          run_contour)

#: Колонки таблицы сигналов. Первая задаёт имена и число позиций.
TABLE_COLUMNS: Tuple[str, ...] = (
    "SignalNames", "Captions", "SystemNames", "ParamNames", "Numbers")
SIGNAL_COLUMN = TABLE_COLUMNS[0]

#: Предел имён за вызов: каждая строка это COM-вызов в контуре, а литерал
#: таблицы уходит в скрипт страницы целиком.
MAX_INSERT_SIGNALS = 256
MAX_SIGNAL_NAME = 256

_BREAKS = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


@dataclass(frozen=True)
class InsertPosition:
    """Разрешённая позиция вставки: номер, режим и подпись для ответа."""

    position: int
    mode: str
    reference: str
    fallback: bool


def _rows(text: str) -> List[str]:
    """Строки по LF; один концевой пустой снят, внутренние заполнители целы."""
    rows = text.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    return rows


def split_rows(raw: str) -> List[str]:
    """Строки сохранённой таблицы: CRLF/CR к LF, один концевой пустой снят.

    Нормализация совпадает с `rules.table_positions`: таблица от среды
    хранится с CRLF, и счётчик позиций не должен расходиться с разбором.
    Пустые заполнители внутри таблицы сохраняются: канон высоты их считает.
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    return _rows(text)


def join_rows(rows: Sequence[str]) -> str:
    """Текст таблицы: строки через CRLF, без концевого перевода."""
    return "\r\n".join(rows)


def validate_signals(text: str) -> List[str]:
    """Разобрать многострочный ввод в имена; мусор отвергается до COM.

    Ввод пользователя режется по всем разделителям строк (включая U+2028),
    а не только по CRLF: иначе такой символ остался бы внутри имени.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _BREAKS.sub("\n", text)
    names = _rows(text)
    if not names:
        raise ToolError(
            "signals пуст: передайте имена сигналов по одному на строку.")
    if len(names) > MAX_INSERT_SIGNALS:
        raise ToolError(
            f"signals: {len(names)} имён, предел {MAX_INSERT_SIGNALS} за вызов.")
    seen: set[str] = set()
    for number, name in enumerate(names, 1):
        if not name.strip():
            raise ToolError(
                f"signals: строка {number} пуста; пустые строки в вставке "
                f"не допускаются.")
        if name != name.strip():
            raise ToolError(
                f"signals: строка {number} имеет пробелы по краям "
                f"({name!r}); имя сигнала пишется без них.")
        if len(name) > MAX_SIGNAL_NAME:
            raise ToolError(
                f"signals: строка {number} длиннее {MAX_SIGNAL_NAME} символов.")
        if name in seen:
            raise ToolError(
                f"signals: имя {name!r} повторяется в одном вызове.")
        seen.add(name)
    return names


def _check_index(index: Any, length: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise ToolError("index должен быть целым числом.")
    if not 0 <= index <= length:
        raise ToolError(
            f"index={index} вне диапазона 0..{length}: 0 это начало данных, "
            f"{length} это вставка в конец.")
    return index


def resolve_position(rows: Sequence[str], *, before: str, after: str,
                     index: Optional[int]) -> InsertPosition:
    """Позиция 0-based среди позиций без заголовка; index - запасной путь."""
    before = before.strip()
    after = after.strip()
    if before and after:
        raise ToolError("before и after не сочетаются: выберите одно.")
    if before or after:
        anchor = before or after
        matches = [number for number, row in enumerate(rows)
                   if row.strip() == anchor]
        if len(matches) == 1:
            position = matches[0] + (1 if after else 0)
            return InsertPosition(position, "after" if after else "before",
                                  anchor, False)
        if len(matches) > 1:
            places = ", ".join(str(number) for number in matches[:20])
            raise ToolError(
                f"якорь {anchor!r} встречается {len(matches)} раз "
                f"(позиции {places}); вставка неоднозначна. Передайте index.")
        if index is None:
            names = [row for row in rows if row.strip()][:20]
            raise ToolError(
                f"якорь {anchor!r} не найден; передайте index или одно из "
                f"имён: {', '.join(names)}")
        position = _check_index(index, len(rows))
        return InsertPosition(position, "index",
                              f"{anchor!r} не найден, взят index {position}",
                              True)
    if index is None:
        return InsertPosition(len(rows), "append", "в конец", False)
    position = _check_index(index, len(rows))
    return InsertPosition(position, "index", f"index {position}", False)


def splice_rows(rows: Sequence[str], physical: int,
                values: Sequence[str]) -> List[str]:
    """Вставить строки на физический номер, добив короткую колонку пустыми."""
    result = list(rows)
    pad = max(0, physical - len(result))
    return (result[:physical] + [""] * pad + list(values)
            + result[physical:])


def _different(expected: Sequence[str], got: Sequence[str]
               ) -> Optional[Tuple[int, str, str]]:
    """Первое расхождение двух колонок: (строка, ожидалось, получено)."""
    for number, (want, actual) in enumerate(zip(expected, got)):
        if want != actual:
            return number, want, actual
    if len(expected) != len(got):
        number = min(len(expected), len(got))
        want = expected[number] if number < len(expected) else "<нет строки>"
        actual = got[number] if number < len(got) else "<нет строки>"
        return number, want, actual
    return None


def _ports_from(lines: Sequence[str], token: str) -> Optional[int]:
    prefix = f"{token}_PORTS="
    for line in lines:
        if line.startswith(prefix):
            try:
                return int(line[len(prefix):].strip())
            except ValueError:
                return None
    return None


def _marker_lines(marker: str, columns: Sequence[str],
                  kind: str) -> List[str]:
    """Строки контура: маркер, значение свойства, маркер для каждой колонки."""
    lines: List[str] = []
    for column in columns:
        lines += [
            f'writelnutf8(fid, "{marker}_{kind}_{column}_BEGIN");',
            f'writelnutf8(fid, getpropasstring(obj, "{column}"));',
            f'writelnutf8(fid, "{marker}_{kind}_{column}_END");',
        ]
    return lines


def _marker_values(lines: List[str], marker: str,
                   columns: Sequence[str], kind: str,
                   problem: str) -> Dict[str, str]:
    """Значения колонок между маркерами; пропавший маркер это отказ."""
    values: Dict[str, str] = {}
    for column in columns:
        value = between_markers(
            lines, f"{marker}_{kind}_{column}_BEGIN",
            f"{marker}_{kind}_{column}_END")
        if value is None:
            raise ToolError(problem.format(column=column))
        values[column] = value
    return values


def read_columns(target: Block, token: str) -> Tuple[Dict[str, str],
                                                     List[str], Optional[int]]:
    """Прочитать колонки таблицы контуром; вернуть тексты, колонки, порты."""
    columns = [SIGNAL_COLUMN]
    skipped: List[str] = []
    for column in TABLE_COLUMNS[1:]:
        try:
            target.get_property(column)
        except Exception:                                      # noqa: BLE001
            skipped.append(column)
            continue
        columns.append(column)
    marker = "IT" + uuid.uuid4().hex[:12]
    body = [f"obj = {target.id};"]
    body += _marker_lines(marker, columns, "READ")
    body.append(f'writelnutf8(fid, "{marker}_PORTS=" '
                ' + inttostr(getblockportcount(obj)));')
    outcome, _restored = run_contour(
        "\n".join(body) + "\n", failed="прочитать таблицу сигналов не удалось")
    refuse_contour_failure(
        outcome, failed="таблица сигналов не прочитана",
        unsure="чтение таблицы сигналов не подтверждено",
        project_note="Проект не изменён: это чтение.")
    raw = _marker_values(
        outcome.lines, marker, columns, "READ",
        "колонка {column} не прочитана контуром: маркеры не найдены; "
        "повторите вызов.")
    return raw, skipped, _ports_from(outcome.lines, marker)


def write_columns(
        target: Block, token: str, expected: Dict[str, str],
        height: Optional[float]) -> Tuple[Dict[str, str], Optional[int]]:
    """Записать колонки, пересобрать порты и прочитать всё обратно."""
    order = [column for column in expected if column != SIGNAL_COLUMN]
    order.append(SIGNAL_COLUMN)
    marker = "IT" + uuid.uuid4().hex[:12]
    body = [f"obj = {target.id};"]
    for column in order:
        body.append(f'setpropasstring(obj, "{column}", '
                    f'{runtime_literal(expected[column])});')
    # Строка таблицы задаёт порт: без пересборки новый порт появится только
    # после сохранения и переоткрытия (живой замер 25.11.2026).
    body.append("initobject(obj);")
    body += _marker_lines(marker, order, "WRITE")
    body.append(f'writelnutf8(fid, "{marker}_PORTS=" '
                ' + inttostr(getblockportcount(obj)));')
    if height is not None:
        body.append("\n".join(size_pair_lines(target.id, "Height", height)))
    outcome, _restored = run_contour(
        "\n".join(body) + "\n", failed="записать сигналы в таблицу не удалось")
    refuse_contour_failure(
        outcome, failed="сигналы в таблицу не записаны",
        unsure="запись сигналов не подтверждена",
        project_note=("Проект уже изменён: колонки таблицы могли записаться "
                      "частично; проверьте таблицу в GUI."))
    written = _marker_values(
        outcome.lines, marker, order, "WRITE",
        "колонка {column} записана, но не прочитана обратно: проект уже "
        "изменён, проверьте таблицу в GUI.")
    return written, _ports_from(outcome.lines, marker)


def _height_note(before: Optional[Tuple[float, float]],
                 after: Optional[Tuple[float, float]],
                 want: Optional[float], positions: int,
                 auto: bool, header: bool) -> str:
    if want is None or before is None or after is None:
        return ""
    kind = height_kind(float(after[1]), want, positions, header, auto)
    if kind == "rule":
        return (f"; высота по правилу: {size_value(before[1])} -> "
                f"{size_value(after[1])} ({positions} поз. x "
                f"{PORT_ROW_HEIGHT} px)")
    if kind == "auto":
        return (f"; высота {size_value(after[1])} это авторазмер среды с "
                f"заголовком; канон по позициям {size_value(want)}")
    return (f"; высота {size_value(after[1])}, а по правилу "
            f"{size_value(want)}: среда приняла не то значение")


def insert_signal_rows(page: Page, target: Block, token: str, text: str, *,
                       before: str = "", after: str = "",
                       index: Optional[int] = None) -> str:
    """Ядро вставки: прочитать таблицу, вставить строки, записать, проверить."""
    names = validate_signals(text)
    if before.strip() and after.strip():
        raise ToolError("before и after не сочетаются: выберите одно.")
    try:
        class_name = (target.class_name or "").strip()
    except Exception:                                          # noqa: BLE001
        class_name = ""
    header = exclude_first(target)
    if header is None:
        raise ToolError(
            f"{token}: fExcludeFirst не прочитан, роль первой строки "
            f"неизвестна. Вставлять вслепую нельзя: запишите таблицу целиком "
            f"через set_block_param.")
    project = session.ensure_project()
    require_stopped(project, token, "SignalNames")
    raw, skipped, ports_before = read_columns(target, token)
    rows = {column: split_rows(raw[column]) for column in raw}
    body = {column: (rows[column][1:] if header and rows[column]
                     else rows[column])
            for column in raw}
    signal_rows = body[SIGNAL_COLUMN]
    positions = table_positions(raw[SIGNAL_COLUMN], header)
    if len(signal_rows) != positions:
        raise ToolError(
            f"{token}: разбор таблицы дал {len(signal_rows)} позиций, канон "
            f"насчитал {positions}; это расхождение парсера, вставка "
            f"отменена.")
    if positions == 0 and header:
        raise ToolError(
            f"{token}: таблица SignalNames пуста, а первая строка это "
            f"заголовок: первое имя стало бы заголовком. Сначала запишите "
            f"таблицу через set_block_param.")
    resolved = resolve_position(signal_rows, before=before, after=after,
                                index=index)
    physical = resolved.position + (1 if header else 0)
    expected: Dict[str, str] = {}
    for column in raw:
        values = names if column == SIGNAL_COLUMN else [""] * len(names)
        expected[column] = join_rows(splice_rows(rows[column], physical,
                                                 values))
    positions_after = positions + len(names)
    want: Optional[float] = None
    before_size: Optional[Tuple[float, float]] = None
    if class_name in ALGORITHM_TABLE_CLASSES:
        want = float(PORT_ROW_HEIGHT * positions_after)
        try:
            before_size = target.get_size()
        except Exception:                                      # noqa: BLE001
            before_size = None
    height: Optional[float] = None
    if (want is not None and before_size is not None
            and float(before_size[1]) != want):
        height = want
    written, ports_after = write_columns(target, token, expected, height)
    for column, want_text in expected.items():
        difference = _different(split_rows(want_text),
                                split_rows(written[column]))
        if difference is not None:
            number, want_row, got_row = difference
            raise ToolError(
                f"{token}: колонка {column} не совпала с записью в строке "
                f"{number + 1}: ожидалось {want_row!r}, получено "
                f"{got_row!r}. Проект уже изменён: другие колонки могли "
                f"записаться; повторная вставка продублирует строки.")
    notes: List[str] = []
    if resolved.fallback:
        notes.append("якорь не найден, использован index")
    if skipped:
        notes.append("колонки не читаются и не писались: "
                     + ", ".join(skipped))
    if class_name not in ALGORITHM_TABLE_CLASSES:
        notes.append("класс вне правила высоты: высота не менялась")
    try:
        after_size = target.get_size()
    except Exception:                                          # noqa: BLE001
        after_size = None
    notes.append(_height_note(before_size, after_size, want,
                              positions_after, auto_change_height(target),
                              header).lstrip("; ").strip("; "))
    notes = [note for note in notes if note]
    if ports_before is not None and ports_after is not None:
        notes.append(f"портов {ports_before} -> {ports_after}")
        if (class_name in ALGORITHM_TABLE_CLASSES
                and "векторный" not in class_name
                and ports_after - ports_before != len(names)):
            notes.append(
                f"ожидался прирост {len(names)}: строка таблицы задаёт порт; "
                f"проверьте блок и линии")
        elif "векторный" in class_name:
            notes.append("векторный класс: строки таблицы не равны портам")
    if resolved.position < positions:
        try:
            wired = bool(page.get_wires())
        except Exception:                                      # noqa: BLE001
            wired = False
        if wired:
            notes.append(
                "вставка не в конец: порты после позиции сдвинулись; "
                "проверьте провода блока")
    head = (f"{token}.SignalNames: вставлено {len(names)} поз. "
            f"{resolved.mode} {resolved.reference}; позиций {positions} -> "
            f"{positions_after}; колонки: {', '.join(expected)}")
    tail = f" {'; '.join(notes)}" if notes else ""
    return head + tail


@mcp.tool(title="Вставить сигналы в таблицу",
          annotations={"readOnlyHint": False})
@runtime.com_threaded(mutates_project=True)
def insert_signals(block: str, signals: str,
                   before: str = "", after: str = "",
                   index: Optional[int] = None) -> str:
    """Вставить сигналы в таблицу блока на смысловую позицию.

    `signals` это имена сигналов по одному на строку. Строка вставляется во
    все параллельные колонки таблицы (`SignalNames`, `Captions`,
    `SystemNames`, `ParamNames`, `Numbers`): в `SignalNames` имя, в
    остальные пустая ячейка. Короткие колонки добиваются пустыми строками до
    нужного номера, длинные сохраняют хвост.

    Позиция задаётся одним из способов: `before` или `after` по имени
    сигнала, либо `index` (0-based среди позиций, заголовок не считается;
    `index`, равный числу позиций, вставляет в конец). Если якорь не найден,
    используется `index`; неоднозначный якорь отвергается. Без якоря и без
    `index` строки добавляются в конец.

    Работают два контура: чтение таблицы и запись с чтением внутри того же
    прогона. Проект должен быть остановлен (`stop`); контур сдвигает
    модельное время. После записи вызывается `initobject`: строка таблицы
    задаёт порт блока, без пересборки новый порт появится только после
    сохранения и переоткрытия (живой замер 25.11.2026).

    Высота ставится по канону 16 px на позицию только для табличных классов
    алгоритма; у остальных классов высота не меняется. Вставка не в конец
    сдвигает порты после позиции: если у блока есть провода, проверьте их.

    Успех подтверждается точным совпадением каждой колонки с записью;
    расхождение даёт отказ, а не «записано». Откат при частичной записи
    невозможен: повторная вставка продублирует строки.

    Args:
        block: имя блока на главной странице или его числовой id.
        signals: имена сигналов по одному на строку; пустые строки и
            повторы в одном вызове отвергаются.
        before: вставить перед сигналом с этим именем.
        after: вставить после сигнала с этим именем.
        index: 0-based позиция среди позиций без заголовка; запасной путь,
            если якорь не найден, и способ вставить в конец.
    """
    page = session.ensure_project().get_main_page()
    target = resolve_block(page, block)
    if target is None:
        return missing_block(block)
    return insert_signal_rows(page, target, block, signals, before=before,
                              after=after, index=index)


#: Предел имён в одном удалении: имена сверяются в Python, COM-вызовов не
#: добавляют, но вход не должен быть безразмерным.
MAX_REMOVE_SIGNALS = 256


def parse_remove_names(text: str) -> List[str]:
    """Имена для удаления: строки ввода, пустые и повторы отбрасываются."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _BREAKS.sub("\n", text)
    names = _rows(text)
    if not names:
        return []
    if len(names) > MAX_REMOVE_SIGNALS:
        raise ToolError(
            f"signals: {len(names)} имён, предел {MAX_REMOVE_SIGNALS}.")
    result: List[str] = []
    seen: set[str] = set()
    for number, name in enumerate(names, 1):
        if not name.strip():
            raise ToolError(f"signals: строка {number} пуста.")
        if name != name.strip():
            raise ToolError(
                f"signals: строка {number} имеет пробелы по краям "
                f"({name!r}).")
        if len(name) > MAX_SIGNAL_NAME:
            raise ToolError(
                f"signals: строка {number} длиннее {MAX_SIGNAL_NAME}.")
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _check_removal_mode(names: Sequence[str], duplicates: bool,
                        index: Optional[int], count: Any) -> None:
    modes = int(bool(names)) + int(bool(duplicates)) + int(index is not None)
    if modes != 1:
        raise ToolError(
            "нужен ровно один режим: signals (имена), duplicates=true "
            "или index (с count).")
    if index is None and count != 1:
        raise ToolError("count используется только вместе с index.")
    if isinstance(count, bool) or not isinstance(count, int):
        raise ToolError("count должен быть целым числом.")
    if count < 1:
        raise ToolError("count должен быть не меньше 1.")


def _check_removal_range(index: Any, count: int, positions: int) -> None:
    if isinstance(index, bool) or not isinstance(index, int):
        raise ToolError("index должен быть целым числом.")
    if not 0 <= index < positions:
        raise ToolError(
            f"index={index} вне диапазона 0..{positions - 1}: удалять "
            f"можно только существующие позиции.")
    if index + count > positions:
        raise ToolError(
            f"index={index} + count={count} выходит за {positions} позиций.")


def select_removals(body: Sequence[str], names: Sequence[str],
                    duplicates: bool, index: Optional[int],
                    count: int) -> List[int]:
    """Позиции body (без заголовка), которые надо удалить."""
    if names:
        wanted = set(names)
        return [number for number, row in enumerate(body)
                if row.strip() in wanted]
    if duplicates:
        seen: set[str] = set()
        remove: List[int] = []
        for number, row in enumerate(body):
            name = row.strip()
            if not name:
                continue
            if name in seen:
                remove.append(number)
            else:
                seen.add(name)
        return remove
    assert index is not None
    return list(range(index, index + count))


def remove_rows(rows: Sequence[str], physical: Sequence[int]) -> List[str]:
    """Убрать строки по физическим номерам; чужие номера пропускаются."""
    drop = set(physical)
    return [row for number, row in enumerate(rows) if number not in drop]


def remove_signal_rows(page: Page, target: Block, token: str, *,
                       signals: str = "", duplicates: bool = False,
                       index: Optional[int] = None,
                       count: int = 1) -> str:
    """Ядро удаления: прочитать таблицу, убрать строки, записать, проверить."""
    names = parse_remove_names(signals)
    _check_removal_mode(names, duplicates, index, count)
    try:
        class_name = (target.class_name or "").strip()
    except Exception:                                          # noqa: BLE001
        class_name = ""
    header = exclude_first(target)
    if header is None:
        raise ToolError(
            f"{token}: fExcludeFirst не прочитан, роль первой строки "
            f"неизвестна. Удалять вслепую нельзя: запишите таблицу целиком "
            f"через set_block_param.")
    project = session.ensure_project()
    require_stopped(project, token, "SignalNames")
    raw, skipped, ports_before = read_columns(target, token)
    rows = {column: split_rows(raw[column]) for column in raw}
    body = {column: (rows[column][1:] if header and rows[column]
                     else rows[column])
            for column in raw}
    signal_rows = body[SIGNAL_COLUMN]
    positions = table_positions(raw[SIGNAL_COLUMN], header)
    if len(signal_rows) != positions:
        raise ToolError(
            f"{token}: разбор таблицы дал {len(signal_rows)} позиций, канон "
            f"насчитал {positions}; это расхождение парсера, удаление "
            f"отменено.")
    if index is not None:
        _check_removal_range(index, count, positions)
    removals = select_removals(signal_rows, names, duplicates, index, count)
    if not removals:
        reason = ("совпадений нет" if names
                  else "дублей нет" if duplicates else "диапазон пуст")
        raise ToolError(
            f"{token}.SignalNames: нечего удалять: {reason}. "
            f"Проект не изменён, таблица не менялась.")
    physical = [number + (1 if header else 0) for number in removals]
    expected: Dict[str, str] = {}
    for column in raw:
        expected[column] = join_rows(remove_rows(rows[column], physical))
    positions_after = positions - len(removals)
    want: Optional[float] = None
    before_size: Optional[Tuple[float, float]] = None
    if class_name in ALGORITHM_TABLE_CLASSES:
        want = float(PORT_ROW_HEIGHT * positions_after)
        try:
            before_size = target.get_size()
        except Exception:                                      # noqa: BLE001
            before_size = None
    height: Optional[float] = None
    if (want is not None and before_size is not None
            and float(before_size[1]) != want):
        height = want
    written, ports_after = write_columns(target, token, expected, height)
    for column, want_text in expected.items():
        difference = _different(split_rows(want_text),
                                split_rows(written[column]))
        if difference is not None:
            number, want_row, got_row = difference
            raise ToolError(
                f"{token}: колонка {column} не совпала с записью в строке "
                f"{number + 1}: ожидалось {want_row!r}, получено "
                f"{got_row!r}. Проект уже изменён: другие колонки могли "
                f"записаться; повторное удаление опасно.")
    notes: List[str] = []
    if names:
        matched = {signal_rows[number].strip() for number in removals}
        missing = [name for name in names if name not in matched]
        if missing:
            notes.append("не найдены: " + ", ".join(missing[:20]))
    if duplicates:
        notes.append("оставлено первых вхождений")
    if skipped:
        notes.append("колонки не читаются и не писались: "
                     + ", ".join(skipped))
    if class_name not in ALGORITHM_TABLE_CLASSES:
        notes.append("класс вне правила высоты: высота не менялась")
    try:
        after_size = target.get_size()
    except Exception:                                          # noqa: BLE001
        after_size = None
    height_note = _height_note(before_size, after_size, want,
                               positions_after, auto_change_height(target),
                               header)
    if height_note:
        notes.append(height_note.lstrip("; ").strip("; "))
    if ports_before is not None and ports_after is not None:
        notes.append(f"портов {ports_before} -> {ports_after}")
        if (class_name in ALGORITHM_TABLE_CLASSES
                and "векторный" not in class_name
                and ports_after - ports_before != -len(removals)):
            notes.append(
                f"ожидалось -{len(removals)}: строка таблицы задаёт порт; "
                f"проверьте блок и линии")
        elif "векторный" in class_name:
            notes.append("векторный класс: строки таблицы не равны портам")
    tail = set(range(positions - len(removals), positions))
    if set(removals) != tail:
        try:
            wired = bool(page.get_wires())
        except Exception:                                      # noqa: BLE001
            wired = False
        if wired:
            notes.append(
                "удаление не с конца: порты после позиции сдвинулись; "
                "проверьте провода блока")
    head = (f"{token}.SignalNames: удалено {len(removals)} поз.; позиций "
            f"{positions} -> {positions_after}; колонки: "
            f"{', '.join(expected)}")
    tail_text = f" {'; '.join(notes)}" if notes else ""
    return head + tail_text


@mcp.tool(title="Удалить сигналы из таблицы",
          annotations={"readOnlyHint": False, "destructiveHint": True})
@runtime.com_threaded(mutates_project=True)
def remove_signals(block: str, signals: str = "",
                   duplicates: bool = False,
                   index: Optional[int] = None,
                   count: int = 1) -> str:
    """Удалить строки таблицы сигналов на позицию.

    Режимы, ровно один за вызов:

    * `signals` - имена по одному на строку, удаляются все совпадения;
    * `duplicates=True` - повторные имена, остаётся первое вхождение;
    * `index` + `count` - диапазон позиций без заголовка (0-based).

    Строка удаляется на одном номере во всех пяти колонках таблицы:
    `SignalNames`, `Captions`, `SystemNames`, `ParamNames`, `Numbers`.
    Короткая колонка не добивается: удалить можно только существующую
    строку. После записи вызывается `initobject`: строка таблицы задаёт
    порт блока, поэтому порты после удалённых строк пересобираются и
    сдвигаются. Высота пересчитывается по канону 16 px на позицию только
    для табличных классов алгоритма.

    Проект должен быть остановлен (`stop`). Успех подтверждается точным
    совпадением каждой колонки с записью; расхождение даёт отказ. Откат при
    частичной записи невозможен. Удаление не с конца сдвигает порты: если
    на странице есть провода, ответ просит их проверить.

    Args:
        block: имя блока на главной странице или его числовой id.
        signals: имена сигналов по одному на строку; пустые строки
            отвергаются, повторы ввода схлопываются.
        duplicates: True - удалить повторные имена, оставить первое.
        index: 0-based позиция начала диапазона; только вместе с count.
        count: сколько строк удалить, начиная с index; по умолчанию одна.
    """
    page = session.ensure_project().get_main_page()
    target = resolve_block(page, block)
    if target is None:
        return missing_block(block)
    return remove_signal_rows(page, target, block, signals=signals,
                              duplicates=duplicates, index=index,
                              count=count)
