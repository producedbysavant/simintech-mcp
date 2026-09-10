"""Тесты разбора базы сигналов SimInTech (без COM)."""

import os
import sys
import textwrap

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api.sdb import SignalDatabase  # noqa: E402

# Реальный формат выгрузки: значения обёрнуты в бэктики
SDB_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <root>
      <database>
        <category>
          <name>`Управление`</name>
          <nametemplate>`%s`</nametemplate>
          <group>
            <name>`Регулятор`</name>
            <signals>
              <data>
                <name>`Kp`</name>
                <caption>`Пропорциональный коэффициент`</caption>
                <type>`0`</type>
                <mode>`1`</mode>
                <value>`1.5`</value>
              </data>
              <data>
                <name>`Ki`</name>
                <caption>`Интегральный коэффициент`</caption>
                <type>`0`</type>
                <mode>`1`</mode>
                <value>`0.5`</value>
              </data>
            </signals>
          </group>
        </category>
      </database>
    </root>
""")


def _write(tmp_path):
    path = tmp_path / "signals.xml"
    path.write_text(SDB_XML, encoding="utf-8")
    return path


def test_from_xml_loads_categories_and_signals(tmp_path):
    db = SignalDatabase.from_xml(_write(tmp_path))

    assert db.is_loaded is True
    assert [c["name"] for c in db.list_categories()] == ["Управление"]


def test_full_signal_name_is_group_underscore_name(tmp_path):
    """Полное имя сигнала — <группа>_<сигнал>, как его адресует SimInTech."""
    info = SignalDatabase.from_xml(_write(tmp_path)).get_signal_info("Регулятор_Kp")

    assert info is not None
    assert info["name"] == "Kp"
    assert info["group"] == "Регулятор"


def test_find_signal_by_pattern(tmp_path):
    found = SignalDatabase.from_xml(_write(tmp_path)).find_signal("K*")

    assert sorted(f["name"] for f in found) == ["Ki", "Kp"]


def test_signals_are_indexed_by_composite_key(tmp_path):
    """Составной ключ <категория>.<группа>.<имя> тоже в индексе."""
    info = SignalDatabase.from_xml(_write(tmp_path)).get_signal_info(
        "Управление.Регулятор.Kp")

    assert info is not None
    assert info["name"] == "Kp"
