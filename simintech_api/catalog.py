"""Каталог имён свойств блоков SimInTech.

В COM API **нет метода перечисления свойств блока** — ни `GetPropCount`, ни
`GetPropName`. Существуют только `GetBlockPropAsString(BlockId, PropName)` и
`SetBlockProp(BlockId, PropName, StrValue)`, которым имя нужно знать заранее.
Поэтому имена свойств хранятся отдельно — в этом каталоге.

Два источника каталога:

* ``generate_catalog()`` — создать по блоку каждого класса в реальном SimInTech,
  экспортировать проект в ``.xprt`` и вычитать фактические имена свойств
  (только Windows). Это достоверный источник.
* ``data/block_catalog.json`` — засеянный каталог, собранный из рабочего кода
  примеров. Может быть неполным.

**Имена свойств короткие** — ``a``, ``y0``, ``xn``, а не читаемые ``value``,
``signs``, ``numInputs``. Документация в ``docs/simintech-language/blocks/``
использует читаемые имена, которые с реальными **не совпадают**; опираться на
неё как на источник имён нельзя.

**Почему ошибка в каталоге опасна.** `SetBlockProp` не отвергает неизвестное
имя свойства: параметр «устанавливается», ошибки не возникает, а расчёт идёт
по прежнему значению. Отказ молчаливый — модель считается неверно, и это
не видно по коду возврата. Поэтому каталог следует генерировать, а не
дописывать вручную.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from .constants import SUPPORTED_COM_BLOCK_CLASSES
from .exceptions import SimInTechError

if TYPE_CHECKING:
    from .core.com_client import COMClient

# ─── Расположение каталога ────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_CATALOG_PATH = DATA_DIR / "block_catalog.json"

# Свойства, присутствующие у любого блока
COMMON_PROPS = ("Name",)

CATALOG_VERSION = 1


# ─── Каталог ──────────────────────────────────────────────────────

class BlockCatalog:
    """Соответствие «класс блока → имена его свойств».

    Args:
        classes: {класс: {свойство: значение по умолчанию}}.
        common: свойства, общие для всех классов.
        meta: произвольные метаданные (источник, дата, версия).
    """

    def __init__(self, classes: Optional[Dict[str, Dict[str, str]]] = None,
                 common: Iterable[str] = COMMON_PROPS,
                 meta: Optional[Dict[str, object]] = None,
                 readonly: Optional[Dict[str, Iterable[str]]] = None):
        self._classes: Dict[str, Dict[str, str]] = dict(classes or {})
        self._common: tuple = tuple(common)
        self._readonly: Dict[str, List[str]] = {
            cls: list(props) for cls, props in (readonly or {}).items()
        }
        self.meta: Dict[str, object] = dict(meta or {})

    # ─── Загрузка / сохранение ──────────────────────────────────────

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "BlockCatalog":
        """Загрузить каталог из JSON. Отсутствующий файл → пустой каталог."""
        target = Path(path) if path else DEFAULT_CATALOG_PATH
        if not target.exists():
            return cls(meta={"source": "empty", "path": str(target)})
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SimInTechError(
                f"Не удалось прочитать каталог блоков {target}: {exc}") from exc
        return cls(
            classes=raw.get("classes", {}),
            common=raw.get("common", COMMON_PROPS),
            meta=raw.get("meta", {}),
            readonly=raw.get("readonly", {}),
        )

    def save(self, path: Optional[Path] = None) -> Path:
        """Сохранить каталог в JSON и вернуть путь."""
        target = Path(path) if path else DEFAULT_CATALOG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def as_dict(self) -> Dict[str, object]:
        """Представление для сериализации."""
        return {
            "version": CATALOG_VERSION,
            "common": list(self._common),
            "classes": self._classes,
            "readonly": self._readonly,
            "meta": self.meta,
        }

    # ─── Доступ ─────────────────────────────────────────────────────

    def classes(self) -> List[str]:
        """Классы, для которых известны свойства."""
        return sorted(self._classes)

    def has(self, class_name: str) -> bool:
        """Известен ли класс."""
        return class_name in self._classes

    def props_for(self, class_name: str) -> List[str]:
        """Известные имена свойств класса (общие + специфичные).

        Для неизвестного класса — только общие свойства. Дубликаты
        устраняются: каталог может явно перечислять и общее свойство.
        """
        specific = self._classes.get(class_name) or {}
        seen = set()
        result: List[str] = []
        for prop in (*self._common, *specific):
            if prop not in seen:
                seen.add(prop)
                result.append(prop)
        return result

    def defaults_for(self, class_name: str) -> Dict[str, str]:
        """Значения по умолчанию для свойств класса."""
        return dict(self._classes.get(class_name, {}))

    def readonly_for(self, class_name: str) -> List[str]:
        """Вычисляемые параметры класса — читать можно, задавать нельзя."""
        return list(self._readonly.get(class_name, ()))

    def is_readonly(self, class_name: str, prop: str) -> bool:
        """True, если параметр вычисляемый (запись в него ни на что не влияет)."""
        return prop in self._readonly.get(class_name, ())

    def __len__(self) -> int:
        return len(self._classes)

    def __repr__(self) -> str:  # pragma: no cover
        return f"BlockCatalog(classes={len(self._classes)})"


# ─── Разбор .xprt ─────────────────────────────────────────────────

# Значения в .xprt обёрнуты в бэктики: <name>`Значение`</name>
_OBJECT_RE = re.compile(r"<object>(.*?)</object>", re.S | re.I)
_CLASS_RE = re.compile(r"<class_name>(.*?)</class_name>", re.S | re.I)
_CUSTOM_RE = re.compile(r"<custom_props>(.*?)</custom_props>", re.S | re.I)
_DATA_RE = re.compile(r"<data>(.*?)</data>", re.S | re.I)
_NAME_RE = re.compile(r"<name>\s*`?([^`<]*)`?\s*</name>", re.I)
_VALUE_RE = re.compile(r"<value>\s*`?([^`<]*)`?\s*</value>", re.I)
_MODE_RE = re.compile(r"<mode>\s*`?(\d+)`?\s*</mode>", re.I)

# Режим параметра (mode) в <custom_props>: 1 — задаваемый, 0 — вычисляемый.
MODE_EDITABLE = 1
MODE_COMPUTED = 0

# Классы-оформление: не блоки, свойств-параметров не несут

# Классы-оформление: не блоки, свойств-параметров не несут
NON_BLOCK_CLASSES = {
    "Wire", "Arc", "Button", "FillCircle", "FillEllipseSector", "FillRect",
    "Line", "Point", "PolyLine", "PolyRound", "Polygon", "Rectangle",
    "RotatedText", "TextLabel", "constLabel", "Комментарий",
}


def clean_value(text: Optional[str]) -> str:
    """Убрать бэктики и пробелы по краям значения из .xprt."""
    if not text:
        return ""
    return text.strip().strip("`").strip()


def _iter_custom_props(obj: str):
    """Пройти по параметрам расчёта блока (секция ``<custom_props>``).

    Возвращает ``(имя, значение, mode)`` для каждой записи.

    Параметры блока лежат именно в ``<custom_props>``. Секция
    ``<visual_props>`` содержит **оформление** (``Color``, ``Points``,
    ``LabelFont``, ...) — параметров расчёта там нет. Проверено на реальном
    ``.xprt`` (SimInTech64, 2026-09-10).
    """
    custom_match = _CUSTOM_RE.search(obj)
    if not custom_match:
        return
    for data in _DATA_RE.findall(custom_match.group(1)):
        name_match = _NAME_RE.search(data)
        if not name_match:
            continue
        prop = clean_value(name_match.group(1))
        if not prop:
            continue
        value_match = _VALUE_RE.search(data)
        value = clean_value(value_match.group(1)) if value_match else ""
        mode_match = _MODE_RE.search(data)
        mode = int(mode_match.group(1)) if mode_match else None
        yield prop, value, mode


def parse_xprt_block_props(xml_text: str) -> Dict[str, Dict[str, str]]:
    """Извлечь ``{класс: {параметр: значение}}`` из XML-проекта (.xprt).

    Возвращаются **только параметры расчёта** — из секции ``<custom_props>``.
    Оформление (``<visual_props>``: ``Color``, ``Points``, ``LabelFont``, ...)
    в каталог не попадает.

    Блоки без ``<custom_props>`` пропускаются — параметров у них нет
    (так выглядят графические объекты: ``Line``, ``PolyLine``, ``Arc``, ...).
    """
    result: Dict[str, Dict[str, str]] = {}

    for obj in _OBJECT_RE.findall(xml_text):
        cls_match = _CLASS_RE.search(obj)
        if not cls_match:
            continue
        class_name = clean_value(cls_match.group(1))
        if not class_name or class_name in NON_BLOCK_CLASSES:
            continue
        if not _CUSTOM_RE.search(obj):
            continue

        props = result.setdefault(class_name, {})
        for prop, value, _mode in _iter_custom_props(obj):
            props.setdefault(prop, value)

    return result


def parse_xprt_readonly(xml_text: str) -> Dict[str, List[str]]:
    """Извлечь ``{класс: [вычисляемые параметры]}`` из XML-проекта.

    Вычисляемые параметры (``mode 0``: например ``xdif``, ``fdif`` у
    «Интегратора») читаются, но не задаются. Их важно знать: запись в такой
    параметр не даёт ошибки и ни на что не влияет — отказ молчаливый.
    """
    result: Dict[str, List[str]] = {}

    for obj in _OBJECT_RE.findall(xml_text):
        cls_match = _CLASS_RE.search(obj)
        if not cls_match:
            continue
        class_name = clean_value(cls_match.group(1))
        if not class_name or class_name in NON_BLOCK_CLASSES:
            continue
        if not _CUSTOM_RE.search(obj):
            continue

        readonly = result.setdefault(class_name, [])
        for prop, _value, mode in _iter_custom_props(obj):
            if mode == MODE_COMPUTED and prop not in readonly:
                readonly.append(prop)

    return result


# ─── Генерация из реального SimInTech ─────────────────────────────

def decode_xprt(raw: bytes) -> str:
    """Декодировать байты .xprt в текст.

    SimInTech (проверено на 2026-09-10, SimInTech64) пишет `.xprt` в
    **UTF-8 с BOM**, а не в cp1251. Чтение как cp1251 превращает русские имена
    классов в мусор — молча, без ошибки, потому что cp1251 декодирует любые
    байты. Поэтому сначала пробуем UTF-8, а cp1251 оставляем запасным
    вариантом для старых версий.
    """
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def export_xprt_text(project, suffix: str = ".xprt") -> str:
    """Экспортировать проект в XML и вернуть текст."""
    tmp = Path(tempfile.gettempdir()) / f"siminapi_catalog_{project.id}{suffix}"
    try:
        project.save_xml(str(tmp))
        return decode_xprt(tmp.read_bytes())
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def generate_catalog(client: "COMClient",
                     classes: Optional[Iterable[str]] = None,
                     keep_project: bool = False) -> BlockCatalog:
    """Построить каталог по реальному SimInTech (только Windows).

    Создаёт по одному блоку каждого класса в новом проекте, экспортирует
    проект в ``.xprt`` и вычитывает фактические имена свойств. Классы, которые
    не удалось создать, пропускаются — их отсутствие видно в ``meta["failed"]``.

    Args:
        client: подключённый COM-клиент.
        classes: классы для обхода (по умолчанию — SUPPORTED_COM_BLOCK_CLASSES).
        keep_project: не закрывать проект (для отладки).
    """
    from .core.project import Project

    targets = list(classes or sorted(SUPPORTED_COM_BLOCK_CLASSES))
    project = Project.new(client)
    failed: List[str] = []
    try:
        page = project.get_main_page()
        for index, class_name in enumerate(targets):
            try:
                block = page.create_block(class_name, 0.0, float(index * 60))
                block.set_name(f"catalog_{index}")
            except Exception:
                failed.append(class_name)
        xml_text = export_xprt_text(project)
    finally:
        if not keep_project:
            try:
                project.close()
            except Exception:
                pass

    wanted = set(targets)
    parsed = parse_xprt_block_props(xml_text)
    readonly = parse_xprt_readonly(xml_text)
    # Оставляем только запрошенные классы — в XML попадает и оформление
    classes_map = {k: v for k, v in parsed.items() if k in wanted}
    readonly_map = {k: v for k, v in readonly.items() if k in wanted}
    return BlockCatalog(
        classes=classes_map,
        readonly=readonly_map,
        meta={
            "source": "generated",
            "requested": targets,
            "failed": failed,
        },
    )


# ─── Кэш каталога по умолчанию ────────────────────────────────────

_default_cache: Optional[BlockCatalog] = None


def load_default_catalog(reload: bool = False) -> BlockCatalog:
    """Каталог по умолчанию (кэшируется в памяти)."""
    global _default_cache
    if _default_cache is None or reload:
        _default_cache = BlockCatalog.load()
    return _default_cache
