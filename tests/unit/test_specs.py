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


def test_type_with_quote_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="k", type='Константа" + evil + "'),
        ])


def test_type_with_newline_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="k", type="Константа\nEvil"),
        ])


def test_prop_key_injection_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="k", type="Константа",
                      props={"a, evil = (": 1}),
        ])


def test_digit_prop_key_allowed():
    spec = ModelSpec(blocks=[
        BlockSpec(name="k", type="Класс", props={"2Str_EVC": 1}),
    ])

    text = render_model_text(spec, include_wires=False)

    assert "2Str_EVC = 1" in text


def test_nan_point_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="k", type="Константа",
                      points=[(float("nan"), 0.0)]),
        ])


@pytest.mark.parametrize("key", ["type", "points", "subsystem", "portnames"])
def test_reserved_props_rejected(key):
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="k", type="Константа", props={key: 1}),
        ])


def test_src_kind_must_be_out():
    with pytest.raises(ValidationError):
        ModelSpec(
            blocks=[
                BlockSpec(name="a", type="Константа"),
                BlockSpec(name="b", type="Усилитель"),
            ],
            wires=[WireSpec(src="a:in:0", dst="b:in:0")],
        )


def test_dst_kind_must_be_in():
    with pytest.raises(ValidationError):
        ModelSpec(
            blocks=[
                BlockSpec(name="a", type="Константа"),
                BlockSpec(name="b", type="Усилитель"),
            ],
            wires=[WireSpec(src="a:out:0", dst="b:out:0")],
        )


def test_self_loop_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(
            blocks=[BlockSpec(name="k", type="Константа")],
            wires=[WireSpec(src="k:out:0", dst="k:in:0")],
        )


def test_float_precision_preserved():
    spec = ModelSpec(blocks=[
        BlockSpec(name="k", type="Константа", props={"a": 1234567.0}),
    ])

    text = render_model_text(spec, include_wires=False)

    assert "1234567.0" in text


def test_point_precision_preserved():
    spec = ModelSpec(blocks=[
        BlockSpec(name="k", type="Константа",
                  points=[(1234567.0, 0.0)]),
    ])

    text = render_model_text(spec, include_wires=False)

    assert "1234567.0" in text


def test_nan_prop_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(blocks=[
            BlockSpec(name="k", type="Константа",
                      props={"a": float("nan")}),
        ])
