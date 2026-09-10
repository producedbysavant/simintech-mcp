"""Тесты CLI-обёртки над mmain.exe (без запуска SimInTech)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from simintech_api import cli_runner  # noqa: E402
from simintech_api.cli_runner import CLIAdapter, CLIResult  # noqa: E402


@pytest.fixture()
def no_mmain(monkeypatch, tmp_path):
    """Сделать так, чтобы mmain.exe не нашёлся нигде.

    Путь по умолчанию подменяется: на машине с установленным SimInTech он
    существует, и без подмены ветка «не найден» недостижима.
    """
    monkeypatch.delenv("SIMINTECH_PATH", raising=False)
    monkeypatch.delenv("SIMINTECH", raising=False)
    monkeypatch.setattr(cli_runner, "DEFAULT_MMAIN_PATH",
                        tmp_path / "нет" / "mmain.exe")
    return tmp_path / "нет" / "mmain.exe"


def test_decode_handles_cp1251():
    """Вывод mmain.exe приходит в cp1251 — русский текст читается."""
    raw = "Ошибка расчёта".encode("cp1251")

    assert CLIAdapter._decode(raw) == "Ошибка расчёта"


def test_decode_falls_back_to_utf8():
    assert CLIAdapter._decode("OK".encode("utf-8")) == "OK"


def test_decode_survives_broken_bytes():
    """Битый вывод не должен ронять разбор."""
    assert isinstance(CLIAdapter._decode(b"\xff\xfe\xfd\x00"), str)


def test_build_cmd_adds_silentmode():
    cli = CLIAdapter(mmain_path=__file__)

    cmd = cli.build_cmd("/start", "/run")

    assert cmd[0] == str(Path(__file__).absolute())
    assert cmd[1] == "/silentmode"
    assert cmd[2:] == ["/start", "/run"]


def test_build_cmd_without_silentmode():
    cli = CLIAdapter(mmain_path=__file__, silent=False)

    assert cli.build_cmd("/exit") == [str(Path(__file__).absolute()), "/exit"]


def test_resolve_mmain_path_accepts_directory(tmp_path):
    """Если передан каталог — ищется mmain.exe внутри."""
    (tmp_path / "mmain.exe").write_bytes(b"")

    cli = CLIAdapter(mmain_path=str(tmp_path))

    assert cli.mmain_path.endswith("mmain.exe")


def test_resolve_mmain_path_uses_env(monkeypatch, tmp_path):
    """Переменная SIMINTECH_PATH имеет приоритет над путём по умолчанию."""
    (tmp_path / "mmain.exe").write_bytes(b"")
    monkeypatch.setenv("SIMINTECH_PATH", str(tmp_path))

    cli = CLIAdapter()

    assert cli.mmain_path.endswith("mmain.exe")


def test_resolve_mmain_path_raises_when_missing(no_mmain):
    with pytest.raises(FileNotFoundError, match="mmain.exe не найден"):
        CLIAdapter()


def test_run_sync_reports_missing_binary(no_mmain):
    """Несуществующий бинарник — результат с success=False, а не исключение."""
    # Создаём с валидным путём и подменяем после: конструктор сам проверяет
    # наличие mmain.exe и на отсутствующем бросил бы исключение раньше.
    cli = CLIAdapter(mmain_path=__file__)
    cli.mmain_path = str(no_mmain)

    result = cli.run_sync("/exit", timeout=5)

    assert isinstance(result, CLIResult)
    assert result.success is False
    assert "не найден" in result.message
