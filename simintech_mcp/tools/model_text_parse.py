"""Разбор выгрузки текста модели: провода, граф, подписи, предупреждения.

Модуль вынесен из `model_text` чистым переносом: поведение не менялось.
Здесь живёт всё, что читает текст выгрузки и собирает из него провода,
прямые пары, граф страницы с разрешением ветвей, подписи ячеек памяти и
ветвей, а также проверки и предупреждения импорта. Инструменты
`export_model_text` и `import_model_text` и общая `page_export_text`
остаются в `model_text`; он реэкспортирует здешние имена под прежними
именами (`wire_value` как `_wire_value`), чтобы импортёры не менялись.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api.core.block import Block
from simintech_api.core.port import Port
from simintech_api.script_probe import (
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_OK,
)

from .. import session
from .page_script import run_contour
from .wires import EMPTY_WIRE_END


def wire_value(line: str) -> str:
    """Значение адреса `src`/`dst` из строки выгрузки (без кавычек)."""
    return line.split("=", 1)[1].strip().strip('",')


def block_of(address: str) -> str:
    """Имя блока или провода из адреса: часть до первого двоеточия."""
    return address.split(":")[0]


def parse_page_wires(text: str) -> "List[Tuple[str, str, str]]":
    """Записи проводов страницы: (имя провода, адрес источника, адрес конца).

    Адрес - как в выгрузке: у прямого провода `block:out:N` / `block:in:N`
    (N - индекс порта), у ветви на месте источника стоит имя провода
    (`src = "wireName:K"`): ветвь - не новый источник, а точка съёма с той же
    линии (K - номер точки с нуля; замеры 2026-09-28/29). Разбор не решает,
    что с записью делать: раскладке (`parse_page_graph`) нужен граф с
    разрешением ветвей, выравниванию (`parse_page_pairs`) - только прямые
    пары с индексами портов.
    """
    wires: "List[Tuple[str, str, str]]" = []
    name = ""
    src = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.endswith(": (") and not line.startswith("type"):
            name, src = line[:-3], ""
            continue
        if not name:
            continue
        if line.startswith("src = "):
            src = wire_value(line)
            continue
        if line.startswith("dst = ") and src:
            wires.append((name, src, wire_value(line)))
            src = ""
    return wires


def graph_from_wires(
        wires: "List[Tuple[str, str, str]]") -> "List[Tuple[str, str]]":
    """Рёбра страницы из записей проводов - с разрешением ветвей.

    Ветвь - не новый источник, а точка съёма с той же линии, поэтому её
    источник разрешается по исходному проводу. Без этого раскладчик не видит
    ветви боевой модели и кладёт каналы на 8 px уже канона за каждую.

    Отделена от `parse_page_graph` ради `layout_place`: там из одной выгрузки
    нужны и граф, и прямые пары, и двойной разбор целого текста (`parse_page_
    wires` дважды) был бы лишним - на больших страницах это заметно
    (находка ревью PR #91).
    """
    sources: "Dict[str, str]" = {
        name: block_of(src) for name, src, _dst in wires}

    def resolve(value: str) -> str:
        for _step in range(8):
            if value not in sources:
                break
            value = sources[value]
        return value

    return [(resolve(block_of(src)), block_of(dst))
            for _name, src, dst in wires]


def parse_page_graph(text: str) -> "List[Tuple[str, str]]":
    """Рёбра страницы из выгрузки - с разрешением ветвей (см. граф)."""
    return graph_from_wires(parse_page_wires(text))


def pairs_from_wires(
        wires: "List[Tuple[str, str, str]]") -> "List[Tuple[str, int, str, int]]":
    """Прямые пары с индексами портов: (источник, out, приёмник, in).

    Только провода, у которых источник - блок (`src = "block:out:N"`). Ветвь
    (`src = "wireName:K"`) пропускается: K - точка съёма с линии, а не порт
    источника, и выравнивание по ней поставило бы выход источника под
    случайную точку маршрута. Индексы N - те же, что у COM-адресации портов:
    живой замер 06.10.2026 - у провода с `dst = "LangBlock_0:in:0"` конец
    сошёлся по координате с первым входом блока. Незнакомый формат адреса
    пропускается, а не угадывается: пара с выдуманным индексом сдвинула бы
    блок по ложной цели.
    """
    pairs: "List[Tuple[str, int, str, int]]" = []
    for _name, src, dst in wires:
        src_block, out_sep, out_index = src.partition(":out:")
        dst_block, in_sep, in_index = dst.partition(":in:")
        if not out_sep or not in_sep:
            continue
        if not (out_index.isdigit() and in_index.isdigit()):
            continue
        pairs.append((src_block, int(out_index), dst_block, int(in_index)))
    return pairs


def parse_page_pairs(text: str) -> "List[Tuple[str, int, str, int]]":
    """Прямые пары из выгрузки (см. пары из записей проводов)."""
    return pairs_from_wires(parse_page_wires(text))


#: Классы ячеек беспроводной передачи сигналов: имя переменной живёт в их
#: `PortNames` (ответ владельца 05.10.2026; живые замеры 05–06.10 и 13.10.2026
#: - `getpropasstring(id, "PortNames")` вернул `ToMem_4=[#RA402]` и т. п.).
#: Выгрузка этих имён не несёт: блоки видны автоименами `ToMem_N`/`FromMem_M`,
#: а номера пар не совпадают (`ToMem_4` читается `FromMem_1` - находка fdd003
#: 13.10.2026), поэтому связать пару можно только по имени.
MEMORY_CELL_CLASSES = ("В память", "Из памяти")

#: Маркер конца строк подписи о ветвях: без него обрыв тела контура выглядел
#: бы как «ветвей нет».
WIRE_PROBE_DONE = "WDONE"

#: Точка в печати языка: «92+64i» (скобки и пробелы допускаются). Дробная
#: часть допускается намеренно: живого формата печати у нас нет (в
#: `check_model` он тоже читается с дробными), а целочисленный шаблон на
#: «92.5+64i» ловил бы «5+64i» и давал чужую клетку (находка ревью PR #154).
POINT_CELL_RE = re.compile(
    r"\(?\s*(-?\d+(?:\.\d+)?)\s*([+-])\s*(\d+(?:\.\d+)?)i\s*\)?")


def wire_probe_body(wire_ids: "List[int]") -> str:
    """Хвост тела контура: концы и родитель каждой линии - строками `W|…`.

    Развёрнуто по id, известным из COM (`page.get_wires`), - как в
    `check_model._check_script`: циклы языка в контуре применяются только в
    проверенных формах, и один оператор на линию их не требует. Функции -
    измеренные: `getwirestartpointcoord`/`getwireendpointcoord`
    (`check_model_layout`), `getparentwireid`/`getparentwirenodeindex`
    (`audit_routing`). Завершает строки маркер `WIRE_PROBE_DONE` - по нему
    `parse_wire_probe` отличает полную подпись от оборванной. Маркер пишется
    и при пустом списке линий: без него страница без проводов давала ложный
    обрыв подписи (находка ревью PR #154).
    """
    lines = [
        f'writelnutf8(fid, "W|{wire_id}|" '
        f"+ getwirestartpointcoord({wire_id}) "
        f'+ "|" + getwireendpointcoord({wire_id}) '
        f'+ "|" + inttostr(getparentwireid({wire_id})) '
        f'+ "|" + inttostr(getparentwirenodeindex({wire_id})));'
        for wire_id in wire_ids
    ]
    lines.append(f'writelnutf8(fid, "{WIRE_PROBE_DONE}");')
    return "\n".join(lines) + "\n"


def wire_probe_lines() -> "Optional[List[str]]":
    """Строки подписи о ветвях - отдельным прогоном контура.

    Прогон отдельный от выгрузки намеренно (находка ревью PR #154): в общем
    теле сбой подписи (например, отказ среды на устаревшем id) уводил исход в
    `aborted`, и `refuse_contour_failure` отменял выгрузку - хотя её текст к
    тому моменту уже записан. Здесь любой сбой (не тот исход или отказ моста)
    становится `None` - «не прочитано» в ответе, - а выгрузка живёт.

    Пустой список линий сбоем **не** является - это «ветвей нет»: провода
    на странице отсутствуют, и прогон не запускается вовсе.
    """
    try:
        page = session.ensure_project().get_current_page()
        wire_ids = [wire.id for wire in page.get_wires()]
    except Exception:                                         # noqa: BLE001
        return None
    if not wire_ids:
        return []
    try:
        outcome, _restored = run_contour(
            wire_probe_body(wire_ids),
            failed="подпись о ветвях не собралась")
    except ToolError:
        return None
    if outcome.kind in (OUTCOME_OK, OUTCOME_MODEL_NOT_RUNNING):
        return outcome.lines
    return None


def parse_wire_probe(
        lines: "Optional[List[str]]"
) -> "Optional[Dict[int, Tuple[str, str, int, int]]]":
    """Подпись о ветвях из строк тела: None - подписи нет или тело оборвалось.

    Формат строки - `W|<id>|<начало>|<конец>|<родитель>|<узел>`. `None` - не
    «ветвей нет»: без маркера пустую подпись не отличить от оборванной, и
    выдавать второе за первое нельзя (`wire_probe_lines` возвращает `None`
    и на сбой прогона). Пустой список - не оборванный прогон, а «проводов не
    было» (прогон не запускался): это пустая подпись.
    """
    if lines is None:
        return None
    if not lines:
        return {}
    probe: "Dict[int, Tuple[str, str, int, int]]" = {}
    done = False
    for line in lines:
        text = line.strip()
        if text == WIRE_PROBE_DONE:
            done = True
            continue
        parts = text.split("|")
        if len(parts) != 6 or parts[0] != "W":
            continue
        try:
            probe[int(parts[1])] = (parts[2], parts[3],
                                    int(parts[4]), int(parts[5]))
        except ValueError:
            continue
    if not done:
        return None
    return probe


def point_cell(text: str) -> "Optional[Tuple[int, int]]":
    """Клетка координаты точки из строки языка («92+64i», «-160-992i»).

    Дробные округляются к целому: клетка - общий язык подписи и карты портов,
    а порты COM отдаёт целыми.
    """
    match = POINT_CELL_RE.search(text)
    if not match:
        return None
    y = float(match.group(3))
    if match.group(2) == "-":
        y = -y
    return (round(float(match.group(1))), round(y))


def ports_of(block: Block, kind: str) -> "List[Tuple[int, Port]]":
    """Порты блока одного вида - (индекс, порт).

    Перебор - до общего числа портов (`get_port_count`): запрос за границей
    вида библиотечные `get_in_port`/`get_out_port` проводят через
    `BlockAfterEdit` (пересоздание портов) - это сцена-правка в инструменте
    чтения, и лишних итераций здесь не делаем (находка ревью PR #154). Один
    такой запрос на блок-вид остаётся - он внутри самой библиотеки, - и
    платится только когда ветви на странице есть (`identity_notes`).
    """
    ports: "List[Tuple[int, Port]]" = []
    take = block.get_in_port if kind == "in" else block.get_out_port
    for index in range(block.get_port_count()):
        try:
            ports.append((index, take(index)))
        except Exception:                                     # noqa: BLE001
            break
    return ports


def register_ports(addressing: "Dict[Tuple[int, int], str]",
                   block: Block, name: str) -> None:
    """Внести порты одного блока в адресацию клеток: координата → «блок:in:N».

    Адреса - в синтаксисе выгрузки (индексы те же, что у `pairs_from_wires`):
    по ним подпись о ветвях называет родителя, а клиент находит запись в
    тексте. Совпавшая клетка у двух разных портов адреса не получает («?») -
    угадывать адресат нельзя.
    """
    for kind in ("in", "out"):
        for index, port in ports_of(block, kind):
            try:
                coords = port.get_coords()
            except Exception:                                 # noqa: BLE001
                continue
            cell = (round(float(coords[0])), round(float(coords[1])))
            address = f"{name}:{kind}:{index}"
            if cell in addressing and addressing[cell] != address:
                addressing[cell] = "?"
            else:
                addressing[cell] = address


def cells_line(cells: "List[str]", unread: "List[str]") -> str:
    """Строка подписи ячеек памяти из собранных значений (пусто - нет ячеек)."""
    if not cells and not unread:
        return ""
    line = "Ячейки памяти (PortNames): " + "; ".join(cells)
    if unread:
        suffix = f"не прочиталось у: {', '.join(unread)}"
        line = f"{line}; {suffix}" if cells else f"Ячейки памяти: {suffix}"
    return line + "\n"


def branch_lines(probe: "Optional[Dict[int, Tuple[str, str, int, int]]]",
                 addressing: "Dict[Tuple[int, int], str]") -> str:
    """Строки подписи о ветвях: ветвь - адресом приёмника, родитель - концами.

    Ветвь адресует родителя именем (`src = "MBTYWire:0"`), а у проводов от COM
    имя одно на всех (замер 08.10.2026; находка fdd003 13.10.2026) - по имени
    родитель не находится. Здесь родитель называется своими концами в
    синтаксисе выгрузки: клиент находит родительскую запись по ним. Конец, не
    сошедшийся с портом, печатается «?» - это и признак испорченной геометрии
    (запечённые импортом маршруты - см. `import_model_text`). Пустой сегмент
    ветвления (конец `0+0i`, `wires.EMPTY_WIRE_END`) пропускается: строка о
    нём была бы строкой о невидимой пустышке (находка ревью PR #154).
    """
    if probe is None:
        return ("Ветви: не прочитано — прогон подписи не отдал строк "
                "(обрыв контура или отказ); родителя ищите в GUI.\n")
    branches = [(wire_id, data) for wire_id, data in sorted(probe.items())
                if data[2] != 0 and data[1] != EMPTY_WIRE_END]
    if not branches:
        return ""

    def address(point: str) -> str:
        cell = point_cell(point)
        if cell is None:
            return "?"
        return addressing.get(cell, "?")

    parts: "List[str]" = []
    for _wire_id, (_start, end, parent_id, node) in branches:
        parent = probe.get(parent_id)
        src = address(parent[0]) if parent else "?"
        dst = address(parent[1]) if parent else "?"
        parts.append(f"dst={address(end)} — на линии src={src} → dst={dst} "
                     f"(узел {node - 1})")
    lines = ["Ветви (родитель назван концами линии — автоимена в выгрузке "
             "неразличимы):"]
    lines.extend(f"  {part}" for part in parts)
    return "\n".join(lines) + "\n"


def identity_notes(blocks: "List[Block]",
                   probe: "Optional[Dict[int, Tuple[str, str, int, int]]]"
                   ) -> str:
    """Подписи идентичности - одним обходом блоков.

    Имя блока читается один раз на блок - и им же подписываются его порты в
    адресации, - а адресация строится, только если ветви на странице есть:
    перебор портов ходит в COM, и на странице без ветвей это была бы плата ни
    за что (находки ревью PR #154 - двойное чтение имён и жадная адресация).
    """
    cells: "List[str]" = []
    unread: "List[str]" = []
    needs_ports = probe is not None and any(
        data[2] != 0 and data[1] != EMPTY_WIRE_END for data in probe.values())
    addressing: "Dict[Tuple[int, int], str]" = {}
    for block in blocks:
        try:
            name = block.get_name()
        except Exception:                                     # noqa: BLE001
            continue
        try:
            is_cell = block.class_name in MEMORY_CELL_CLASSES
        except Exception:                                     # noqa: BLE001
            is_cell = False
        if is_cell:
            try:
                raw = block.get_property("PortNames")
            except Exception:                                 # noqa: BLE001
                unread.append(name)
            else:
                value = " ".join(str(raw).split())
                cells.append(f"{name} = {value}" if value
                             else f"{name} = (пусто)")
        if needs_ports:
            register_ports(addressing, block, name)
    return cells_line(cells, unread) + branch_lines(probe, addressing)


def wire_count() -> Optional[int]:
    """Число линий текущей страницы или `None`, если перечислить не удалось."""
    try:
        return len(session.ensure_project().get_current_page().get_wires())
    except Exception:                                          # noqa: BLE001
        return None


#: Имя записи текста: `Имя: (` в начале строки - так выглядят и объекты, и
#: провода (выгрузка). Скан по строкам - не разбор текста (философия
#: `duplicate_record_names`): он питает предупреждение, а не управляет
#: сборкой, поэтому приблизительность ему прощается, а молчание - нет.
RECORD_NAME_RE = re.compile(r"\s*([A-Za-z_][\w]*)\s*:\s*\(")

#: Имя записи в тексте: буква или подчёркивание, дальше любые буквы и цифры.
#: `\w` в Python Unicode-осведомлён, поэтому кириллические имена тоже видны.
RECORD_START_RE = re.compile(r"([^\W\d]\w*)\s*:\s*\(")


def top_level_text(body: str) -> str:
    """Текст записи без вложенных скобочных групп (свойства верхнего уровня).

    Вложенная `subsystem: (...)` не должна отдавать `type = "wire"` или
    `src`/`dst` внешней записи: иначе блок с вложенным проводом выглядел бы
    проводом, а чужой `src` приписался бы не тому имени.
    """
    out: "List[str]" = []
    depth = 0
    in_quote = False
    for char in body:
        if char == '"':
            in_quote = not in_quote
            out.append(char if depth == 0 else " ")
        elif in_quote:
            out.append(char if depth == 0 else " ")
        elif char == "(":
            depth += 1
            out.append(" ")
        elif char == ")":
            depth -= 1
            out.append(" ")
        else:
            out.append(char if depth == 0 else " ")
    return "".join(out)


def record_entries(model_text: str) -> "List[Tuple[str, str]]":
    """Записи верхнего уровня: `(имя, тело)`.

    Текст может быть обёрнут в `( … )` или подан записями без обёртки; глубина
    верхнего уровня определяется по обёртке. Записи вложенных субмоделей
    пропускаются: импорт идёт в текущий контейнер, и одноимённый блок в
    субмодели не должен влиять на проверку.
    """
    stripped = model_text.strip()
    top = 1 if stripped.startswith("(") and stripped.endswith(")") else 0
    records: "List[Tuple[str, str]]" = []
    depth = 0
    in_quote = False
    pos = 0
    for match in RECORD_START_RE.finditer(model_text):
        for char in model_text[pos:match.start()]:
            if char == '"':
                in_quote = not in_quote
            elif not in_quote:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
        pos = match.start()
        if in_quote or depth != top:
            continue
        start = match.end() - 1
        end = start
        nested = 0
        quote = False
        while end < len(model_text):
            char = model_text[end]
            if char == '"':
                quote = not quote
            elif not quote:
                if char == "(":
                    nested += 1
                elif char == ")":
                    nested -= 1
                    if nested == 0:
                        break
            end += 1
        records.append((match.group(1), model_text[start + 1:end]))
        depth += 1
        pos = match.end()
    return records


def wire_records(model_text: str) -> "List[Tuple[str, str, str]]":
    """Записи проводов верхнего уровня, включая однострочную форму импорта.

    Сначала пробуется `parse_page_wires` (форма выгрузки), затем сканер
    записей: импортный текст часто пишут одной строкой и с `type` не первым
    свойством, а компилятор принимает любой порядок. Это не разбор языка:
    нужны только имя и пара адресов.
    """
    wires = parse_page_wires(model_text)
    if wires:
        return wires
    pairs: "List[Tuple[str, str, str]]" = []
    for name, body in record_entries(model_text):
        top = top_level_text(body)
        if not re.search(r"\btype\s*=\s*\"wire\"", top):
            continue
        src = re.search(r"\bsrc\s*=\s*\"([^\"]+)\"", top)
        dst = re.search(r"\bdst\s*=\s*\"([^\"]+)\"", top)
        if src and dst:
            pairs.append((name, src.group(1), dst.group(1)))
    return pairs


def demux_out_of_range_wires(model_text: str) -> "List[str]":
    """Записи src, где «Демультиплексор» out:N при N>=2 теряет источник.

    Живой замер 02.11.2026: out:0 и out:1 импортируются как ветви, а out:2 и
    выше среда создаёт линией без `src`; повторный импорт к тем же `dst`
    добавляет пустые дубли. Проверка идёт до контура: иначе ответ «+2» скрывал
    бы линию, которая не работает.

    Неизвестность не разрешение: если класс источника не объявлен в тексте и
    не читается на текущей странице, импорт отказывает, а не пропускает
    проверку молча.
    """
    wires = wire_records(model_text)
    if not wires:
        return []
    declared: "Dict[str, str]" = {}
    for name, body in record_entries(model_text):
        top = top_level_text(body)
        match = re.search(r"\btype\s*=\s*\"([^\"]+)\"", top)
        if match and match.group(1).strip() != "wire":
            declared.setdefault(name.casefold(), match.group(1).strip())
    page = None
    classes: "Dict[str, Optional[str]]" = {}
    bad: "List[str]" = []
    for name, src, _dst in wires:
        block, sep, out_index = src.partition(":out:")
        if not sep or not out_index.isdecimal():
            continue
        try:
            out_number = int(out_index)
        except ValueError:
            continue
        if out_number < 2:
            continue
        key = block.casefold()
        class_name = declared.get(key)
        if class_name is None:
            if key in classes:
                class_name = classes[key]
            else:
                try:
                    if page is None:
                        page = session.ensure_project().get_current_page()
                    target = page.find_block(block)
                    if target is None:
                        for candidate in page.get_blocks():
                            try:
                                if str(candidate.get_name()).casefold() == key:
                                    target = candidate
                                    break
                            except Exception:                  # noqa: BLE001
                                continue
                    if target is None:
                        raise ToolError(
                            f"источник '{block}' для ветви {name} не найден ни "
                            f"в тексте, ни на текущей странице: проверить, не "
                            f"«Демультиплексор» ли это, нельзя")
                    class_name = str(target.class_name).strip()
                except ToolError:
                    raise
                except Exception as exc:                       # noqa: BLE001
                    raise ToolError(
                        f"не удалось прочитать класс блока-источника "
                        f"'{block}' для ветви {name}: {exc}. Проверка ветвей "
                        f"демультиплексора не выполнена") from exc
                classes[key] = class_name
        if class_name and class_name.casefold() == "демультиплексор":
            bad.append(f"{name}: {src}")
    return bad


def text_wire_roles(model_text: str) -> "Tuple[bool, bool]":
    """Роли проводов текста: (есть провода, есть провода на блоки этого текста).

    Развилка предупреждения - по ссылкам, а не по соседству: мусорную
    геометрию запекает трассировка по габаритам ТОЛЬКО ЧТО добавленных
    блоков, поэтому дефект называется прямо лишь у проводов, чьи концы - на
    блоках этого же текста. Дозагрузка блока к уже расставленной модели
    (провода между существующими блоками) чужой геометрией не пугается -
    находка ревью PR #150: прежний скан «есть и объекты, и провода рядом»
    срабатывал на ней как на дефекте. Имена концов - как в адресах
    `src`/`dst`; ветви разрешаются по родителю (`graph_from_wires`).
    """
    wires = parse_page_wires(model_text)
    if not wires:
        return False, False
    wire_names = {name for name, _src, _dst in wires}
    objects: "set[str]" = set()
    for line in model_text.splitlines():
        match = RECORD_NAME_RE.match(line)
        if match:
            objects.add(match.group(1))
    objects -= wire_names
    same_text = any(src in objects or dst in objects
                    for src, dst in graph_from_wires(wires))
    return True, same_text


def trace_note(before: Optional[int], after: Optional[int],
               roles: "Tuple[bool, bool]") -> str:
    """Напоминание о геометрии добавленных линий: что с ней будет и что делать.

    Маршрут импортированной линии среда запекает при создании - до пересчёта
    габаритов только что добавленных блоков (замеры 04–06.10.2026), и у
    проводов на блоки этого же текста он выходит мусорным и не перестраивается
    ничем (живое воспроизведение 10.10.2026). Поэтому ответ различает случаи
    по ролям проводов (`text_wire_roles`): у проводов на блоки этого же
    текста дефект называется прямо, а канон сборки - `connect` после импорта
    блоков (живо проверен 10.10.2026; отдельные тексты - второй путь, и он не
    для моделей с памятью); у проводов на существующие блоки - предупреждение
    о запекании и проверка выгрузкой. Прежний текст ответа звал
    «`layout_place` проложит линии» и для запечённой геометрии был неверен - с
    ним клиент шёл чинить нормализацией то, что ею не чинится (замечание
    fdd002, 10.10.2026).

    Считается **прирост** линий (чтения до и после контура), а не общее
    число: прежде по одному счётчику ответ утверждал «после импорта они не
    трассированы» про все линии страницы, включая уже проложенные чужие
    (находка ревью 02.10.2026). Ошибка чтения не превращается в отказ: модель
    уже импортирована, и отказ после изменения состояния выглядел бы как
    «импорт не удался».
    """
    if before is None or after is None:
        return ("ВНИМАНИЕ: перечислить линии страницы не удалось — "
                "ортогональность проверьте в GUI.")
    added = after - before
    if after == 0:
        return "Линий связи на странице нет — трассировать нечего."
    if added <= 0:
        return (f"Линии связи: {after}; новых импорт не добавил — "
                f"существующие линии не затронуты.")
    wires_in_text, same_text_wires = roles
    if wires_in_text and same_text_wires:
        return (
            f"Линии связи: +{added} (всего {after}) — провода из текста, и "
            f"часть из них — на блоки этого же текста: маршруты таких линий "
            f"среда запекает мусорными — звенья вида (-160,-992) вне рабочей "
            f"области, одинаковые при любых позициях блоков (живое "
            f"воспроизведение 10.10.2026). Запечённый маршрут не "
            f"перестраивается ничем: ни `layout_place`/`NormalizeWire` "
            f"(заполненный маршрут нормализация не перестраивает — мусорные "
            f"звенья остаются), ни "
            f"`save_project` + `reload_project`; адреса концов корректны — "
            f"испорчена геометрия, не связность. Блоки расставит `layout_place` "
            f"без аргументов, но эти маршруты он не починит: их пересчитывает "
            f"только пересоздание линий — снятие и `connect` заново (живой "
            f"замер 10.10.2026: импорт только блоков → `connect` → перерисовка "
            f"→ маршруты корректны) либо сборка в GUI. Модели с блоками "
            f"памяти — исключение: их собирайте ОДНИМ текстом (замер "
            f"08.10.2026: последовательные импорты ломают расчёт), а провода "
            f"такого текста чините пересозданием.")
    if wires_in_text:
        return (
            f"Линии связи: +{added} (всего {after}) — провода из текста; "
            f"ссылок на блоки этого же текста в них не видно (именно на таких "
            f"ссылках запекается мусорная геометрия — «вокруг блоков в "
            f"(0,0)»). Маршрут среда считает при создании сама — точки текста "
            f"игнорируются (замеры 04–06.10.2026); линия, пришедшая без "
            f"маршрута (`points = []`), дорисовывается "
            f"`layout_place(normalize_only=True)`; блоки, добавленные "
            f"текстом, расставит `layout_place` без аргументов. Запечённый "
            f"мусорный маршрут (звенья вдали от схемы) не пересчитывается — "
            f"лечится пересозданием линии; проверка — выгрузкой (`points`).")
    return (
        f"Линии связи: +{added} (всего {after}); записей проводов в тексте "
        f"не видно, но линии добавились — геометрию им среда запекает при "
        f"создании, до перерисовки, и заполненный маршрут нормализация не "
        f"пересчитывает — проверьте выгрузкой или снимком.")


def duplicate_record_names(model_text: str) -> List[Tuple[str, int]]:
    """Имена записей текста, встречающиеся больше одного раза: (имя, число).

    Записи модели - поля record-литерала (`Имя: ( … )`). Среда **не
    компилирует повтор имени записи**: текст с двумя `Wire:` отвергается
    целиком (живой замер 08.10.2026 - исход not-compiled, ни один объект не
    создан), и ошибка видна только в окне редактора SimInTech. При этом дубли
    порождает и сама среда: `export_model_text` печатает ветви под одним
    автоименем (`MBTYWire` ×N), так что круг «выгрузка → импорт» спотыкается
    об это штатно.

    Повтор имени в **разных** субмоделях этим сканом тоже считается - потому
    находка идёт «первой причиной к проверке» при not-compiled, а не
    приговором: текст - не наш формат, и полный разбор его грамматики
    сознательно не делается (см. докстринг инструмента). Замер 08.10.2026:
    две субмодели, в каждой запись `pin1`, - импорт **прошёл**; повтор в
    одной записи (два `Wire` на верхнем уровне) - not-compiled.
    """
    counts: Dict[str, int] = {}
    for line in model_text.splitlines():
        match = RECORD_NAME_RE.match(line)
        if match:
            name = match.group(1)
            counts[name] = counts.get(name, 0) + 1
    return sorted(((name, count) for name, count in counts.items() if count > 1),
                  key=lambda item: (-item[1], item[0]))


def place_imported_value_labels(model_text: str) -> str:
    """Вернуть подписи значений (`constLabel`) к их блокам после импорта.

    `createmodel` ставит подпись в **фиксированную** точку, а не туда, что
    записана в тексте: живой замер 30.10.2026 на минимальном тексте из двух
    блоков: подпись константы с `points=[(200, 82)]` встала в (88, 94), а
    подпись усилителя с `points=[(600, 382)]` в (248, 192). В модели,
    собранной импортом, подписи разных блоков сходятся в одну точку и
    накладываются друг на друга (на живом приводе 480 кВт так сошлись
    «0.1», «29.8» и «6.28319»). Поэтому после импорта подписи ставятся к
    своим родителям тем же правилом, что у `fit_value_labels`.

    Карта «подпись → родитель» читается **выгрузкой страницы**
    (`parentblock` через COM не отдаётся), а выгрузка стоит контурного
    прогона. Поэтому подгонка запускается только когда в тексте есть хоть
    одна запись `constLabel`: текст без подписей не должен платить за неё.
    """
    if "constLabel" not in model_text:
        return ""
    # Локальный импорт: `fits` сам импортирует этот модуль, и модульный
    # импорт дал бы цикл.
    from .fits import fit_value_labels_page

    lines: List[str] = []
    try:
        # Импорт идёт в текущий контейнер (`createmodel` в активной странице),
        # и подписи заводятся там же; отчёт об изменениях считает ту же
        # страницу (`object_names`).
        page = session.ensure_project().get_current_page()
        moved = fit_value_labels_page(page, "импорт", lines)
    except Exception as exc:                                  # noqa: BLE001
        # Подписи: оформление, их сбой не отменяет собранную модель, но и
        # молчать о нём нельзя: иначе «куча» останется незамеченной.
        return (f"\nПодписи значений: не поправлены "
                f"({type(exc).__name__}: {exc}).")
    if moved:
        return (f"\nПодписи значений подтянуты к блокам: {moved}.\n"
                + "\n".join(lines))
    if lines:
        return "\nПодписи значений: на месте.\n" + "\n".join(lines)
    return "\nПодписи значений: на месте."
