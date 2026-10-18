"""ModelSpec: валидация и рендер декларативного текста."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simintech_mcp.specs import (
    BlockSpec,
    ModelSpec,
    WireSpec,
    render_model_text,
)


def test_render_blocks_only_has_no_wires():
    spec = ModelSpec(
        blocks=[
            BlockSpec(name="k_0", type="Константа", props={"a": 2}),
            BlockSpec(name="kx_0", type="Усилитель", props={"a": 3}),
        ],
        wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
    )

    text = render_model_text(spec, include_wires=False)

    assert "k_0: (" in text
    assert 'type = "wire"' not in text
    assert "kx_0: (" in text


def test_render_includes_subsystem_wires():
    spec = ModelSpec(blocks=[
        BlockSpec(
            name="sub", type="Субмодель",
            subsystem=ModelSpec(
                blocks=[
                    BlockSpec(name="in", type="Порт входа",
                              portnames="X"),
                    BlockSpec(name="g", type="Усилитель", props={"a": 2}),
                ],
                wires=[WireSpec(src="in:out:0", dst="g:in:0")],
            ),
        )
    ])

    text = render_model_text(spec, include_wires=False)

    assert "subsystem:" in text
    assert 'type = "wire"' in text


def test_duplicate_names_case_insensitive_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="K_0", type="Константа"),
            BlockSpec(name="k_0", type="Усилитель"),
        ])


def test_wire_endpoint_must_exist():
    with pytest.raises(ValidationError):
        ModelSpec(
            blocks=[BlockSpec(name="k_0", type="Константа")],
            wires=[WireSpec(src="k_0:out:0", dst="missing:in:0")],
        )


def test_quote_in_string_prop_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="p", type="Порт входа", portnames='X"Y'),
        ])


def test_props_portnames_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="p", type="Порт входа",
                      props={"portnames": "X"}),
        ])


def test_last_property_has_no_trailing_comma():
    spec = ModelSpec(blocks=[BlockSpec(name="k_0", type="Константа",
                                       props={"a": 2})])

    text = render_model_text(spec, include_wires=False)

    assert ",)" not in text
    assert ",\n)" not in text


def test_portnames_list_uses_clrf():
    spec = ModelSpec(blocks=[
        BlockSpec(name="p", type="Порт входа",
                  portnames=["A", "B"]),
    ])

    text = render_model_text(spec, include_wires=False)

    assert 'portnames = "A"+clrf+"B"' in text
