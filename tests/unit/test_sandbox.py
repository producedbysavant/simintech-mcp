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

    data, truncated = sandbox.read_bounded(str(path), 100)

    assert truncated is True
    assert len(data) == 100, "прочитано должно быть ровно 100 байт"


def test_read_tail_bounded_keeps_last_bytes(tmp_path):
    """Хвост журнала берётся с конца: последняя строка не теряется."""
    path = tmp_path / "log.jsonl"
    lines = [b'{"n":%d}\n' % i for i in range(100)]
    path.write_bytes(b"".join(lines))

    data, partial, mtime = sandbox.read_tail_bounded(str(path), 40)

    assert partial is True
    assert len(data) == 40
    assert data.endswith(b'{"n":99}\n')
    assert mtime > 0


def test_read_tail_bounded_marks_line_boundary_as_complete(tmp_path):
    """Смещение ровно на начале строки не требует отбрасывать первую."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(b"one\ntwo\nthree\n")

    data, partial, mtime = sandbox.read_tail_bounded(str(path), 6)

    assert partial is False
    assert data == b"three\n"
    assert mtime > 0


def test_read_tail_bounded_marks_mid_line_as_partial(tmp_path):
    """Смещение внутрь строки помечает первую строку неполной."""
    path = tmp_path / "log.jsonl"
    path.write_bytes(b"one\ntwo\nthree\n")

    data, partial, mtime = sandbox.read_tail_bounded(str(path), 5)

    assert partial is True
    assert data == b"hree\n"
    assert mtime > 0


def test_load_result_tail_returns_mtime_and_missing(tmp_path, monkeypatch):
    """Общий помощник отдаёт mtime и текст отсутствия в одном ответе."""
    monkeypatch.setenv(sandbox.OUTPUT_DIR_ENV, str(tmp_path))
    path = tmp_path / "log.jsonl"
    path.write_bytes(b"one\ntwo\n")

    data, partial, mtime, error = sandbox.load_result_tail(
        "log.jsonl", 100, "нет {path}")

    assert error == ""
    assert data == b"one\ntwo\n"
    assert partial is False
    assert mtime > 0

    _data, _partial, _mtime, error = sandbox.load_result_tail(
        "absent.jsonl", 100, "нет {path}")

    assert error == "нет absent.jsonl"


def test_read_bounded_keeps_small_file_intact(tmp_path):
    """Файл меньше предела читается целиком и не помечается обрезанным."""

    path = tmp_path / "small.bin"
    path.write_bytes(b"abc")

    assert sandbox.read_bounded(str(path), 100) == (b"abc", False)
