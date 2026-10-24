"""Низкоуровневая запись размера блока: «Значение», перерисовка, линии.

Модуль не знает про `blocks.py` и `sizes.py`: его берут `set_block_size`,
ветка `set_block_param(nport)` и план фитов. Это разрывает скрытый цикл
`blocks` <-> `sizes`: `sizes` импортирует `blocks` для поиска блока, а
`blocks` не должен импортировать `sizes` обратно.
"""

from __future__ import annotations

from typing import Any, List, NamedTuple

from simintech_api import Block

from .. import session
from .layout import normalize_page_wires, overlap_report
from .page_script import activate_or_refuse, refuse_contour_failure, run_contour


def size_value(value: float) -> str:
    """Значение размера строкой: целые без `.0`, как их пишет среда."""
    return f"{value:g}"


def _set_size_body(block_id: int, width: float, height: float) -> str:
    """Тело записи размера: снять формулу и записать значение.

    `SetGraphBlockProp` кладёт габарит в «Формулу», а языковая пара
    `setpropformula(blk, "Width", "")` + `setprop(blk, "Width", N)` даёт
    «Значение». Очистка формулы обязательна и стоит первой: при живой
    формуле `setprop` маскируется сохранением.
    """
    return "\n".join(
        size_pair_lines(block_id, "Width", width)
        + size_pair_lines(block_id, "Height", height)) + "\n"


def size_pair_lines(block_id: int, prop: str, value: float) -> List[str]:
    """Строки тела контура для одной оси: снять формулу и записать значение.

    Общий эмиттер `set_block_size` и плана фитов (`fits._size_fixes_body`):
    рецепт обязан жить в одной точке, иначе инструменты начнут оставлять
    разный `.xprt` для одной операции.
    """
    return [
        f"blk = {block_id};",
        f'setpropformula(blk, "{prop}", "");',
        f'setprop(blk, "{prop}", {size_value(value)});',
    ]


class SizeWriteResult(NamedTuple):
    """Итог записи габарита: было, стало, хвост нормализации, наложения."""

    before: tuple[float, float]
    after: tuple[float, float]
    tail_note: str
    overlaps: str


def write_block_size(page: Any, target: Block, width: float, height: float,
                     *, action: str = "размер не записан") -> SizeWriteResult:
    """Записать обе оси в «Значение», перерисовать и трассировать линии.

    Одна точка рецепта для `set_block_size` и ветки `set_block_param(nport)`:
    страница активируется, тело контура снимает формулу и пишет значение,
    затем идут перерисовка и `NormalizeWire`. Сбой нормализации не отменяет
    уже записанный размер, а называется примечанием.

    Наложения считаются по изменённому блоку: рост высоты легко накрывает
    соседа, и это должно быть видно в ответе, а не только после отдельного
    `check_model_layout`.
    """
    before = target.get_size()
    activate_or_refuse(page, action=action)
    outcome, _restored = run_contour(
        _set_size_body(target.id, float(width), float(height)),
        failed="записать размер блока не удалось")
    refuse_contour_failure(
        outcome, failed=action, unsure="размер не подтверждён",
        aborted_hint=("Проверьте габарит в GUI (`get_size` прочитает "
                      "фактический)."))
    session.ensure_project().repaint()
    tail_note = ""
    try:
        normalize_page_wires(page)
    except Exception:                                         # noqa: BLE001
        tail_note = (" Линии не трассированы: перечислить их не удалось "
                     "(сбой чтения через COM), проверьте схему.")
    after = target.get_size()
    try:
        name = target.get_name()
    except Exception:                                         # noqa: BLE001
        name = f"id={target.id}"
    try:
        overlaps = overlap_report(page, {name})
    except Exception:                                         # noqa: BLE001
        overlaps = ""
    return SizeWriteResult(before=before, after=after,
                           tail_note=tail_note, overlaps=overlaps)
