"""Чтение/запись сигналов SimInTech."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

from ..constants import DataType, READ_METHODS, WRITE_METHODS
from ..exceptions import SignalError
from ..model import TDataDescriptor

if TYPE_CHECKING:
    from .project import Project


class Signal:
    """Сигнал проекта. Получается через Project.signal(name).

    Args:
        project: проект.
        descriptor: TDataDescriptor (DataId, DataType).
        name: имя сигнала (для диагностики).
    """

    def __init__(self, project: "Project", descriptor: TDataDescriptor, name: str):
        self._project = project
        self._descriptor = descriptor
        self._name = name

    @property
    def project(self) -> "Project":
        return self._project

    @property
    def name(self) -> str:
        return self._name

    @property
    def descriptor(self) -> TDataDescriptor:
        return self._descriptor

    @property
    def data_type(self) -> DataType:
        try:
            return DataType(self._descriptor.DataType)
        except ValueError:
            return DataType.DOUBLE

    # ─── Чтение ─────────────────────────────────────────────────────

    def read(self) -> Any:
        """Прочитать значение сигнала согласно DataType."""
        dt = self.data_type
        method = READ_METHODS.get(dt)
        if method is None:
            raise SignalError(f"Нет метода чтения для типа {dt} ({self._name})")
        value = self._project.client.call(method, self._descriptor)
        return _normalize_scalar(value, dt)

    def read_array(self) -> List[Any]:
        """Прочитать массив целиком (типы ARRAY/INT_ARRAY)."""
        count = self.array_count()
        result: List[Any] = []
        getter = ("GetExtArrayElement" if self.data_type == DataType.ARRAY
                  else "GetIntArrayElement")
        for i in range(count):
            v = self._project.client.call(getter, self._descriptor, i)
            result.append(float(v) if self.data_type == DataType.ARRAY else int(v))
        return result

    def read_array_element(self, index: int) -> float:
        getter = ("GetExtArrayElement" if self.data_type == DataType.ARRAY
                  else "GetIntArrayElement")
        return self._project.client.call(getter, self._descriptor, index)

    def array_count(self) -> int:
        return int(self._project.client.call("GetArrayCount", self._descriptor))

    # ─── Запись ─────────────────────────────────────────────────────

    def write(self, value: Any) -> "Signal":
        """Записать значение согласно DataType."""
        dt = self.data_type
        method = WRITE_METHODS.get(dt)
        if method is None:
            raise SignalError(f"Нет метода записи для типа {dt} ({self._name})")
        if dt == DataType.STRING:
            self._project.client.call(method, self._descriptor, str(value))
        elif dt == DataType.INTEGER or dt == DataType.BOOL:
            self._project.client.call(method, self._descriptor, int(value))
        else:
            self._project.client.call(method, self._descriptor, float(value))
        return self

    def write_array(self, values: List[float]) -> "Signal":
        """Записать массив целиком (предварительно выставив размер)."""
        self.set_array_count(len(values))
        setter = ("SetExtArrayElement" if self.data_type == DataType.ARRAY
                  else "SetIntArrayElement")
        for i, v in enumerate(values):
            if self.data_type == DataType.ARRAY:
                self._project.client.call(setter, self._descriptor, i, float(v))
            else:
                self._project.client.call(setter, self._descriptor, i, int(v))
        return self

    def set_array_element(self, index: int, value: float) -> "Signal":
        setter = ("SetExtArrayElement" if self.data_type == DataType.ARRAY
                  else "SetIntArrayElement")
        if self.data_type == DataType.ARRAY:
            self._project.client.call(setter, self._descriptor, index, float(value))
        else:
            self._project.client.call(setter, self._descriptor, index, int(value))
        return self

    def set_array_count(self, count: int) -> "Signal":
        self._project.client.call("SetArrayCount", self._descriptor, int(count))
        return self

    def __repr__(self) -> str:  # pragma: no cover
        return f"Signal({self._name!r}, type={self.data_type.name})"


def _normalize_scalar(value: Any, dt: DataType) -> Any:
    """Привести скалярное значение COM к Python-типу."""
    if value is None:
        return None
    if hasattr(value, "value"):   # comtypes VARIANT/scalar
        value = value.value
    if dt == DataType.BOOL:
        return bool(value)
    if dt == DataType.INTEGER:
        return int(value)
    if dt == DataType.DOUBLE:
        return float(value)
    return value
