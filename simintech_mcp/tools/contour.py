"""Каркас контурных отказов: тело в `initialization` и разбор исхода.

Общий слой инструментов, исполняющих тело в секции `initialization` через
мост (`page_script`): прогон и единообразный отказ по исходу контура.
Выделено из `blocks.py` (issue #121), когда тот перерос 2700 строк.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.script_probe import (
    OUTCOME_ABORTED,
    OUTCOME_NOT_COMPILED,
    OUTCOME_SECTION_NOT_RUN,
    ContourOutcome,
)

from .page_script import bridge, discard_result, result_path


def run_contour_body(body: str, *, failed: str) -> ContourOutcome:
    """Выполнить тело правки контуром; отказ моста — наружу.

    `ScriptBridgeError` означает неопределённое состояние проекта: тело
    могло не установиться, а могло и отработать. Поэтому он выходит отказом,
    а не исходом — тот же контракт, что у инструментов языкового слоя.
    Контурный файл результата убирается на любом пути (`discard_result`).
    """
    path = result_path()
    try:
        run = bridge().run_page_script(body, path)
    except ScriptBridgeError as exc:
        discard_result(path)
        raise ToolError(
            f"{failed}: {exc}. Тело идёт в секцию `initialization`, поэтому "
            "расчёт должен сдвинуть модельное время: проверьте, что модель "
            "считает — неподключённый вход останавливает расчёт всей модели "
            "молча.") from exc
    discard_result(path)
    return run.outcome


def refuse_contour_failure(outcome: ContourOutcome, *, failed: str,
                           unsure: str, aborted_hint: str,
                           section_note: str = "") -> None:
    """Отказ по несделанному исходу контура — общий каркас пяти инструментов.

    Пять контурных инструментов (`disconnect_wire`, `remove_block`,
    `set_block_script`, `set_block_size`, фиты габаритов) дословно повторяли
    разбор `not-compiled`/`aborted`/`section-not-run` — копии разъезжались бы
    при первой правке текста (находка ревью). Каркас здесь один, различия —
    в подлежащих: `failed` («размер не записан») — для «не собралось» и «не
    запускалось», `unsure` («размер не подтверждён») — для обрыва,
    `aborted_hint` — что проверить после обрыва, `section_note` — уточнение
    после «тело не запускалось» (у `remove_block` оно своё).

    Прелюдии (чистка реестра по перечислению страницы) инструменты делают
    **до** вызова: у `aborted` она своя у каждого.
    """
    if outcome.kind == OUTCOME_NOT_COMPILED:
        raise ToolError(
            f"{failed}: тело не собралось (текст ошибки — в окне сообщений "
            f"редактора SimInTech; через COM он не читается). Проект не "
            f"изменён.")
    if outcome.kind == OUTCOME_ABORTED:
        detail = (f" Последняя строка тела: {outcome.lines[-1]!r}."
                  if outcome.lines else "")
        raise ToolError(
            f"{unsure}: тело оборвалось на исполнении.{detail} "
            f"{aborted_hint}")
    if outcome.kind == OUTCOME_SECTION_NOT_RUN:
        raise ToolError(
            f"{unsure}: секция `initialization` не выполнилась — тело не "
            f"запускалось{section_note}. Повторите вызов.")


def int_after_eq(text: str) -> int:
    """Число после «=» в строке тела; не разобралось — 0."""
    try:
        return int(text.split("=", 1)[1])
    except (IndexError, ValueError):
        return 0
