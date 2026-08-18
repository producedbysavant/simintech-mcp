"""Дискретная сетка препятствий для трассировки линий.

Координаты схемы SimInTech конвертируются в узлы сетки с шагом GRID_SIZE.
Ячейки, занятые блоками и (опционально) другими линиями, считаются
препятствиями. Трассировка идёт по 4-соседям (ортогонально).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..constants import GRID_SIZE


class ObstacleGrid:
    """Сетка с препятствиями для поиска пути (A*).

    Args:
        width: размер сетки в узлах (по X).
        height: размер сетки в узлах (по Y).
        grid_size: шаг сетки в пикселях схемы.
    """

    def __init__(self, width: int, height: int, grid_size: int = GRID_SIZE):
        self.grid_size = grid_size
        self.width = width
        self.height = height
        # Множество занятых узлов: (gx, gy) -> True
        self._obstacles: Dict[Tuple[int, int], str] = {}

    # ─── Конверсия координат ────────────────────────────────────────

    def to_grid(self, x: float, y: float) -> Tuple[int, int]:
        return (int(round(x / self.grid_size)), int(round(y / self.grid_size)))

    def to_scheme(self, gx: int, gy: int) -> Tuple[float, float]:
        return (gx * self.grid_size, gy * self.grid_size)

    # ─── Препятствия ────────────────────────────────────────────────

    def add_rect(self, x: float, y: float, w: float, h: float,
                 label: str = "block") -> None:
        """Пометить прямоугольник как препятствие (с отступом).

        Отступ (expand) добавляет свободную зону вокруг блока, чтобы
        линии не шли вплотную.
        """
        margin = 1  # 1 узел = GRID_SIZE px вокруг
        gx0, gy0 = self.to_grid(x, y)
        gx1, gy1 = self.to_grid(x + w, y + h)
        for gx in range(gx0 - margin, gx1 + margin + 1):
            for gy in range(gy0 - margin, gy1 + margin + 1):
                self._mark(gx, gy, label)

    def add_point(self, x: float, y: float, label: str = "obstacle") -> None:
        self._mark(*self.to_grid(x, y), label)

    def is_blocked(self, gx: int, gy: int) -> bool:
        if not (0 <= gx < self.width and 0 <= gy < self.height):
            return True  # за пределами сетки считаем занятым
        return (gx, gy) in self._obstacles

    def is_blocked_scheme(self, x: float, y: float) -> bool:
        return self.is_blocked(*self.to_grid(x, y))

    def clear(self) -> None:
        self._obstacles.clear()

    # ─── Внутреннее ─────────────────────────────────────────────────

    def _mark(self, gx: int, gy: int, label: str) -> None:
        if 0 <= gx < self.width and 0 <= gy < self.height:
            self._obstacles[(gx, gy)] = label

    def __repr__(self) -> str:  # pragma: no cover
        return (f"ObstacleGrid({self.width}x{self.height}, "
                f"grid={self.grid_size}, obstacles={len(self._obstacles)})")
