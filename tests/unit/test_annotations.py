"""Аннотации: текст подписей и ячейки памяти из снимка `.xprt`."""

from __future__ import annotations

from pathlib import Path

import pytest

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import annotations as ann

from _support import _error, _text


XPRT_WITH_ANNOTATIONS = """<project>
  <page>
    <name>`Схема`</name>
    <object>
      <name>`TextLabel7`</name>
      <class_name>`TextLabel`</class_name>
      <points><value>`[(120 , 40)]`</value></points>
      <visual_props>
        <data>
          <name>`Text`</name>
          <value>`Первая строка`#13#10`вторая строка`#13#10</value>
        </data>
      </visual_props>
    </object>
    <object>
      <name>`TextLabel3`</name>
      <class_name>`constLabel`</class_name>
      <points><value>`[(0 , 0)]`</value></points>
      <visual_props>
        <data><name>`Text`</name><value>`k=`#13#10</value></data>
      </visual_props>
    </object>
    <object>
      <name>`RTFComment10`</name>
      <class_name>`Комментарий`</class_name>
      <points><value>`[(1 , 2)]`</value></points>
      <visual_props>
        <data><name>`LabelText`</name><value>`О примере`</value></data>
      </visual_props>
    </object>
    <object>
      <name>`Sub_Control`</name>
      <class_name>`Субмодель`</class_name>
      <page>
        <object>
          <name>`TextLabel9`</name>
          <class_name>`TextLabel`</class_name>
          <points><value>`[(10 , 20)]`</value></points>
          <visual_props>
            <data><name>`Text`</name><value>`Внутри`#13#10</value></data>
          </visual_props>
        </object>
        <object>
          <name>`ToMem_4`</name>
          <class_name>`В память`</class_name>
          <visual_props>
            <data><name>`PortNames`</name><value>`#RA402`</value></data>
          </visual_props>
        </object>
        <object>
          <name>`FromMem_1`</name>
          <class_name>`Из памяти`</class_name>
          <visual_props>
            <data><name>`PortNames`</name>
            <value>`{state_flags_name}`</value></data>
          </visual_props>
        </object>
      </page>
    </object>
    <object>
      <name>`Block1`</name>
      <class_name>`Константа`</class_name>
      <blockimage>
        <page>
          <object>
            <name>`TextLabel`</name>
            <class_name>`TextLabel`</class_name>
            <visual_props>
              <data><name>`Text`</name><value>`ICON`</value></data>
            </visual_props>
          </object>
        </page>
      </blockimage>
    </object>
  </page>
</project>"""


class _XmlProject:
    """Проект, чей `save_xml` пишет заданный снимок."""

    def __init__(self, xml: str):
        self._xml = xml

    def save_xml(self, path: str) -> None:
        Path(path).write_text(self._xml, encoding="utf-8")


class _NoFileProject:
    """`save_xml` сообщает об успехе, файла не создаёт."""

    def save_xml(self, path: str) -> None:
        return None


def _install(monkeypatch, xml: str) -> None:
    monkeypatch.setattr(session, "_project", _XmlProject(xml))
    monkeypatch.setattr(session, "_client", None)


@pytest.mark.anyio
async def test_list_annotations_reads_labels_and_cells(monkeypatch):
    """Text, LabelText и PortNames читаются; blockimage не считается."""
    _install(monkeypatch, XPRT_WITH_ANNOTATIONS)

    text = _text(await mcp.call_tool("list_annotations", {}))

    assert "Подписи: 4." in text
    assert "TextLabel7 [TextLabel] (120, 40):" in text
    assert "Первая строка" in text and "вторая строка" in text
    assert "RTFComment10 [Комментарий]" in text and "О примере" in text
    assert "TextLabel3 [constLabel]" in text and "k=" in text
    assert "Главная / Sub_Control" in text
    assert "TextLabel9 [TextLabel] (10, 20):" in text and "Внутри" in text
    assert "ToMem_4 [В память] = #RA402" in text
    assert "FromMem_1 [Из памяти] = {state_flags_name}" in text
    assert "ICON" not in text


@pytest.mark.anyio
async def test_list_annotations_finds_project_under_header(monkeypatch):
    """Вендорская выгрузка оборачивает проект в Header: страницы находятся."""
    _install(monkeypatch, """<Header><project><page>
      <name>`Схема`</name>
      <object><name>`HeaderLabel`</name>
      <class_name>`TextLabel`</class_name>
      <points><value>`[(5 , 6)]`</value></points>
      <visual_props>
        <data><name>`Text`</name><value>`Под Header`</value></data>
      </visual_props></object>
    </page></project></Header>""")

    text = _text(await mcp.call_tool("list_annotations", {}))

    assert "HeaderLabel [TextLabel] (5, 6):" in text
    assert "Под Header" in text


@pytest.mark.anyio
async def test_list_annotations_empty_model_is_not_an_error(monkeypatch):
    """Модель без подписей и ячеек: честный пустой ответ, а не отказ."""
    _install(monkeypatch, """<project><page><name>`Схема`</name>
      <object><name>`k_0`</name><class_name>`Константа`</class_name></object>
    </page></project>""")

    text = _text(await mcp.call_tool("list_annotations", {}))

    assert "Подписей на страницах нет." in text
    assert "Ячеек памяти на страницах нет." in text


@pytest.mark.anyio
async def test_list_annotations_refuses_broken_snapshot(monkeypatch):
    """Повреждённый XML - отказ, а не «подписей нет»."""
    _install(monkeypatch, "<project><page>")

    message = await _error("list_annotations", {})

    assert "не разбирается как XML" in message


@pytest.mark.anyio
async def test_list_annotations_names_missing_text_record(monkeypatch):
    """У подписи нет записи Text: названо состоянием, а не пустым текстом."""
    _install(monkeypatch, """<project><page><name>`Схема`</name>
      <object><name>`TextLabel1`</name>
      <class_name>`TextLabel`</class_name>
      <visual_props></visual_props></object>
    </page></project>""")

    text = _text(await mcp.call_tool("list_annotations", {}))

    assert "нет записи Text" in text


@pytest.mark.anyio
async def test_list_annotations_refuses_missing_snapshot(monkeypatch):
    """SaveProjectXML не создал файл - отказ с рецептом, а не пустой ответ."""
    monkeypatch.setattr(session, "_project", _NoFileProject())
    monkeypatch.setattr(session, "_client", None)

    message = await _error("list_annotations", {})

    assert "выгрузка проекта не создана" in message


def test_annotation_lines_report_truncation():
    """Предел записей назван полным счётчиком."""
    records = [
        ann.Annotation(page="Главная", name=f"TextLabel{i}",
                       class_name="TextLabel", point=None, text="x",
                       error="")
        for i in range(ann.MAX_REPORTED + 1)
    ]

    lines = ann._annotation_lines(records, ann.MAX_REPORTED)

    assert any("и ещё 1 (всего 51)" in line for line in lines)


def test_clip_marks_long_text():
    """Длинный текст обрезается с пометкой, короткий не трогается."""
    short, short_cut = ann._clip("x" * ann.MAX_TEXT_CHARS)
    long, long_cut = ann._clip("x" * (ann.MAX_TEXT_CHARS + 1))

    assert short_cut is False and len(short) == ann.MAX_TEXT_CHARS
    assert long_cut is True and long.endswith("…")
