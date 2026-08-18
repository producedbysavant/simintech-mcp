"""Тесты конвертеров значений и парсинга Points."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api.utils.converters import (
    block_center,
    block_size,
    parse_points,
    value_to_prop_string,
)


def test_value_to_prop_string_types():
    assert value_to_prop_string(10) == "10"
    assert value_to_prop_string(2.5) == "2.5"
    assert value_to_prop_string(2.0) == "2"
    assert value_to_prop_string(True) == "1"
    assert value_to_prop_string(False) == "0"
    assert value_to_prop_string("text") == "text"
    assert value_to_prop_string([1, 2, 3]) == "[1, 2, 3]"


def test_parse_points():
    s = "[(128,72),(144,72),(128,64),(128,84)]"
    pts = parse_points(s)
    assert pts == [(128.0, 72.0), (144.0, 72.0), (128.0, 64.0), (128.0, 84.0)]


def test_parse_points_empty():
    assert parse_points(None) == []
    assert parse_points("") == []
    assert parse_points("[]") == []


def test_block_center():
    s = "[(100,50),(130,50),(100,30),(100,60)]"
    assert block_center(s) == (100.0, 50.0)


def test_block_size():
    # центр (100,50); правая точка (130,50) -> ширина 60; нижняя (100,30) -> высота 40
    s = "[(100,50),(130,50),(100,30),(100,60)]"
    w, h = block_size(s)
    assert w == 60.0
    assert h == 40.0


def test_block_size_default():
    w, h = block_size(None)
    assert w == 60.0
    assert h == 40.0
