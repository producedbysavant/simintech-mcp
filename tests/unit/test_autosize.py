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
    assert autosize.script_state(_Block(error=True)) == "foreign"


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
