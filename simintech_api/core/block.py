"""Блок схемы SimInTech."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ..exceptions import PortError
from ..utils.converters import value_to_prop_string

if TYPE_CHECKING:
    from .project import Project
    from .port import Port


class Block:
    """Блок на схеме. Создаётся через Page.create_block().

    Args:
        project: проект, которому принадлежит блок.
        block_id: COM-идентификатор блока (__int64).
        class_name: имя класса (для удобства; может быть неизвестно).
    """

    def __init__(self, project: "Project", block_id: int,
                 class_name: Optional[str] = None):
        self._project = project
        self._id = block_id
        self._class_name = class_name

    @property
    def id(self) -> int:
        """COM-идентификатор блока."""
        return self._id

    @property
    def project(self) -> "Project":
        """Проект, которому принадлежит блок."""
        return self._project

    @property
    def client(self):
        """COM-клиент проекта."""
        return self._project.client

    @property
    def class_name(self) -> str:
        """Имя класса блока (кэшируется при создании; иначе — GetBlockPluginName)."""
        if self._class_name is None:
            try:
                self._class_name = _as_str(self.client.call(
                    "GetBlockPluginName", self._id))
            except Exception:
                self._class_name = ""
        return self._class_name

    # ─── Свойства ───────────────────────────────────────────────────

    def set_property(self, name: str, value: object) -> "Block":
        """Установить свойство блока (значение приводится к строке)."""
        self.client.call("SetBlockProp", self._id, name,
                         value_to_prop_string(value))
        return self

    def get_property(self, name: str) -> str:
        """Прочитать свойство блока как строку."""
        return _as_str(self.client.call("GetBlockPropAsString", self._id, name))

    def get_points(self) -> str:
        """Сырое значение свойства Points (строка SimInTech)."""
        return self.get_property("Points")

    def get_name(self) -> str:
        """Имя блока (свойство Name)."""
        return self.get_property("Name")

    def set_name(self, name: str) -> "Block":
        """Установить имя блока."""
        return self.set_property("Name", name)

    # ─── Позиция и размеры ──────────────────────────────────────────

    def set_position(
        self,
        x: float,
        y: float,
        *,
        width: Optional[float] = None,
        height: Optional[float] = None,
        angle: float = 0.0,
    ) -> "Block":
        """Установить положение блока.

        x, y — координаты ЛЕВОГО ВЕРХНЕГО угла (не центр!) в системе
        SetBlockPosition (Left, Top, Width, Height, Angle).
        Для центрирования используйте set_center().
        """
        w = width if width is not None else 60.0
        h = height if height is not None else 40.0
        self.client.call("SetBlockPosition", self._id, x, y, w, h, angle)
        return self

    def set_center(
        self,
        cx: float,
        cy: float,
        *,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> "Block":
        """Установить блок по центру (cx, cy) с заданными размерами."""
        w = width if width is not None else 60.0
        h = height if height is not None else 40.0
        left = cx - w / 2.0
        top = cy - h / 2.0
        return self.set_position(left, top, width=w, height=h)

    # ─── Порты ──────────────────────────────────────────────────────

    def get_port_count(self) -> int:
        """Количество портов блока."""
        return _as_int(self.client.call("GetPortCount", self._id))

    def get_block_port(self, index: int) -> "Port":
        """Получить порт по общему индексу (0..count-1)."""
        from .port import Port
        port_id = _as_int(self.client.call("GetBlockPort", self._id, index))
        if port_id == 0:
            raise PortError(f"Блок {self._id}: порт с индексом {index} не найден")
        return Port(self, port_id, index=index)

    def get_in_port(self, index: int = 0) -> "Port":
        """Получить входной порт по номеру (0-based)."""
        from .port import Port
        port_id = _as_int(self.client.call("GetInPort", self._id, index))
        if port_id == 0:
            # Fallback-паттерн из build_demos.ps1: BlockAfterEdit + перебор
            self._refresh_ports()
            port_id = _as_int(self.client.call("GetInPort", self._id, index))
        if port_id == 0:
            raise PortError(f"Блок {self._id}: входной порт {index} не найден")
        return Port(self, port_id, index=index)

    def get_out_port(self, index: int = 0) -> "Port":
        """Получить выходной порт по номеру (0-based)."""
        from .port import Port
        port_id = _as_int(self.client.call("GetOutPort", self._id, index))
        if port_id == 0:
            self._refresh_ports()
            port_id = _as_int(self.client.call("GetOutPort", self._id, index))
        if port_id == 0:
            raise PortError(f"Блок {self._id}: выходной порт {index} не найден")
        return Port(self, port_id, index=index)

    def get_ports(self) -> List["Port"]:
        """Получить все порты блока (по общему индексу)."""
        result = []
        for i in range(self.get_port_count()):
            try:
                result.append(self.get_block_port(i))
            except PortError:
                break
        return result

    # ─── Соединение ─────────────────────────────────────────────────

    def connect(self, other: "Block", out_index: int = 0, in_index: int = 0):
        """Соединить выход self с входом other линией связи."""
        page = self.project.get_current_page()
        out_port = self.get_out_port(out_index)
        in_port = other.get_in_port(in_index)
        return page.create_wire(out_port, in_port)

    # ─── Внутреннее ─────────────────────────────────────────────────

    def _refresh_ports(self) -> None:
        """Попытка пересоздать порты после изменения блока (BlockAfterEdit)."""
        try:
            self.client.call("BlockAfterEdit", self._id)
        except Exception:
            pass


def _as_int(value) -> int:
    if hasattr(value, "value"):
        return int(value.value)
    return int(value)


def _as_str(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)
