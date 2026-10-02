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


def rect_of(points_text: str,
            size: "tuple[float, float]") -> "tuple[float, float, float, float] | None":
    """Габарит блока: центр из `Points` ± половина размера.

    Живой замер 02.10.2026: `Points` — **не контур блока**, и min/max его
    точек габаритом не является («Константа» 32×16 даёт полилинию 16×32).
    Первая точка полилинии — центр блока (совпадает с `set_center` до
    десятых, а при создании — с «левый верхний + половина размера»), вторая —
    выходной порт (центр + (16, 0)). Поэтому габарит строится из центра и
    `get_size`, а не из размаха точек. `None` — свойство пусто/не разбирается
    или размер недоступен: вызывающий обязан назвать такой блок.
    """
    pairs = _POINT_RE.findall(points_text or "")
    if not pairs:
        return None
    cx, cy = float(pairs[0][0]), float(pairs[0][1])
    w, h = size
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def overlaps(rect_a: "tuple[float, float, float, float]",
             rect_b: "tuple[float, float, float, float]") -> bool:
    """Пересекаются ли прямоугольники (строго; касание — не наложение)."""
    return (rect_a[0] < rect_b[2] and rect_b[0] < rect_a[2]
            and rect_a[1] < rect_b[3] and rect_b[1] < rect_a[3])
