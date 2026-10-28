"""Снимок проекта в `.xprt` текстом: чтение без запуска расчёта.

`SaveProjectXML` пишет XML-выгрузку проекта и не запускает модель, в отличие
от контурного чтения (`getpropasstring`), которое перезапускает расчёт и
сдвигает модельное время. Снимком читаются записи, которых COM не отдаёт:
скрипт блока (`get_block_script`) и `visual_props` подписей
(`list_annotations`).
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from fastmcp.exceptions import ToolError
from simintech_api.catalog import decode_xprt


@contextmanager
def xprt_snapshot_path(project: Any) -> Generator[Path, None, None]:
    """Путь к временной выгрузке `.xprt`; файл живёт до конца контекста.

    Потоковый разбор читает этот путь, а не строку на десятки мегабайт.
    `SaveProjectXML` сообщает об успехе, но файла может не быть (сбой среды):
    это отказ, а не пустой снимок.
    """
    with tempfile.TemporaryDirectory(
            prefix="simintech-xprt-",
            ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "project.xprt"
        project.save_xml(str(path))
        if not path.is_file():
            raise ToolError(
                f"выгрузка проекта не создана: SaveProjectXML сообщил об "
                f"успехе, но файла {path.name} нет. Снимок - путь чтения "
                f"без запуска расчёта, и продолжать без него нельзя.")
        yield path


def snapshot_xprt(project: Any) -> str:
    """Снимок проекта в `.xprt` как текст, без запуска расчёта."""
    with xprt_snapshot_path(project) as path:
        raw = path.read_bytes()
    return decode_xprt(raw)
