"""Песочница результатов: каталог и ограниченное чтение."""

from __future__ import annotations

import os
import sys

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp import sandbox


def test_default_output_dir_is_created_private(tmp_path, monkeypatch):
    """Каталога нет — он создаётся, и права не раздают его всем."""
    if sys.platform == "win32":
        pytest.skip("права POSIX на Windows не проверяются")

    target = tmp_path / "новый"
    monkeypatch.setattr(sandbox, "default_output_dir", lambda: str(target))

    root = sandbox.output_root()

    assert os.path.isdir(root)
    assert (os.stat(root).st_mode & 0o777) == 0o700


def test_default_output_dir_rejects_symlink(tmp_path, monkeypatch):
    """Подменённый ссылкой стандартный каталог не принимается.

    Каталог результатов лежит в предсказуемом месте: если его заранее создать
    символической ссылкой, `realpath` увёл бы песочницу в выбранное атакующим
    место, и ограничение стало бы фиктивным.

    Проверяется подменой `os.path.islink`, а не настоящей ссылкой: создание
    ссылок на Windows требует привилегий, а сервер работает именно там — тест
    с `skip` на Windows не проверял бы ничего на целевой платформе.
    """

    target = tmp_path / "simintech-output"
    target.mkdir()
    monkeypatch.setattr(sandbox, "default_output_dir", lambda: str(target))
    monkeypatch.setattr(os.path, "islink", lambda p: True)

    with pytest.raises(ToolError, match="символическая ссылка"):
        sandbox.output_root()


def test_default_output_dir_rejects_non_directory(tmp_path, monkeypatch):
    """Путь существует, но это файл — читать из него нечего."""

    target = tmp_path / "simintech-output"
    target.write_text("не каталог", encoding="utf-8")
    monkeypatch.setattr(sandbox, "default_output_dir", lambda: str(target))

    with pytest.raises(ToolError, match="не является каталогом"):
        sandbox.output_root()


def test_read_bounded_reads_no_more_than_limit(tmp_path):
    """Из файла читается не больше предела: проверка размера ДО чтения.

    «Прочитать целиком, потом отказать по размеру» защитой не является —
    память к моменту проверки уже израсходована. Здесь проверяется, что из
    файла действительно берётся ограниченный кусок.
    """

    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 5000)

    data, truncated = sandbox._read_bounded(str(path), 100)

    assert truncated is True
    assert len(data) == 100, "прочитано должно быть ровно 100 байт"


def test_read_bounded_keeps_small_file_intact(tmp_path):
    """Файл меньше предела читается целиком и не помечается обрезанным."""

    path = tmp_path / "small.bin"
    path.write_bytes(b"abc")

    assert sandbox._read_bounded(str(path), 100) == (b"abc", False)
