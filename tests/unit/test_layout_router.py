"""Тесты трассировки AStarRouter."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from simintech_api.exceptions import LayoutError
from simintech_api.layout import AStarRouter, ObstacleGrid


def test_straight_line_empty_path():
    """Порты на одной линии без препятствий -> прямое соединение ([])."""
    grid = ObstacleGrid(50, 30)
    router = AStarRouter()
    path = router.route((20, 20), (240, 20), grid, start_side=1, end_side=0)
    assert path == []


def test_route_around_block():
    grid = ObstacleGrid(60, 40)
    grid.add_rect(100, 50, 60, 40)  # блок между портами
    router = AStarRouter()
    path = router.route((20, 20), (250, 20), grid, start_side=1, end_side=0)
    assert len(path) > 0
    # Путь не проходит через блок
    for (x, y) in path:
        inside = 100 - 1 <= x <= 160 + 1 and 50 - 1 <= y <= 90 + 1
        assert not inside, f"Путь проходит через блок: {x},{y}"


def test_route_ends_at_ports():
    """Первый и последний сегменты пути выходят из портов по нормали."""
    grid = ObstacleGrid(60, 40)
    grid.add_rect(100, 50, 60, 40)
    router = AStarRouter()
    path = router.route((20, 20), (250, 20), grid, start_side=1, end_side=0)
    # Вылет справа из порта (20,20): (20 + 2*20, 20) = (60, 20)
    # Вылет слева в порт (250,20): (250 - 2*20, 20) = (210, 20)
    assert path[0] == (60, 20)
    assert path[-1] == (210, 20)


def test_route_impossible_raises():
    """Блокировка всех проходов -> LayoutError."""
    grid = ObstacleGrid(10, 10)
    # Стена во всю высоту сетки (столбец gx=5, gy=0..9). grid_size=20,
    # стена на x=100 px. Порты на x=10 и x=190 px разделены стеной.
    for gy in range(10):
        grid._mark(5, gy, "wall")
    router = AStarRouter()
    with pytest.raises(LayoutError):
        router.route((10, 10), (190, 10), grid)


def test_smooth_collinear():
    """Сглаживание: коллинеарные точки удаляются."""
    grid = ObstacleGrid(50, 30)
    router = AStarRouter()
    # Путь с обходом препятствия — проверяем, что результирующий путь
    # не содержит лишних поворотов
    grid.add_rect(100, 50, 60, 40)
    path = router.route((20, 20), (250, 20), grid, start_side=1, end_side=0)
    for i in range(1, len(path) - 1):
        prev, cur, nxt = path[i - 1], path[i], path[i + 1]
        # Не должно быть трёх коллинеарных подряд
        collinear = (prev[0] == cur[0] == nxt[0]) or (prev[1] == cur[1] == nxt[1])
        assert not collinear, f"Лишняя коллинеарная точка: {cur}"
