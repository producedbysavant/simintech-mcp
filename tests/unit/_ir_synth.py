"""Малые синтетические .xprt для P5-контракта MCP.

Полные генераторы живут в simintech-code; здесь только формы, нужные
end-to-end проверкам инструментов. Никакой случайности и файлов, кроме
явного `write`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _object(name: str, class_name: str, obj_type: int = 100,
            ports: tuple[tuple[str, int], ...] = ()) -> str:
    body = "".join(
        f"<port><name>`{port}`</name><mode>`{mode}`</mode></port>"
        for port, mode in ports)
    block = f"<ports>{body}</ports>" if body else ""
    return (f"<object><name>`{name}`</name>"
            f"<class_name>`{class_name}`</class_name>"
            f"<obj_type>`{obj_type}`</obj_type>{block}</object>")


def _wire(name: str, start: int, start_port: int,
          end: int, end_port: int) -> str:
    return (f"<object><name>`{name}`</name>"
            f"<class_name>`Математическая связь`</class_name>"
            f"<obj_type>`101`</obj_type>"
            f"<start_bl_index>`{start}`</start_bl_index>"
            f"<start_port_index>`{start_port}`</start_port_index>"
            f"<end_bl_index>`{end}`</end_bl_index>"
            f"<end_port_index>`{end_port}`</end_port_index></object>")


def _wrap(body: str) -> str:
    return f"<Header><project><page>{body}</page></project></Header>"


def chain(count: int, class_name: str = "Усилитель") -> str:
    parts = [
        _object(f"B{index}", class_name, 100, (("u", 0), ("y", 1)))
        for index in range(count)
    ]
    parts.extend(
        _wire(f"W{index}", index, 1, index + 1, 0)
        for index in range(count - 1))
    return _wrap("".join(parts))


def loose(count: int) -> str:
    return _wrap("".join(
        _object(f"B{index}", "Усилитель", 100, (("u", 0), ("y", 1)))
        for index in range(count)))


def star(sinks: int) -> str:
    parts = [_object("S", "Константа", 100, (("y", 1),))]
    parts.extend(
        _object(f"T{index}", "Временной график", 106, (("u", 0),))
        for index in range(sinks))
    parts.append(_wire("W0", 0, 0, 1, 0))
    for index in range(1, sinks):
        parts.append(
            f"<object><name>`W{index}`</name>"
            f"<class_name>`Математическая связь`</class_name>"
            f"<obj_type>`101`</obj_type>"
            f"<start_bl_index>`-1`</start_bl_index>"
            f"<start_port_index>`0`</start_port_index>"
            f"<parent_wire_name>`W0`</parent_wire_name>"
            f"<parent_wire_index>`0`</parent_wire_index>"
            f"<parentwirenode>`0`</parentwirenode>"
            f"<end_bl_index>`{index + 1}`</end_bl_index>"
            f"<end_port_index>`0`</end_port_index></object>")
    return _wrap("".join(parts))


def direct_star(sinks: int) -> str:
    parts = [_object("S", "Константа", 100, (("y", 1),))]
    parts.extend(
        _object(f"T{index}", "Временной график", 106, (("u", 0),))
        for index in range(sinks))
    parts.extend(_wire(f"W{index}", 0, 0, index + 1, 0)
                 for index in range(sinks))
    return _wrap("".join(parts))


def cycle() -> str:
    return _wrap(
        _object("A", "Усилитель", 100, (("u", 0), ("y", 1)))
        + _object("B", "Усилитель", 100, (("u", 0), ("y", 1)))
        + _wire("W1", 0, 1, 1, 0)
        + _wire("W2", 1, 1, 0, 0))


def self_loop() -> str:
    return _wrap(
        _object("A", "Усилитель", 100, (("u", 0), ("y", 1)))
        + _wire("W1", 0, 1, 0, 0))


def unknown_type(count: int) -> str:
    return _wrap("".join(
        _object(f"U{index}", "Усилитель", 999, (("u", 0), ("y", 1)))
        for index in range(count)))


def verbose_classes(count: int, classes: int = 20,
                    width: int = 300) -> str:
    names = [f"C{index}_" + "x" * width for index in range(classes)]
    return _wrap("".join(
        _object(f"B{index}", names[index % classes], 100)
        for index in range(count)))


def wrong_ports(count: int) -> str:
    return _wrap("".join(
        _object(f"U{index}", "Усилитель", 100) for index in range(count)))


def blocks_spec(count: int) -> dict[str, Any]:
    return {
        "schema": "system-spec/1",
        "name": "wrong-ports",
        "blocks": [
            {"name": f"U{index}", "class": "Усилитель",
             "ports": {"inputs": ["u"], "outputs": ["y"]}}
            for index in range(count)
        ],
    }


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path
