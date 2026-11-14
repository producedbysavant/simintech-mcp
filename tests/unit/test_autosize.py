"""Скрипт саморазмера: текст, состояние слота и разбор установки."""

from __future__ import annotations

from simintech_mcp.tools import autosize


class _Block:
    """Блок с читаемым/нечитаемым слотом `OnInitScript`."""

    def __init__(self, script: str | None = "", *, error: bool = False,
                 name: str = "b_0", block_id: int = 7):
        self._script = script
        self._error = error
        self._name = name
        self.id = block_id

    def get_property(self, name):
        if self._error:
            raise OSError("COM недоступен")
        if name != "OnInitScript":
            raise AssertionError(f"спрошено {name!r}")
        return self._script

    def get_name(self):
        return self._name


def test_script_text_only_for_rule_classes():
    """Скрипт есть у классов правила и нет у прочих."""
    assert autosize.script_text("Порт входа") is not None
    assert autosize.script_text("Порт выхода") is not None
    assert autosize.script_text("Субмодель") is not None
    assert autosize.script_text("Оператор И") is not None
    assert autosize.script_text("Константа") is None
    assert autosize.script_text("В память") is None


def test_scripts_use_initialization_and_self():
    """Форма, проверенная живьём: секция `initialization` и `self.Height`."""
    port = autosize.script_text("Порт входа")
    rows = autosize.script_text("Субмодель")
    for text in (port, rows):
        assert autosize.MARKER in text
        assert "initialization" in text
        assert "self.Height" in text
        assert "beforecompile" not in text
    assert "self.PortNames" in port
    assert "getportinfo" in rows
    assert "mod" in rows and "div" in rows


def test_script_state_never_overwrites_foreign():
    """Пустой слот ставим, свой узнаём, чужой и нечитаемый не трогаем."""
    assert autosize.script_state(_Block("")) == "empty"
    assert autosize.script_state(_Block("   \r\n")) == "empty"
    assert autosize.script_state(
        _Block(autosize.MARKER + "\ninitialization\nend;")) == "ours"
    assert autosize.script_state(_Block("initialization\nend;")) == "foreign"
    assert autosize.script_state(_Block(error=True)) == "unreadable"


def test_install_body_writes_only_rule_blocks():
    """Тело контура адресует блоки по id и проверяет пустоту слота."""
    body = autosize.install_body(
        [(_Block(name="p_1", block_id=11), "Порт входа"),
         (_Block(name="k_1", block_id=12), "Константа")],
        "TOK")
    assert "obj = 11;" in body
    assert "obj = 12;" not in body
    assert "OnInitScript" in body
    assert "TOK_SET p_1" in body
    assert "TOK_BUSY p_1" in body


def test_parse_install_reads_marks():
    """Строки контура разбираются по маркерам, чужие строки игнорируются."""
    result = autosize.parse_install(
        ["TOK_SET a", "шум", "TOK_BUSY b", "TOK_SET c"], "TOK")
    assert result.installed == ["a", "c"]
    assert result.skipped == ["b"]


class _Ports:
    """Блок правила с портами и слотом, который меняется по записи."""

    def __init__(self, name, block_id, *, script="", in_ports=3, out_ports=2,
                 height=48.0):
        self._name = name
        self.id = block_id
        self._script = script
        self._in = in_ports
        self._out = out_ports
        self._height = height

    def get_name(self):
        return self._name

    def get_property(self, name):
        if name != "OnInitScript":
            raise AssertionError(name)
        return self._script

    def get_size(self):
        return (48.0, self._height)

    def get_in_port_count(self):
        return self._in

    def get_port_count(self):
        return self._in + self._out

    def get_out_port(self, index):
        from simintech_api.exceptions import PortError

        if index < self._out:
            return object()
        raise PortError("нет выходного порта")


class _Outcome:
    def __init__(self, lines):
        self.kind = "ok"
        self.lines = lines


def _run_stub(monkeypatch, blocks):
    """Подменить контур: как среда, исполнить ветку SET или BUSY.

    Тело несёт обе ветки, поэтому подделка решает по состоянию слота в
    момент вызова, ровно как `if getpropasstring(...) = ""`.
    """
    import re

    calls = {}

    def fake_contour(body, *, failed):
        calls["body"] = body
        found = re.search(r'"([A-Za-z0-9]+)_SET ', body)
        token = found.group(1) if found else "TOK"
        lines = []
        for raw_id in re.findall(r"obj = (\d+);", body):
            block = next((b for b in blocks.values()
                          if str(b.id) == raw_id), None)
            if block is None:
                continue
            if block._script.strip() == "":
                lines.append(f"{token}_SET {block.get_name()}")
                block._script = autosize.script_text("Субмодель")
            else:
                lines.append(f"{token}_BUSY {block.get_name()}")
        return _Outcome(lines), ""

    monkeypatch.setattr(autosize, "run_contour", fake_contour)
    monkeypatch.setattr(autosize, "activate_or_refuse",
                        lambda *a, **k: None)
    monkeypatch.setattr(autosize, "refuse_contour_failure",
                        lambda *a, **k: None)
    return calls


def test_install_scripts_sizes_foreign_blocks_too(monkeypatch):
    """Чужой слот не перетирается, но высоту блок получает: это обещание."""
    empty = _Ports("sub_a", 1)
    foreign = _Ports("sub_b", 2, script="initialization\nend;")
    calls = _run_stub(monkeypatch, {"sub_a": empty, "sub_b": foreign})

    result = autosize.install_scripts(
        object(), [(empty, "Субмодель"), (foreign, "Субмодель")],
        where="тест")

    assert result.installed == ["sub_a"]
    assert result.skipped == ["sub_b"]
    assert any("sub_a" in item for item in result.sized)
    assert any("sub_b" in item for item in result.sized)
    body = calls["body"]
    assert f"obj = {empty.id};" in body
    assert f"obj = {foreign.id};" not in body
    # Высота по сетке: 3 входа / 2 выхода -> 96.
    assert "setprop(blk, \"Height\", 96);" in body


def test_install_scripts_skips_contour_when_nothing_to_do(monkeypatch):
    """Всё уже сделано: контур не запускается вовсе."""
    ours = _Ports("sub_a", 1,
                  script=autosize.script_text("Субмодель"), height=96.0)
    calls = _run_stub(monkeypatch, {"sub_a": ours})

    result = autosize.install_scripts(
        object(), [(ours, "Субмодель")], where="тест")

    assert result.installed == []
    assert result.sized == ()
    assert "body" not in calls


def test_install_scripts_names_unreadable_slot(monkeypatch):
    """Нечитаемый слот не выдаётся за чужой скрипт."""
    class _Bad(_Ports):
        def get_property(self, name):
            raise OSError("COM недоступен")

    bad = _Bad("sub_c", 3)
    _run_stub(monkeypatch, {})

    result = autosize.install_scripts(
        object(), [(bad, "Субмодель")], where="тест")

    assert result.unreadable == ("sub_c",)
    assert result.skipped == []
