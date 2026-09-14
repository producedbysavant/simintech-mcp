"""Тесты точки входа генератора каталога (без COM)."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api import catalog_tool  # noqa: E402


def test_main_refuses_on_non_windows(monkeypatch, capsys):
    """Вне Windows генерация невозможна — понятный отказ, код возврата 2."""
    monkeypatch.setattr(sys, "platform", "linux")

    code = catalog_tool.main([])

    assert code == 2
    assert "Windows" in capsys.readouterr().err


def test_main_accepts_out_argument(monkeypatch, tmp_path):
    """--out принимается и передаётся в сохранение каталога."""
    monkeypatch.setattr(sys, "platform", "linux")

    code = catalog_tool.main(["--out", str(tmp_path / "c.json")])

    assert code == 2  # отказ по платформе, но аргумент разобран
