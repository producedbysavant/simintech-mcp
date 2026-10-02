"""Чистая геометрия габаритов: прямоугольники блоков и их пересечения.

Живёт отдельным модулем, а не внутри `tools/layout.py`: этими функциями
пользуются и расстановка (`layout_place` — метрика наложений), и проверка
оформления (`check_model_layout`). Разбор строки `Points` здесь один: формат —
свойство SimInTech, и второй его разбор рано или поздно разошёлся бы с первым.
"""

from __future__ import annotations

import re

#: Точка в строке `Points` — «(x , y)» (пробелы вокруг запятой ставит среда).
_POINT_RE = re.compile(r"\(([-\d.]+)\s*,\s*([-\d.]+)\)")


def rect_of(points_text: str) -> "tuple[float, float, float, float] | None":
    """Габаритный прямоугольник блока из строки свойства `Points`.

    `Points` — полилиния контура в формате `[(x , y), ...]`; для наложения
    достаточно габарита (min/max). `None` — свойство пусто или не разбирается:
    вызывающий обязан назвать такой блок, а не молча счесть его подходящим.
    """
    pairs = _POINT_RE.findall(points_text or "")
    if not pairs:
        return None
    xs = [float(x) for x, _ in pairs]
    ys = [float(y) for _, y in pairs]
    return (min(xs), min(ys), max(xs), max(ys))


def overlaps(rect_a: "tuple[float, float, float, float]",
             rect_b: "tuple[float, float, float, float]") -> bool:
    """Пересекаются ли прямоугольники (строго; касание — не наложение)."""
    return (rect_a[0] < rect_b[2] and rect_b[0] < rect_a[2]
            and rect_a[1] < rect_b[3] and rect_b[1] < rect_a[3])
