"""Структурированная модель: Pydantic-валидация и рендер текста SimInTech."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from pydantic import BaseModel, Field, model_validator


_ENDPOINT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*):(?P<kind>out|in):(?P<index>\d+)$")
#: Ключ свойства: буква, цифра или подчёркивание в начале; дальше точки.
#: Цифра в начале допустима: в каталоге есть `2Str_EVC` и `3Dvis`.
_PROP_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.]*$")
#: Свойства, которые задаются полями записи, а не через props.
_RESERVED_PROPS = frozenset({"type", "points", "portnames", "subsystem"})
_DEFAULT_POINTS = [(0.0, 0.0), (16.0, 0.0), (0.0, -16.0), (0.0, 32.0)]


class WireSpec(BaseModel):
    """Провод: концы в формате блок:out:N и блок:in:N."""

    src: str = Field(description="Источник: имя:out:индекс")
    dst: str = Field(description="Приёмник: имя:in:индекс")


class BlockSpec(BaseModel):
    """Блок или субмодель."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)
    props: dict[str, Any] = Field(default_factory=dict)
    points: list[tuple[float, float]] | None = None
    portnames: str | list[str] | None = None
    subsystem: Optional["ModelSpec"] = None


class ModelSpec(BaseModel):
    """Модель: блоки и провода текущего уровня."""

    blocks: list[BlockSpec] = []
    wires: list[WireSpec] = []

    @model_validator(mode="after")
    def _validate_level(self) -> "ModelSpec":
        names: Dict[str, str] = {}
        for block in self.blocks:
            key = block.name.lower()
            if key in names:
                raise ValueError(
                    f"имена блоков различаются только регистром: "
                    f"{names[key]!r} и {block.name!r}; createmodel приводит "
                    f"имена к нижнему регистру")
            names[key] = block.name
        for wire in self.wires:
            src_name = _validate_endpoint(wire.src, names, "src", "out")
            dst_name = _validate_endpoint(wire.dst, names, "dst", "in")
            if src_name.lower() == dst_name.lower():
                raise ValueError(
                    f"самопетля: {wire.src} -> {wire.dst}; провод из выхода "
                    f"блока в его собственный вход обычно означает опечатку")
        for block in self.blocks:
            _validate_text(block.type, f"{block.name}.type")
            if block.portnames is not None:
                values = ([block.portnames]
                          if isinstance(block.portnames, str)
                          else block.portnames)
                for value in values:
                    _validate_text(value, "portnames")
            for point in block.points or []:
                _validate_point(point, f"{block.name}.points")
            for key, value in block.props.items():
                if key in _RESERVED_PROPS:
                    raise ValueError(
                        f"{block.name}: свойство {key!r} задаётся полем "
                        f"записи, а не в props")
                _validate_key(key, block.name)
                _validate_value(value, key)
        return self


def _validate_endpoint(value: str, names: Dict[str, str], side: str,
                       expected_kind: str) -> str:
    match = _ENDPOINT_RE.match(value)
    if match is None:
        raise ValueError(
            f"{side}={value!r}: ожидается имя:{expected_kind}:индекс")
    if match.group("kind") != expected_kind:
        raise ValueError(
            f"{side}={value!r}: для {side} нужен порт {expected_kind}, "
            f"а указан {match.group('kind')}")
    name = match.group("name")
    if name.lower() not in names:
        raise ValueError(
            f"{side}={value!r}: блока {name!r} нет на этом уровне")
    return name


def _validate_text(value: str, label: str) -> None:
    if '"' in value:
        raise ValueError(
            f"{label}={value!r}: кавычка в строке не поддерживается; "
            f"используйте chr(34) в тексте или уберите кавычку")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(
            f"{label}: управляющие символы в строке не поддерживаются")


def _validate_key(key: str, block: str) -> None:
    if not _PROP_KEY_RE.fullmatch(key):
        raise ValueError(
            f"{block}: недопустимое имя свойства {key!r}; разрешены буквы, "
            f"цифры, подчёркивание и точки")


def _validate_point(point: Tuple[float, float], label: str) -> None:
    if not all(math.isfinite(value) for value in point):
        raise ValueError(f"{label}: координата должна быть конечным числом")


def _validate_value(value: Any, label: str) -> None:
    if isinstance(value, str):
        _validate_text(value, label)
    elif isinstance(value, (list, tuple)):
        for item in cast(Sequence[Any], value):
            _validate_value(item, label)
    elif isinstance(value, dict):
        raise ValueError(f"{label}: словари не поддерживаются")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label}: значение должно быть конечным числом")
    elif not isinstance(value, (int, bool)):
        raise ValueError(
            f"{label}: тип {type(value).__name__} не поддерживается")


def _render_number(value: float) -> str:
    """Число без потери точности: repr даёт кратчайшую обратимую запись."""
    return repr(value)


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("значение должно быть конечным числом")
        return _render_number(value)
    if isinstance(value, str):
        _validate_text(value, "value")
        return f'"{value}"'
    if isinstance(value, (list, tuple)):
        items = cast(Sequence[Any], value)
        return "[" + ", ".join(_render_value(item) for item in items) + "]"
    raise ValueError(f"неподдерживаемое значение: {value!r}")


def _render_points(points: List[Tuple[float, float]]) -> str:
    pairs = (f"({_render_number(x)}, {_render_number(y)})" for x, y in points)
    return "[" + ",".join(pairs) + "]"


def _render_portnames(value: Union[str, List[str]]) -> str:
    if isinstance(value, str):
        _validate_text(value, "portnames")
        return f'"{value}"'
    parts: list[str] = []
    for item in value:
        _validate_text(item, "portnames")
        parts.append(f'"{item}"')
    return "+clrf+".join(parts)


def _render_block(block: BlockSpec, level: int) -> str:
    pad = "  " * level
    props: List[str] = [
        f'type = "{block.type}"',
        f"points={_render_points(block.points or _DEFAULT_POINTS)}",
    ]
    if block.portnames is not None:
        props.append(f"portnames = {_render_portnames(block.portnames)}")
    for key, value in block.props.items():
        props.append(f"{key} = {_render_value(value)}")
    if block.subsystem is not None:
        inner = _render_level(block.subsystem, level + 2)
        props.append(f"subsystem:\n{pad}  (\n{inner}\n{pad}  )")
    body = (",\n" + pad + "  ").join(props)
    return f"{pad}{block.name}: (\n{pad}  {body}\n{pad})"


def _render_wire(wire: WireSpec, index: int, level: int) -> str:
    pad = "  " * level
    return (
        f'{pad}wire_{index}: (\n'
        f'{pad}  type = "wire",\n'
        f'{pad}  points=[],\n'
        f'{pad}  src = "{wire.src}",\n'
        f'{pad}  dst = "{wire.dst}"\n'
        f'{pad})'
    )


def _render_level(spec: ModelSpec, level: int) -> str:
    records: List[str] = []
    for block in spec.blocks:
        records.append(_render_block(block, level))
    for index, wire in enumerate(spec.wires, start=1):
        records.append(_render_wire(wire, index, level))
    return ",\n".join(records)


def render_model_text(spec: ModelSpec, *,
                      include_wires: bool = True) -> str:
    """Текст записей без внешней обёртки const model: (...).

    Верхнеуровневые провода по умолчанию не рендерятся в сборке: их создаёт
    `connect` по фактическим портам. Провода внутри субмоделей остаются в
    тексте, потому что другого пути к ним нет.
    """
    records: List[str] = []
    for block in spec.blocks:
        records.append(_render_block(block, 1))
    if include_wires:
        for index, wire in enumerate(spec.wires, start=1):
            records.append(_render_wire(wire, index, 1))
    return ",\n".join(records)


ModelSpec.model_rebuild()
