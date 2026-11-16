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

    def get_port_count(self):
        return 5

    def get_out_port(self, index):
        # Один выход: у nport-классов он есть, у прочих классов фейка тоже.
        if index == 0:
            return object()
        from simintech_api.exceptions import PortError

        raise PortError("нет выходного порта")

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


def test_required_height_helper_is_shared_rule():
    """Одна функция правила: число, ноль и класс вне правила."""
    assert rules.required_height("Оператор И", 4) == 64
    assert rules.required_height("Порт входа", 2) == 32
    assert rules.required_height("Субмодель", 3) == 48
    assert rules.required_height("Оператор И", 0) is None
    assert rules.required_height("Константа", 4) is None


def test_grid_height_puts_all_sides_on_8_px_grid():
    """Сетка 8 px: H = 16*НОК сторон (живой замер 14.11.2026).

    Среда раскладывает порты стороны равномерно, позиции `H*(2k+1)/(2N)`:
    при 3 входах и 2 выходах высота 96 даёт 16/48/80 и 24/72, все кратны 8.
    """
    assert rules.grid_height([3, 2]) == 96
    # 4/1: НОК(4,1)=4, прежние 64 не меняются.
    assert rules.grid_height([4, 1]) == 64
    assert rules.grid_height([5]) == 80
    assert rules.grid_height([1, 5]) == 80
    assert rules.grid_height([0, 0]) is None
    assert rules.grid_height([]) is None


def test_required_height_accepts_side_counts():
    """Разное число входов и выходов считается по сетке."""
    assert rules.required_height("Субмодель", (3, 2)) == 96
    assert rules.required_height("Субмодель", (6, 1)) == 96
    assert rules.required_height("Субмодель", (0, 4)) == 64
