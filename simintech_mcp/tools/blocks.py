"""Инструменты работы с блоками и связями.

Имена параметров проверяются `catalog.check_params` до вызова COM; разбор
текста свойств (`_split_props`, `_parse_val`, `_coerce_param_value`) — здесь же,
потому что нужен только этим инструментам.
"""

from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page, Wire
from simintech_api.catalog import decode_xprt, parse_xprt_block_script
from simintech_api.constants import standard_block_size
from simintech_api.exceptions import PortError, ScriptBridgeError
from simintech_api.script_probe import (
    OUTCOME_ABORTED,
    OUTCOME_NOT_COMPILED,
    OUTCOME_SECTION_NOT_RUN,
    ContourOutcome,
)

from .. import catalog, runtime, session
from ..app import mcp
from ..geometry import CHAR_WIDTH_ESTIMATE
from .check_model import read_port_names
from .layout import first_point, normalize_page_wires
from .model_text import page_export_text
from .page_script import (
    bridge,
    describe_outcome,
    discard_result,
    result_path,
)


# ─── Блоки и связи ────────────────────────────────────────────────

def _created_ref(block: Any) -> str:
    """Ссылка на уже созданный блок для текста отказа.

    Зовётся из `except`-ветки, поэтому сама бросать не должна: сбой чтения
    имени подменил бы исходную причину отказа. Если имя не читается, остаётся
    id блока — по нему блок тоже можно найти в `list_blocks`.
    """
    try:
        name = block.get_name()
    except Exception:
        name = ""
    if name:
        return f"name={name}"
    try:
        return f"id={block.id}"
    except Exception:
        return "имя и id прочитать не удалось"


def _missing_block(name: str) -> str:
    """Отказ «блока нет на странице» — в одном месте.

    Строка повторялась в четырёх ветках. Расхождение формулировок между ними
    не косметика: `_call_guarded` распознаёт отказ по префиксу `ERROR:`, и
    ветка, потерявшая его, вернула бы отказ как успех.
    """
    return f"ERROR: блок '{name}' не найден на странице"


#: Предел числа пар в `props` за вызов `add_block`. Каждая пара — отдельный
#: COM-вызов в единственном выделенном потоке, поэтому неограниченный `props`
#: из параметра клиента занял бы его надолго, а по таймауту COM-вызов не
#: прерывается — тот же класс опасности, что у `step` (`MAX_STEP_COUNT`) и
#: `get_signal` (`MAX_ARRAY_ITEMS`).
MAX_BLOCK_PROPS = 64

#: Предел числа входов в `in_ports`: `SetPortCount` — один COM-вызов, который
#: на таком числе портов займёт выделенный поток надолго.
MAX_BLOCK_IN_PORTS = 64


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def add_block(class_name: str, name_hint: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "", in_ports: int = 0,
              allow_unknown_props: bool = False) -> str:
    """Добавить блок на главную страницу проекта.

    **Существующие блоки не двигаются**: инструмент только добавляет объект.
    Уже расставленные блоки и ручная раскладка переживают повторные правки —
    блоки двигает только `layout_place`, и то по явному вызову (контракт
    #24 п.6).

    **Классы, отвергаемые защитой библиотеки.** «Порт выхода» `add_block`
    отвергает: отказ идёт до COM — в allowlist библиотеки класс помечен
    «не проверено, годен ли в расчёте» (замер 2026-09-18: `CreateBlock` его
    создаёт и свойства читаются, но расчётная годность не подтверждена).
    Импортом текста «Порт выхода» создаётся — сразу с `portnames` (замер
    03.10.2026), — но та же непроверенность годности остаётся: пользуйтесь
    осознанно. «Из памяти» снят с запрета 05.10.2026 — годность в расчёте
    подтверждена живым замером (пара `ToMem_0`/`FromMem_0` с `PortNames`
    считает, значение ходит через неё). Но «как обычный класс» он создать
    не даётся: **прямой `CreateBlock` отдаёт его без портов** (замер
    simintech-code, `topology.py`: «создаётся, но напрямую — без портов») —
    порт настраивается `PortNames` + `initobject` (так и был поставлен
    замер годности). Обычные классы приходят со штатными портами.

    Args:
        class_name: класс блока (русское имя, напр. 'Константа',
            'Усилитель', 'Сумматор', 'Интегратор', 'Синусоида',
            'Ступенька', 'Временной график', 'В файл').
        name_hint: желаемое имя. **Заведомо не применяется**: COM не
            переименовывает блоки, имя остаётся автоматическим (`k_0`, `kx_0`).
            Ответ вернёт фактическое имя — используйте его в `connect`,
            `get_block_params`, `layout_place`.
        x, y: координаты **левого верхнего угла** блока, не центра (так их
            трактует SimInTech: `SetBlockPosition` — это Left/Top). Можно не
            задавать — их расставит `layout_place`, он центрирует сам.
        props: параметры через запятую, напр. 'a=2' или 'a=[1, -1]'.
            Имена короткие и различаются по классам: у «Константы» — `a`
            (не `y0`), у «Сумматора» — `a` (веса входов). Имена сверяются с
            каталогом блоков **до** создания блока: неизвестное имя — отказ
            со списком известных, а не молчаливая запись в никуда. Повторы
            имён схлопываются (побеждает последнее значение — так же, как
            применяет сам SimInTech), а больше `MAX_BLOCK_PROPS` уникальных
            имён за вызов отвергается.
        in_ports: число входных портов (0 — не менять, иначе
            1…`MAX_BLOCK_IN_PORTS`). Нужно для блоков с настраиваемым числом
            входов: у «Сумматора» их по умолчанию два, и более длинный `a`
            сам по себе портов не добавляет. У части классов число входов
            задаётся **собственным параметром** (напр. `nport`): запись
            `props="nport=8"` пины **не** пересобирает — COM-путь их не
            размножает (`initobject`, `reinittopology`, save+переоткрытие не
            помогают, замер 03.10.2026). Такие блоки собирайте
            `import_model_text` с `nport = N` в тексте: пины создаются при
            создании блока. То же у «В файл»: `props="count=2"` пишет параметр
            (читается обратно как 2), но входной порт остаётся один —
            `initobject` и повторный `set_block_param` порт не добавляют
            (замер 03.10.2026).
        allow_unknown_props: True — не сверять имена с каталогом. Нужно, если
            параметр у блока есть, а в каталог не попал (каталог собран не
            для всех классов).
    """
    project = session.ensure_project()
    # Параметры разбираются и проверяются ДО создания блока: иначе отказ
    # оставил бы на схеме блок, которого нет в ответе инструмента.
    pairs: Dict[str, ParamValue] = {}
    ignored: list[str] = []
    if props:
        for pair in _split_props(props):
            if "=" in pair:
                k, _, v = pair.partition("=")
                # Словарь, а не список: повтор имени SimInTech применяет
                # последовательно (побеждает последнее значение), пользы от
                # повторов нет, а каждый — ещё один COM-вызов. Заодно это
                # считает уникальные имена для предела ниже.
                pairs[k.strip()] = _parse_val(v.strip())
            else:
                # Молча выбросить нельзя: «a=2 мусор» применил бы `a` и не
                # сказал, что вторая часть потеряна.
                ignored.append(pair)
    if len(pairs) > MAX_BLOCK_PROPS:
        raise ToolError(
            f"props: {len(pairs)} параметров больше предела "
            f"{MAX_BLOCK_PROPS} за вызов: каждая пара — отдельный COM-вызов "
            f"в единственном выделенном потоке, и такой вызов надолго занял "
            f"бы его (по таймауту COM-вызов не прерывается). Разбейте запись: "
            f"добавьте блок с частью параметров, остальные задайте через "
            f"`set_block_param`."
        )
    if in_ports < 0:
        # Библиотека отвергает это (`SetPortCount` требует >= 1), но уже
        # ПОСЛЕ `CreateBlock` — на схеме оставался бы блок, которого нет в
        # ответе инструмента.
        raise ToolError(
            f"in_ports={in_ports} меньше 1: число входов не может быть "
            f"отрицательным (0 — «не менять»)."
        )
    if in_ports > MAX_BLOCK_IN_PORTS:
        raise ToolError(
            f"in_ports={in_ports} больше предела {MAX_BLOCK_IN_PORTS}: "
            f"`SetPortCount` — один COM-вызов, который на таком числе портов "
            f"занял бы выделенный поток надолго (по таймауту COM-вызов не "
            f"прерывается)."
        )
    notes: List[str] = []
    catalog.check_params(class_name, list(pairs),
                         allow_unknown=allow_unknown_props, notes=notes)

    page = project.get_main_page()
    block = page.create_block(class_name, x, y)
    try:
        if name_hint:
            block.set_name(name_hint)
        if in_ports:
            block.set_in_port_count(in_ports)
            # Число входов меняет штатный размер блока: у «Сумматора» 32x32
            # при двух входах и 32x48 при трёх (замерено по эталонным моделям).
            size = standard_block_size(class_name, in_ports)
            if size:
                block.set_position(x, y, width=size[0], height=size[1])
        for name, value in pairs.items():
            block.set_property(name, value)
        actual = block.get_name()
    except Exception as exc:
        # Блок уже создан: молча уронить вызов нельзя — агент счёл бы его
        # неудавшимся, повторил бы `add_block` и оставил на схеме второго
        # сироту, а первый (частично настроенный) висел бы незамеченным.
        raise ToolError(
            f"Блок '{class_name}' уже создан ({_created_ref(block)}), но "
            f"настроить его не удалось: {exc}. Блок остался на схеме — "
            f"работайте с ним по этому имени (`connect`, `set_block_param`, "
            f"`get_block_params`); повторный `add_block` создаст второй."
        ) from exc

    if name_hint and actual != name_hint:
        # Проверено на SimInTech64: SetBlockProp("Name") НЕ переименовывает
        # блок — имя остаётся автоматическим (k_0, kx_0, ...), ни в
        # get_name(), ни в .xprt. Молчаливое расхождение опаснее отказа:
        # последующий connect по имени не найдёт блок.
        notes.append(f"имя '{name_hint}' НЕ применилось — блок называется "
                     f"'{actual}'; переименование через COM недоступно")
    if ignored:
        notes.append(f"параметры без '=' пропущены: {', '.join(ignored)}")
    tail = (" " + "; ".join(notes) + ".") if notes else ""
    return (f"Блок '{class_name}' создан (id={block.id}, name={actual})."
            f"{tail}")


def _resolve_block(page: Page, token: str) -> Optional[Block]:
    """Найти блок по имени или по числовому id.

    Имя адресует блок не всегда однозначно: у пары «В память»/«Из памяти» оно
    одно и то же — имя ячейки (например `#m1`), так её и находят обе половины.
    `find_block` вернул бы первую попавшуюся, и соединить пару было бы нельзя.
    Числовой id их различает, а `list_blocks` печатает его рядом с именем.

    Имя проверяется **первым** — блок с числовым именем («5») остаётся
    адресуемым по имени, а id служит запасным путём (находка ревью
    05.10.2026). Цифры — `isdecimal()`, а не `isdigit()`: надстрочные «²» и
    «①» числятся digit, но `int()` на них падает — отказ был бы про ValueError
    вместо честного «блок не найден».
    """
    token = token.strip()
    found = page.find_block(token)
    if found is not None:
        return found
    if token.isdecimal():
        wanted = int(token)
        for block in page.get_blocks():
            if getattr(block, "id", None) == wanted:
                return block
    return None


def _resolved_name(block: Block, fallback: str) -> str:
    """Имя блока для реестра связей и ответа; запасной путь — сам токен.

    В реестр (`session.WIRES`) обязан лечь токен, который поймёт `layout_place`
    (он адресует блоки именами): сырой токен бывает id или с пробелами и молча
    выпал бы из графа (находка ревью 05.10.2026). `get_name()` у читаемого
    блока не бросает; запасной путь — на случай сбоя COM уже после создания
    линии: терять связь из реестра нельзя.
    """
    try:
        return block.get_name()
    except Exception:                                         # noqa: BLE001
        return fallback.strip()


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
    b1 = _resolve_block(page, src)
    b2 = _resolve_block(page, dst)
    if b1 is None:
        return _missing_block(src)
    if b2 is None:
        return _missing_block(dst)
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
    src_name = _resolved_name(b1, src)
    dst_name = _resolved_name(b2, dst)
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


def _run_contour_body(body: str, *, failed: str) -> ContourOutcome:
    """Выполнить тело правки контуром; отказ моста — наружу.

    `ScriptBridgeError` означает неопределённое состояние проекта: тело
    могло не установиться, а могло и отработать. Поэтому он выходит отказом,
    а не исходом — тот же контракт, что у инструментов языкового слоя.
    Контурный файл результата убирается на любом пути (`discard_result`).
    """
    path = result_path()
    try:
        run = bridge().run_page_script(body, path)
    except ScriptBridgeError as exc:
        discard_result(path)
        raise ToolError(
            f"{failed}: {exc}. Тело идёт в секцию `initialization`, поэтому "
            "расчёт должен сдвинуть модельное время: проверьте, что модель "
            "считает — неподключённый вход останавливает расчёт всей модели "
            "молча.") from exc
    discard_result(path)
    return run.outcome


def _refuse_contour_failure(outcome: ContourOutcome, *, failed: str,
                            unsure: str, aborted_hint: str,
                            section_note: str = "") -> None:
    """Отказ по несделанному исходу контура — общий каркас пяти инструментов.

    Пять контурных инструментов (`disconnect_wire`, `remove_block`,
    `set_block_script`, `set_block_size`, фиты габаритов) дословно повторяли
    разбор `not-compiled`/`aborted`/`section-not-run` — копии разъезжались бы
    при первой правке текста (находка ревью). Каркас здесь один, различия —
    в подлежащих: `failed` («размер не записан») — для «не собралось» и «не
    запускалось», `unsure` («размер не подтверждён») — для обрыва,
    `aborted_hint` — что проверить после обрыва, `section_note` — уточнение
    после «тело не запускалось» (у `remove_block` оно своё).

    Прелюдии (чистка реестра по перечислению страницы) инструменты делают
    **до** вызова: у `aborted` она своя у каждого.
    """
    if outcome.kind == OUTCOME_NOT_COMPILED:
        raise ToolError(
            f"{failed}: тело не собралось (текст ошибки — в окне сообщений "
            f"редактора SimInTech; через COM он не читается). Проект не "
            f"изменён.")
    if outcome.kind == OUTCOME_ABORTED:
        detail = (f" Последняя строка тела: {outcome.lines[-1]!r}."
                  if outcome.lines else "")
        raise ToolError(
            f"{unsure}: тело оборвалось на исполнении.{detail} "
            f"{aborted_hint}")
    if outcome.kind == OUTCOME_SECTION_NOT_RUN:
        raise ToolError(
            f"{unsure}: секция `initialization` не выполнилась — тело не "
            f"запускалось{section_note}. Повторите вызов.")


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
        return any(getattr(block, "id", None) == block_id
                   for block in page.get_blocks())
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
    blocks: list[Block] = []
    try:
        blocks = page.get_blocks()
    except Exception:                                             # noqa: BLE001
        blocks = []
    for block in blocks:
        try:
            if block.id == block_id:
                return f"'{block.get_name()}' (id={block_id})"
        except Exception:                                         # noqa: BLE001
            continue
    return f"id={block_id} (имя среди блоков страницы не найдено)"


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
    b1 = _resolve_block(page, src)
    b2 = _resolve_block(page, dst)
    if b1 is None:
        return _missing_block(src)
    if b2 is None:
        return _missing_block(dst)
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
    outcome = _run_contour_body(
        _disconnect_wire_body(b1.id, out_index, b2.id, in_index),
        failed="снять линию не удалось")
    if outcome.kind == OUTCOME_ABORTED:
        # Реестр сессии приводится по факту перечисления страницы: тело могло
        # успеть снять линию до обрыва записи, и запись о ней — уже мёртвая.
        # Перечисление не удалось — реестр не трогается: «не знаем» не даёт
        # права забывать (отказ об этом и так говорит).
        for wire_id in _vanished_wires(before_ids, _page_wire_ids(page)):
            session.forget_wire(wire_id)
    _refuse_contour_failure(
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
    b1 = _resolve_block(page, src)
    b2 = _resolve_block(page, dst)
    if b1 is None:
        return _missing_block(src)
    if b2 is None:
        return _missing_block(dst)
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
    try:
        page.activate()
    except Exception as exc:                                  # noqa: BLE001
        raise ToolError(
            f"ветвь не создана: страницу блока не удалось сделать активной "
            f"({type(exc).__name__}: {exc}) — тело искало бы блоки по id на "
            f"другой странице. Проект не изменён.") from exc
    outcome = _run_contour_body(
        _connect_branch_body(b1.id, out_index, b2.id, in_index, point_index),
        failed="создать ветвление не удалось")
    if outcome.kind == OUTCOME_NOT_COMPILED:
        raise ToolError(
            "ветвь не создана: тело не собралось (текст ошибки — в окне "
            "сообщений редактора SimInTech; через COM он не читается). "
            "Проект не изменён.")
    if outcome.kind == OUTCOME_ABORTED:
        detail = (f" Последняя строка тела: {outcome.lines[-1]!r}."
                  if outcome.lines else "")
        raise ToolError(
            f"ветвь не подтверждена: тело оборвалось на исполнении.{detail} "
            f"Ветвь могла создаться — проверьте схему (`list_wires`, "
            f"`export_model_text`).")
    if outcome.kind == OUTCOME_SECTION_NOT_RUN:
        raise ToolError(
            "ветвь не подтверждена: секция `initialization` не выполнилась — "
            "тело не запускалось. Повторите вызов.")
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
    src_name = _resolved_name(b1, src)
    dst_name = _resolved_name(b2, dst)
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
    """Число после «=» в строке тела; не разобралось — 0."""
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
    target = _resolve_block(page, block)
    if target is None:
        return _missing_block(block)
    try:
        name = target.get_name()
    except Exception:                                         # noqa: BLE001
        name = block.strip()

    before_ids = _page_wire_ids(page)
    outcome = _run_contour_body(
        _remove_block_body(target.id, with_wires),
        failed="удалить блок не удалось")
    if outcome.kind == OUTCOME_ABORTED:
        # Реестр — по факту перечисления: тело могло успеть снять линии до
        # обрыва записи, и записи о них уже мёртвые.
        for wire_id in _vanished_wires(before_ids, _page_wire_ids(page)):
            session.forget_wire(wire_id)
    _refuse_contour_failure(
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


# ─── Скрипт блока «Язык программирования» ────────────────────────────────────

#: Имя свойства скрипта блока в языке и выгрузке.
_BLOCK_SCRIPT_PROP = "script"


def _normalize_script(text: str) -> str:
    """Переводы строк — к CRLF: в такой форме их несёт среда.

    Литерал встроенного языка не может содержать перевод строки, поэтому
    текст собирается построчно (`_runtime_literal`), а обратное чтение
    сравнивается с запрошенным после этой же нормализации: клиент вправе
    прислать текст с LF.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def _runtime_literal(text: str) -> str:
    """Литерал встроенного языка для **рантайма**: chr(34) и chr(13)+chr(10).

    `CLRF` здесь не годится: измерено 01.10.2026 — это константа
    декларативного текста, в рантайме её нет (скрипт с `clrf` не компилируется).

    Вход нормализуется к LF **внутри**: перевод строки в куске литерала
    недопустим, а собранный скрипт страницы прогоняется библиотечным
    `build_page_script` через `splitlines()`, который режет и по сырому `\\r`
    (находка ревью: CRLF-вход давал куски вида `"a\\r"` — вызов рвался посреди
    литерала). Перевод строки выражается только `chr(13) + chr(10)`.
    """
    if not text:
        return '""'
    parts: List[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        for piece_index, piece in enumerate(line.split('"')):
            if piece_index:
                parts.append("chr(34)")
            if piece:
                parts.append('"' + piece + '"')
        if index < len(lines) - 1:
            parts.append("chr(13) + chr(10)")
    return " + ".join(parts) if parts else '""'


def _block_script_snapshot(project: Any) -> str:
    """Снимок проекта в `.xprt` как текст — без запуска расчёта.

    `SaveProjectXML` — тот же путь, которым мост читает скриптовые записи:
    выгрузка не запускает расчёт и не сдвигает модельное время (в отличие от
    чтения скрипта контуром — `getpropasstring` перезапускает модель).
    Каталог временный: файл уходит вместе с ним.
    """
    with tempfile.TemporaryDirectory(
            prefix="simintech-block-script-",
            ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "page.xprt"
        project.save_xml(str(path))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ToolError(
                f"выгрузка проекта не создана: SaveProjectXML сообщил об "
                f"успехе, но файла {path} нет ({exc}). Снимок — путь чтения "
                f"скрипта блока без запуска расчёта, и продолжать без него "
                f"нельзя.") from exc
    return decode_xprt(raw)


@mcp.tool()
@runtime.com_threaded
def get_block_script(block: str) -> str:
    """Прочитать скрипт блока — например, «Языка программирования».

    Скрипт читается **снимком выгрузки** (`SaveProjectXML`), как скрипт
    страницы у `get_page_script`: расчёт не запускается и модельное время не
    сдвигается. Контурный путь (`getpropasstring`) для чтения не годится — он
    перезапускает модель и уничтожает результаты вызывающего.

    Скрипт есть у блока класса «Язык программирования» (запись `Script` в
    свойствах блока). У блока без такой записи ответ — «скрипта нет», а не
    отказ: это состояние блока, а не ошибка вызова. Ищется блок **главной
    страницы** (как у `connect`): у субмодели своей записи `Script` нет, и
    скрипт вложенного в неё блока за её собственный не выдаётся.

    Args:
        block: имя блока (автоимя из `list_blocks`) или его числовой id.
    """
    project = session.ensure_project()
    target = _resolve_block(project.get_main_page(), block)
    if target is None:
        return _missing_block(block)
    try:
        class_name = target.class_name
    except Exception:                                             # noqa: BLE001
        class_name = ""
    marked = f"'{block}'" + (f" [{class_name}]" if class_name else "")
    try:
        script = parse_xprt_block_script(_block_script_snapshot(project), block)
    except ScriptBridgeError as exc:
        raise ToolError(
            f"прочитать скрипт блока {marked} не удалось: {exc}. Снимок "
            f"выгрузки не разобран или значение записи не той формы — "
            f"повторный вызов после `save_project`/переоткрытия может помочь; "
            f"проект этим вызовом не тронут.") from exc
    if script is None:
        return (f"У блока {marked} скрипта нет: в выгрузке проекта у него нет "
                f"записи `Script`. Скрипт есть у блоков «Язык "
                f"программирования»; состав блоков — `list_blocks`.")
    if not script.strip():
        return (f"Скрипт блока {marked} пуст: запись `Script` есть, значение "
                f"пустое.")
    return f"Скрипт блока {marked}:\n{script}"


class _ScriptReply(NamedTuple):
    """Разобранный ответ тела записи скрипта.

    `kind` — «written» | «no-block» | «unknown»; `old`/`new` — прежний и
    перечитанный скрипт, `ports_before`/`ports_after` — число портов блока.
    """

    kind: str
    old: str = ""
    new: str = ""
    ports_before: int = 0
    ports_after: int = 0


#: Строка ответа тела с числом портов: `ports=2->4` — целиком, не поиском
#: подстроки: такие же символы могут стоять в тексте скрипта (находка ревью).
_PORTS_RE = re.compile(r"ports=(\d+)->(\d+)")


def _parse_script_reply(lines: List[str], token: str) -> _ScriptReply:
    """Разобрать строки тела: скрипты между маркерами, число портов.

    Маркеры уникальны на вызов (токен), и область разбора ограничена ими же:
    сентинел `err=no-block` и строка `ports=…` ищутся **вне** текста скрипта
    (эхо-текст идёт между маркерами). Иначе строка-сентинел внутри самого
    скрипта подменяла бы ответ — ровно то, от чего маркеры и защищают
    (находка ревью: скрипт с комментарием `// ports=9->9` давал чужие числа).
    """
    def between(which: str) -> Optional[str]:
        begin = f"{token}_{which}_BEGIN"
        end = f"{token}_{which}_END"
        try:
            first = lines.index(begin)
            last = lines.index(end)
        except ValueError:
            return None
        if last < first:
            return None
        return "\n".join(lines[first + 1:last])

    old = between("OLD")
    new = between("NEW")
    if old is None or new is None:
        # Ветка «блок не найден» маркеров не печатает вовсе — её сентинел
        # ищется только здесь, когда разбор скриптов уже не состоялся.
        if "err=no-block" in [line.strip() for line in lines]:
            return _ScriptReply("no-block")
        return _ScriptReply("unknown")
    ports_before = ports_after = 0
    for line in lines[lines.index(f"{token}_NEW_END") + 1:]:
        match = _PORTS_RE.fullmatch(line.strip())
        if match:
            ports_before = int(match.group(1))
            ports_after = int(match.group(2))
            break
    return _ScriptReply("written", old=old, new=new,
                        ports_before=ports_before, ports_after=ports_after)


def _set_block_script_body(block_name: str, script: str, token: str) -> str:
    """Тело записи скрипта блока — форма, проверенная живым прогоном.

    Рецепт (замеры 01.10.2026 и 03.10.2026): `setprop(obj, "script", …)`,
    затем `reinitlangblock(obj)` — без второго шага пины не пересобираются
    (свежий блок остаётся с дефолтным портом, провода к другим пинам —
    половинками), а `set_block_param("script")` молча не применяется.
    Прежний скрипт читается до записи, новый — после: это подтверждение
    записи. Число портов до/после показывает, что пересборка пинов прошла.
    """
    literal_name = _runtime_literal(block_name)
    literal_script = _runtime_literal(script)
    return (
        f"obj = findobjectbyname({literal_name});\n"
        'if obj = 0 then writelnutf8(fid, "err=no-block");\n'
        "if obj <> 0 then begin\n"
        f'  old_script = getpropasstring(obj, "{_BLOCK_SCRIPT_PROP}");\n'
        "  ports_before = getblockportcount(obj);\n"
        f'  setprop(obj, "{_BLOCK_SCRIPT_PROP}", {literal_script});\n'
        "  reinitlangblock(obj);\n"
        f'  new_script = getpropasstring(obj, "{_BLOCK_SCRIPT_PROP}");\n'
        "  ports_after = getblockportcount(obj);\n"
        f'  writelnutf8(fid, "{token}_OLD_BEGIN");\n'
        "  writelnutf8(fid, old_script);\n"
        f'  writelnutf8(fid, "{token}_OLD_END");\n'
        f'  writelnutf8(fid, "{token}_NEW_BEGIN");\n'
        "  writelnutf8(fid, new_script);\n"
        f'  writelnutf8(fid, "{token}_NEW_END");\n'
        '  writelnutf8(fid, "ports=" + inttostr(ports_before) + "->" + '
        "inttostr(ports_after));\n"
        "end;\n"
    )


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def set_block_script(block: str, script: str) -> str:
    """Записать скрипт в блок «Язык программирования» и пересобрать его пины.

    **Прежний скрипт — в ответе** (как у `set_page_script`): запись о прежнем
    содержимом не сообщает, и клиент должен иметь возможность его вернуть.

    **Как это устроено.** Запись — пара «`setprop(obj, "script", …)` +
    `reinitlangblock(obj)`» через контур (замеры 01.10.2026 и 03.10.2026):
    без второго шага пины не пересобираются — свежий блок остаётся с
    дефолтным портом, а провода к остальным пинам создаются половинками;
    `set_block_param("script")` при этом молча не применяется. После записи
    текст перечитывается — ответ подтверждает запись фактом, а не строкой
    тела.

    **Об ошибках компиляции среда молчит** — текст ошибки виден только в окне
    сообщений редактора SimInTech. Признака «скрипт не собрался» у инструмента
    нет: пины пересобираются и у скрипта с синтаксической ошибкой (замер:
    4 → 2), поэтому «валидность» по числу портов не проверяется. Ответ
    называет число портов до и после — по нему видно, что пересборка прошла.

    **Пустой скрипт отвергается**: он оставляет блок без портов (замер:
    2 → 0) и рвёт соединения. Если цель — осознанная очистка, передайте текст
    с комментарием — так она видна и в модели.

    **Пересборка пинов может оставить провода половинками** (у блока пины
    пересоздаются, а провода из прошлой конфигурации — нет): проверьте
    соединения и досоедините `connect`-ом; вердикт в ответе называет, считает
    ли модель. Блоки ищутся на **главной странице** проекта (как у `connect`
    и `get_block_script`).

    Args:
        block: имя блока (автоимя из `list_blocks`) или его числовой id.
        script: новый текст скрипта (канон: секции `input`/`output`/`var` и
            тело; переводы строк — любые, нормализуются к CRLF).
    """
    if not script.strip():
        raise ToolError(
            "скрипт пуст: пустой текст оставляет блок без портов (замер: "
            "2 → 0) и рвёт соединения. Если цель — очистка, передайте текст "
            "с одним комментарием — так она видна и в модели.")
    if "CTX_BEGIN" in script or "CTX_END" in script:
        raise ToolError(
            "текст скрипта содержит служебные маркеры контура "
            "(`CTX_BEGIN`/`CTX_END`): текст возвращается ответом через файл "
            "результата, границы которого эти маркеры и держат, — запись "
            "такого текста рвёт разбор ответа. Соберите маркер в тексте "
            "конкатенацией (например, `\"CTX\" + \"_END\"`), если он нужен "
            "как содержание, и повторите.")
    project = session.ensure_project()
    target = _resolve_block(project.get_main_page(), block)
    if target is None:
        return _missing_block(block)
    normalized = _normalize_script(script)
    token = "BLK" + uuid.uuid4().hex[:12]
    outcome = _run_contour_body(
        _set_block_script_body(block, normalized, token),
        failed="записать скрипт блока не удалось")
    _refuse_contour_failure(
        outcome, failed="скрипт не записан",
        unsure="запись скрипта не подтверждена",
        aborted_hint=("Скрипт блока мог измениться — проверьте его "
                      "`get_block_script`."))
    reply = _parse_script_reply(outcome.lines, token)
    if reply.kind == "no-block":
        raise ToolError(
            f"скрипт не записан: блок '{block}' не найден при исполнении "
            f"тела — он мог исчезнуть со страницы. Проект не изменён.")
    if reply.kind != "written":
        raise ToolError(
            "запись скрипта не подтверждена: тело отработало, но не оставило "
            "распознаваемого ответа. Проверьте скрипт блока `get_block_script`.")
    if _normalize_script(reply.new) != normalized:
        raise ToolError(
            f"запись не подтверждена: перечитанный скрипт не совпал с "
            f"запрошенным. В блоке теперь:\n{reply.new}\nВерните нужный текст "
            f"повторным вызовом — записи «наполовину» молча не проходят.")
    tail = (f"\n---- прежний скрипт ----\n{reply.old}" if reply.old
            else "\nПрежний скрипт был пуст.")
    return (f"Скрипт блока '{block}' записан. Портов: {reply.ports_before} → "
            f"{reply.ports_after}.\n"
            f"{describe_outcome(outcome, what='Вердикт')}{tail}")


#: Сколько блоков `list_blocks` показывает по умолчанию. Обрезка существует
#: ради контекста агента на больших страницах, но молчать о ней нельзя:
#: у потребителя не было способа увидеть id блока дальше 50-го — живой случай
#: 06.10.2026: агент перебирал числовые id вслепую, потому что `list_blocks`
#: не называл ни остатка, ни того, как его получить. Теперь называет.
DEFAULT_LIST_BLOCKS = 50

#: Предел явного `limit` у `list_blocks` — отсечка ошибок единиц, как
#: `MAX_BLOCK_SIZE`: значение больше — это уже не «сколько показать», а
#: недоразумение. `limit=0` — все блоки страницы: их число задаёт страница,
#: а не клиент, — тот же объём, что `layout_place` обходит без аргументов.
MAX_LIST_BLOCKS = 1000


@mcp.tool()
@runtime.com_threaded
def list_blocks(limit: int = DEFAULT_LIST_BLOCKS) -> str:
    """Вывести список блоков текущей страницы проекта.

    `limit: сколько блоков показать (1…`MAX_LIST_BLOCKS`); `0` — все блоки
    страницы, по умолчанию — первые `DEFAULT_LIST_BLOCKS`. Обрезка не молчит:
    в конце ответа сказано, сколько блоков всего и как показать остальные
    (`limit=0`). Список — единственное место, где видны id блоков (их берут
    `connect`, `layout_place`, `get_block_params`), поэтому у потребителя
    должна быть возможность получить их все, а не первые 50.

    `get_name` — COM-вызов на блок, отсюда предел у `limit` (см. общий
    инвариант пределов у параметров с числом COM-вызовов).
    """
    if limit < 0 or limit > MAX_LIST_BLOCKS:
        raise ToolError(
            f"limit={limit} вне диапазона: 0 — все блоки страницы, "
            f"1…{MAX_LIST_BLOCKS} — сколько показать.")
    blocks = session.ensure_project().get_main_page().get_blocks()
    if not blocks:
        return "Блоков на странице нет"
    shown = blocks if limit == 0 else blocks[:limit]
    lines: list[str] = []
    for b in shown:
        try:
            nm = b.get_name()
        except Exception:
            nm = ""
        lines.append(f"  {nm or '(без имени)'} [{b.class_name}] (id={b.id})")
    more = ""
    if len(shown) < len(blocks):
        rest = len(blocks) - len(shown)
        more = (f"\n  ... и ещё {rest} (всего {len(blocks)}; "
                f"показать все — `limit=0`)")
    return "Блоки:\n" + "\n".join(lines) + more


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


@mcp.tool()
@runtime.com_threaded
def get_block_params(block: str) -> str:
    """Прочитать параметры блока.

    COM API не умеет перечислять свойства блока, поэтому читаются имена из
    каталога блоков (`simintech_api/data/block_catalog.json`). Имена короткие
    и различаются по классам: у «Константы» `a`, у «Ступеньки» `t`/`y0`/`yk`,
    у «Интегратора» `k`/`x0`.

    У класса, которого в каталоге нет, читаются только общие свойства (в
    каталоге это `Name`) — об этом сказано в ответе примечанием: имена такого
    класса не проверяются, в том числе при записи (`set_block_param`).

    **Состав порт-блока здесь не читается.** У классов «Порт входа»/«Порт
    выхода» каталог не содержит `portnames` (у «Порта входа» — только `a`,
    `block_can_be_stub`, `src_type`), поэтому списка имён сигналов в ответе не
    появляется — хотя порты им и задаются (`setprop … "PortNames"`), и в
    выгрузке он виден. Пока это не добавлено в каталог/чтение (issue #19),
    состав порт-блока читается выгрузкой (`export_model_text`); живое
    наблюдение 01.10.2026.

    Args:
        block: имя блока на главной странице — автоматическое (их даёт
            `list_blocks`) — или его числовой id; переименование через COM
            недоступно.
    """
    page = session.ensure_project().get_main_page()
    target = _resolve_block(page, block)
    if target is None:
        return _missing_block(block)
    try:
        props = target.get_properties()
        class_name = target.class_name
    except Exception as exc:
        return f"ERROR: {exc}"

    # Ветка решается по каталогу, а не по пустоте `props`: у класса вне каталога
    # `props_for` отдаёт общие свойства, и `Name` читается всегда — то есть
    # непустой ответ ничего не говорит о покрытии класса, а пустой у класса ИЗ
    # каталога значит отказ чтения, а не «класса нет». Раньше «пусто» читалось
    # как «класс отсутствует в каталоге»: агент видел «у класса один параметр»
    # и не узнавал, что имена для него не проверяются, — тогда как на записи
    # примечание было; а при упавшем чтении получал неверный диагноз,
    # указывающий на установку каталога.
    notes: List[str] = []
    covered = catalog.is_covered(class_name, notes)
    if not props and covered:
        return (f"ERROR: блок '{block}' [{class_name}]: класс в каталоге блоков "
                f"есть, но не прочиталось ни одно его свойство — это отказ "
                f"чтения (COM), а не отсутствие класса в каталоге.")
    lines = [f"  {k} = {v}" for k, v in sorted(props.items())]
    lines += [f"  {note}" for note in notes]
    return f"Блок '{block}' [{class_name}]:\n" + "\n".join(lines)


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def set_block_param(block: str, param: str, value: str,
                    allow_unknown: bool = False) -> str:
    """Установить параметр блока и переинициализировать блок.

    Блок переинициализируется (`InitBlock`) — без этого изменение может не
    дойти до расчёта: карта COM API отмечает, что `SetBlockProp` не влияет
    на уже инициализированные блоки (например, «Константа»).

    **«Формула» и «Значение» — разные поля** (живой замер 02.10.2026,
    «Константа»): `SetBlockProp` пишет **саму «Формулу»** параметра — при
    живой формуле правка её переписывает (`a.formula`: 13.5 → 17), и пересчёт
    ничего не «переопределяет». Языковой `setprop` (в теле `run_page_script`)
    пишет «Значение» и при живой формуле **маскируется**: выглядит
    применённым, но ни чтение, ни расчёт его не видят — в таких случаях
    формулу ставит `setpropformula`.

    Имя параметра сверяется с каталогом блоков **до** записи. Раньше здесь
    было предупреждение уже после записи, а сам `SetBlockProp` неизвестные
    имена не отвергает: значение уходило в никуда, и по ответу нельзя было
    отличить применённый параметр от неприменённого.

    Отдельно отвергается `Name`: он в каталоге есть (общее свойство), но
    блок не переименовывает — COM такой записи не применяет.

    После записи параметр перечитывается, и ответ печатает прочитанное
    значение, а не строку из аргумента: среда приводит значение к своему виду
    (`0.000041` уходит в COM как `4.1e-05`), поэтому по прежнему ответу нельзя
    было увидеть, что записалось. Расхождение с запрошенным — примечанием, а не
    отказом: запись уже прошла.

    **Другие блоки не двигаются**: правка касается только названного блока —
    координаты и габариты остальных не меняются (контракт #24 п.6), ручная
    раскладка переживает повторные правки параметров.

    Args:
        block: имя блока на главной странице (автоимя из `list_blocks`) или
            его числовой id.
        param: имя параметра блока (см. `get_block_params`).
        value: значение строкой; массивы — в стиле SimInTech, напр. '[1, -1]'.
        allow_unknown: True — не сверять имя с каталогом (для параметров,
            которых в каталоге нет).
    """
    page = session.ensure_project().get_main_page()
    target = _resolve_block(page, block)
    if target is None:
        return _missing_block(block)
    notes: List[str] = []
    try:
        catalog.check_params(target.class_name, [param],
                             allow_unknown=allow_unknown, notes=notes)
        target.set_property(param, _coerce_param_value(value))
        target.init()
    except ToolError:
        # Отказ проверки (нет параметра, вычисляемый, `Name`) — уже готовый
        # `ToolError`; возвращать его текстом с префиксом значило бы гонять
        # типизированный отказ через строку и восстанавливать тип обратно.
        raise
    except Exception as exc:
        return f"ERROR: {exc}"
    # Ответ печатает то, что реально лежит в блоке, а не строку клиента:
    # `value_to_prop_string` приводит значение к своему виду ('0.000041' уходит
    # в COM как '4.1e-05'), и по прежнему ответу, повторявшему ввод, нельзя было
    # увидеть, что записалось. Перечитать не удалось — показываем ввод и
    # говорим об этом: запись уже прошла, и «ERROR» здесь был бы ложным отказом.
    try:
        actual = target.get_property(param)
        reread = True
    except Exception as exc:
        actual = value
        reread = False
        notes.append(f"перечитать '{param}' не удалось ({exc}) — показано "
                     f"запрошенное значение")
    if reread and actual != value:
        # Без догадки о причине: расхождение бывает и приведением формы
        # ('0.000041' → '4.1e-05'), и неприменённой записью, а объявить одну
        # из них — тот же неверный диагноз, от которого чинит эта правка.
        notes.append(f"в блоке '{param}' = '{actual}', а запрошено '{value}'")
    tail = f" {'; '.join(notes)}" if notes else ""
    return f"{block}.{param} = {actual}" + tail


#: Предел размера блока в пикселях. Это не предел числа COM-вызовов, а
#: отсечка очевидных ошибок единиц изменения («360» против «360 мм» или
#: «3.6»): настоящий размер блока — десятки-сотни пикселей.
MAX_BLOCK_SIZE = 10000


def _size_value(value: float) -> str:
    """Значение размера строкой: целые — без `.0`, как их пишет сама среда."""
    return f"{value:g}"


def _size_text(sizes: tuple[float, float]) -> str:
    """Размер для ответов: `360x120` — формат среды (`GetBlockPropAsString`)."""
    width, height = sizes
    return f"{_size_value(width)}x{_size_value(height)}"


#: Классы порт-блоков: у них высота не свободна, а задана жёстким правилом
#: 16 px на строку сигнала (правило передано владельцем, 01.10.2026; его
#: отсутствие «сильно ломает отображение»). Замер того же дня (поставка
#: 2.26.6.23): список сигналов **читается через COM** — `PortNames` отдаётся
#: строкой с разделителем `\r\n` (`'in\r\n'` у однозначного порта,
#: `'a1\r\na2\r\n'` у двухзначного), — а среда габарит сама **не** подгоняет:
#: порт с двумя именами остаётся 64×16.
_PORT_HEIGHT_CLASSES = ("Порт входа", "Порт выхода")

#: Высота одной строки сигнала порт-блока в пикселях: 1 сигнал — 16, 2 — 32
#: и так далее. Правило жёсткое: любая другая высота ломает отображение.
PORT_ROW_HEIGHT = 16


def _port_signal_count(target: Block) -> Optional[int]:
    """Число сигналов порт-блока по `PortNames`; `None` — прочитать не удалось.

    Пустой список — тот же `None`, а не ноль сигналов: у порт-блока имя есть
    всегда (замер: `'in\r\n'` сразу после создания), поэтому «пусто» здесь
    означает отказ чтения, а не измеренное состояние.
    """
    try:
        value = target.get_property("PortNames")
    except Exception:                                             # noqa: BLE001
        return None
    names = [line for line in str(value).splitlines() if line.strip()]
    return len(names) or None


def _port_required_height(name: str, target: Block) -> Optional[int]:
    """Обязательная высота порт-блока (`PORT_ROW_HEIGHT` × число строк).

    `None` — класс, к правилу не относящийся. «Не знаю» записью не
    открывается: нечитаемый класс блока (пустой или сбой чтения) и
    нечитаемый список сигналов — отказ (`ToolError`), а не пропуск проверки
    (находка ревью: fail-open на сбое чтения класса записывал бы ломающую
    высоту «с успехом»).
    """
    try:
        class_name = target.class_name
    except Exception as exc:                                      # noqa: BLE001
        raise ToolError(
            f"Высоту блока '{name}' задать нельзя: класс блока не читается "
            f"({type(exc).__name__}: {exc}), а у классов «Порт входа»/«Порт "
            f"выхода» высота подчиняется правилу {PORT_ROW_HEIGHT} px на "
            f"строку сигнала — к правилу ли этот блок, проверить нечем."
        ) from exc
    if not str(class_name).strip():
        raise ToolError(
            f"Высоту блока '{name}' задать нельзя: имя класса прочиталось "
            f"пустым, а у классов «Порт входа»/«Порт выхода» высота "
            f"подчиняется правилу {PORT_ROW_HEIGHT} px на строку сигнала — "
            f"к правилу ли этот блок, проверить нечем.")
    if class_name not in _PORT_HEIGHT_CLASSES:
        return None
    count = _port_signal_count(target)
    if count is None:
        raise ToolError(
            f"Высоту порт-блока '{name}' задать нельзя: у класса "
            f"«{class_name}» высота подчиняется правилу {PORT_ROW_HEIGHT} px "
            f"на строку сигнала, а список сигналов (`PortNames`) не читается "
            f"— проверить высоту нечем, и запись наугад могла бы испортить "
            f"отображение.")
    return PORT_ROW_HEIGHT * count


def _set_size_body(block_id: int, width: float, height: float) -> str:
    """Тело записи размера блока — в «Значение», как ввод в GUI без формулы.

    `SetGraphBlockProp` (COM-путь) кладёт габарит в **формулу**: в `.xprt`
    `textvalue` (формула) получает число, а «Значение» может остаться прежним
    — владелец видел это в диалоге свойств блока (через fdd002, 06.10.2026:
    Формула=352, Значение=64). Языковая пара даёт «Значение» без формулы:
    `setpropformula(blk, "Width", "")` снимает формулу (пустая строка
    принимается, флаг 1), `setprop(blk, "Width", N)` пишет значение.

    Живые замеры 06.10.2026 (2.26.9.29): после пары и `getprop`, и
    `get_size` (`GetGraphBlockProp`) читают новое значение (120×28), а в
    `.xprt` лежит `value='120'`, `textvalue=''`; при ЖИВОЙ формуле `setprop`
    маскируется (сохранение берёт формулу) — потому очистка обязательна и
    стоит первой.
    """
    return "\n".join([
        f"blk = {block_id};",
        'setpropformula(blk, "Width", "");',
        f'setprop(blk, "Width", {_size_value(width)});',
        'setpropformula(blk, "Height", "");',
        f'setprop(blk, "Height", {_size_value(height)});',
    ]) + "\n"


@mcp.tool()
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
            f"Ширина {_size_value(width)} вне пределов: ширина — в пикселях, "
            f"больше 0 и не больше {MAX_BLOCK_SIZE} (отсечка ошибок единиц "
            f"измерения: настоящий размер блока — десятки-сотни пикселей).")
    project = session.ensure_project()
    page = project.get_main_page()
    target = _resolve_block(page, block)
    if target is None:
        return _missing_block(block)
    required = _port_required_height(block, target)
    if required is not None and float(height) != float(required):
        height_text = (f"{height:g}" if float(height).is_integer()
                       else repr(float(height)))
        unsat = ""
        if required > MAX_BLOCK_SIZE:
            unsat = (f" Правило даёт {required} px, а предел размера — "
                     f"{MAX_BLOCK_SIZE}: этому блоку высоту этим инструментом "
                     f"задать нельзя вовсе.")
        raise ToolError(
            f"Высоту порт-блока '{block}' задать нельзя: у «Порта входа»/"
            f"«Порта выхода» высота подчиняется правилу {PORT_ROW_HEIGHT} px "
            f"на строку сигнала — у этого блока строк "
            f"{required // PORT_ROW_HEIGHT} (`PortNames`), значит высота — "
            f"{required} px, а запрошено {height_text}. Среда габарит сама не "
            f"подгоняет (замер 01.10.2026: порт с двумя именами остаётся "
            f"64×16), и другая высота ломает отображение строк; ширина при "
            f"этом свободна." + unsat)
    if not 0 < height <= MAX_BLOCK_SIZE:
        # Предельная проверка высоты стоит ПОСЛЕ правила: запрос ровно
        # правила (11200 при 700 строках) иначе упирался бы в предел, не
        # услышав, что тупик неразрешим (находка ревью).
        unsat = ""
        if required is not None and required > MAX_BLOCK_SIZE:
            unsat = (f" Учтите: правило {PORT_ROW_HEIGHT} px на строку требует "
                     f"для этого блока {required} px — этим инструментом "
                     f"высоту ему задать нельзя вовсе.")
        raise ToolError(
            f"Высота {_size_value(height)} вне пределов: высота — в пикселях, "
            f"больше 0 и не больше {MAX_BLOCK_SIZE} (отсечка ошибок единиц "
            f"измерения: настоящий размер блока — десятки-сотни пикселей)."
            + unsat)
    before = target.get_size()
    requested = (float(width), float(height))
    # Страница блока становится активной: скрипт ставится в текущую страницу
    # (`SetPageScript` → `GetCurentPage`), и `blk = <id>` ищется на ней. Без
    # этого запись из GUI, уведённого в субмодель, ушла бы мимо блока или в
    # блок-двойник с тем же id (находка ревью PR #94): прежний COM-путь от
    # активной страницы не зависел. Не удалось активировать — отказ: писать
    # вслепую нельзя.
    try:
        page.activate()
    except Exception as exc:                                  # noqa: BLE001
        raise ToolError(
            f"размер не записан: страницу блока не удалось сделать активной "
            f"({type(exc).__name__}: {exc}) — запись по id могла бы уйти в "
            f"блок другой страницы. Проект не изменён.") from exc
    # Запись — контуром, в «Значение» (см. `_set_size_body`): COM-путь
    # `SetGraphBlockProp` кладёт габарит в «Формулу».
    outcome = _run_contour_body(
        _set_size_body(target.id, float(width), float(height)),
        failed="записать размер блока не удалось")
    _refuse_contour_failure(
        outcome, failed="размер не записан", unsure="размер не подтверждён",
        aborted_hint=("Проверьте габарит в GUI (`get_size` прочитает "
                      "фактический)."))
    # Порядок как у расстановки: изменённая геометрия → перерисовка →
    # трассировка линий (контракт `layout_place`).
    project.repaint()
    for wire in page.get_wires():
        # `normalize()` безопасен и на линии, созданной не этой сессией.
        wire.normalize()
    after = target.get_size()
    if required is not None and float(after[1]) != float(required):
        # Среда могла преобразовать запись (как `_EvenOnlyBlock` в тестах):
        # «успех с примечанием» здесь оставил бы порт со сломанным
        # отображением и отчитался успехом (находка ревью).
        raise ToolError(
            f"Размер блока '{block}' записан, но среда приняла высоту "
            f"{_size_text(after)}: у порт-блока она обязана быть {required} px "
            f"({PORT_ROW_HEIGHT} на строку, строк "
            f"{required // PORT_ROW_HEIGHT}) — отображение строк испорчено. "
            f"Это отступление от замера 01.10.2026; повторите вызов, а если "
            f"повтор не помогает — сообщите среду и версию.")
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
            f"{_size_text(after)}{note}")


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


def _plan_port_width(block: Block, where: str,
                     lines: List[str], plan: List[_SizeFix]) -> None:
    """Запланировать расширение порт-блока под самую длинную подпись.

    Ширина — по той же оценке, что ловит `check_model_layout`
    (`CHAR_WIDTH_ESTIMATE` px на символ): правка и проверка обязаны
    сходиться, иначе проверка продолжила бы ругаться на исправленное.
    Высота не трогается — у порт-блоков она подчинена правилу 16 px на
    строку сигнала (`set_block_size`). Записи здесь нет: саму правку
    применяет общий прогон страницы.
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
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {name}: ширина не читается "
                     f"({type(exc).__name__}: {exc}) — пропущен.")
        return
    longest = max(names, key=len)
    estimate = len(longest) * CHAR_WIDTH_ESTIMATE
    if estimate <= width:
        return
    plan.append(_SizeFix(
        block=block, name=name, prop="Width", before=width,
        want=float(estimate),
        note=(f"«{longest}» ~{estimate:g} px, оценка "
              f"×{CHAR_WIDTH_ESTIMATE:g}")))


def _plan_submodel_height(block: Block, where: str,
                          lines: List[str], plan: List[_SizeFix]) -> None:
    """Запланировать высоту блока-субмодели — 16 px на его внешний порт.

    Импорт ставит субмодели 48×32 независимо от числа портов (замеры
    06.10.2026: и у нашей сборки, и у fdd002 при 6 портах — те же 48×32).
    Читаемая высота — `PORT_ROW_HEIGHT` × число портов (6 портов → 96);
    ширина не трогается. Записи здесь нет: саму правку применяет общий
    прогон страницы.
    """
    try:
        name = block.get_name()
    except Exception:                                         # noqa: BLE001
        name = f"id={block.id}"
    try:
        ports = int(block.get_port_count())
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {name}: порты не читаются "
                     f"({type(exc).__name__}: {exc}) — высота не проверена.")
        return
    if ports <= 0:
        return
    want = float(ports * PORT_ROW_HEIGHT)
    try:
        height = float(block.get_size()[1])
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: {name}: высота не читается "
                     f"({type(exc).__name__}: {exc}) — пропущена.")
        return
    if height == want:
        return
    plan.append(_SizeFix(
        block=block, name=name, prop="Height", before=height, want=want,
        note=f"{ports} порт(ов) × {PORT_ROW_HEIGHT:g}"))


def _size_fixes_body(plan: List[_SizeFix]) -> str:
    """Тело контура для плана правок — пары «снять формулу + записать значение».

    Тот же путь, что у `set_block_size` (живые замеры 06.10.2026): COM-метод
    `SetGraphBlockProp` кладёт число в «Формулу», а языковая пара — в
    «Значение». Очистка формулы обязательна и стоит первой: при живой формуле
    `setprop` маскируется (сохранение берёт формулу).
    """
    lines: List[str] = []
    for fix in plan:
        lines.append(f"blk = {fix.block.id};")
        lines.append(f'setpropformula(blk, "{fix.prop}", "");')
        lines.append(f'setprop(blk, "{fix.prop}", {_size_value(fix.want)});')
    return "\n".join(lines) + "\n"


def _apply_size_plan(page: Page, where: str, lines: List[str],
                     plan: List[_SizeFix]) -> int:
    """Применить план правок одним контурным прогоном; вернуть число сбывшихся.

    Одним прогоном — не оптимизация, а условие: контур перезапускает расчёт,
    и прогон на каждый блок стоил бы его N раз (у порт-блоков N — десятки).
    Страница перед прогоном активируется: скрипт ставится в текущую страницу,
    и блоки ищутся по id на ней; не удалось активировать — **отказ**, а не
    прогон вслепую: промах записи выглядел бы как «среда не приняла запись»
    и увёл бы диагноз в сторону среды (находка ревью PR #102). Подтверждение
    — перечитывание `get_size`: «среда не приняла запись» остаётся
    примечанием, а не успехом.
    """
    try:
        page.activate()
    except Exception as exc:                                  # noqa: BLE001
        raise ToolError(
            f"габариты не записаны: страницу «{where}» не удалось сделать "
            f"активной ({type(exc).__name__}: {exc}) — запись по id могла бы "
            f"уйти в блок другой страницы. Проект не изменён.") from exc
    outcome = _run_contour_body(_size_fixes_body(plan),
                                failed="записать габариты не удалось")
    _refuse_contour_failure(
        outcome, failed="габариты не записаны",
        unsure="габариты не подтверждены",
        aborted_hint=("Часть правок могла примениться — проверьте габариты "
                      "(`get_block_params` размер не читает; смотрите снимок "
                      "или повторите `fit_port_blocks`)."))
    changed = 0
    for fix in plan:
        word = "ширина" if fix.prop == "Width" else "высота"
        index = 0 if fix.prop == "Width" else 1
        try:
            after = float(fix.block.get_size()[index])
        except Exception:                                     # noqa: BLE001
            lines.append(f"{where}: {fix.name}: {word} записана, но "
                         f"перечитать не удалось — проверьте снимком.")
            changed += 1
            continue
        if after == fix.before:
            lines.append(f"{where}: {fix.name}: {word} не изменилась "
                         f"({_size_value(fix.before)}) — среда не приняла "
                         f"запись.")
            continue
        lines.append(
            f"{where}: {fix.name}: {word} {_size_value(fix.before)} → "
            f"{_size_value(after)} ({fix.note}).")
        changed += 1
    return changed


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


def _fit_page_sizes(page: Page, where: str, affected: List[Any],
                    lines: List[str]) -> int:
    """Поправить габариты одной страницы; вернуть число изменённых блоков.

    Порт-блоки — ширина по подписям (та же оценка, что у
    `check_model_layout`); блоки-субмодели — высота по числу внешних портов.
    Правки собираются в план и применяются **одним** контурным прогоном
    (`_apply_size_plan`): запись идёт в «Значение», а не в «Формулу».
    """
    try:
        blocks = page.get_blocks()
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"{where}: блоки не перечислить "
                     f"({type(exc).__name__}: {exc}).")
        return 0
    plan: List[_SizeFix] = []
    for block in blocks:
        try:
            class_name = str(block.class_name).strip()
        except Exception:                                     # noqa: BLE001
            continue
        if class_name in _PORT_HEIGHT_CLASSES:
            _plan_port_width(block, where, lines, plan)
        elif class_name == "Субмодель":
            _plan_submodel_height(block, where, lines, plan)
    if not plan:
        return 0
    changed = _apply_size_plan(page, where, lines, plan)
    if changed and page not in affected:
        affected.append(page)
    return changed


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def fit_port_blocks() -> str:
    """Подогнать габариты блоков, связанных с портами, по правилам.

    Два правила (оба — из наблюдений владельца и замеров 06.10.2026):

    * **ширина порт-блоков** — по длиннейшей подписи `PortNames`
      (импорт нормализует рамку ~32 px независимо от поданных `points`, и
      длинные имена вылезают). Оценка та же, что у `check_model_layout`
      (`CHAR_WIDTH_ESTIMATE` px/символ): после подгонки его примечание
      «подписи шире рамки» снимается (само примечание — не дефект:
      свисание текста владелец допускает — замечание 07.10.2026);
    * **высота блока-субмодели** — `PORT_ROW_HEIGHT` × число его внешних
      портов (импорт ставит 48×32 независимо от портов). Ширина субмодели
      не трогается.

    **Субмодели обходятся рекурсивно** (`Project.submodel_page`, предел
    `MAX_SUBMODEL_DEPTH`, защита от повторного входа): ширина портов ВНУТРИ
    субмоделей тем же импортом тоже не задаётся, а `check_model_layout`
    смотрит только текущую страницу. Активной в конце снова становится
    главная страница.

    **Запись — в «Значение», а не в «Формулу».** Правки идут языковой парой
    `setpropformula(…)` + `setprop(…)` (та же, что у `set_block_size`):
    COM-путь `SetGraphBlockProp` кладёт число в «Формулу», и диалог свойств
    показывает расхождение. План правок страницы применяется **одним**
    контурным прогоном — контур перезапускает расчёт, и прогон на каждый
    блок стоил бы его N раз; страница перед прогоном активируется.

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
    changed = 0
    try:
        for page, where in pages:
            changed += _fit_page_sizes(page, where, affected, lines)
    finally:
        # Активной возвращается главная — и при отказе посередине обхода:
        # `_apply_size_plan` мог отказать на странице субмодели, оставив её
        # текущей, а следующая контурная операция (выгрузка, снимок) снимает
        # ИМЕННО активную страницу (находка ревью PR #102).
        try:
            main.activate()
        except Exception:                                     # noqa: BLE001
            pass
    if not changed:
        head = (f"Габариты по правилам: менять нечего (оценка "
                f"×{CHAR_WIDTH_ESTIMATE:g} px/символ; страниц обойдено: "
                f"{len(pages)}).")
        result = head + ("\n" + "\n".join(lines) if lines else "")
    else:
        project.repaint()
        for page in affected:
            try:
                for wire in page.get_wires():
                    wire.normalize()
            except Exception as exc:                          # noqa: BLE001
                lines.append(f"линии страницы не трассированы "
                             f"({type(exc).__name__}: {exc}).")
        result = (f"Габариты подогнаны: {changed} (страниц обойдено: "
                  f"{len(pages)}).\n" + "\n".join(lines))
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
        label = _resolve_block(page, label_name)
        parent = _resolve_block(page, parent_name)
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
    """
    project = session.ensure_project()
    main = project.get_main_page()
    lines: List[str] = []
    pages = _walk_pages(project, main, lines)
    moved = 0
    for page, where in pages:
        moved += _fit_value_labels_page(page, where, lines)
    # Активной возвращается главная: обход активировал каждую страницу, а
    # следующая контурная операция (выгрузка) снимает ИМЕННО активную
    # (живой случай 06.10.2026: после fit_port_blocks активной была
    # субмодель, и выгрузка принесла её текст без единой подписи).
    try:
        main.activate()
    except Exception:                                         # noqa: BLE001
        pass
    if not moved:
        head = "Подписи значений на месте."
        return head + ("\n" + "\n".join(lines) if lines else "")
    project.repaint()
    return (f"Подписи значений подтянуты к блокам: {moved} "
            f"(страниц обойдено: {len(pages)}).\n" + "\n".join(lines))


#: Значение параметра блока: скаляр или массив скаляров (стиль SimInTech).
ParamValue = Union[int, float, str, List[Union[int, float, str]]]


def _split_props(text: str) -> List[str]:
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


def _parse_val(text: str) -> Union[int, float, str]:
    text = text.strip()
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        return text


def _coerce_param_value(text: str) -> ParamValue:
    """Разобрать значение параметра блока из строки.

    Массивы в стиле SimInTech ('[1, -1]') → list; иначе — число или строка.
    """
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        body = stripped[1:-1].strip()
        if not body:
            return []
        return [_parse_val(p) for p in body.split(",") if p.strip()]
    return _parse_val(stripped)
