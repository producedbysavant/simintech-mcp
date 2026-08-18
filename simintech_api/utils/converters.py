"""Конвертеры типов: Python <-> COM (BSTR/VARIANT), значения свойств."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..constants import DataType
from ..model import TDataDescriptor


def value_to_prop_string(value: Any) -> str:
    """Преобразовать Python-значение в строку для SetBlockProp(StrValue).

    SetBlockProp принимает ВСЕ значения строками (BSTR).
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        # float -> компактная запись без хвостовых нулей
        return str(int(value)) if float(value).is_integer() and not isinstance(value, bool) else repr(float(value))
    if isinstance(value, (list, tuple)):
        return _array_to_str(list(value))
    return str(value)


def _array_to_str(values: List[Any]) -> str:
    """Список значений -> строку в стиле SimInTech: [1,2,3]."""
    inner = ", ".join(str(v) for v in values)
    return f"[{inner}]"


def _to_descriptor(value: Any) -> TDataDescriptor:
    """Восстановить TDataDescriptor из результата comtypes-вызова.

    comtypes может возвращать структуру напрямую (объект TDataDescriptor),
    кортеж (DataId, DataType) или объект с полями .DataId/.DataType.
    """
    if isinstance(value, TDataDescriptor):
        return value
    if value is None:
        return TDataDescriptor()
    if isinstance(value, (tuple, list)):
        data_id = value[0] if len(value) > 0 else 0
        data_type = value[1] if len(value) > 1 else 0
        return TDataDescriptor(int(data_id), int(data_type))
    data_id = getattr(value, "DataId", getattr(value, "data_id", 0))
    data_type = getattr(value, "DataType", getattr(value, "data_type", 0))
    return TDataDescriptor(int(data_id), int(data_type))


def parse_points(points_str: Optional[str]) -> List[Tuple[float, float]]:
    """Разобрать строку свойства Points в список пар (x, y).

    Формат SimInTech: `[(128,72),(144,72),(128,64),(128,84)]`.
    Первая точка — центр блока.
    """
    if not points_str:
        return []
    text = points_str.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return []
    body = text[1:-1]
    if not body.strip():
        return []
    result: List[Tuple[float, float]] = []
    for item in body.split("),("):
        item = item.strip().strip("(").strip(")")
        parts = item.split(",")
        if len(parts) >= 2:
            try:
                result.append((float(parts[0].strip()), float(parts[1].strip())))
            except ValueError:
                continue
    return result


def block_center(points: Optional[str]) -> Tuple[float, float]:
    """Центр блока (первая точка Points); если нет — (0, 0)."""
    pts = parse_points(points)
    return (pts[0] if pts else (0.0, 0.0))


def block_size(points: Optional[str],
               default_w: float = 60.0,
               default_h: float = 40.0) -> Tuple[float, float]:
    """Ширина/высота блока по Points.

    По соглашению SimInTech: вторая точка — центр+половина ширины,
    третья — центр−половина высоты. Если данных нет — дефолт.
    """
    pts = parse_points(points)
    if len(pts) < 3:
        return default_w, default_h
    center = pts[0]
    right = pts[1]
    bottom = pts[2]
    width = 2.0 * abs(right[0] - center[0])
    height = 2.0 * abs(bottom[1] - center[1])
    if width <= 0:
        width = default_w
    if height <= 0:
        height = default_h
    return width, height


def descriptor_payload(desc: TDataDescriptor) -> TDataDescriptor:
    """Подготовить дескриптор к передаче по значению в Read*/Write*.

    В comtypes структуру можно передавать как есть; эта функция
    страховка на случай, если вызывающий передал кортеж/словарь.
    """
    if isinstance(desc, TDataDescriptor):
        return desc
    if isinstance(desc, (tuple, list)) and len(desc) >= 2:
        return TDataDescriptor(int(desc[0]), int(desc[1]))
    raise TypeError(
        f"Ожидался TDataDescriptor, получено {type(desc).__name__}"
    )
