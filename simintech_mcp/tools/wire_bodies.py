"""Тела контуров и разбор ответов связевых операций.

Выделено из `wires.py`: там остались только инструменты (`connect`,
`connect_ports`, `disconnect_wire`, `connect_branch`, `remove_block`,
`list_wires`), а подготовка тел для контура, разбор их ответов и мелкие
помощники страницы переехали сюда. Тела и разборщики меняются по живым
замерам и проверяются отдельными тестами; в модуле инструментов они
утяжеляли навигацию. Поведение не менялось: `wires` реэкспортирует эти
имена под прежними именами с подчёркиванием, поэтому внешние импорты
остаются рабочими.
"""

from __future__ import annotations

import re
from typing import Any, List, NamedTuple, Optional, Tuple

from simintech_api.exceptions import PortError

from .blocks import block_by_id

#: Тип объекта «линия связи» в переборе страницы (`getobjtypeid`): замер
#: 07.10.2026 - линии дают 101, блоки 100, субмодели 102.
WIRE_TYPE_ID = 101

#: Признак пустого сегмента ветвления (линия без приёмника) - конечная точка
#: `0+0i`. Сравнение **строковое**: живой замер 08.10.2026 - числовое `= 0`
#: пустышку не отличает (все линии дают ноль).
EMPTY_WIRE_END = "0+0i"


def wire_scan_lines(indent: str, *, on_empty: Optional[List[str]] = None,
                    on_live: Optional[List[str]] = None) -> List[str]:
    """Каркас перебора линий страницы для тела контура; строки - с `indent`.

    Общий для снятия (скан пустых сегментов - `disconnect_wire_body`) и
    ветвления (подсчёт ветвей узла - `connect_branch_body`): тип линии и
    признак пустого конца - замеры, и две копии строк разошлись бы при
    уточнении (находка ревью 08.10.2026). `on_empty` исполняется для пустого
    сегмента (снятие называет его `orphan=`), `on_live` - для живой линии
    (ветвление считает ветви того же узла); не переданная секция не
    генерируется - пустого `begin … end` в теле не остаётся.
    """
    body: List[str] = [
        "contid = getcurrentcontainer;",
        "objcnt = getobjcount(contid);",
        "for(obj_idx = 1, objcnt) begin",
        "  objid = getobj(contid, obj_idx);",
        f"  if getobjtypeid(objid) = {WIRE_TYPE_ID} then begin",
    ]
    if on_empty is not None:
        body.append(
            f'    if getwireendpointcoord(objid) = "{EMPTY_WIRE_END}" then'
            f" begin")
        body += [f"      {line}" for line in on_empty]
        body.append("    end;")
    if on_live is not None:
        body.append(
            f'    if getwireendpointcoord(objid) <> "{EMPTY_WIRE_END}" then'
            f" begin")
        body += [f"      {line}" for line in on_live]
        body.append("    end;")
    body += ["  end;", "end;"]
    return [indent + line for line in body]


def as_port_id(value: Any) -> int:
    """COM-значение в int: comtypes может вернуть вариант с `.value`."""
    if hasattr(value, "value"):
        value = value.value
    return int(value)


def raw_port_id(block: Any, method: str, index: int) -> Optional[int]:
    """Id порта через сырой COM-вызов; None, если клиента нет.

    В отличие от `Block.get_out_port`, сырой `GetOutPort` не вызывает
    `BlockAfterEdit`: preflight только читает и не трогает порты.
    """
    client = getattr(block, "client", None)
    if client is None:
        return None
    return as_port_id(client.call(method, block.id, index))


def port_exists(block: Any, index: int, output: bool) -> bool:
    """Есть ли направленный порт с таким индексом, без мутаций."""
    method = "GetOutPort" if output else "GetInPort"
    raw = raw_port_id(block, method, index)
    if raw is not None:
        return raw != 0
    getter = block.get_out_port if output else block.get_in_port
    try:
        getter(index)
        return True
    except PortError:
        return False


def port_count(block: Any, output: bool) -> Optional[int]:
    """Сколько направленных портов; None, если счёт прочитать нельзя."""
    try:
        total = int(block.get_port_count())
        return sum(1 for index in range(total)
                   if port_exists(block, index, output))
    except Exception:                                          # noqa: BLE001
        return None


def endpoint_error(block: Any, name: str, index: int,
                   output: bool) -> Optional[str]:
    """Почему порт не годится для `connect`; None: годится."""
    if port_exists(block, index, output):
        return None
    kind = "выходных" if output else "входных"
    detail = f"у блока {name!r} нет {kind} портов с индексом {index}"
    count = port_count(block, output)
    if count is None:
        detail += f"; количество {kind} портов прочитать не удалось"
    else:
        detail += f"; доступно {kind} портов: {count} (индексы 0-based"
        if count:
            detail += f", допустимо 0..{count - 1}"
        detail += ")"
    detail += ("; если порт ненаправленный или электрический, используйте "
               "connect_ports")
    return detail


def disconnect_wire_body(src_id: int, out_index: int,
                         dst_id: int, in_index: int) -> str:
    """Тело снятия линии для контура - форма, проверенная живым прогоном.

    Рецепт (замер 03.10.2026, поставка 2.26.6.23; пробы - во внутреннем
    хранилище): `getportwireid(вход)` - линия, приходящая во вход;
    `findstartport(вход)` - один из upstream-концов линии (на простых линиях -
    выход её начала); `removeprimitiv(id)` снимает линию, и старт расчёта это
    переживает - в отличие от БЛОКА, где `removeprimitiv` роняет
    `ProjectStart` (дефект вендора, воспроизводитель отправлен ему).

    Линия удаляется, только если среда называет её началом тот самый выход
    `src`: «снять не ту связь» хуже отказа, а по COM начало линии не читается
    вовсе. Ответ - строки через `fid` (дескриптор результата контура): их
    видит `outcome.lines`, и они уцелевают при частичном обрыве.

    Защита от нулевых портов - на случай, когда тело исполнилось не на той
    странице, где искали блоки: `getportwireid(0)` не измерен, и вызов с
    нулевым портом мог бы оборвать тело посреди работы.

    Имена - от трёх знаков и не `i`/`j`/`c`: кодогенерация SimInTech
    резервирует их под свои счётчики (стандарт ЭВС360, code-style); тело
    может быть скопировано в блок, и запас здесь бесплатен (находка ревью
    PR #108: правило было применено к `remove_block`, а здесь остались
    `w`/`fs`).

    В конце - **скан пустых сегментов ветвления** (только чтение): линия
    без приёмника отличается нулевой конечной точкой, и в контуре это ловит
    строковое сравнение `getwireendpointcoord(objid) = "0+0i"` (живые замеры
    08.10.2026: числовое `= 0` пустышку **не** отличает - все линии дают 0).
    Такие сегменты среда создаёт парой к ветви (`connect_branch`), они
    переживают снятие ветви и видны только выгрузкой; здесь они называются
    (`orphan=`), а удаляются вручную - серия `removeprimitiv` в одном
    прогоне «слепит» топологию портов, поэтому скан только читает.
    """
    return (
        f"p_in = getinportid({dst_id}, {in_index});\n"
        f"p_out = getoutportid({src_id}, {out_index});\n"
        'if p_in = 0 then writelnutf8(fid, "err=no-in-port");\n'
        'if p_out = 0 then writelnutf8(fid, "err=no-out-port");\n'
        "if p_in <> 0 then begin\n"
        "  if p_out <> 0 then begin\n"
        "    wireId = getportwireid(p_in);\n"
        '    if wireId = 0 then writelnutf8(fid, "err=not-connected");\n'
        "    if wireId <> 0 then begin\n"
        "      startPort = findstartport(p_in);\n"
        "      if startPort = p_out then begin\n"
        "        removeprimitiv(wireId);\n"
        '        writelnutf8(fid, "removed=" + inttostr(wireId) + " pw=" + '
        "inttostr(getportwireid(p_in)));\n"
        "      end;\n"
        "      if startPort <> p_out then begin\n"
        '        writelnutf8(fid, "err=other-src blk=" + '
        "inttostr(getportblockid(startPort)));\n"
        "      end;\n"
        "    end;\n"
        "  end;\n"
        "end;\n"
        # Скан пустых сегментов ветвления - общий каркас `wire_scan_lines`
        # (одна копия с `connect_branch_body`, находка ревью 08.10.2026).
        + "".join(line + "\n" for line in wire_scan_lines(
            "", on_empty=[
                'writelnutf8(fid, "orphan=" + inttostr(objid) + " " '
                '+ getobjname(objid));',
            ]))
    )


class DropReply(NamedTuple):
    """Разобранный ответ тела: что снято - или почему не стало.

    `kind` - «removed» | «not-connected» | «other-src» | «no-in-port» |
    «no-out-port» | «unknown». `wire_id` - снятая линия, `port_left` - линия,
    которую вход всё ещё видит после снятия, `block_id` - фактический
    источник линии при «other-src».
    """

    kind: str
    wire_id: int = 0
    port_left: int = 0
    block_id: int = 0


def parse_drop_reply(lines: List[str]) -> DropReply:
    """Разобрать строки тела в исход.

    «unknown» - не «ничего не произошло»: тело ответа не оставило, и решать
    по молчанию нечем (вызывающий на него отказывает).
    """
    for line in lines:
        text = line.strip()
        if text == "err=not-connected":
            return DropReply("not-connected")
        if text == "err=no-in-port":
            return DropReply("no-in-port")
        if text == "err=no-out-port":
            return DropReply("no-out-port")
        if "=" not in text:
            continue
        fields = dict(part.split("=", 1) for part in text.split() if "=" in part)
        if text.startswith("removed="):
            # Отдельно от прочих: строка содержит и `removed`, и `pw`, и
            # словарь собирается целиком - частичный разбор молча показал бы
            # нули там, где значения есть.
            try:
                return DropReply("removed", wire_id=int(fields["removed"]),
                                 port_left=int(fields.get("pw", "0")))
            except (KeyError, ValueError):
                continue
        if text.startswith("err=other-src"):
            try:
                return DropReply("other-src", block_id=int(fields["blk"]))
            except (KeyError, ValueError):
                continue
    return DropReply("unknown")


def parse_orphan_lines(lines: List[str]) -> List[Tuple[str, str]]:
    """Строки `orphan=<id> <имя>` из ответа тела - пустые сегменты ветвления.

    Тело печатает их сканом (`disconnect_wire_body`); имя может прийти
    пустым - id нет, поэтому строки без id отбрасываются. Порядок - как
    напечатало тело (обход страницы), стабильности тут не требуется.
    """
    found: List[Tuple[str, str]] = []
    for raw in lines:
        text = raw.strip()
        if not text.startswith("orphan="):
            continue
        oid, _, oname = text[len("orphan="):].partition(" ")
        if oid:
            found.append((oid, oname.strip()))
    return found


def page_wire_ids(page: Any) -> Optional[List[int]]:
    """Идентификаторы линий страницы или None - перечислить не удалось.

    Читаются **до и после** правки, и не только числом: снятие линии может
    задеть не одну - среда допускает несколько линий в один вход и линии с
    несколькими концами. По разнице множеств видно, какие именно линии ушли,
    и реестр сессии чистится по факту, а не по одному названному id.
    """
    try:
        return [wire.id for wire in page.get_wires()]
    except Exception:                                             # noqa: BLE001
        return None


def page_has_block_id(page: Any, block_id: int) -> Optional[bool]:
    """Есть ли на странице блок с этим id; None - перечислить не удалось.

    Подтверждение удаления - по id, а не по имени-токену: у пары
    «В память»/«Из памяти» имя одно на две половины, и `find_block` вернул
    бы вторую - «не подтверждено» после настоящего удаления (находка ревью
    06.10.2026).
    """
    try:
        return block_by_id(page, block_id) is not None
    except Exception:                                         # noqa: BLE001
        return None


def vanished_wires(before_ids: Optional[List[int]],
                   after_ids: Optional[List[int]]) -> List[int]:
    """Идентификаторы линий, пропавших между двумя перечислениями страницы.

    Перечислить не удалось хоть раз - пустой список: «не знаем» здесь не
    отличается от «не исчезли», и вызывающий обязан это назвать отдельно
    (примечанием), а не выдать за благополучный ноль.
    """
    if before_ids is None or after_ids is None:
        return []
    after = set(after_ids)
    return [wire_id for wire_id in before_ids if wire_id not in after]


def block_caption(page: Any, block_id: int) -> str:
    """Имя блока по id для отказа «линия идёт от другого». Никогда не бросает.

    Не нашли блок среди блоков страницы - называем id и говорим об этом:
    источник линии может лежать на другой странице, и выдать id за имя было
    бы догадкой.
    """
    try:
        block = block_by_id(page, block_id)
    except Exception:                                             # noqa: BLE001
        block = None
    if block is None:
        return f"id={block_id} (имя среди блоков страницы не найдено)"
    try:
        name = block.get_name()
    except Exception:                                             # noqa: BLE001
        name = ""
    return (f"'{name}' (id={block_id})" if name
            else f"id={block_id} (имя блока не прочиталось)")


def connect_branch_body(src_id: int, out_index: int, dst_id: int,
                        in_index: int, point_index: int) -> str:
    """Тело ветвления: от линии выхода src к входу dst, точка point_index.

    `createwire(prj, line_type=0, parent, K, start_port=0, end_port, 0)` -
    `start_port = 0` значит «ветвь от линии, а не от порта». Живой замер
    07.10.2026: тело отработало, ветвь легла (в выгрузке
    `src = "MBTYWire:0"`), `getparentwirenodeindex` новой линии вернул 1 при
    `K = 0`. `K` вне точек данных среда **молча сворачивает к первой**
    (замер: `K = 1` при одной точке дал узел 1 и `src = "...:0"`) - поэтому
    диапазон проверяется здесь, **до** `createwire`: `K = 0` допустим всегда
    (при нуле точек создаёт первую - `getpointcount` 0 → 1), `K > 0` требует
    `K < cnt`.
    """
    lines: List[str] = [
        "prj = getcurrentprojectid;",
        f"outport = getoutportid({src_id}, {out_index});",
        'if outport = 0 then writelnutf8(fid, "err=no-out-port");',
        "if outport <> 0 then begin",
        "  parent = getportwireid(outport);",
        '  if parent = 0 then writelnutf8(fid, "err=no-parent-wire");',
        "  if parent <> 0 then begin",
        f"    inp = getinportid({dst_id}, {in_index});",
        '    if inp = 0 then writelnutf8(fid, "err=no-in-port");',
        "    if inp <> 0 then begin",
        "      cnt = getpointcount(parent);",
        f"      if ({point_index} > 0) and ({point_index} >= cnt) then",
        '        writelnutf8(fid, "err=point-range cnt=" + inttostr(cnt));',
        f"      if ({point_index} = 0) or ({point_index} < cnt) then begin",
        f"        new = createwire(prj, 0, parent, {point_index}, 0, inp, 0);",
        '        if new = 0 then writelnutf8(fid, "err=not-created");',
        "        if new <> 0 then begin",
        "          node = getparentwirenodeindex(new);",
        "          par = getparentwireid(new);",
        # Ветви в том же узле - подсчёт для правила «не больше 3 линий в узле»
        # (`rules.MAX_NODE_LINES`): перебор линий страницы - общим каркасом
        # `wire_scan_lines` (одна копия со сканом снятия, находка ревью
        # 08.10.2026), ветви того же родителя и узла - здесь. Пустые сегменты
        # (конец `0+0i` - невидимая пара, которую среда рождает вместе с
        # ветвью) отсекает каркас: живой замер 08.10.2026 - без фильтра узел
        # из трёх ветвей выглядел бы как пять линий вместо четырёх.
        "          incnt = 0;",
        *wire_scan_lines(
            "          ",
            on_live=[
                "if getparentwireid(objid) = par then begin",
                "  if getparentwirenodeindex(objid) = node then"
                " incnt = incnt + 1;",
                "end;",
            ]),
        '          writelnutf8(fid, "created=" + inttostr(new) + " parent=" +'
        ' inttostr(par) + " node=" + inttostr(node) + " nodecount=" +'
        " inttostr(incnt));",
        "        end;",
        "      end;",
        "    end;",
        "  end;",
        "end;",
    ]
    return "\n".join(lines) + "\n"


class BranchReply(NamedTuple):
    """Разобранный ответ тела ветвления: исход и, при успехе, связи."""

    kind: str                 # created | no-parent-wire | point-range | ...
    wire_id: int = 0          # id новой ветви (created)
    parent_id: int = 0        # родительская линия по среде
    node: int = 0             # узел на родительской линии (у среды с единицы)
    node_count: int = 0       # ветвей в том узле, включая созданную
    points: int = 0           # cnt из err=point-range


def parse_branch_reply(lines: List[str]) -> BranchReply:
    """Разобрать строки тела: `created=… parent=… node=…` или `err=…`."""
    for raw in lines:
        line = raw.strip()
        match = re.match(
            r"created=(\d+) parent=(\d+) node=(\d+) nodecount=(\d+)$", line)
        if match:
            return BranchReply(
                kind="created", wire_id=int(match.group(1)),
                parent_id=int(match.group(2)), node=int(match.group(3)),
                node_count=int(match.group(4)))
        match = re.match(r"err=point-range cnt=(\d+)$", line)
        if match:
            return BranchReply(kind="point-range", points=int(match.group(1)))
        match = re.match(r"err=([a-z-]+)$", line)
        if match:
            return BranchReply(kind=match.group(1))
    return BranchReply(kind="no-reply")


def remove_block_body(block_id: int, with_wires: bool) -> str:
    """Тело удаления блока для контура: только удаление, отдельным прогоном.

    Дефект вендора (access violation `mbtylib.dll` на старте расчёта) даёт
    **связка** «`createmodel` и `removeprimitiv` в одном прогоне
    `initialization`»: серия проб 04.10.2026 (пробы - во внутреннем
    хранилище) и повтор на 2.26.9.29 06.10.2026. Удаление отдельным
    прогоном - и загруженного блока, и созданного в прошлом вызове - старт
    переживает (живые замеры 06.10.2026, R1b). В теле поэтому нет ни одного
    создания.

    Порты перебираются **по общему индексу** (`getblockportcount` +
    `getblockportid`): перебор отдельно входов и выходов пропускал бы
    ненаправленные порты - линия на них осталась бы сиротой при «успехе»
    (находка ревью 06.10.2026). Линии блока читаются `getportwireid`: концы
    линий через COM не читаются, а порт называет подключённую к нему линию.
    При `with_wires=False` линии не трогаются: тело называет их, блок
    остаётся; решение «снимать или нет» принимает вызывающий.
    """
    lines: List[str] = [
        f"blk = {block_id};",
        "nports = getblockportcount(blk);",
    ]
    # Набор уже увиденных линий - строкой: `pos` ищет подстроку (справка
    # языка: `indx = pos(sub_str, str)`). Без него линия, у которой ОБА
    # конца на портах этого блока, снималась бы (или считалась) дважды -
    # второй `removeprimitiv` по снятому id мог бы оборвать тело, а
    # счётчики в ответе удвоились бы (находка ревью 06.10.2026).
    lines.append('seen = "|";')
    if not with_wires:
        lines.append("busy = 0;")
    # Цикл - `while`: паскалевская форма `for i := 0 to N` в контуре не
    # компилируется (живой прогон 06.10.2026, «тело не собралось»), а
    # справочная форма «конечного цикла» `for (i = 0, N)` в контуре не
    # пробована; `while` проверена живыми телами проб (серия removeprimitiv
    # 04.10) и здесь ограничена числом портов - зацикливание невозможно.
    # Имена - длиннее двух знаков и не `i`/`j`/`c`: кодогенерация SimInTech
    # резервирует их под свои счётчики (стандарт ЭВС360, code-style); тело
    # контура может быть скопировано в блок, и запас здесь бесплатен.
    lines.append("portIdx = 0;")
    lines.append("while portIdx < nports do begin")
    lines.append("  portId = getblockportid(blk, portIdx);")
    lines.append('  if portId = 0 then writelnutf8(fid, "err=no-port");')
    lines.append("  if portId <> 0 then begin")
    lines.append("    wireId = getportwireid(portId);")
    lines.append("    if wireId <> 0 then begin")
    lines.append('      if pos("|" + inttostr(wireId) + "|", seen) = 0 then '
                 "begin")
    lines.append('        seen = seen + inttostr(wireId) + "|";')
    if with_wires:
        lines.append("        removeprimitiv(wireId);")
        lines.append('        writelnutf8(fid, "cut=" + inttostr(wireId));')
    else:
        lines.append('        writelnutf8(fid, "wire=" + inttostr(wireId));')
        lines.append("        busy = busy + 1;")
    lines.append("      end;")
    lines.append("    end;")
    lines.append("  end;")
    lines.append("  portIdx = portIdx + 1;")
    lines.append("end;")
    if with_wires:
        lines.append("removeprimitiv(blk);")
        lines.append('writelnutf8(fid, "removed=" + inttostr(blk));')
    else:
        lines.append('if busy > 0 then writelnutf8(fid, '
                     '"busy=" + inttostr(busy));')
        lines.append("if busy = 0 then begin")
        lines.append("  removeprimitiv(blk);")
        lines.append('  writelnutf8(fid, "removed=" + inttostr(blk));')
        lines.append("end;")
    return "\n".join(lines) + "\n"


class RemoveReply(NamedTuple):
    """Разобранный ответ тела удаления.

    `kind` - «removed» | «busy» | «unknown»; `wires` - подключённые линии
    (при «busy») или снятые (при «removed» в режиме `with_wires`);
    `bad_port` - True, если среда не отдала порт по индексу в контуре.
    """

    kind: str
    block_id: int = 0
    wires: Tuple[int, ...] = ()
    bad_port: bool = False


def int_after_eq(text: str) -> int:
    """Число после «=» в строке тела; не разобралось - 0.

    Единственный потребитель - разбор ответа `remove_block`; живёт рядом с
    ним, а не в общем каркасе контура (находка ревью PR #122): модуль-каркас
    не должен знать формат ответа конкретного тела.
    """
    try:
        return int(text.split("=", 1)[1])
    except (IndexError, ValueError):
        return 0


def parse_remove_reply(lines: List[str]) -> RemoveReply:
    """Разобрать строки тела: что снято/занято - или «ответа нет»."""
    wires: List[int] = []
    bad_port = False
    block_id = 0
    removed = False
    busy = False
    for line in lines:
        text = line.strip()
        if text == "err=no-port":
            bad_port = True
        elif text.startswith("removed="):
            block_id = int_after_eq(text)
            removed = True
        elif text.startswith("busy="):
            busy = True
        elif text.startswith(("wire=", "cut=")):
            wires.append(int_after_eq(text))
    if removed:
        return RemoveReply("removed", block_id=block_id, wires=tuple(wires),
                           bad_port=bad_port)
    if busy:
        return RemoveReply("busy", wires=tuple(wires), bad_port=bad_port)
    return RemoveReply("unknown", wires=tuple(wires), bad_port=bad_port)
