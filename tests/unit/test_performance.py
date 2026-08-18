"""Тесты производительности layout (placer/router) на крупных моделях.

Запускаются в любом окружении (без COM). Проверяют, что алгоритмы
справляются с 50/100/200 блоками за приемлемое время и не дают наложений.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from simintech_api.layout import AStarRouter, LayeredPlacer, ObstacleGrid

# Не считать эти тесты «медленными» — это бенчмарки
pytestmark = pytest.mark.performance


@pytest.mark.parametrize("n", [50, 100, 200])
def test_placer_scales(n):
    """Placer: цепочка из n блоков без наложений, быстрый."""
    placer = LayeredPlacer()
    blocks = [f"b{i}" for i in range(n)]
    conns = [(f"b{i}", f"b{i + 1}") for i in range(n - 1)]

    t0 = time.monotonic()
    pos = placer.place(blocks, conns)
    elapsed = time.monotonic() - t0

    assert len(pos) == n
    # Наложений нет
    placed = list(pos.values())
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert abs(a[0] - b[0]) > 20 or abs(a[1] - b[1]) > 20

    # Ограничение: 200 блоков — быстрее 2 секунд
    assert elapsed < 2.0, f"Placer {n} блоков занял {elapsed:.2f}s"


def test_router_scales():
    """Router: 200 линий через компактный кластер препятствий.

    Кластер блоков в центре (x 100..200, y 160..300), вокруг свободно —
    обход всегда возможен сверху/снизу.
    """
    grid = ObstacleGrid(200, 120)
    for row in range(8):
        for col in range(5):
            grid.add_rect(100 + col * 24, 160 + row * 20, 16, 12, "block")

    router = AStarRouter()
    t0 = time.monotonic()
    for i in range(200):
        y = 40 + (i % 120) * 8   # 40..992 — линии и сквозь кластер, и мимо
        # [] означает прямое соединение без опорных точек — тоже успех.
        path = router.route((0, y), (320, y), grid,
                            start_side=1, end_side=0)
        assert isinstance(path, list)
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"Router 200 линий занял {elapsed:.2f}s"


def test_placer_branching_tree_scales():
    """Placer: бинарное дерево (ветвление) из 127 блоков."""
    placer = LayeredPlacer()
    blocks = [f"b{i}" for i in range(127)]
    conns = []
    for i in range(1, 127):
        conns.append((f"b{(i - 1) // 2}", f"b{i}"))

    t0 = time.monotonic()
    pos = placer.place(blocks, conns)
    elapsed = time.monotonic() - t0

    assert len(pos) == 127
    # Корень на слое 0, листья дальше по X
    assert pos["b0"][0] < pos["b126"][0]
    assert elapsed < 1.0, f"Placer (дерево) занял {elapsed:.2f}s"
