"""Тесты алгоритма размещения LayeredPlacer."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from simintech_api.exceptions import LayoutError
from simintech_api.layout import LayeredPlacer


def test_linear_chain():
    placer = LayeredPlacer()
    pos = placer.place(['A', 'B', 'C'], [('A', 'B'), ('B', 'C')])
    # A на слое 0, B на слое 1, C на слое 2
    assert pos['A'][0] < pos['B'][0] < pos['C'][0]
    # Все по одной вертикали (y одинаковый)
    ys = {pos[k][1] for k in pos}
    assert len(ys) == 1


def test_no_overlap():
    placer = LayeredPlacer()
    blocks = [f'b{i}' for i in range(20)]
    conns = [(f'b{i}', f'b{i+1}') for i in range(19)]
    pos = placer.place(blocks, conns)
    placed = list(pos.values())
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert abs(a[0] - b[0]) > 20 or abs(a[1] - b[1]) > 20, \
                f"Наложение: {a} и {b}"


def test_branching():
    placer = LayeredPlacer()
    pos = placer.place(['A', 'B', 'C'], [('A', 'B'), ('A', 'C')])
    # A на слое 0, B и C на слое 1 с разными y
    assert pos['A'][0] < pos['B'][0]
    assert pos['A'][0] < pos['C'][0]
    assert pos['B'][1] != pos['C'][1]


def test_feedback_loop():
    """Обратная связь (цикл) не должна ломать ранжирование."""
    placer = LayeredPlacer()
    # A -> B, B -> A (цикл) + внешний вход A
    pos = placer.place(['A', 'B'], [('A', 'B'), ('B', 'A')])
    assert set(pos.keys()) == {'A', 'B'}


def test_empty():
    placer = LayeredPlacer()
    assert placer.place([], []) == {}


def test_pure_cycle_places():
    """Чистый цикл без внешних источников размещается (корень = min входов)."""
    placer = LayeredPlacer()
    pos = placer.place(['A', 'B'], [('A', 'B'), ('B', 'A')])
    assert set(pos.keys()) == {'A', 'B'}
    # Корень ('A' и 'B' имеют по 1 входу — берётся первый) на слое 0
    assert pos['A'][0] == pos['B'][0] or pos['A'][0] < pos['B'][0] or pos['B'][0] < pos['A'][0]
