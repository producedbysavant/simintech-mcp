"""Инструменты работы с блоками и связями.

Имена параметров проверяются `catalog._check_params` до вызова COM; разбор
текста свойств (`_split_props`, `_parse_val`, `_coerce_param_value`) — здесь же,
потому что нужен только этим инструментам.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastmcp.exceptions import ToolError
from simintech_api.constants import standard_block_size

from .. import catalog, runtime, session
from ..app import mcp


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
@runtime._com_threaded(mutates_project=True)
def add_block(class_name: str, name_hint: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "", in_ports: int = 0,
              allow_unknown_props: bool = False) -> str:
    """Добавить блок на главную страницу проекта.

    **Существующие блоки не двигаются**: инструмент только добавляет объект.
    Уже расставленные блоки и ручная раскладка переживают повторные правки —
    блоки двигает только `layout_place`, и то по явному вызову (контракт
    #24 п.6).

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
            сам по себе портов не добавляет.
        allow_unknown_props: True — не сверять имена с каталогом. Нужно, если
            параметр у блока есть, а в каталог не попал (каталог собран не
            для всех классов).
    """
    project = session._ensure_project()
    # Параметры разбираются и проверяются ДО создания блока: иначе отказ
    # оставил бы на схеме блок, которого нет в ответе инструмента.
    pairs: Dict[str, ParamValue] = {}
    ignored = []
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
    catalog._check_params(class_name, list(pairs),
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


@mcp.tool()
@runtime._com_threaded(mutates_project=True)
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
        src: имя/алиас блока-источника.
        dst: имя/алиас блока-приёмника.
        out_index: номер выходного порта источника (0-based).
        in_index: номер входного порта приёмника (0-based).
    """
    page = session._ensure_project().get_main_page()
    b1 = page.find_block(src)
    b2 = page.find_block(dst)
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
        # молча возвращает 0). Отказ приходит до записи в `_WIRES`.
        raise ToolError(
            f"Линия {src}[{out_index}] -> {dst}[{in_index}] не создана: "
            f"среда вернула id=0 (такое бывает, если порты принадлежат разным "
            f"страницам/слоям). Связь не запомнена — проверьте порты и "
            f"повторите `connect`."
        )
    # Храним и концы связи: по ним `layout_place` выравнивает блоки так, чтобы
    # линия шла без лишнего излома.
    session._WIRES.append((wire, src, out_index, dst, in_index))
    return f"Соединено {src} -> {dst} (wire={wire.id})"


@mcp.tool()
@runtime._com_threaded
def list_blocks() -> str:
    """Вывести список блоков текущей страницы проекта."""
    blocks = session._ensure_project().get_main_page().get_blocks()
    if not blocks:
        return "Блоков на странице нет"
    lines = []
    for b in blocks[:50]:
        try:
            nm = b.get_name()
        except Exception:
            nm = ""
        lines.append(f"  {nm or '(без имени)'} [{b.class_name}] (id={b.id})")
    more = f"\n  ... и ещё {len(blocks) - 50}" if len(blocks) > 50 else ""
    return "Блоки:\n" + "\n".join(lines) + more


@mcp.tool()
@runtime._com_threaded
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
    page = session._ensure_project().get_main_page()
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
@runtime._com_threaded
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
            `list_blocks`); переименование через COM недоступно.
    """
    page = session._ensure_project().get_main_page()
    target = page.find_block(block)
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
@runtime._com_threaded(mutates_project=True)
def set_block_param(block: str, param: str, value: str,
                    allow_unknown: bool = False) -> str:
    """Установить параметр блока и переинициализировать блок.

    Блок переинициализируется (`InitBlock`) — без этого изменение может не
    дойти до расчёта: карта COM API отмечает, что `SetBlockProp` не влияет
    на уже инициализированные блоки (например, «Константа»).

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
        block: имя блока на главной странице (автоимя из `list_blocks`).
        param: имя параметра блока (см. `get_block_params`).
        value: значение строкой; массивы — в стиле SimInTech, напр. '[1, -1]'.
        allow_unknown: True — не сверять имя с каталогом (для параметров,
            которых в каталоге нет).
    """
    page = session._ensure_project().get_main_page()
    target = page.find_block(block)
    if target is None:
        return _missing_block(block)
    notes: List[str] = []
    try:
        catalog._check_params(target.class_name, [param],
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


def _size_text(sizes) -> str:
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


def _port_signal_count(target) -> Optional[int]:
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


def _port_required_height(name: str, target) -> Optional[int]:
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


@mcp.tool()
@runtime._com_threaded(mutates_project=True)
def set_block_size(block: str, width: float, height: float) -> str:
    """Задать размер блока в пикселях схемы.

    Размер — **графическое** свойство: пишется через `SetGraphBlockProp`
    (`Width`/`Height`), а обычный `SetBlockProp` (и `set_block_param`) эти
    имена молча игнорирует — в каталоге параметров их поэтому нет. Живой
    замер 01.10.2026 (поставка 2.26.6.23): «Усилитель» 32×32 → 140×80; у
    «Порта входа» тем же методом гналась и высота (64×16 → 360×120) — ровно
    тот случай, который теперь отвергается правилом ниже: у однозначного
    порта высота 16, и 120 ломает строки. Нечётные и дробные значения
    принимаются (141×79 применились и удержались) — сетки 2 px у этого пути
    нет, а прежнее «среда нечётные не принимает» относилось к записи через
    контур страницы (`setprop` в скрипте), у которой своя судьба значения.

    Заданный размер **переживает инициализацию**: после `ProjectStart`
    прочитанные габариты не изменились (замер там же) — заново среда блок не
    ужимает. После записи схема перерисовывается, и линии страницы
    перетрассировуются (`NormalizeWire`): координаты портов после смены
    размера сместились, как при перемещении блока.

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
        block: имя блока (автоимя из `list_blocks`).
        width: ширина в пикселях (> 0, не больше `MAX_BLOCK_SIZE`).
        height: высота в пикселях (> 0, не больше `MAX_BLOCK_SIZE`).
    """
    if not 0 < width <= MAX_BLOCK_SIZE:
        raise ToolError(
            f"Ширина {_size_value(width)} вне пределов: ширина — в пикселях, "
            f"больше 0 и не больше {MAX_BLOCK_SIZE} (отсечка ошибок единиц "
            f"измерения: настоящий размер блока — десятки-сотни пикселей).")
    project = session._ensure_project()
    page = project.get_main_page()
    target = page.find_block(block)
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
    try:
        target.set_graph_prop("Width", _size_value(width))
        target.set_graph_prop("Height", _size_value(height))
    except Exception as exc:                                  # noqa: BLE001
        return f"ERROR: {exc}"
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
