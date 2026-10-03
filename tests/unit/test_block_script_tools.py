"""Скрипт блока «Язык программирования»: чтение снимком, запись контуром."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import page_script

from _support import _ConnectingBlock, _error, _text

#: Фрагмент выгрузки с записью `Script` (форма измерена 03.10.2026):
#: объекты главной страницы — прямые дети первого `<page>`.
XPRT_WITH_SCRIPT = """<project>
  <page>
    <name>`Схема`</name>
  <object>
    <name>`LangBlock_0`</name>
    <class_name>`Язык программирования`</class_name>
    <visual_props>
      <data>
        <name>`Script`</name>
        <value>`input`#13#10`    u: double;`#13#10`output`#13#10""" \
    + """`    y: double;`#13#10#13#10`y = u;`#13#10</value>
      </data>
    </visual_props>
  </object>
  </page>
</project>"""

XPRT_WITHOUT_SCRIPT = """<project>
  <page>
    <name>`Схема`</name>
  <object>
    <name>`k_0`</name>
    <class_name>`Константа`</class_name>
    <custom_props>
      <data><name>`a`</name><mode>`1`</mode><value>`1`</value></data>
    </custom_props>
  </object>
  </page>
</project>"""


class _ScriptBlock(_ConnectingBlock):
    """Блок с читаемым классом: ответ чтения называет класс."""

    def __init__(self, name, block_id,
                 class_name="Язык программирования"):
        super().__init__(name, block_id)
        self._class_name = class_name

    @property
    def class_name(self):
        return self._class_name


class _ScriptProject:
    """Проект: блок находится, `save_xml` пишет заданный снимок (переход)."""

    def __init__(self, blocks, xprt=XPRT_WITH_SCRIPT):
        self._blocks = blocks
        self._xprt = xprt
        self.id = 7
        self.saved = 0

    def get_main_page(self):
        return self

    def find_block(self, name):
        return self._blocks.get(name)

    def save_xml(self, path):
        self.saved += 1
        Path(path).write_text(self._xprt, encoding="utf-8")


class _FakeClient:
    """COM-клиент: контуру достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _BridgeScripts:
    """Мост-подделка: отвечает маркерами с токеном, взятым из тела.

    Токен уникален на вызов и порождается инструментом — подделка вытаскивает
    его из тела, как это делает среда, и оборачивает им прежний/новый скрипт.
    """

    kind = "ok"
    old = "input u;\noutput y;\n\ny = u;"
    new = ""
    ports = "1->4"
    body = ""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body, result_path):
        from simintech_api.core.script_bridge import PageRunResult
        from simintech_api.script_probe import ContourOutcome

        type(self).body = body
        match = re.search(r'"(\w+)_OLD_BEGIN"', body)
        token = match.group(1)
        lines = ([f"{token}_OLD_BEGIN", *type(self).old.split("\n"),
                  f"{token}_OLD_END"]
                 + [f"{token}_NEW_BEGIN", *type(self).new.split("\n"),
                    f"{token}_NEW_END"]
                 + [f"ports={type(self).ports}"])
        return PageRunResult(
            outcome=ContourOutcome(kind=type(self).kind, lines=lines),
            restored_script="")


def _install(monkeypatch, tmp_path, project, bridge=None):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", project)
    if bridge is not None:
        monkeypatch.setattr(page_script, "ScriptBridge", bridge)
        bridge.body = ""  # «контур не звали» — свойство каждого теста


# ─── get_block_script ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_block_script_reads_from_snapshot(monkeypatch, tmp_path):
    """Скрипт читается снимком выгрузки — контур не запускается.

    Мост в этом тесте не подменяется: если бы инструмент пошёл контурным
    путём, он дёрнул бы `session._ensure_client()` без живого COM и упал —
    успешный ответ и есть доказательство «без запуска расчёта».
    """
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project)

    text = _text(await mcp.call_tool("get_block_script",
                                     {"block": "LangBlock_0"}))

    assert "Скрипт блока 'LangBlock_0' [Язык программирования]:" in text
    assert "y = u;" in text
    assert project.saved == 1, "снимок проекта не снят"


@pytest.mark.anyio
async def test_get_block_script_reports_absent_script(monkeypatch, tmp_path):
    """У блока без записи `Script` — «скрипта нет», а не отказ."""
    project = _ScriptProject(
        {"k_0": _ScriptBlock("k_0", 1, class_name="Константа")},
        xprt=XPRT_WITHOUT_SCRIPT)
    _install(monkeypatch, tmp_path, project)

    text = _text(await mcp.call_tool("get_block_script", {"block": "k_0"}))

    assert "скрипта нет" in text
    assert "записи `Script`" in text


@pytest.mark.anyio
async def test_get_block_script_refuses_unknown_block(monkeypatch, tmp_path):
    """Несуществующий блок — отказ до снимка."""
    project = _ScriptProject({})
    _install(monkeypatch, tmp_path, project)

    message = await _error("get_block_script", {"block": "нет_такого"})

    assert "не найден на странице" in message
    assert project.saved == 0, "снимок снят, хотя блока нет"


# ─── set_block_script ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_block_script_writes_and_returns_old(monkeypatch, tmp_path):
    """Запись: setprop + reinitlangblock, прежний скрипт — в ответе."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)
    new_script = "input\r\n    x: double;\r\noutput\r\n    y: double;\r\n"
    _BridgeScripts.new = new_script

    text = _text(await mcp.call_tool(
        "set_block_script", {"block": "LangBlock_0", "script": new_script}))

    assert "Скрипт блока 'LangBlock_0' записан. Портов: 1 → 4." in text
    assert "---- прежний скрипт ----" in text
    assert "y = u;" in text, "прежний скрипт не отдан клиенту"
    body = _BridgeScripts.body
    assert 'setprop(obj, "script"' in body
    assert "reinitlangblock(obj);" in body, "пины не пересобираются"
    assert 'getpropasstring(obj, "script")' in body


@pytest.mark.anyio
async def test_set_block_script_normalizes_lf(monkeypatch, tmp_path):
    """LF-текст нормализуется к CRLF и сравнивается после нормализации."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)
    _BridgeScripts.new = "a\r\nb\r\n"

    text = _text(await mcp.call_tool(
        "set_block_script", {"block": "LangBlock_0", "script": "a\nb\n"}))

    assert "записан" in text


@pytest.mark.anyio
async def test_set_block_script_refuses_empty_script(monkeypatch, tmp_path):
    """Пустой скрипт — отказ: он оставляет блок без портов (замер: 2 → 0)."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)

    message = await _error("set_block_script",
                           {"block": "LangBlock_0", "script": "  \n"})

    assert "пуст" in message
    assert _BridgeScripts.body == "", "контур запущен с пустым скриптом"


@pytest.mark.anyio
async def test_set_block_script_refuses_when_reread_differs(monkeypatch, tmp_path):
    """Перечитанный текст не совпал — «запись не подтверждена», не успех."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)
    _BridgeScripts.new = "совсем другой текст"

    message = await _error("set_block_script", {
        "block": "LangBlock_0", "script": "input\r\n    x: double;\r\n"})

    assert "не подтверждена" in message
    assert "не совпал" in message


@pytest.mark.anyio
async def test_set_block_script_refuses_unknown_block(monkeypatch, tmp_path):
    """Несуществующий блок — отказ до контура."""
    project = _ScriptProject({})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)
    _BridgeScripts.body = ""

    message = await _error("set_block_script",
                           {"block": "нет_такого", "script": "x = 1;"})

    assert "не найден на странице" in message
    assert _BridgeScripts.body == "", "контур запущен, хотя блока нет"


@pytest.mark.anyio
async def test_set_block_script_refuses_when_body_does_not_compile(
        monkeypatch, tmp_path):
    """Тело не собралось — отказ, проект не изменён."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)

    class _Broken(_BridgeScripts):
        kind = "not-compiled"

    monkeypatch.setattr(page_script, "ScriptBridge", _Broken)

    message = await _error("set_block_script",
                           {"block": "LangBlock_0", "script": "x = 1;"})

    assert "не собралось" in message
    assert "не изменён" in message


@pytest.mark.anyio
async def test_get_block_script_refuses_broken_snapshot(monkeypatch, tmp_path):
    """Повреждённый снимок — отказ, а не «скрипта нет».

    Парсер библиотеки повреждённый XML не глотает (`ScriptBridgeError`):
    «снимок не разобрался» и «у блока записи нет» — разные состояния, и
    выдать первое за второе значило бы соврать о модели.
    """
    project = _ScriptProject(
        {"LangBlock_0": _ScriptBlock("LangBlock_0", 1)},
        xprt="<project><page>")
    _install(monkeypatch, tmp_path, project)

    message = await _error("get_block_script", {"block": "LangBlock_0"})

    assert "не удалось" in message
    assert "скрипта нет" not in message
