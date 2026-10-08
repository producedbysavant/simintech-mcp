"""Инструменты связей: connect, disconnect_wire, connect_branch, remove_block.

Сюда же — перечисление линий страницы (`list_wires`), тела контура и
разборщики ответов связевых операций. Выделено из `blocks.py` (issue #121).
"""

from __future__ import annotations

import re
from typing import Any, List, NamedTuple, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api import Wire
from simintech_api.exceptions import PortError
from simintech_api.script_probe import OUTCOME_ABORTED

from .. import runtime, session
from ..app import mcp
from .blocks import (
    block_by_id,
    missing_block,
    resolve_block,
    resolved_name,
)
from .layout import normalize_page_wires
from .page_script import (
    activate_or_refuse,
    describe_outcome,
    refuse_contour_failure,
    run_contour,
)


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def connect(src: str, dst: str,
            out_index: int = 0, in_index: int = 0) -> str:
    """Соединить выход блока src с входом блока dst линией связи.

    Созданная линия запоминается, но **не трассируется здесь**: трассировка
    (`layout_place`) делается, когда блоки займут свои места. Нормализовать
    сразу нельзя — блоки в этот момент стоят в (0,0) друг на друге, и
    `NormalizeWire` прокладывает маршрут в обход наложенных блоков, оставляя в
    геометрии точки вида (-160,-1056). Проверено на SimInTech64 2026-09-15:
    такие точки потом не пересчитываются, и линия остаётся кривой даже после
    расстановки.

    **Существующие блоки не двигаются**: `connect` только создаёт линию.
    Координаты блоков не меняются — ни у источника, ни у приёмника (контракт
    #24 п.6); выравнивание по портам делает `layout_place`.

    Args:
        src: имя/алиас блока-источника или его числовой id (`list_blocks`
            печатает id рядом с именем).
        dst: имя/алиас блока-приёмника или его числовой id.
        out_index: номер выходного порта источника (0-based).
        in_index: номер входного порта приёмника (0-based).
    """
    page = session.ensure_project().get_main_page()
    b1 = resolve_block(page, src)
    b2 = resolve_block(page, dst)
    if b1 is None:
        return missing_block(src)
    if b2 is None:
        return missing_block(dst)
    wire = b1.connect(b2, out_index=out_index, in_index=in_index)
    if not getattr(wire, "id", 0):
        # Ноль — признак «ничего не создано»: `create_block` бросает `BlockError`
        # на нулевом id. Проверка **договорная**: на SimInTech64 (2026-09-17)
        # `CreateWire` не вернул 0 ни на одном входе — вырожденные, но ненулевые
        # входы дают ненулевой id, а структурно неверные бросают `ComCallError`
        # (access violation), см. `Page.create_wire`. Оставлена как страховка:
        # пропустить ноль нельзя — агент счёл бы вход подключённым, тогда как
        # дальше по стеку отказ не виден (`NormalizeWire` на неверном WireId
        # молча возвращает 0). Отказ приходит до записи в `WIRES`.
        raise ToolError(
            f"Линия {src}[{out_index}] -> {dst}[{in_index}] не создана: "
            f"среда вернула id=0 (такое бывает, если порты принадлежат разным "
            f"страницам/слоям). Связь не запомнена — проверьте порты и "
            f"повторите `connect`."
        )
    src_name = resolved_name(b1, src)
    dst_name = resolved_name(b2, dst)
    # Храним и концы связи: по ним `layout_place` выравнивает блоки так, чтобы
    # линия шла без лишнего излома. Имена — фактические, а не сырой токен:
    # граф `layout_place` строится по именам блоков, и токен-идентификатор
    # молча выпал бы из него (находка ревью 05.10.2026).
    session.WIRES.append((wire, src_name, out_index, dst_name, in_index))
    return f"Соединено {src_name} -> {dst_name} (wire={wire.id})"


def _disconnect_wire_body(src_id: int, out_index: int,
                          dst_id: int, in_index: int) -> str:
    """Тело снятия линии для контура — форма, проверенная живым прогоном.

    Рецепт (замер 03.10.2026, поставка 2.26.6.23; пробы — во внутреннем
    хранилище): `getportwireid(вход)` — линия, приходящая во вход;
    `findstartport(вход)` — один из upstream-концов линии (на простых линиях —
    выход её начала); `removeprimitiv(id)` снимает линию, и старт расчёта это
    переживает — в отличие от БЛОКА, где `removeprimitiv` роняет
    `ProjectStart` (дефект вендора, воспроизводитель отправлен ему).

    Линия удаляется, только если среда называет её началом тот самый выход
    `src`: «снять не ту связь» хуже отказа, а по COM начало линии не читается
    вовсе. Ответ — строки через `fid` (дескриптор результата контура): их
    видит `outcome.lines`, и они уцелевают при частичном обрыве.

    Защита от нулевых портов — на случай, когда тело исполнилось не на той
    странице, где искали блоки: `getportwireid(0)` не измерен, и вызов с
    нулевым портом мог бы оборвать тело посреди работы.

    Имена — от трёх знаков и не `i`/`j`/`c`: кодогенерация SimInTech
    резервирует их под свои счётчики (стандарт ЭВС360, code-style); тело
    может быть скопировано в блок, и запас здесь бесплатен (находка ревью
    PR #108: правило было применено к `remove_block`, а здесь остались
    `w`/`fs`).
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
    )


class _DropReply(NamedTuple):
    """Разобранный ответ тела: что снято — или почему не стало.

    `kind` — «removed» | «not-connected» | «other-src» | «no-in-port» |
    «no-out-port» | «unknown». `wire_id` — снятая линия, `port_left` — линия,
    которую вход всё ещё видит после снятия, `block_id` — фактический
    источник линии при «other-src».
    """

    kind: str
    wire_id: int = 0
    port_left: int = 0
    block_id: int = 0


def _parse_drop_reply(lines: List[str]) -> _DropReply:
    """Разобрать строки тела в исход.

    «unknown» — не «ничего не произошло»: тело ответа не оставило, и решать
    по молчанию нечем (вызывающий на него отказывает).
    """
    for line in lines:
        text = line.strip()
        if text == "err=not-connected":
            return _DropReply("not-connected")
        if text == "err=no-in-port":
            return _DropReply("no-in-port")
        if text == "err=no-out-port":
            return _DropReply("no-out-port")
        if "=" not in text:
            continue
        fields = dict(part.split("=", 1) for part in text.split() if "=" in part)
        if text.startswith("removed="):
            # Отдельно от прочих: строка содержит и `removed`, и `pw`, и
            # словарь собирается целиком — частичный разбор молча показал бы
            # нули там, где значения есть.
            try:
                return _DropReply("removed", wire_id=int(fields["removed"]),
                                  port_left=int(fields.get("pw", "0")))
            except (KeyError, ValueError):
                continue
        if text.startswith("err=other-src"):
            try:
                return _DropReply("other-src", block_id=int(fields["blk"]))
            except (KeyError, ValueError):
                continue
    return _DropReply("unknown")


def _page_wire_ids(page: Any) -> Optional[List[int]]:
    """Идентификаторы линий страницы или None — перечислить не удалось.

    Читаются **до и после** правки, и не только числом: снятие линии может
    задеть не одну — среда допускает несколько линий в один вход и линии с
    несколькими концами. По разнице множеств видно, какие именно линии ушли,
    и реестр сессии чистится по факту, а не по одному названному id.
    """
    try:
        return [wire.id for wire in page.get_wires()]
    except Exception:                                             # noqa: BLE001
        return None


def _page_has_block_id(page: Any, block_id: int) -> Optional[bool]:
    """Есть ли на странице блок с этим id; None — перечислить не удалось.

    Подтверждение удаления — по id, а не по имени-токену: у пары
    «В память»/«Из памяти» имя одно на две половины, и `find_block` вернул
    бы вторую — «не подтверждено» после настоящего удаления (находка ревью
    06.10.2026).
    """
    try:
        return block_by_id(page, block_id) is not None
    except Exception:                                         # noqa: BLE001
        return None


def _vanished_wires(before_ids: Optional[List[int]],
                    after_ids: Optional[List[int]]) -> List[int]:
    """Идентификаторы линий, пропавших между двумя перечислениями страницы.

    Перечислить не удалось хоть раз — пустой список: «не знаем» здесь не
    отличается от «не исчезли», и вызывающий обязан это назвать отдельно
    (примечанием), а не выдать за благополучный ноль.
    """
    if before_ids is None or after_ids is None:
        return []
    after = set(after_ids)
    return [wire_id for wire_id in before_ids if wire_id not in after]


def _block_caption(page: Any, block_id: int) -> str:
    """Имя блока по id для отказа «линия идёт от другого». Никогда не бросает.

    Не нашли блок среди блоков страницы — называем id и говорим об этом:
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


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def disconnect_wire(src: str, dst: str,
                    out_index: int = 0, in_index: int = 0) -> str:
    """Снять линию связи, идущую из выхода src во вход dst.

    Это парная операция к `connect`: переподключение — это `disconnect_wire`,
    затем `connect` с новыми концами, два вызова.

    **Снимается только та связь, которую назвали.** Перед удалением тело
    спрашивает у среды, что она считает началом линии, приходящей во вход
    `dst[in_index]` (`findstartport`), и удаляет её, только если начало — это
    выход `src[out_index]`; иначе отказ с именем фактического блока, и проект
    не тронут. Оговорка по замеру 2026-09-23: `findstartport` называет
    **один из upstream-концов линии** — на простых линиях это источник, но на
    модели со служебным слоем он дал конец, расходящийся с вендорским эталоном.
    Снять связь, не касающуюся входа `dst`, инструмент всё равно не может:
    удаляется линия, подключённая именно к этому входу (`getportwireid`), —
    при любом расхождении приходит отказ. У линии в SimInTech бывает
    несколько концов (ветвление, слияние): снятие такой линии уносит их все.

    **Удаления в COM API нет** (`DeleteObjectPoint` — про точки). В языке
    линию убирают `removeprimitiv` и `removeobject` по id (по имени-строке
    `removeobject` рвёт тело — замер 03.10.2026); тело снимает первым.
    **Снятие среду не роняет** (замеры 03.10.2026, поставка 2.26.6.23; у
    БЛОКА тот же вызов роняет `ProjectStart` — access violation в
    `mbtylib.dll`, дефект вендора, но лишь в связке «создание и удаление в
    одном прогоне»; блоки удаляет `remove_block` — отдельным прогоном).
    Но расчёт после снятия может **структурно стоять**: если вход остался
    без подключённой линии, модель не считает до нового `connect`. Вердикт в
    ответе (`model-not-running`) это называет.

    **Модель перезапускается контуром.** Тело идёт в секцию `initialization`:
    расчёт стартует заново, прежний скрипт страницы возвращается на место,
    изменения живут в памяти до `save_project`. Снятая линия убирается и из
    реестра сессии (`session.forget_wire`, а при уходе нескольких линий — по
    всем исчезнувшим) — `layout_place` больше не считает их опорой.

    **Несколько линий в один вход.** Среда это допускает. Снимается та, что
    среда показывает на входе (`getportwireid`); замеры 03.10.2026: при двух
    линиях снятая исчезает, а вторая остаётся в модели **объектом-сиротой** —
    графически есть, но порт её не видит, и расчёт после такого снятия стоит
    (проверено приёмкой и воспроизведено: обе линии сами считаются, снятие
    одной из них — старт мёртв; лечит повторный `connect`). Ответ называет
    число линий страницы до и после (если перечислить удалось; сбой чтения
    назван отдельно) и предупреждает, если исчезло больше одной. Вход, во
    который сходилось несколько линий, после снятия стоит проверить: если
    расчёт мёртв — верните входу линию `connect`-ом или примите висячий вход
    осознанно.

    **Ослепшая топология.** После серии `removeprimitiv` по id (например,
    снятия линий циклом в одном контурном прогоне) среда может «ослепнуть»
    на порты: `getportwireid` отдаёт 0, и инструмент говорит «во вход не
    приходит ни одной линии» — при том, что в данных линия жива (выгрузка
    показывает её `src`/`dst`). Это состояние среды, не модели: сохранение и
    переоткрытие проекта (`save_project` → `reload_project`/`open_project`)
    его снимает — на свежем экземпляре линия читается и снимается штатно
    (наблюдения и лечение 06.10.2026). Если отказ пришёл по этой причине —
    не ищите ошибку в именах и индексах: сначала переоткройте проект.

    **«Снято» — по подтверждению среды, не по строке тела.** Успех
    возвращается, лишь когда линия не видна на входе и число линий страницы
    уменьшилось; иначе — отказ «снятие не подтверждено».

    Блоки ищутся на **главной странице** проекта (`connect` устроен так же),
    и её же активирует перечисление объектов — тело контура исполняется по
    той же странице.

    Args:
        src: имя блока-источника или его числовой id (автоимя и id печатает
            `list_blocks`).
        dst: имя блока-приёмника или его числовой id.
        out_index: номер выходного порта источника (0-based).
        in_index: номер входного порта приёмника (0-based).
    """
    project = session.ensure_project()
    page = project.get_main_page()
    b1 = resolve_block(page, src)
    b2 = resolve_block(page, dst)
    if b1 is None:
        return missing_block(src)
    if b2 is None:
        return missing_block(dst)
    # Порты проверяются до контура: тело получило бы нулевой порт на
    # несуществующем номере, а расчёт уже был бы запущен и остановлен.
    try:
        b1.get_out_port(out_index)
    except PortError as exc:
        raise ToolError(
            f"у блока '{src}' нет выходного порта {out_index}: {exc}. Номера "
            f"портов — с нуля, как у `connect`; состав портов уточните по "
            f"схеме.") from exc
    try:
        b2.get_in_port(in_index)
    except PortError as exc:
        raise ToolError(
            f"у блока '{dst}' нет входного порта {in_index}: {exc}. Номера "
            f"портов — с нуля, как у `connect`; состав портов уточните по "
            f"схеме.") from exc

    before_ids = _page_wire_ids(page)
    outcome, _restored = run_contour(
        _disconnect_wire_body(b1.id, out_index, b2.id, in_index),
        failed="снять линию не удалось")
    if outcome.kind == OUTCOME_ABORTED:
        # Реестр сессии приводится по факту перечисления страницы: тело могло
        # успеть снять линию до обрыва записи, и запись о ней — уже мёртвая.
        # Перечисление не удалось — реестр не трогается: «не знаем» не даёт
        # права забывать (отказ об этом и так говорит).
        for wire_id in _vanished_wires(before_ids, _page_wire_ids(page)):
            session.forget_wire(wire_id)
    refuse_contour_failure(
        outcome, failed="связь не снята", unsure="снятие не подтверждено",
        aborted_hint=("Успело ли удаление выполниться — по этому исходу не "
                      "определить: проверьте схему (`list_wires` — число "
                      "линий, `export_model_text` — концы). Прежний скрипт "
                      "страницы возвращён."))
    reply = _parse_drop_reply(outcome.lines)
    if reply.kind == "not-connected":
        raise ToolError(
            f"связь не снята: во вход {dst}[{in_index}] не приходит ни одной "
            f"линии — снимать нечего, проект не изменён. Проверьте пару "
            f"блоков и номера портов (концы линий через COM не читаются; "
            f"`export_model_text` покажет их выгрузкой).")
    if reply.kind == "other-src" and not reply.block_id:
        raise ToolError(
            f"связь не снята: среда не назвала начало линии во входе "
            f"{dst}[{in_index}] (`findstartport` вернул 0) — снять линию по "
            f"паре нельзя: неизвестно, откуда она идёт. Проект не изменён. "
            f"Так выглядят связи без читаемого начала (замер 2026-09-23) — "
            f"такую связь правьте в GUI.")
    if reply.kind == "other-src":
        source = _block_caption(page, reply.block_id)
        raise ToolError(
            f"связь не снята: начало линии во входе {dst}[{in_index}] среда "
            f"относит к блоку {source}, а не к '{src}'[{out_index}] — снять её "
            f"по этой паре нельзя, проект не изменён. Цель — снять линию, "
            f"приходящую в этот вход: назовите источником {source}; цель — "
            f"снять связь '{src}'[{out_index}] → '{dst}'[{in_index}]: в этом "
            f"входе её нет, проверьте пару.")
    if reply.kind in ("no-in-port", "no-out-port"):
        which = "входной" if reply.kind == "no-in-port" else "выходной"
        raise ToolError(
            f"связь не снята: среда не нашла {which} порт у блока в контуре, "
            f"хотя через COM он читается. Порт мог пересоздаться между "
            f"проверкой и прогоном (пересчёт портов) — повторите вызов; если "
            f"повтор не помогает, сверьте состав портов с моделью.")
    if reply.kind != "removed":
        raise ToolError(
            "снятие не подтверждено: тело отработало, но не оставило ответа "
            "— что оно успело сделать, по этому признаку не определить. "
            "Проверьте схему (`list_wires`, `export_model_text`).")

    after_ids = _page_wire_ids(page)
    vanished = _vanished_wires(before_ids, after_ids)
    # «Снято» подтверждается фактом, а не строкой тела: «removed=» пишет само
    # тело, а репозиторий не приучен верить коду возврата (run/step проверяют
    # рост времени, save_project — содержимое файла). Два признака ловят
    # разное: та же линия на входе — среда её не сняла; не уменьшившееся
    # число линий — на странице не исчезло ничего.
    if reply.port_left == reply.wire_id:
        raise ToolError(
            f"снятие не подтверждено: среда по-прежнему показывает линию "
            f"(id={reply.wire_id}) во входе {dst}[{in_index}] — удаление не "
            f"прошло; линия на месте. Повторите вызов, а если повтор не "
            f"помогает — сообщите среду и версию.")
    if (before_ids is not None and after_ids is not None
            and len(after_ids) >= len(before_ids)):
        raise ToolError(
            f"снятие не подтверждено: число линий страницы не уменьшилось "
            f"({len(before_ids)} → {len(after_ids)}), хотя тело сообщило об "
            f"удалении. Проверьте схему (`list_wires`, `export_model_text`): "
            f"линия могла остаться на месте.")
    # Реестр чистится по исчезнувшим: снятие может унести не одну линию
    # (несколько линий в один вход, линия с несколькими концами) — запись о
    # сестре тоже мертва. Названную линию забываем и сверх разницы — тело
    # сообщило «removed», а перечислиться страница могла и не успеть.
    for wire_id in vanished:
        session.forget_wire(wire_id)
    if reply.wire_id not in vanished:
        session.forget_wire(reply.wire_id)
    notes: List[str] = []
    if reply.port_left:
        notes.append(
            f"ВНИМАНИЕ: во входе {dst}[{in_index}] после снятия ещё видна "
            f"линия (id={reply.port_left}): во вход сходится несколько линий "
            f"(среда это допускает) — проверьте схему.")
    if before_ids is not None and after_ids is not None:
        note = f"Линий связи на странице: {len(before_ids)} → " \
               f"{len(after_ids)}."
        if len(vanished) > 1:
            note += (" Исчезло больше одной линии — у снятой, вероятно, были "
                     "другие концы (среда допускает линии с несколькими "
                     "концами); проверьте схему.")
        notes.append(note)
    else:
        notes.append(
            "Число линий страницы прочитать не удалось — сверьтесь со схемой "
            "(`list_wires`, `export_model_text`).")
    tail = "\n" + "\n".join(notes) if notes else ""
    return (f"Связь {src}[{out_index}] → {dst}[{in_index}] снята "
            f"(wire={reply.wire_id}).\n"
            f"{describe_outcome(outcome, what='Вердикт')}{tail}")


def _connect_branch_body(src_id: int, out_index: int, dst_id: int,
                         in_index: int, point_index: int) -> str:
    """Тело ветвления: от линии выхода src к входу dst, точка point_index.

    `createwire(prj, line_type=0, parent, K, start_port=0, end_port, 0)` —
    `start_port = 0` значит «ветвь от линии, а не от порта». Живой замер
    07.10.2026: тело отработало, ветвь легла (в выгрузке
    `src = "MBTYWire:0"`), `getparentwirenodeindex` новой линии вернул 1 при
    `K = 0`. `K` вне точек данных среда **молча сворачивает к первой**
    (замер: `K = 1` при одной точке дал узел 1 и `src = "...:0"`) — поэтому
    диапазон проверяется здесь, **до** `createwire`: `K = 0` допустим всегда
    (при нуле точек создаёт первую — `getpointcount` 0 → 1), `K > 0` требует
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
        '          writelnutf8(fid, "created=" + inttostr(new) + " parent=" +'
        ' inttostr(par) + " node=" + inttostr(node));',
        "        end;",
        "      end;",
        "    end;",
        "  end;",
        "end;",
    ]
    return "\n".join(lines) + "\n"


class _BranchReply(NamedTuple):
    """Разобранный ответ тела ветвления: исход и, при успехе, связи."""

    kind: str                 # created | no-parent-wire | point-range | ...
    wire_id: int = 0          # id новой ветви (created)
    parent_id: int = 0        # родительская линия по среде
    node: int = 0             # узел на родительской линии (у среды с единицы)
    points: int = 0           # cnt из err=point-range


def _parse_branch_reply(lines: List[str]) -> _BranchReply:
    """Разобрать строки тела: `created=… parent=… node=…` или `err=…`."""
    for raw in lines:
        line = raw.strip()
        match = re.match(
            r"created=(\d+) parent=(\d+) node=(\d+)$", line)
        if match:
            return _BranchReply(
                kind="created", wire_id=int(match.group(1)),
                parent_id=int(match.group(2)), node=int(match.group(3)))
        match = re.match(r"err=point-range cnt=(\d+)$", line)
        if match:
            return _BranchReply(kind="point-range", points=int(match.group(1)))
        match = re.match(r"err=([a-z-]+)$", line)
        if match:
            return _BranchReply(kind=match.group(1))
    return _BranchReply(kind="no-reply")


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def connect_branch(src: str, dst: str, out_index: int = 0,
                   in_index: int = 0, point_index: int = 0) -> str:
    """Создать ветвление от существующей линии выхода src к входу dst.

    Парный к `connect` случай: `connect` создаёт **линию**, этот инструмент —
    **ветвь к уже идущей линии** (правка существующей модели). Сборке с нуля
    он не нужен: среда сама сворачивает повторный `connect` из занятого
    выхода в авто-ветвь (живой замер 05.10.2026) — но та ветвь идёт от точки,
    которую выбрала среда, а этот инструмент даёт точку назвать.

    **Точка ветвления — `point_index`, с нуля.** У линии из `connect` точек
    данных нет (`getpointcount = 0`, живой замер 07.10.2026): `point_index=0`
    крепит ветвь и создаёт первую точку (0 → 1). **K вне существующих точек
    среда молча сворачивает к первой** (замер: `K = 1` при одной точке дал
    узел 1 и `src = "<родитель>:0"` в выгрузке) — поэтому `K > 0` требует
    `K < cnt`, а нулевой допустим всегда. Как создать вторую точку данных,
    не измерено: на `K > 0` рассчитывайте осознанно и проверяйте ответ.

    **Подтверждение — узел и родитель в ответе** (`getparentwirenodeindex`
    новой линии = `point_index + 1` на проверенном `K = 0`;
    `getparentwireid` — родитель). Выгрузкой (`export_model_text`) ветвь
    видна как `src = "<имя родителя>:K"` — имя родителя автоимя
    (`MBTYWire…`), не id; инструмент выгрузку не снимает: контурный прогон
    дорог (≈20 с — перезапуск расчёта), а узел приходит из того же прогона.

    **Один контурный прогон** на вызов. Страница блока активируется перед
    прогоном: скрипт ставится в текущую страницу (находка ревью PR #94);
    не удалось активировать — отказ, а не запись вслепую. Исходы
    `not-compiled`/`aborted`/`section-not-run` — отказ, как у прочих
    контурных инструментов.

    **Существующие блоки не двигаются**: инструмент только добавляет ветвь
    (контракт #24 п.6).

    Args:
        src: имя блока-источника или его числовой id — выход, ИЗ которого уже
            идёт линия (`list_blocks` печатает оба).
        dst: имя блока-приёмника или его числовой id.
        out_index: номер выходного порта источника (0-based).
        in_index: номер входного порта приёмника (0-based).
        point_index: номер точки данных родительской линии с нуля (`0` —
            проверенный случай, ≥ 0).
    """
    if point_index < 0:
        raise ToolError(
            f"point_index={point_index} отрицательный: точка данных — с нуля, "
            f"проверенный случай — 0.")
    project = session.ensure_project()
    page = project.get_main_page()
    b1 = resolve_block(page, src)
    b2 = resolve_block(page, dst)
    if b1 is None:
        return missing_block(src)
    if b2 is None:
        return missing_block(dst)
    # Порты проверяются до контура: тело получило бы нулевой порт на
    # несуществующем номере, и диагноз вышел бы ложным («у выхода нет
    # линии»). Тот же порядок, что у `disconnect_wire`.
    try:
        b1.get_out_port(out_index)
    except PortError as exc:
        raise ToolError(
            f"у блока '{src}' нет выходного порта {out_index}: {exc}. "
            f"Состав портов — `get_block_params` или выгрузка; проект не "
            f"изменён.") from exc
    try:
        b2.get_in_port(in_index)
    except PortError as exc:
        raise ToolError(
            f"у блока '{dst}' нет входного порта {in_index}: {exc}. "
            f"Состав портов — `get_block_params` или выгрузка; проект не "
            f"изменён.") from exc
    activate_or_refuse(page, action="ветвь не создана")
    outcome, _restored = run_contour(
        _connect_branch_body(b1.id, out_index, b2.id, in_index, point_index),
        failed="создать ветвление не удалось")
    refuse_contour_failure(
        outcome, failed="ветвь не создана", unsure="ветвь не подтверждена",
        aborted_hint=("Ветвь могла создаться — проверьте схему (`list_wires`, "
                      "`export_model_text`)."))
    reply = _parse_branch_reply(outcome.lines)
    if reply.kind == "no-parent-wire":
        raise ToolError(
            f"ветвь не создана: из выхода '{src}'[{out_index}] линия не идёт "
            f"(её id — ноль). `connect_branch` — только ветвление к "
            f"существующей линии: для первой связи с этим выходом — "
            f"`connect`. Проект не изменён.")
    if reply.kind == "point-range":
        # Совет по диапазону — по числу точек: при нуле точек «K < 0» было бы
        # невыполнимым советом, противоречащим запрету отрицательного K
        # (находка ревью PR #110).
        advice = ("`point_index=0` (проверенный случай)" if reply.points == 0
                  else f"`point_index=0` (всегда) или `K < {reply.points}`")
        raise ToolError(
            f"ветвь не создана: у линии выхода '{src}'[{out_index}] "
            f"{reply.points} точек данных, а запрошена точка {point_index} — "
            f"среда свернула бы ветвь к первой молча. Допустимо: {advice}. "
            f"Проект не изменён.")
    if reply.kind in ("no-in-port", "no-out-port"):
        which = "входной" if reply.kind == "no-in-port" else "выходной"
        raise ToolError(
            f"ветвь не создана: среда не нашла {which} порт у блока в "
            f"контуре, хотя через COM он читается. Порт мог пересоздаться "
            f"между проверкой и прогоном (пересчёт портов) — повторите "
            f"вызов; если повтор не помогает, сверьте состав портов с "
            f"моделью. Проект не изменён.")
    if reply.kind == "not-created":
        raise ToolError(
            "ветвь не создана: `createwire` вернул ноль. Проверьте, что у "
            "выхода есть линия и приёмник на той же странице; проект не "
            "изменён.")
    if reply.kind != "created":
        raise ToolError(
            "ветвь не подтверждена: тело отработало, но не оставило "
            "распознаваемого ответа — что оно успело сделать, по этому "
            "признаку не определить. Проверьте схему (`list_wires`, "
            "`export_model_text`).")
    src_name = resolved_name(b1, src)
    dst_name = resolved_name(b2, dst)
    session.WIRES.append((Wire(project, reply.wire_id), src_name, out_index,
                          dst_name, in_index))
    notes: List[str] = []
    if reply.node != point_index + 1:
        # Соответствие «узел = K+1» измерено только на K=0: для K>0 среда
        # могла нумеровать иначе — примечание не имеет права утверждать
        # «недостижима» на непроверенном соответствии (находка ревью).
        if point_index == 0:
            notes.append(
                f"ВНИМАНИЕ: среда сообщила узел {reply.node} при K=0 "
                f"(ожидался 1): точка недостижима на этой линии — ветвь "
                f"легла к ближайшей. Проверьте схему (`export_model_text`: "
                f"адрес ветви `src = \"<родитель>:K\"`).")
        else:
            notes.append(
                f"ВНИМАНИЕ: узел {reply.node}, а точке {point_index} по "
                f"замеру K=0 отвечал бы узел {point_index + 1} — "
                f"соответствие для K>0 не измерено. Сверьте адрес ветви "
                f"выгрузкой (`export_model_text`: `src = \"<родитель>:K\"`).")
    tail = "\n" + "\n".join(notes) if notes else ""
    return (f"Ветвь создана: {src_name}[{out_index}] → {dst_name}[{in_index}] "
            f"(wire={reply.wire_id}; родитель {reply.parent_id}, узел "
            f"{reply.node}).\n"
            f"{describe_outcome(outcome, what='Вердикт')}{tail}")


def _remove_block_body(block_id: int, with_wires: bool) -> str:
    """Тело удаления блока для контура: только удаление, отдельным прогоном.

    Дефект вендора (access violation `mbtylib.dll` на старте расчёта) даёт
    **связка** «`createmodel` и `removeprimitiv` в одном прогоне
    `initialization`»: серия проб 04.10.2026 (пробы — во внутреннем
    хранилище) и повтор на 2.26.9.29 06.10.2026. Удаление отдельным
    прогоном — и загруженного блока, и созданного в прошлом вызове — старт
    переживает (живые замеры 06.10.2026, R1b). В теле поэтому нет ни одного
    создания.

    Порты перебираются **по общему индексу** (`getblockportcount` +
    `getblockportid`): перебор отдельно входов и выходов пропускал бы
    ненаправленные порты — линия на них осталась бы сиротой при «успехе»
    (находка ревью 06.10.2026). Линии блока читаются `getportwireid`: концы
    линий через COM не читаются, а порт называет подключённую к нему линию.
    При `with_wires=False` линии не трогаются: тело называет их, блок
    остаётся; решение «снимать или нет» принимает вызывающий.
    """
    lines: List[str] = [
        f"blk = {block_id};",
        "nports = getblockportcount(blk);",
    ]
    # Набор уже увиденных линий — строкой: `pos` ищет подстроку (справка
    # языка: `indx = pos(sub_str, str)`). Без него линия, у которой ОБА
    # конца на портах этого блока, снималась бы (или считалась) дважды —
    # второй `removeprimitiv` по снятому id мог бы оборвать тело, а
    # счётчики в ответе удвоились бы (находка ревью 06.10.2026).
    lines.append('seen = "|";')
    if not with_wires:
        lines.append("busy = 0;")
    # Цикл — `while`: паскалевская форма `for i := 0 to N` в контуре не
    # компилируется (живой прогон 06.10.2026, «тело не собралось»), а
    # справочная форма «конечного цикла» `for (i = 0, N)` в контуре не
    # пробована; `while` проверена живыми телами проб (серия removeprimitiv
    # 04.10) и здесь ограничена числом портов — зацикливание невозможно.
    # Имена — длиннее двух знаков и не `i`/`j`/`c`: кодогенерация SimInTech
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


class _RemoveReply(NamedTuple):
    """Разобранный ответ тела удаления.

    `kind` — «removed» | «busy» | «unknown»; `wires` — подключённые линии
    (при «busy») или снятые (при «removed» в режиме `with_wires`);
    `bad_port` — True, если среда не отдала порт по индексу в контуре.
    """

    kind: str
    block_id: int = 0
    wires: Tuple[int, ...] = ()
    bad_port: bool = False


def _int_after_eq(text: str) -> int:
    """Число после «=» в строке тела; не разобралось — 0.

    Единственный потребитель — разбор ответа `remove_block`; живёт рядом с
    ним, а не в общем каркасе контура (находка ревью PR #122): модуль-каркас
    не должен знать формат ответа конкретного тела.
    """
    try:
        return int(text.split("=", 1)[1])
    except (IndexError, ValueError):
        return 0


def _parse_remove_reply(lines: List[str]) -> _RemoveReply:
    """Разобрать строки тела: что снято/занято — или «ответа нет»."""
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
            block_id = _int_after_eq(text)
            removed = True
        elif text.startswith("busy="):
            busy = True
        elif text.startswith(("wire=", "cut=")):
            wires.append(_int_after_eq(text))
    if removed:
        return _RemoveReply("removed", block_id=block_id, wires=tuple(wires),
                            bad_port=bad_port)
    if busy:
        return _RemoveReply("busy", wires=tuple(wires), bad_port=bad_port)
    return _RemoveReply("unknown", wires=tuple(wires), bad_port=bad_port)


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def remove_block(block: str, with_wires: bool = False) -> str:
    """Удалить блок со страницы — узкая безопасная форма `removeprimitiv`.

    Удаления в COM API нет; в языке объект убирает `removeprimitiv`, но у
    БЛОКА этот вызов роняет старт расчёта (access violation `mbtylib.dll`).
    Серия проб (04.10.2026; повтор на 2.26.9.29 — 06.10.2026) уточнила
    границу: AV даёт только связка «`createmodel` и удаление в ОДНОМ прогоне
    `initialization`». Этот инструмент удаляет **отдельным прогоном** и
    ничего в нём не создаёт — замер 06.10.2026: блок удалён, расчёт после
    этого идёт.

    **Связи по умолчанию не снимаются.** Если у блока есть подключённые
    линии — отказ с их перечислением: снимите нужные `disconnect_wire`
    (он проверяет концы) или повторите вызов с `with_wires=True` — тогда
    линии уйдут вместе с блоком, одним прогоном. Осторожно: у линии бывают
    другие концы (ветвление) — снятие уносит их все; ответ называет число
    линий страницы до и после.

    **Подтверждение — по среде.** «Удалён» говорится, лишь когда блок
    больше не находится на странице (адресация — как у `connect`: имя или
    числовой id); при `with_wires` — и число линий уменьшилось; иначе
    отказ. После удаления — перерисовка и трассировка (`NormalizeWire`),
    как у `set_block_center`; реестр сессии забывает снятые линии.

    **После серии удалений топология портов может «ослепнуть»** (линия в
    данных жива, а `getportwireid` отдаёт 0): лечится сохранением и
    переоткрытием проекта — наблюдения и порядок действий в
    `disconnect_wire`.

    Args:
        block: имя блока или его числовой id (`list_blocks` печатает оба).
        with_wires: True — снять подключённые к блоку линии вместе с ним.
    """
    project = session.ensure_project()
    page = project.get_main_page()
    target = resolve_block(page, block)
    if target is None:
        return missing_block(block)
    # Имя — из найденного объекта: тот же хелпер, что у `connect` и соседей
    # (находка ревью PR #122 — здесь жила инлайн-копия).
    name = resolved_name(target, block)

    before_ids = _page_wire_ids(page)
    outcome, _restored = run_contour(
        _remove_block_body(target.id, with_wires),
        failed="удалить блок не удалось")
    if outcome.kind == OUTCOME_ABORTED:
        # Реестр — по факту перечисления: тело могло успеть снять линии до
        # обрыва записи, и записи о них уже мёртвые.
        for wire_id in _vanished_wires(before_ids, _page_wire_ids(page)):
            session.forget_wire(wire_id)
    refuse_contour_failure(
        outcome, failed="блок не удалён", unsure="удаление не подтверждено",
        aborted_hint=("Успело ли удаление выполниться — по этому исходу не "
                      "определить: проверьте схему (`list_blocks`, "
                      "`list_wires`). Прежний скрипт страницы возвращён."),
        section_note=(", блок не удалён (причина по этому признаку не "
                      "определяется)"))
    reply = _parse_remove_reply(outcome.lines)
    if reply.bad_port and reply.kind != "removed":
        # Порт не отдался по индексу — тело ответа об удалении не оставило.
        # Отказ; линии, снятые до места отказа, учитываются перечислением
        # страницы, чтобы реестр не держал мёртвые записи (находка ревью
        # 06.10.2026).
        for wire_id in _vanished_wires(before_ids, _page_wire_ids(page)):
            session.forget_wire(wire_id)
        raise ToolError(
            "удаление не подтверждено: среда не отдала порт блока по "
            "индексу в контуре (общий перебор портов). Порт мог "
            "пересоздаться между проверкой и прогоном — повторите вызов; "
            "если повтор не помогает, сверьте блок со схемой. Блок НЕ "
            "удалён; проверьте, не осталось ли висячих линий.")
    if reply.kind == "busy" and not with_wires:
        listed = ", ".join(str(wire_id) for wire_id in reply.wires)
        shown = f": {listed}" if listed else ""
        raise ToolError(
            f"блок '{name}' (id={target.id}) не удалён: к нему подключены "
            f"линии ({len(reply.wires)}{shown}). Снимите нужные "
            f"`disconnect_wire` или повторите с with_wires=True — тогда линии "
            f"уйдут вместе с блоком. Проект не изменён.")
    if reply.kind != "removed":
        raise ToolError(
            "удаление не подтверждено: тело отработало, но не оставило "
            "ответа — что оно успело сделать, по этому признаку не "
            "определить. Проверьте схему (`list_blocks`, `list_wires`).")
    # «Удалён» — по среде и по ID, а не по исходному токену: у пары
    # «В память»/«Из памяти» имя одно на две половины, и проверка токеном
    # нашла бы вторую — «не подтверждено» после настоящего удаления (находка
    # ревью 06.10.2026). При `with_wires` — ещё и число линий уменьшилось.
    present = _page_has_block_id(page, target.id)
    if present is None:
        raise ToolError(
            "удаление не подтверждено: перечислить блоки страницы не удалось "
            "(сбой чтения через COM) — подтвердить нечем. Проверьте схему "
            "(`list_blocks`).")
    if present:
        raise ToolError(
            f"удаление не подтверждено: блок '{name}' (id={target.id}) "
            f"по-прежнему найден на странице. Повторите вызов, а если повтор "
            f"не помогает — сообщите среду и версию.")
    after_ids = _page_wire_ids(page)
    vanished = _vanished_wires(before_ids, after_ids)
    if (with_wires and reply.wires
            and before_ids is not None and after_ids is not None
            and len(after_ids) >= len(before_ids)):
        raise ToolError(
            f"снятие линий не подтверждено: число линий страницы не "
            f"уменьшилось ({len(before_ids)} → {len(after_ids)}), хотя тело "
            f"сообщило о снятии. Проверьте схему (`list_wires`, "
            f"`export_model_text`).")
    for wire_id in vanished:
        session.forget_wire(wire_id)
    if with_wires:
        for wire_id in reply.wires:
            session.forget_wire(wire_id)
    project.repaint()
    notes: List[str] = []
    if reply.bad_port:
        notes.append(
            "ВНИМАНИЕ: порт блока в контуре не читался — линия этого порта "
            "могла не сняться; проверьте схему.")
    try:
        routes = normalize_page_wires(page)
    except Exception:                                         # noqa: BLE001
        routes = ""
        notes.append("Линии не трассированы: перечислить их не удалось "
                     "(сбой чтения через COM) — проверьте схему.")
    if before_ids is not None and after_ids is not None:
        note = (f"Линий связи на странице: {len(before_ids)} → "
                f"{len(after_ids)}.")
        if len(vanished) > len(reply.wires):
            note += (" Исчезло больше линий, чем снимал инструмент: у линии "
                     "были другие концы (ветвление) — проверьте схему.")
        notes.append(note)
    else:
        notes.append("Число линий страницы прочитать не удалось — сверьтесь "
                     "со схемой (`list_wires`, `export_model_text`).")
    wires_note = (f" вместе с линиями ({len(reply.wires)})"
                  if with_wires and reply.wires else "")
    tail = "\n".join(notes)
    return (f"Блок '{name}' (id={target.id}) удалён{wires_note}.\n"
            f"{describe_outcome(outcome, what='Вердикт')}\n"
            f"{tail}{routes}")


@mcp.tool()
@runtime.com_threaded
def list_wires() -> str:
    """Перечислить линии связи текущей страницы проекта.

    Единственный способ увидеть связи, которых не создавала эта сессия: проект,
    открытый из файла или собранный в GUI, раньше давал пустой список, потому
    что линии запоминались только при вызове `connect`.

    **Что этот инструмент не умеет:** сказать, какие блоки соединяет линия. В
    COM API нет ни методов для портов связи, ни координат линии (свойство
    `Points` пусто даже после нормализации), поэтому пара «откуда → куда» не
    читается, и инструмент её не выдумывает. Доступны количество линий и их
    идентификаторы — этого хватает, например, чтобы заметить неподключённый
    вход: расчёт с висящим входом молча стоит на месте.

    **Уточнено 2026-09-28 (живой замер, поставка 2.26.6.23):** ограничение — про
    COM; концы линий отдаёт выгрузка текущего контейнера встроенным языком
    (`savemodeltofile`: `type = "wire"`, адреса концов `src = "block:out:N"` и
    `dst = "block:in:N"`, адрес ветви `src = "wireName:K"`). Сам инструмент
    по-прежнему читает только COM и пару «откуда → куда» не выдумывает.
    Подробности — README, «Ограничения», и §10.20 журнала.
    """
    page = session.ensure_project().get_main_page()
    wires = page.get_wires()
    if not wires:
        return "Линий связи на странице нет"
    blocks = len(page.get_blocks())
    return (f"Линий связи: {len(wires)} (блоков на странице: {blocks}). "
            f"Идентификаторы: "
            f"{', '.join(str(w.id) for w in wires[:20])}"
            + (" …" if len(wires) > 20 else "")
            + "\nКонцы линий (какие блоки соединены) через COM не читаются — "
              "известны только количество и идентификаторы.")
