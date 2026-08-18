"""Страница схемы SimInTech."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

from ..constants import (
    DEFAULT_BLOCK_H,
    DEFAULT_BLOCK_W,
    WIRE_TYPE_AUTOMATICS,
)
from ..exceptions import BlockError, PageError, UnsupportedBlockError

if TYPE_CHECKING:
    from .project import Project
    from .block import Block
    from .port import Port
    from .wire import Wire


class Page:
    """Страница проекта. Создаётся через Project.get_main_page() и т.п."""

    def __init__(self, project: "Project", page_id: int):
        self._project = project
        self._id = page_id

    @property
    def id(self) -> int:
        return self._id

    @property
    def project(self) -> "Project":
        return self._project

    # ─── Активация страницы ─────────────────────────────────────────

    def activate(self) -> None:
        """Сделать страницу текущей (SetCurrentPage)."""
        self._project.client.call("SetCurrentPage", self._project.id, self._id)

    # ─── Блоки ──────────────────────────────────────────────────────

    def create_block(
        self,
        class_name: str,
        x: float,
        y: float,
        *,
        width: Optional[float] = None,
        height: Optional[float] = None,
        layer_no: int = 0,
        parent_block: int = 0,
    ) -> "Block":
        """Создать блок на странице.

        Args:
            class_name: имя класса блока (русское, регистрозависимо),
                напр. "Константа", "Усилитель".
            x, y: координаты центра блока.
            width, height: размеры; по умолчанию — дефолтные.
            layer_no: номер слоя (0 — основной).
            parent_block: id родительского блока (для встраиваемых), 0 — нет.
        """
        from ..constants import UNSUPPORTED_COM_BLOCK_CLASSES
        if class_name in UNSUPPORTED_COM_BLOCK_CLASSES:
            raise UnsupportedBlockError(
                f"Класс '{class_name}' не создаётся через COM CreateBlock "
                f"(см. константы.UNSUPPORTED_COM_BLOCK_CLASSES). "
                f"Используйте встроенный язык SimInTech / макрос."
            )
        self.activate()
        block_id = _as_i64(self._project.client.call(
            "CreateBlock",
            self._project.id, layer_no, parent_block, class_name,
        ))
        if block_id == 0:
            raise BlockError(f"CreateBlock не создал блок класса '{class_name}'")
        from .block import Block
        block = Block(self._project, block_id, class_name=class_name)
        block.set_position(
            x, y,
            width=width if width is not None else DEFAULT_BLOCK_W,
            height=height if height is not None else DEFAULT_BLOCK_H,
        )
        return block

    def get_blocks(self) -> List["Block"]:
        """Получить все блоки текущей страницы (GetPageBlockId по индексу)."""
        from .block import Block
        self.activate()
        count = _as_i64(self._project.client.call(
            "GetPageObjectCount", self._project.id))
        result: List[Block] = []
        for i in range(count):
            block_id = _as_i64(self._project.client.call(
                "GetPageBlockId", self._project.id, i))
            if block_id:
                result.append(Block(self._project, block_id))
        return result

    def find_block(self, name: str) -> Optional["Block"]:
        """Найти блок по свойству Name."""
        from ..exceptions import ComCallError
        for block in self.get_blocks():
            try:
                if block.get_property("Name") == name:
                    return block
            except ComCallError:
                continue
        return None

    # ─── Линии связи ────────────────────────────────────────────────

    def create_wire(
        self,
        start_port: "Port",
        end_port: "Port",
        points: Optional[List[Tuple[float, float]]] = None,
        *,
        wire_type: int = WIRE_TYPE_AUTOMATICS,
        layer_no: int = 0,
    ) -> "Wire":
        """Создать линию связи между двумя портами.

        Args:
            start_port: порт-источник (выход).
            end_port: порт-приёмник (вход).
            points: опорные точки линии (промежуточные), опционально.
            wire_type: 0 — автоматика, 1 — гидравлика.
            layer_no: номер слоя.
        """
        from .wire import Wire
        point_count = len(points) if points else 0
        wire_id = _as_i64(self._project.client.call(
            "CreateWire",
            self._project.id, layer_no, wire_type, 0, -1,
            start_port.id, end_port.id, point_count,
        ))
        wire = Wire(self._project, wire_id)
        if points:
            for idx, (x, y) in enumerate(points):
                wire.set_point(idx, x, y)
        return wire


def _as_i64(value) -> int:
    if hasattr(value, "value"):
        return int(value.value)
    return int(value)
