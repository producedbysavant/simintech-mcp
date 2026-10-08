"""Инструменты работы с блоками: создание, осмотр, параметры.

Ядро блочных инструментов: `add_block`, `list_blocks`,
`get_block_params`, `set_block_param`, адресация блока по имени/id
(`resolve_block`) и разбор значений свойств. Связи — `wires.py`,
скрипт блока — `block_script.py`, габариты — `sizes.py` и `fits.py`
(рефакторинг issue #121 из монолита).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastmcp.exceptions import ToolError
from simintech_api import Block, Page
from simintech_api.constants import standard_block_size

from .. import catalog, runtime, session
from ..app import mcp


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


def missing_block(name: str) -> str:
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
    создаёт и свойства читаются). Импортом текста «Порт выхода» создаётся —
    сразу с `portnames` (замер 03.10.2026) — и в расчёте **годен**:
    изолированные замеры 08.10.2026 (2.26.9.29) — «Порт выхода» с
    подключённым входом модель считает (положительный замер вместо прежней
    непроверенности). Создать класс по-прежнему можно только импортом:
    отказ `add_block` — политика allowlist библиотеки, не признак
    негодности. «Из памяти» снят с запрета 05.10.2026 — годность в расчёте
    подтверждена живым замером (пара `ToMem_0`/`FromMem_0` с `PortNames`
    считает, значение ходит через неё). Но «как обычный класс» он создать
    не даётся: **прямой `CreateBlock` отдаёт его без портов** (замер
    simintech-code, `topology.py`: «создаётся, но напрямую — без портов») —
    порт настраивается `PortNames` + `initobject` (так и был поставлен
    замер годности). Обычные классы приходят со штатными портами.

    **Порт-блоки расчёт не останавливают.** Висячий «Порт входа» (и с
    `portnames`, и без) расчёт НЕ блокирует — в отличие от неподключённого
    входа обычного блока, который молча останавливает расчёт всей модели;
    флаг `block_can_be_stub` на расчёт не влияет (изолированные замеры
    08.10.2026, 2.26.9.29: 26 висячих портов «Порт входа» с флагом —
    по-прежнему «идёт»). То есть интерфейсный задел можно оставить висячим.
    «Неподключённый вход останавливает расчёт» — правило для блоков
    расчёта, не для порт-блоков.

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


def block_by_id(page: Page, block_id: int) -> Optional[Block]:
    """Блок страницы с этим числовым id — или None.

    Адресация по id нужна там, где имя неоднозначно: пара «В память» /
    «Из памяти» делит одно имя ячейки, и поиск по имени вернул бы одну
    половину вместо нужной. Единая точка для `resolve_block`, подтверждения
    удаления (`wires._page_has_block_id`) и подписи источника в отказах
    (`wires._block_caption`) — три копии скана расходились бы при первой
    правке защиты (находка ревью PR #122). Провал чтения `block.id` на
    одном блоке (сбой COM) его пропускает; сбой перечисления страницы —
    наружу, вызывающие решают сами.
    """
    for block in page.get_blocks():
        try:
            if block.id == block_id:
                return block
        except Exception:                                     # noqa: BLE001
            continue
    return None


def resolve_block(page: Page, token: str) -> Optional[Block]:
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
        return block_by_id(page, int(token))
    return None


def resolved_name(block: Block, fallback: str) -> str:
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
    target = resolve_block(page, block)
    if target is None:
        return missing_block(block)
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
    target = resolve_block(page, block)
    if target is None:
        return missing_block(block)
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
