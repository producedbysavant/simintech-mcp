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


def test_grid_height_follows_the_larger_side():
    """Высота - по большей стороне (решение владельца 19.11.2026).

    Совпадение сетки 8 px у обеих сторон требует `16*НОК`: у 28/26 это
    5824 px, блок высотой в шесть экранов. Поэтому приоритет у большей
    стороны, а меньшая ложится мимо сетки.
    """
    assert rules.grid_height([3, 2]) == 48
    assert rules.grid_height([4, 1]) == 64
    assert rules.grid_height([5]) == 80
    assert rules.grid_height([1, 5]) == 80
    assert rules.grid_height([28, 26]) == 448
    assert rules.grid_height([0, 0]) is None
    assert rules.grid_height([]) is None


def test_read_port_counts_uses_raw_com_without_scene_edit():
    """Счёт портов идёт сырым COM: API-методы на промахе зовут BlockAfterEdit."""
    from simintech_mcp.tools.blocks import read_port_counts

    class _Client:
        def __init__(self):
            self.calls = []

        def call(self, method, block_id, index):
            self.calls.append((method, index))
            limit = 3 if method == "GetInPort" else 2
            return index + 1 if index < limit else 0

    class _Block:
        id = 9

        def __init__(self):
            self.client = _Client()

        def get_port_count(self):
            return 5

        def get_in_port_count(self):
            raise AssertionError("API-путь не должен вызываться при клиенте")

        def get_out_port(self, index):
            raise AssertionError("API-путь не должен вызываться при клиенте")

    block = _Block()
    assert read_port_counts(block) == (3, 2)
    methods = {method for method, _ in block.client.calls}
    assert methods == {"GetInPort", "GetOutPort"}


def test_required_height_accepts_side_counts():
    """Разное число входов и выходов считается по сетке."""
    assert rules.required_height("Субмодель", (3, 2)) == 48
    assert rules.required_height("Субмодель", (6, 1)) == 96
    assert rules.required_height("Субмодель", (0, 4)) == 64


def test_table_height_counts_positions_without_header():
    """Таблица: позиции без заголовка, пустые заполнители считаются."""
    table = "Имя сигнала\r\n" + "\r\n".join(["a", "", "b", ""]) + "\r\n"
    assert rules.table_positions(table) == 4
    assert rules.table_height(table) == 64
    assert rules.table_positions(table, header=False) == 5
    assert rules.table_height("") is None
    assert rules.table_height("Имя сигнала\r\n") is None
