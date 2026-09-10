"""Тесты конвертеров значений и парсинга Points."""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api.utils.converters import (
    _to_descriptor,
    block_center,
    block_size,
    descriptor_is_valid,
    is_descriptor,
    parse_points,
    value_to_prop_string,
)
from simintech_api.model import TDataDescriptor


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


# ─── Дескрипторы: не подменять родной класс comtypes ──────────────

class ForeignDescriptor:
    """Имитация класса TDataDescriptor, сгенерированного comtypes.

    Одноимённый нашему, но ДРУГОЙ класс — именно так ведёт себя comtypes,
    генерируя структуру из библиотеки типов. `isinstance` его не ловит.
    """

    def __init__(self, data_id, data_type):
        self.DataId = data_id
        self.DataType = data_type


def test_foreign_descriptor_passes_through_unchanged():
    """Родной дескриптор comtypes возвращается как есть.

    Подмена его нашим одноимённым классом ломает ReadAsFloat/WriteAsFloat:
    comtypes принимает только тип из библиотеки и падает с «expected
    TDataDescriptor instance instead of TDataDescriptor». Проверено на
    SimInTech64 — именно это делало чтение сигналов неработающим.
    """
    foreign = ForeignDescriptor(123, 0)

    result = _to_descriptor(foreign)

    assert result is foreign
    assert type(result) is ForeignDescriptor


def test_own_descriptor_passes_through():
    own = TDataDescriptor(5, 0)

    assert _to_descriptor(own) is own


def test_tuple_is_wrapped_into_own_descriptor():
    """Кортеж (из фейкового COM или [out]-параметра) оборачивается в наш тип."""
    result = _to_descriptor((7, 1))

    assert isinstance(result, TDataDescriptor)
    assert (result.DataId, result.DataType) == (7, 1)


def test_none_gives_empty_descriptor():
    assert _to_descriptor(None).DataId == 0


def test_is_descriptor_detects_by_attributes_not_type():
    assert is_descriptor(ForeignDescriptor(1, 0)) is True
    assert is_descriptor(TDataDescriptor(1, 0)) is True
    assert is_descriptor((1, 0)) is False
    assert is_descriptor(None) is False


def test_descriptor_is_valid_works_for_foreign():
    """Валидность определяется по DataId, а не по типу класса."""
    assert descriptor_is_valid(ForeignDescriptor(42, 0)) is True
    assert descriptor_is_valid(ForeignDescriptor(0, 0)) is False
    assert descriptor_is_valid(TDataDescriptor(42, 0)) is True
    assert descriptor_is_valid(None) is False
