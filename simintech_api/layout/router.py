"""Трассировка линий связи (A* на ортогональной сетке).

Вход: координаты портов-источника и приёмника + сетка препятствий.
Выход: список опорных точек (x, y) в координатах схемы для SetWirePoint.

Особенности:
- движение только по 4 направлениям (ортогонально);
- штраф за повороты — линии более прямые;
- если старт и цель на одной линии и путь свободен — возвращает [].
"""

from __future__ import annotations

import heapq
from typing import List, Optional, Tuple

from ..constants import GRID_SIZE, PORT_STUB
from ..exceptions import LayoutError
from .grid import ObstacleGrid


class AStarRouter:
    """Поиск ортогонального пути между портами.

    Args:
        grid_size: шаг сетки.
        turn_penalty: дополнительная стоимость за поворот (чем больше — тем
            более «прямые» линии).
    """

    def __init__(self, grid_size: int = GRID_SIZE, turn_penalty: float = 2.0):
        self.grid_size = grid_size
        self.turn_penalty = turn_penalty

    def route(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        grid: ObstacleGrid,
        *,
        start_side: int = 1,   # 0=left,1=right,2=top,3=bottom (PortSide)
        end_side: int = 0,
    ) -> List[Tuple[float, float]]:
        """Проложить путь от start до end.

        Args:
            start, end: координаты портов на схеме (x, y).
            grid: сетка препятствий.
            start_side / end_side: стороны портов (влияют на первый/последний
                участок, чтобы линия выходила перпендикулярно порту).

        Returns:
            Список опорных точек в координатах схемы (может быть пустым —
            прямое соединение).

        Raises:
            LayoutError: путь не найден.
        """
        # Короткий «вылет» из порта по нормали — чтобы линия не начиналась
        # внутри блока и была перпендикулярна стороне.
        s = _stub_from_port(start, start_side, self.grid_size)
        e = _stub_from_port(end, end_side, self.grid_size)

        # Если старт и цель визуально рядом — прямое соединение без узлов
        if abs(s[0] - e[0]) <= self.grid_size and abs(s[1] - e[1]) <= self.grid_size:
            return []

        sg = grid.to_grid(*s)
        eg = grid.to_grid(*e)

        if sg == eg:
            return []

        # Если прямая между вылетами свободна — ортогональное соединение
        # без опорных точек (NormalizeWire сделает линию прямой).
        if _straight_free(grid, sg, eg):
            return []

        path = self._astar(grid, sg, eg)
        if not path:
            raise LayoutError(
                f"Не удалось проложить путь {start} -> {end} "
                f"(сетка {grid.width}x{grid.height})"
            )

        # Возврат в координаты схемы + сглаживание коллинеарных точек
        points = [grid.to_scheme(gx, gy) for (gx, gy) in path]
        # Первая и последняя точки должны быть реальными портами
        if points:
            points[0] = s
            points[-1] = e
        return _smooth(points)

    # ─── Внутреннее ─────────────────────────────────────────────────

    def _astar(
        self,
        grid: ObstacleGrid,
        start: Tuple[int, int],
        end: Tuple[int, int],
    ) -> Optional[List[Tuple[int, int]]]:
        """A* на сетке; возвращает список узлов (gx, gy) включая концы."""
        open_set: List[Tuple[float, int, int, Tuple[int, int]]] = []
        heapq.heappush(open_set, (0.0, start[0], start[1], start))
        came_from: dict = {start: None}
        g_score: dict = {start: 0.0}
        # Направление прихода для штрафа за повороты
        came_dir: dict = {start: None}

        def h(a: Tuple[int, int], b: Tuple[int, int]) -> float:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        while open_set:
            _, _, _, current = heapq.heappop(open_set)
            if current == end:
                return _reconstruct(came_from, end)

            cx, cy = current
            dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            for dx, dy in dirs:
                nxt = (cx + dx, cy + dy)
                if grid.is_blocked(*nxt):
                    continue
                move_cost = 1.0
                prev_dir = came_dir.get(current)
                if prev_dir is not None and prev_dir != (dx, dy):
                    move_cost += self.turn_penalty
                tentative = g_score[current] + move_cost
                if tentative < g_score.get(nxt, float("inf")):
                    came_from[nxt] = current
                    came_dir[nxt] = (dx, dy)
                    g_score[nxt] = tentative
                    f = tentative + h(nxt, end)
                    heapq.heappush(open_set, (f, nxt[0], nxt[1], nxt))
        return None


def _straight_free(grid: ObstacleGrid, a: Tuple[int, int],
                   b: Tuple[int, int]) -> bool:
    """True, если ортогональная прямая a→b не пересекает препятствий."""
    if a[0] == b[0]:
        y0, y1 = sorted((a[1], b[1]))
        for gy in range(y0, y1 + 1):
            if grid.is_blocked(a[0], gy):
                return False
        return True
    if a[1] == b[1]:
        x0, x1 = sorted((a[0], b[0]))
        for gx in range(x0, x1 + 1):
            if grid.is_blocked(gx, a[1]):
                return False
        return True
    return False


def _reconstruct(came_from: dict, end: Tuple[int, int]) -> List[Tuple[int, int]]:
    path = [end]
    node = end
    while came_from.get(node) is not None:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return path


def _smooth(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Удалить промежуточные точки, лежащие на одной прямой."""
    if len(points) <= 2:
        return points
    result = [points[0]]
    for i in range(1, len(points) - 1):
        prev = points[i - 1]
        cur = points[i]
        nxt = points[i + 1]
        # Коллинеарны (горизонталь/вертикаль)
        collinear = (
            (prev[0] == cur[0] == nxt[0]) or
            (prev[1] == cur[1] == nxt[1])
        )
        if not collinear:
            result.append(cur)
    result.append(points[-1])
    return result


def _stub_from_port(port: Tuple[float, float], side: int,
                    grid_size: int) -> Tuple[float, float]:
    """Точка «вылета» из порта по нормали к стороне (PORT_STUB, кратно сетке)."""
    stub = max(PORT_STUB, grid_size)
    x, y = port
    if side == 1:      # right
        return (x + stub, y)
    if side == 0:      # left
        return (x - stub, y)
    if side == 3:      # bottom
        return (x, y + stub)
    return (x, y - stub)  # top
