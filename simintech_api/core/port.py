"""Порт блока SimInTech."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..constants import PortSide
from ..exceptions import PortError
from ..model import PortInfo

if TYPE_CHECKING:
    from .block import Block


class Port:
    """Порт блока. Получается через Block.get_in_port()/get_out_port()."""

    def __init__(self, block: "Block", port_id: int, index: Optional[int] = None):
        self._block = block
        self._id = port_id
        self._index = index

    @property
    def id(self) -> int:
        return self._id

    @property
    def block(self) -> "Block":
        return self._block

    @property
    def index(self) -> Optional[int]:
        return self._index

    @property
    def client(self):
        return self._block.client

    # ─── Информация о порте ─────────────────────────────────────────

    def get_info(self) -> PortInfo:
        """Получить полную информацию о порте (GetPortInfo).

        В comtypes возвращается кортеж [out]-параметров:
        (Name, Side, Mode, TypeId, ItemId, IsInverse, IsCenter,
         IsInvisible, X, Y, XGlobal, YGlobal).
        """
        raw = self.client.call("GetPortInfo", self._id)
        if isinstance(raw, PortInfo):
            return raw
        if isinstance(raw, (tuple, list)) and len(raw) >= 12:
            return PortInfo(
                port_id=self._id,
                name=_as_str(raw[0]),
                side=int(raw[1]),
                mode=int(raw[2]),
                type_id=int(raw[3]),
                item_id=int(raw[4]),
                is_inverse=int(raw[5]),
                is_center=int(raw[6]),
                is_invisible=int(raw[7]),
                x=float(raw[8]),
                y=float(raw[9]),
                x_global=float(raw[10]),
                y_global=float(raw[11]),
            )
        raise PortError(f"GetPortInfo вернул неожиданный формат: {type(raw).__name__}")

    def get_name(self) -> str:
        return self.get_info().name

    def get_side(self) -> PortSide:
        try:
            return PortSide(self.get_info().side)
        except ValueError:
            return PortSide(0)

    def get_coords(self) -> tuple[float, float]:
        """Глобальные координаты порта на странице (для трассировки)."""
        info = self.get_info()
        return (info.x_global, info.y_global)

    # ─── Настройка порта ────────────────────────────────────────────

    def set_name(self, name: str) -> "Port":
        self.client.call("SetPortName", self._id, name)
        return self

    def set_side(self, side: PortSide | int) -> "Port":
        self.client.call("SetPortSide", self._id, int(side))
        return self

    def set_mode(self, mode: int) -> "Port":
        self.client.call("SetPortMode", self._id, int(mode))
        return self

    # ─── Удобство ───────────────────────────────────────────────────

    def connect(self, other: "Port", points: Optional[list] = None):
        """Соединить этот порт (источник) с другим (приёмник) линией."""
        from .page import Page
        page = self._block.project.get_current_page()
        return page.create_wire(self, other, points)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Port({self._id})"


def _as_str(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)
