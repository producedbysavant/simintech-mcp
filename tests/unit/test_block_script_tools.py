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

    Мост в этом тесте не подменяется: контурный путь упал бы на первом же
    COM-вызове моста (у фейкового клиента нет `call`) — успешный ответ и есть
    доказательство «без запуска расчёта».
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
    # Порядок обязателен: прежний скрипт читается ДО записи, новый — ПОСЛЕ;
    # иначе в ответ уйдёт новый текст как «прежний», а подтверждение записи
    # станет тавтологией (находка ревью тестов).
    assert (body.index('old_script = getpropasstring')
            < body.index('setprop(obj, "script"'))
    assert (body.index('new_script = getpropasstring')
            > body.index('setprop(obj, "script"'))


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


# ─── Литерал и разбор ответа: гейты, которые нельзя обойти текстом скрипта ──


def test_runtime_literal_uses_codes_for_line_breaks_only():
    """Перевод строки — только `chr(13) + chr(10)`; сырых CR/LF в литерале нет.

    Находка ревью: вход нормализуется к CRLF, а литерал резался по `\\n` —
    в куски попадал сырой `\\r`, и собранный скрипт страницы (библиотечный
    `build_page_script` режет тело `splitlines()`, в том числе по `\\r`) рвался
    посреди литерала. Этот тест закрывает форму литерала напрямую.
    """
    from simintech_mcp.tools.blocks import _runtime_literal

    literal = _runtime_literal("a\r\nb\r\n")

    assert literal == '"a" + chr(13) + chr(10) + "b" + chr(13) + chr(10)'
    assert "\r" not in literal and "\n" not in literal


def test_runtime_literal_escapes_quotes_and_handles_empty():
    """Кавычка — `chr(34)`; пустой текст — пустая строка."""
    from simintech_mcp.tools.blocks import _runtime_literal

    assert _runtime_literal('say "hi"') == '"say " + chr(34) + "hi" + chr(34)'
    assert _runtime_literal("") == '""'


@pytest.mark.anyio
async def test_set_block_script_body_escapes_literal(monkeypatch, tmp_path):
    """Тело инструмента несёт литерал без сырых переводов строк.

    Подделка моста (`_BridgeScripts`) ответы не выводит из литерала, поэтому
    форма проверяется по телу напрямую — иначе мутация «литерал собран
    неверно» осталась бы незамеченной (находка ревью тестов).
    """
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)
    _BridgeScripts.new = "x = 1;\r\ny = 2;\r\n"

    await mcp.call_tool("set_block_script",
                        {"block": "LangBlock_0", "script": "x = 1;\r\ny = 2;\r\n"})

    body = _BridgeScripts.body
    assert '"x = 1;" + chr(13) + chr(10) + "y = 2;" + chr(13) + chr(10)' in body
    assert '"x = 1;\r' not in body, "сырой CR попал внутрь литерала"


def test_parse_script_reply_ignores_sentinels_inside_script():
    """`err=no-block` и `ports=…` внутри текста скрипта — не ответ тела.

    Находка ревью: сентинелы искались по всему выводу, и комментарий
    `// ports=9->9` в прежнем скрипте перебивал настоящие числа, а строка
    ровно `err=no-block` давала ложный «блок не найден» после записи.
    """
    from simintech_mcp.tools.blocks import _parse_script_reply

    token = "BLKabc"
    # Строка ровно `err=no-block` — не комментарий: именно так сентинел
    # подделывался в находке ревью (строку скрипта не спутать с ответом тела).
    lines = [f"{token}_OLD_BEGIN", "err=no-block", "// ports=9->9",
             f"{token}_OLD_END", f"{token}_NEW_BEGIN", "y = u;",
             f"{token}_NEW_END", "ports=1->4"]

    reply = _parse_script_reply(lines, token)

    assert reply.kind == "written"
    assert reply.ports_before == 1 and reply.ports_after == 4
    assert "err=no-block" in reply.old, "текст прежнего скрипта потерян"


def test_parse_script_reply_no_block_only_without_markers():
    """Сентинел `err=no-block` без маркеров — «блок не найден»."""
    from simintech_mcp.tools.blocks import _parse_script_reply

    assert _parse_script_reply(["err=no-block"], "BLKabc").kind == "no-block"
    assert _parse_script_reply(["мусор"], "BLKabc").kind == "unknown"


@pytest.mark.anyio
async def test_set_block_script_refuses_ctx_markers(monkeypatch, tmp_path):
    """Текст со служебными маркерами контура отвергается до записи.

    Текст скрипта возвращается ответом через файл результата контура, и
    строка `CTX_END` в нём рвёт разбор исхода (находка ревью).
    """
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)

    message = await _error("set_block_script", {
        "block": "LangBlock_0", "script": 's = "CTX_END";'})

    assert "служебные маркеры" in message
    assert _BridgeScripts.body == "", "контур запущен с текстом-маркером"


@pytest.mark.anyio
async def test_set_block_script_does_not_confirm_after_abort(monkeypatch, tmp_path):
    """Обрыв тела — «запись не подтверждена», а не успех."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)

    class _Aborted(_BridgeScripts):
        kind = "aborted"
        new = "x = 1;\r\n"

    monkeypatch.setattr(page_script, "ScriptBridge", _Aborted)

    message = await _error("set_block_script",
                           {"block": "LangBlock_0", "script": "x = 1;"})

    assert "не подтверждена" in message
    assert "оборвалось" in message


@pytest.mark.anyio
async def test_set_block_script_unknown_reply(monkeypatch, tmp_path):
    """Ответ без маркеров и без сентинелов — «не оставило распознаваемого»."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)

    class _Mute(_BridgeScripts):
        old = ""
        new = ""

        def run_page_script(self, body, result_path):
            from simintech_api.core.script_bridge import PageRunResult
            from simintech_api.script_probe import ContourOutcome
            type(self).body = body
            return PageRunResult(
                outcome=ContourOutcome(kind="ok", lines=["нечто"]),
                restored_script="")

    monkeypatch.setattr(page_script, "ScriptBridge", _Mute)

    message = await _error("set_block_script",
                           {"block": "LangBlock_0", "script": "x = 1;"})

    assert "не оставило распознаваемого ответа" in message


@pytest.mark.anyio
async def test_set_block_script_reports_empty_old_script(monkeypatch, tmp_path):
    """Пустой прежний скрипт назван словами, а не пустым блоком."""
    project = _ScriptProject({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project, _BridgeScripts)
    _BridgeScripts.old = ""
    _BridgeScripts.new = "x = 1;"

    text = _text(await mcp.call_tool("set_block_script",
                                     {"block": "LangBlock_0", "script": "x = 1;"}))

    assert "Прежний скрипт был пуст" in text


@pytest.mark.anyio
async def test_get_block_script_survives_unreadable_class(monkeypatch, tmp_path):
    """Нечитаемый класс — ответ без класса, а не отказ."""

    class _NoClass(_ScriptBlock):
        @property
        def class_name(self):
            raise RuntimeError("COM: класс не читается")

    project = _ScriptProject({"LangBlock_0": _NoClass("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project)

    text = _text(await mcp.call_tool("get_block_script",
                                     {"block": "LangBlock_0"}))

    assert "Скрипт блока 'LangBlock_0':" in text
    assert "y = u;" in text


@pytest.mark.anyio
async def test_get_block_script_refuses_when_snapshot_missing(monkeypatch, tmp_path):
    """Снимок не создан (`SaveProjectXML` «успех» без файла) — отказ."""

    class _NoFile(_ScriptProject):
        def save_xml(self, path):
            self.saved += 1  # файла нет: так выглядит «успех без файла»

    project = _NoFile({"LangBlock_0": _ScriptBlock("LangBlock_0", 1)})
    _install(monkeypatch, tmp_path, project)

    message = await _error("get_block_script", {"block": "LangBlock_0"})

    assert "выгрузка проекта не создана" in message
