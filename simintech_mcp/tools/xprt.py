"""Снимок проекта в `.xprt` текстом: чтение без запуска расчёта.

`SaveProjectXML` пишет XML-выгрузку проекта и не запускает модель, в отличие
от контурного чтения (`getpropasstring`), которое перезапускает расчёт и
сдвигает модельное время. Снимком читаются записи, которых COM не отдаёт:
скрипт блока (`get_block_script`) и `visual_props` подписей
(`list_annotations`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError
from simintech_api.catalog import decode_xprt


def snapshot_xprt(project: Any) -> str:
    """Снимок проекта в `.xprt` как текст, без запуска расчёта.

    Каталог временный: файл уходит вместе с ним. Имя в сообщении об отказе -
    только базовое: полный путь временного каталога клиенту ничего не даёт, а
    ошибка чтения важна.

    `SaveProjectXML` сообщает об успехе, но файла может не быть (сбой среды).
    Это отказ, а не пустой снимок: «записи нет» и «снимок не создан» - разные
    состояния, и выдавать одно за другое нельзя.
    """
    with tempfile.TemporaryDirectory(
            prefix="simintech-xprt-",
            ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "project.xprt"
        project.save_xml(str(path))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ToolError(
                f"выгрузка проекта не создана: SaveProjectXML сообщил об "
                f"успехе, но файла {path.name} нет ({exc}). Снимок - путь "
                f"чтения без запуска расчёта, и продолжать без него нельзя."
            ) from exc
    return decode_xprt(raw)
