"""Инструменты работы с блоками и связями.

Имена параметров проверяются `catalog._check_params` до вызова COM; разбор
текста свойств (`_split_props`, `_parse_val`, `_coerce_param_value`) — здесь же,
потому что нужен только этим инструментам.
"""

from __future__ import annotations

from typing import List, Union

from fastmcp.exceptions import ToolError
from simintech_api.constants import standard_block_size

from .. import catalog, runtime, session
from ..app import mcp


# ─── Блоки и связи ────────────────────────────────────────────────

def _missing_block(name: str) -> str:
    """Отказ «блока нет на странице» — в одном месте.

    Строка повторялась в четырёх ветках. Расхождение формулировок между ними
    не косметика: `_call_guarded` распознаёт отказ по префиксу `ERROR:`, и
    ветка, потерявшая его, вернула бы отказ как успех.
    """
    return f"ERROR: блок '{name}' не найден на странице"


@mcp.tool()
@runtime._com_threaded
def add_block(class_name: str, name_hint: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "", in_ports: int = 0,
              allow_unknown_props: bool = False) -> str:
    """Добавить блок на главную страницу проекта.

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
            со списком известных, а не молчаливая запись в никуда.
        in_ports: число входных портов (0 — не менять). Нужно для блоков с
            настраиваемым числом входов: у «Сумматора» их по умолчанию два,
            и более длинный `a` сам по себе портов не добавляет.
        allow_unknown_props: True — не сверять имена с каталогом. Нужно, если
            параметр у блока есть, а в каталог не попал (каталог собран не
            для всех классов).
    """
    project = session._ensure_project()
    # Параметры разбираются и проверяются ДО создания блока: иначе отказ
    # оставил бы на схеме блок, которого нет в ответе инструмента.
    pairs = []
    ignored = []
    if props:
        for pair in _split_props(props):
            if "=" in pair:
                k, _, v = pair.partition("=")
                pairs.append((k.strip(), _parse_val(v.strip())))
            else:
                # Молча выбросить нельзя: «a=2 мусор» применил бы `a` и не
                # сказал, что вторая часть потеряна.
                ignored.append(pair)
    notes: List[str] = []
    catalog._check_params(class_name, [name for name, _ in pairs],
                          allow_unknown=allow_unknown_props, notes=notes)

    page = project.get_main_page()
    block = page.create_block(class_name, x, y)
    if name_hint:
        block.set_name(name_hint)
    if in_ports:
        block.set_in_port_count(in_ports)
        # Число входов меняет штатный размер блока: у «Сумматора» 32x32 при
        # двух входах и 32x48 при трёх (замерено по эталонным моделям).
        size = standard_block_size(class_name, in_ports)
        if size:
            block.set_position(x, y, width=size[0], height=size[1])
    for name, value in pairs:
        block.set_property(name, value)

    actual = block.get_name()
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
@runtime._com_threaded
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

    if not props:
        return (f"Блок '{block}' [{class_name}]: параметры неизвестны — "
                f"класс отсутствует в каталоге блоков")
    lines = [f"  {k} = {v}" for k, v in sorted(props.items())]
    return f"Блок '{block}' [{class_name}]:\n" + "\n".join(lines)


@mcp.tool()
@runtime._com_threaded
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
    tail = f" {'; '.join(notes)}" if notes else ""
    return f"{block}.{param} = {value}" + tail


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
