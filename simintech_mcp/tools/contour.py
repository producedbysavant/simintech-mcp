"""Каркас контурных отказов: тело в `initialization` и разбор исхода.

Общий слой инструментов, исполняющих тело в секции `initialization` через
мост (`page_script`): прогон и единообразный отказ по исходу контура.
Выделено из `blocks.py` (issue #121), когда тот перерос 2700 строк.
"""

from __future__ import annotations

from typing import Any, List, Optional

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


def activate_or_refuse(page: Any, *, action: str,
                       where: Optional[str] = None) -> None:
    """Сделать страницу правки текущей или отказать — до контура.

    Скрипт ставится в текущую страницу (`SetPageScript` → `GetCurentPage`),
    и адресация по id ищется на ней: без активации запись ушла бы мимо блока
    или в блок-двойник с тем же id (находка ревью PR #94). Не удалось
    активировать — **отказ**, а не запись вслепую: промах выглядел бы как
    «среда не приняла запись» и уводил бы диагноз в сторону среды.

    Блок повторялся у трёх инструментов (ветвь, размер, габариты фитов) с
    дословным обоснованием и разными текстами — тексты сведены сюда
    (находка ревью PR #122): `action` называет действие («ветвь не
    создана»), `where` — страницу, когда её имя известно (у фитов).
    """
    try:
        page.activate()
    except Exception as exc:                                  # noqa: BLE001
        named = f" «{where}»" if where else ""
        raise ToolError(
            f"{action}: страницу{named} не удалось сделать активной "
            f"({type(exc).__name__}: {exc}) — запись по id могла бы уйти в "
            f"блок другой страницы. Проект не изменён.") from exc


def return_main_active(main: Any, lines: List[str]) -> None:
    """Вернуть главную страницу активной, назвав провал возврата.

    Обход фитов активирует каждую страницу, а следующая контурная операция
    (выгрузка, снимок) снимает ИМЕННО активную (живой случай 06.10.2026).
    Молчаливый `pass` здесь оставлял бы субмодель текущей и инструмент
    отчитывался бы успехом (находка ревью PR #122): не получилось вернуть —
    это примечание, а не отказ (правка-то сделана), но и не тишина.
    """
    try:
        main.activate()
    except Exception as exc:                                  # noqa: BLE001
        lines.append(f"главную не удалось вернуть активной "
                     f"({type(exc).__name__}: {exc}) — следующая контурная "
                     f"операция снимет ТЕКУЩУЮ страницу, а не главную.")
