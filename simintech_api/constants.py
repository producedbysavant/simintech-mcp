"""Константы COM API SimInTech.

Значения DataType восстановлены из практики (см. SIT_SimInTech_Vneshnij_API.pdf,
mmain.hpp и docs/reference/com_api_inventory.md) — официально enum не
задокументирован.
"""

from enum import IntEnum


class DataType(IntEnum):
    """Типы данных сигналов SimInTech (поле TDataDescriptor.DataType)."""
    DOUBLE = 0        # double
    INTEGER = 1       # integer (int64)
    BOOL = 2          # boolean (читается/пишется как int)
    STRING = 4        # string
    ARRAY = 5         # array (double[])
    INT_ARRAY = 12    # intarray (int64[])


# Имя метода чтения по DataType
READ_METHODS = {
    DataType.DOUBLE: "ReadAsFloat",
    DataType.INTEGER: "ReadAsInteger",
    DataType.BOOL: "ReadAsInteger",
    DataType.STRING: "ReadAsString",
}

# Имя метода записи по DataType
WRITE_METHODS = {
    DataType.DOUBLE: "WriteAsFloat",
    DataType.INTEGER: "WriteAsInteger",
    DataType.BOOL: "WriteAsInteger",
    DataType.STRING: "WriteAsString",
}


class PortSide(IntEnum):
    """Сторона порта (поле GetPortInfo Side)."""
    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3


class PortMode(IntEnum):
    """Режим порта."""
    INPUT = 0
    OUTPUT = 1
    # прочие режимы (универсальный, параметр и т.п.) не задокументированы
    UNKNOWN = -1


# Пространственные константы layout (в пикселях схемы SimInTech)
GRID_SIZE = 20          # шаг сетки трассировки
LAYER_GAP = 160         # расстояние между слоями (X)
BLOCK_GAP = 80          # расстояние между блоками в слое (Y)
DEFAULT_BLOCK_W = 60    # ширина блока по умолчанию
DEFAULT_BLOCK_H = 40    # высота блока по умолчанию
PORT_STUB = 20          # длина короткого участка линии от порта до канала

# WireType для CreateWire: 0 — автоматика, 1 — гидравлика
WIRE_TYPE_AUTOMATICS = 0
WIRE_TYPE_HYDRAULICS = 1

# Классы блоков, НЕ создаваемые через COM CreateBlock (из практики; обходить
# через встроенный язык SimInTech / макросы)
UNSUPPORTED_COM_BLOCK_CLASSES = {
    "Из памяти",
    "Порт выхода",                # регистрация порта состояния
    "Флаг входа в состояние",     # вызывает AV
    # CreateBlock возвращает 0, создавая вместо блока заглушку-«табличку»
    # (класс в списке, но объект не тот). Проверено на SimInTech64 (2026-09-10).
    "Выход данных состояния",
    "Состояние автомата",
}

# Классы блоков, проверенно создаваемые через COM CreateBlock
SUPPORTED_COM_BLOCK_CLASSES = {
    "Константа",
    "Усилитель",
    "Сумматор",
    "Интегратор",
    "Производная",
    "Ступенька",
    "Синусоида",
    "Временной график",
    "Сравнивающее устройство",
    "Порт входа",
    "Задержка на шаг интегрирования",
    "RS-триггер с приоритетом по установке",
}
