"""Правила владельца: константы размеров и классы с входными контактами."""

from __future__ import annotations

from simintech_mcp import rules
from simintech_mcp.tools import sizes


class _Block:
    """Минимальный блок для проверки правила высоты без COM."""

    def __init__(self, class_name: str):
        self.class_name = class_name

    def get_in_port_count(self):
        return 4

    def get_property(self, name):
        return ""


def test_row_rule_numbers():
    """Стандарт владельца: 16 px на вход, поле 8 px, ширина 32."""
    assert rules.PORT_ROW_HEIGHT == 16
    assert rules.PORT_MARGIN == rules.PORT_ROW_HEIGHT // 2 == 8
    assert rules.NPORT_BLOCK_WIDTH == 32


def test_required_height_applies_to_nport_classes_only():
    """Правило высоты срабатывает у nport-классов и не трогает оговорённые."""
    for class_name in rules.NPORT_SIZE_CLASSES:
        assert sizes._required_height(
            "b", _Block(class_name)) == rules.PORT_ROW_HEIGHT * 4
    for class_name in rules.NPORT_OTHER_CLASSES:
        assert sizes._required_height("b", _Block(class_name)) is None


def test_port_height_classes_are_separate():
    """Порт-блоки считаются по строкам PortNames, а не по nport."""
    assert set(rules.PORT_HEIGHT_CLASSES).isdisjoint(rules.ROW_HEIGHT_CLASSES)
