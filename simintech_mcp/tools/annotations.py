"""Подписи страниц и ячейки памяти из снимка `.xprt`.

COM не отдаёт `visual_props`: `get_block_params` читает только имена свойств
из каталога, а подписи (`TextLabel`, `RotatedText`, `constLabel`,
`Комментарий`) в каталог не входят. `export_model_text` пишет текст модели
(`savemodeltofile`) и подписей не содержит. Поэтому подписи и имена ячеек
читаются снимком `SaveProjectXML`: расчёт не запускается, модельное время не
сдвигается.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET
from fastmcp.exceptions import ToolError
from simintech_api.catalog import decode_xprt_value

from .. import runtime, session
from ..app import mcp
from .layout import first_point
from .xprt import snapshot_xprt


#: Классы подписей и записи `visual_props`, где лежит текст. Замер на
#: вендорских `.xprt` (SimInTech64, 24.10.2026): у `TextLabel`, `RotatedText`
#: и `constLabel` текст в `Text`, у `Комментарий` - в `LabelText`.
LABEL_TEXT_PROPS = {
    "TextLabel": ("Text",),
    "RotatedText": ("Text",),
    "constLabel": ("Text",),
    "Комментарий": ("LabelText", "Text"),
}

#: Классы ячеек беспроводной памяти и запись с именем переменной.
MEMORY_CELL_CLASSES = ("В память", "Из памяти")
MEMORY_NAME_PROP = "PortNames"

#: Сколько записей каждого вида показывать: полный счётчик остаётся в ответе.
MAX_REPORTED = 50

#: Предел текста одной подписи; остаток обрезается с пометкой.
MAX_TEXT_CHARS = 500


class Annotation(NamedTuple):
    """Подпись страницы: где, кто, координаты и текст."""

    page: str
    name: str
    class_name: str
    point: Optional[Tuple[float, float]]
    text: str
    error: str


class MemoryCell(NamedTuple):
    """Ячейка памяти: где, имя блока и имя переменной `PortNames`."""

    page: str
    name: str
    class_name: str
    port_names: str
    error: str


def _clean_value(text: Optional[str]) -> str:
    """Снять кавычки и пробелы по краям значения выгрузки."""
    return (text or "").strip().strip("'`").strip()


def _object_name(obj: Element, index: int) -> str:
    """Имя объекта; автоимя - класс или номер, а не пустая строка."""
    name = _clean_value(obj.findtext("name"))
    if name:
        return name
    return _clean_value(obj.findtext("class_name")) or f"object_{index}"


def _object_class(obj: Element) -> str:
    return _clean_value(obj.findtext("class_name"))


def _record_value(obj: Element, prop: str) -> Optional[str]:
    """Сырое значение записи `prop` из свойств объекта.

    Оформление лежит в `visual_props`, но у части классов то же имя может
    оказаться в `custom_props`; читаются обе секции, первая найденная запись
    побеждает. `None` - записи нет (это состояние, а не пустой текст).
    """
    for section_name in ("visual_props", "custom_props"):
        section = obj.find(section_name)
        if section is None:
            continue
        for data in section.iter("data"):
            if _clean_value(data.findtext("name")) != prop:
                continue
            return data.findtext("value")
    return None


def _decode(raw: Optional[str]) -> str:
    """Раскодировать значение выгрузки: кавычки, куски и коды `#NN`."""
    if raw is None:
        return ""
    return decode_xprt_value(raw).rstrip("\r\n")


def _point_of(obj: Element) -> Optional[Tuple[float, float]]:
    """Первая точка `Points` - якорь подписи; `None` - не читается."""
    node = obj.find("points")
    if node is None:
        return None
    return first_point(node.findtext("value") or "")


def _iter_pages(root: Element) -> List[Tuple[str, Element]]:
    """Страницы проекта: главная и вложенные `object/page`.

    `blockimage/page` - иконка блока, а не страница модели: её объекты не
    подписи страницы и в обход не входят. Вложенная страница лежит прямым
    ребёнком объекта-субмодели, поэтому обход идёт только по прямым детям.
    """
    project = root if root.tag == "project" else root.find(".//project")
    if project is None:
        return []
    pages: List[Tuple[str, Element]] = []
    for index, page in enumerate(project.findall("page")):
        label = "Главная" if index == 0 else f"Страница {index + 1}"
        pages.append((label, page))
        _collect_nested_pages(page, label, pages)
    return pages


def _collect_nested_pages(page: Element, path: str,
                          pages: List[Tuple[str, Element]]) -> None:
    for index, obj in enumerate(page.findall("object")):
        child = obj.find("page")
        if child is None:
            continue
        child_path = f"{path} / {_object_name(obj, index)}"
        pages.append((child_path, child))
        _collect_nested_pages(child, child_path, pages)


def _collect(root: Element) -> Tuple[List[Annotation], List[MemoryCell]]:
    """Собрать подписи и ячейки памяти по всем страницам снимка."""
    annotations: List[Annotation] = []
    cells: List[MemoryCell] = []
    for page_path, page in _iter_pages(root):
        for index, obj in enumerate(page.findall("object")):
            class_name = _object_class(obj)
            if class_name in LABEL_TEXT_PROPS:
                name = _object_name(obj, index)
                raw = None
                for prop in LABEL_TEXT_PROPS[class_name]:
                    raw = _record_value(obj, prop)
                    if raw is not None:
                        break
                if raw is None:
                    error = ("нет записи " +
                             "/".join(LABEL_TEXT_PROPS[class_name]))
                    text = ""
                else:
                    try:
                        text = _decode(raw)
                    except Exception as exc:                  # noqa: BLE001
                        text = ""
                        error = (f"текст не разобран "
                                 f"({type(exc).__name__}: {exc})")
                    else:
                        error = ""
                annotations.append(Annotation(
                    page=page_path, name=name, class_name=class_name,
                    point=_point_of(obj), text=text, error=error))
                continue
            if class_name not in MEMORY_CELL_CLASSES:
                continue
            name = _object_name(obj, index)
            raw = _record_value(obj, MEMORY_NAME_PROP)
            if raw is None:
                cells.append(MemoryCell(
                    page=page_path, name=name, class_name=class_name,
                    port_names="", error="нет записи PortNames"))
                continue
            try:
                port_names = " ".join(_decode(raw).split())
            except Exception as exc:                          # noqa: BLE001
                cells.append(MemoryCell(
                    page=page_path, name=name, class_name=class_name,
                    port_names="",
                    error=(f"PortNames не разобран "
                           f"({type(exc).__name__}: {exc})")))
            else:
                cells.append(MemoryCell(
                    page=page_path, name=name, class_name=class_name,
                    port_names=port_names, error=""))
    return annotations, cells


def _clip(text: str) -> Tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS].rstrip() + "…", True


def _annotation_lines(records: List[Annotation], limit: int) -> List[str]:
    """Строки подписей, сгруппированные по странице."""
    groups: Dict[str, List[Annotation]] = {}
    for record in records[:limit]:
        groups.setdefault(record.page, []).append(record)
    lines: List[str] = []
    for page, items in groups.items():
        lines.append(f"  {page}:")
        for item in items:
            head = (f"    {item.name} [{item.class_name}] "
                    f"({_point_text(item.point)}):")
            if item.error:
                lines.append(f"{head} {item.error}")
                continue
            text = item.text
            if not text.strip():
                lines.append(f"{head} (пусто)")
                continue
            clipped, cut = _clip(text)
            lines.append(head)
            lines.extend(f"      {line}" for line in clipped.splitlines())
            if cut:
                lines.append(f"      … текст обрезан до {MAX_TEXT_CHARS} "
                             f"символов")
    if len(records) > limit:
        lines.append(f"  ... и ещё {len(records) - limit} "
                     f"(всего {len(records)}).")
    return lines


def _point_text(point: Optional[Tuple[float, float]]) -> str:
    if point is None:
        return "координаты не читаются"
    return f"{point[0]:g}, {point[1]:g}"


def _cell_lines(records: List[MemoryCell], limit: int) -> List[str]:
    """Строки ячеек памяти, сгруппированные по странице."""
    groups: Dict[str, List[MemoryCell]] = {}
    for record in records[:limit]:
        groups.setdefault(record.page, []).append(record)
    lines: List[str] = []
    for page, items in groups.items():
        lines.append(f"  {page}:")
        for item in items:
            if item.error:
                lines.append(f"    {item.name} [{item.class_name}]: "
                             f"{item.error}")
                continue
            value = item.port_names or "(пусто)"
            lines.append(f"    {item.name} [{item.class_name}] = {value}")
    if len(records) > limit:
        lines.append(f"  ... и ещё {len(records) - limit} "
                     f"(всего {len(records)}).")
    return lines


@mcp.tool(title="Аннотации и ячейки памяти",
          annotations={"readOnlyHint": True})
@runtime.com_threaded
def list_annotations() -> str:
    """Прочитать подписи страниц и имена ячеек памяти из снимка `.xprt`.

    **Зачем.** Комментарии пользователя в `TextLabel`/`Комментарий` и имена
    переменных у «В память»/«Из памяти» COM через `get_block_params` не
    отдаёт: эти классы входят в `NON_BLOCK_CLASSES`, а текст лежит в
    `visual_props`, которого нет в COM-чтении. `export_model_text` подписи не
    выгружает. Снимок `SaveProjectXML` отдаёт их без запуска расчёта и без
    сдвига модельного времени.

    **Что читается.** Замер на вендорских `.xprt` (SimInTech64, 24.10.2026):
    у `TextLabel`, `RotatedText` и `constLabel` текст в `visual_props.Text`,
    у `Комментарий` - в `visual_props.LabelText`, у ячеек памяти имя
    переменной - в `visual_props.PortNames`. Значение разбирается той же
    кодировкой, что `Script`: куски в кавычках и бэктиках плюс коды `#13#10`.

    **Обход.** Главная страница и вложенные `object/page` (субмодели, карты
    состояний); `blockimage/page` - иконка блока, её объекты не подписи
    страницы. Путь страницы собирается из имён объектов: по нему различаются
    одноимённые `TextLabel3` с разных страниц.

    **Пределы.** Показываются первые 50 записей каждого вида и не более 500
    символов одной подписи; полный счётчик и пометка об обрезке остаются в
    ответе. Пустая страница - не ошибка: ответ говорит, что подписей нет.
    Повреждённый снимок (XML не разбирается) - отказ: пустой ответ выдал бы
    повреждённый проект за модель без подписей.
    """
    project = session.ensure_project()
    xml_text = snapshot_xprt(project)
    try:
        root = ET.fromstring(xml_text)
    except (ParseError, DefusedXmlException) as exc:
        raise ToolError(
            f"снимок проекта не разбирается как XML ({exc}): читать подписи "
            f"нечем. Снимок - путь чтения без запуска расчёта, и пустой "
            f"ответ вместо отказа выдал бы повреждённый проект за модель без "
            f"подписей.") from exc

    annotations, cells = _collect(root)
    lines = ["Аннотации модели (снимок SaveProjectXML, расчёт не "
             "запускался).", ""]
    if annotations:
        lines.append(f"Подписи: {len(annotations)}.")
        lines.extend(_annotation_lines(annotations, MAX_REPORTED))
    else:
        lines.append("Подписей на страницах нет.")
    lines.append("")
    if cells:
        lines.append(f"Ячейки памяти (PortNames): {len(cells)}.")
        lines.extend(_cell_lines(cells, MAX_REPORTED))
    else:
        lines.append("Ячеек памяти на страницах нет.")
    return "\n".join(lines).rstrip() + "\n"
