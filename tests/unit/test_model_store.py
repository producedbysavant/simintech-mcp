"""Снимки Model IR: свежесть, вытеснение и привязка к проекту."""

import os
import sys

import pytest
from fastmcp.exceptions import ToolError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api.model_ir import parse_xprt  # noqa: E402

from simintech_mcp import model_store  # noqa: E402

MODEL = """<Header><project><page>
  <object>
    <name>`K`</name><class_name>`Константа`</class_name><obj_type>`100`</obj_type>
  </object>
</page></project></Header>"""


def setup_function():
    model_store.clear()


def test_store_get_and_evict():
    model = parse_xprt(MODEL)
    first = model_store.store(model, bound=False)
    second = model_store.store(model, bound=False)
    third = model_store.store(model, bound=False)
    assert model_store.get(third).ref == third
    assert model_store.get(second).ref == second
    with pytest.raises(ToolError, match="неизвестна"):
        model_store.get(first)


def test_project_snapshot_becomes_stale_after_mutation():
    model = parse_xprt(MODEL)
    ref = model_store.store(model, bound=True, project_id=7)
    snapshot = model_store.get(ref)
    model_store.require_fresh(snapshot)
    model_store.bump_generation()
    with pytest.raises(ToolError, match="устарел"):
        model_store.require_fresh(model_store.get(ref))


def test_invalidate_project_keeps_file_snapshots():
    model = parse_xprt(MODEL)
    project_ref = model_store.store(model, bound=True, project_id=1)
    file_ref = model_store.store(model, bound=False)
    model_store.invalidate_project()
    with pytest.raises(ToolError, match="неизвестна"):
        model_store.get(project_ref)
    assert model_store.get(file_ref).ref == file_ref


def test_file_snapshot_survives_generation():
    model = parse_xprt(MODEL)
    ref = model_store.store(model, bound=False)
    model_store.bump_generation()
    snapshot = model_store.get(ref)
    model_store.require_fresh(snapshot)
