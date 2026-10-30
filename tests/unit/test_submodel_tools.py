"""Просмотр субмоделей: снимок IR, библиотечный файл, явный файл."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_mcp import model_store  # noqa: E402
from simintech_mcp.server import mcp  # noqa: E402
from simintech_mcp.tools.submodel import resolve_library_file  # noqa: E402

from _support import _text  # noqa: E402

PORT_IN_BLOCK = """  <object>
    <name>`PI`</name>
    <class_name>`Порт входа`</class_name>
    <obj_type>`103`</obj_type>
    <ports><port><name>`PWM`</name><mode>`0`</mode></port></ports>
    <visual_props>
      <data><name>`PortNames`</name><value>`PWM`</value></data>
    </visual_props>
  </object>
"""

PORT_OUT_BLOCK = """  <object>
    <name>`PO`</name>
    <class_name>`Порт выхода`</class_name>
    <obj_type>`103`</obj_type>
    <ports><port><name>`A`</name><mode>`1`</mode></port></ports>
    <visual_props>
      <data><name>`PortNames`</name><value>`A`</value></data>
    </visual_props>
  </object>
"""

GAIN_BLOCK = """  <object>
    <name>`G`</name>
    <class_name>`Усилитель`</class_name>
    <obj_type>`100`</obj_type>
    <ports>
      <port><name>`outport`</name><mode>`1`</mode></port>
      <port><name>`inport`</name><mode>`0`</mode></port>
    </ports>
  </object>
"""

#: Файл-субмодель: главная страница и есть её содержимое (так устроен
#: библиотечный `.prt`, открытый средой как проект).
INNER_PAGE = ("<Header><project><page>\n"
              + PORT_IN_BLOCK + PORT_OUT_BLOCK + GAIN_BLOCK
              + "</page></project></Header>")

#: Проект, в котором субмодель SM несёт ту же страницу внутри.
SUBMODEL = ("<Header><project><page>\n"
            "  <object>\n"
            "    <name>`SM`</name>\n"
            "    <class_name>`ЭП - ИНВ - Инвертор`</class_name>\n"
            "    <obj_type>`102`</obj_type>\n"
            "    <ports>\n"
            "      <port><name>`PWM`</name><mode>`0`</mode></port>\n"
            "      <port><name>`A`</name><mode>`2`</mode></port>\n"
            "    </ports>\n"
            "    <page>\n"
            + PORT_IN_BLOCK + PORT_OUT_BLOCK + GAIN_BLOCK
            + "    </page>\n"
            "  </object>\n"
            "</page></project></Header>")

EXTERNAL = """<Header><project><page>
  <object>
    <name>`U`</name>
    <class_name>`ЭЦД2 - Источник напряжения`</class_name>
    <obj_type>`102`</obj_type>
    <filename>`ecd/ist/x.prt`</filename>
    <ports><port><name>`us`</name><mode>`0`</mode></port></ports>
  </object>
</page></project></Header>"""


def setup_function():
    model_store.clear()


def test_library_file_resolves_under_install_root(tmp_path, monkeypatch):
    """Относительный filename ищется в bin/include_mvtu каталога поставки."""
    target = tmp_path / "bin" / "include_mvtu" / "ecd" / "ist" / "x.prt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"prt")
    monkeypatch.setenv("SIMINTECH_PATH", str(tmp_path))
    assert resolve_library_file("ecd\\ist\\x.prt") == str(target)


def test_library_file_rejects_traversal(tmp_path, monkeypatch):
    """`..` в filename не выводит поиск за пределы поставки."""
    outside = tmp_path / "secret.prt"
    outside.write_bytes(b"prt")
    monkeypatch.setenv("SIMINTECH_PATH", str(tmp_path))
    with pytest.raises(Exception) as exc:
        resolve_library_file("../secret.prt")
    assert "`..`" in str(exc.value)


def test_library_file_reports_tried_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMINTECH_PATH", str(tmp_path))
    with pytest.raises(Exception) as exc:
        resolve_library_file("ecd\\ist\\missing.prt")
    text = str(exc.value)
    assert "include_mvtu" in text and "missing.prt" in text


@pytest.mark.anyio
async def test_inspect_inner_submodel_from_snapshot(monkeypatch, tmp_path):
    """Внутренняя субмодель отдаётся из снимка без COM."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.xprt").write_text(SUBMODEL, encoding="utf-8")
    loaded = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.xprt"})))

    result = json.loads(_text(await mcp.call_tool(
        "inspect_submodel",
        {"model_ref": loaded["model_ref"], "address": "main::SM"})))
    assert result["source"] == "snapshot"
    names = {item["address"] for item in result["objects"]}
    assert {"main/SM::PI", "main/SM::PO", "main/SM::G"} <= names
    assert result["ports"] == [
        {"index": 0, "name": "PWM", "direction": "in"},
        {"index": 1, "name": "A", "direction": "undirected"},
    ]
    assert result["wires"] == []


@pytest.mark.anyio
async def test_inspect_external_submodel_without_file_refuses(
        monkeypatch, tmp_path):
    """Внешняя субмодель без файла поставки: отказ называет, что искали."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("SIMINTECH_PATH", str(tmp_path / "none"))
    (tmp_path / "m.xprt").write_text(EXTERNAL, encoding="utf-8")
    loaded = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.xprt"})))
    with pytest.raises(Exception) as exc:
        await mcp.call_tool(
            "inspect_submodel",
            {"model_ref": loaded["model_ref"], "address": "main::U"})
    assert "include_mvtu" in str(exc.value)


@pytest.mark.anyio
async def test_inspect_rejects_non_submodel(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.xprt").write_text(SUBMODEL, encoding="utf-8")
    loaded = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.xprt"})))
    with pytest.raises(Exception) as exc:
        await mcp.call_tool(
            "inspect_submodel",
            {"model_ref": loaded["model_ref"], "address": "main/SM::G"})
    assert "субмодель" in str(exc.value)


@pytest.mark.anyio
async def test_inspect_requires_address_or_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    with pytest.raises(Exception) as exc:
        await mcp.call_tool("inspect_submodel", {})
    assert "path" in str(exc.value) and "address" in str(exc.value)


@pytest.mark.anyio
async def test_inspect_xprt_file_reads_without_com(tmp_path):
    """`.xprt` разбирается напрямую, без среды."""
    target = tmp_path / "sub.xprt"
    target.write_text(INNER_PAGE, encoding="utf-8")
    result = json.loads(_text(await mcp.call_tool(
        "inspect_submodel", {"path": str(target)})))
    assert result["source"] == "file"
    assert result["filename"] == "sub.xprt"
    classes = {item["class_name"] for item in result["objects"]}
    assert {"Порт входа", "Порт выхода", "Усилитель"} <= classes


@pytest.mark.anyio
async def test_inspect_file_requires_absolute_path(tmp_path):
    with pytest.raises(Exception) as exc:
        await mcp.call_tool("inspect_submodel", {"path": "sub.xprt"})
    assert "абсолютный" in str(exc.value)
