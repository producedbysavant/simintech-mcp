"""Языковой слой: скрипт страницы и тела операций — через контур моста.

Инструменты поверх `ScriptBridge.run_page_script`: тело идёт в секцию
`initialization` (только там разрешено создавать объекты), исход различается на
пять состояний, прежний скрипт страницы возвращается на место. Отчёт об
изменениях собирается **здесь**: библиотека отдаёт `restored_script` и исход, а
«сколько объектов стало» — это COM-чтение снаружи контура (спецификация §3.7,
`simintech-code/docs/superpowers/specs/2026-09-29-language-contour.md`).

Отличие от `model_text.export_model_text`: там тело идёт под `if firststep then`
(так работала проба), здесь — в `initialization`, как требует создание объектов.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from fastmcp.exceptions import ToolError
from simintech_api.core.script_bridge import ScriptBridge
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.script_probe import (
    OUTCOME_ABORTED,
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_NOT_COMPILED,
    OUTCOME_OK,
    OUTCOME_SECTION_NOT_RUN,
    ContourOutcome,
)

from .. import runtime, sandbox, session
from ..app import mcp

#: Сколько объектов страницы перечислять в отчёте: список — для человека, а не
#: для машинной обработки, поэтому длинный хвост обрезается.
MAX_REPORTED_OBJECTS = 20

#: Имя файла результата контура внутри каталога результатов. Клиенту оно не
#: нужно: строки тела возвращаются в ответе, а каталог — тот же, что у
#: остальных инструментов.
RESULT_FILE = "page-script-result.txt"


def _bridge() -> ScriptBridge:
    """Мост для текущего проекта — общая часть всех инструментов модуля."""
    return ScriptBridge(session._ensure_client(), session._ensure_project().id)


def _object_names() -> list[str]:
    """Имена объектов текущей страницы — снимки «до» и «после».

    Именно **текущей**: мост ставит скрипт в страницу, которую называет
    `GetCurentPage`, и отчёт обязан считать по той же странице. `list_blocks`
    инструмента берёт главную — для отчёта это было бы расхождение на модели,
    где работа идёт внутри субмодели.
    """
    page = session._ensure_project().get_current_page()
    names = []
    for obj in page.get_blocks():
        try:
            names.append(obj.get_name())
        except Exception:                                            # noqa: BLE001
            names.append("(без имени)")
    return names


def _change_report(before: list[str], after: list[str],
                   restored: str) -> str:
    """Отчёт об изменениях: было/стало, добавленные имена, возврат скрипта.

    Сравнение идёт по **мультимножествам имён**, а не по числу: среда сама
    переименовывает объекты (`kx_0`), поэтому «стало больше» и «добавлен объект
    X» — разные утверждения, и склеивать их нельзя. Имя, исчезнувшее из снимка,
    добавленным не считается и не показывается: об удалении контур не сообщает
    (удаление объекта в этой сборке не предлагается вовсе — спецификация §8).
    """
    added = list(after)
    for name in before:
        if name in added:
            added.remove(name)
    shown = ", ".join(added[:MAX_REPORTED_OBJECTS])
    tail = f"; добавлено: {shown}" if shown else "; добавленных объектов нет"
    more = (f" (и ещё {len(added) - MAX_REPORTED_OBJECTS})"
            if len(added) > MAX_REPORTED_OBJECTS else "")
    restored_note = ("Прежний скрипт страницы возвращён: да" if restored
                     else "Прежний скрипт страницы возвращён: нет — он был пуст")
    return (f"Отчёт об изменениях: объектов было {len(before)}, стало "
            f"{len(after)}{tail}{more}. {restored_note}")


@mcp.tool()
@runtime._com_threaded
def get_page_script() -> str:
    """Прочитать скрипт текущей страницы проекта.

    Скрипт живёт в проекте как текст встроенного языка: он исполняется при
    инициализации страницы и на шагах расчёта. Читается он снимком выгрузки
    проекта — COM-метода чтения скрипта не существует (`SetPageScript` пишет и
    о прежнем содержимом не сообщает, `GetPageScript` в интерфейсе нет).

    **Расчёт при чтении не запускается.** Это не деталь: `ProjectStart` — это
    инициализация, и она обнуляет модельное время; инструмент чтения, сдвигающий
    время, уничтожал бы результаты уже сделанного расчёта. Живой замер
    2026-09-29: на остановленном проекте время до и после чтения совпадает.

    Пустой скрипт — не отказ: у проекта из шаблона скрипта страницы нет вовсе, и
    об этом сообщается текстом.
    """
    try:
        script = _bridge().read_page_script()
    except ScriptBridgeError as exc:
        raise ToolError(
            f"прочитать скрипт страницы не удалось: {exc}. Чтение опознаёт "
            "страницу по снимку выгрузки проекта, поэтому отказ означает, что "
            "состояние скриптовых записей неопределённо — сверьтесь с копией "
            "проекта, прежде чем что-то в него писать."
        ) from exc
    if not script.strip():
        return ("Скрипт текущей страницы пуст: у этой страницы скрипта нет. "
                "Поставить его можно инструментом `set_page_script`.")
    return f"Скрипт текущей страницы:\n{script}"


def _install_script(script: str) -> None:
    """Оставить скрипт в текущей странице.

    Отдельной функцией — чтобы тест мог подменить её и проверить, что скрипт
    остаётся **только** после успешной проверки компиляции.
    """
    _bridge().install_script(script)


def _result_path() -> Path:
    """Путь файла результата внутри каталога результатов (песочница)."""
    return Path(os.path.join(sandbox.output_root(), RESULT_FILE))


def _run(body: str) -> Tuple[ContourOutcome, str]:
    """Выполнить тело контуром: `(исход, прежний скрипт)`, либо отказ `ToolError`.

    Отказ моста (`ScriptBridgeError`) означает, что состояние проекта
    неопределённо: тело могло не установиться, а могло и отработать. Поэтому он
    не превращается в исход, а выходит наружу — с объяснением, что проверить.
    """
    try:
        run = _bridge().run_page_script(body, _result_path())
    except ScriptBridgeError as exc:
        raise ToolError(
            f"выполнить скрипт страницы не удалось: {exc}. Тело идёт в секцию "
            "`initialization`, поэтому расчёт должен сдвинуть модельное время: "
            "проверьте, что модель считает — неподключённый вход останавливает "
            "расчёт всей модели молча."
        ) from exc
    return run.outcome, run.restored_script


def _describe_outcome(outcome: ContourOutcome, *, what: str) -> str:
    """Строка состояния для исхода, при котором работа **состоялась**.

    Исходы `not-compiled` и `aborted` сюда не попадают: они означают, что
    работа не сделана, и обрабатываются отказом у вызывающего.
    """
    if outcome.kind == OUTCOME_OK:
        return f"{what}: скрипт собрался и отработал, расчёт идёт."
    if outcome.kind == OUTCOME_MODEL_NOT_RUNNING:
        return (f"{what}: скрипт собрался и отработал, но модель не считает — "
                "вероятная причина: неподключённый вход останавливает расчёт "
                "всей модели.")
    if outcome.kind == OUTCOME_SECTION_NOT_RUN:
        return (f"{what}: секция `initialization` не выполнилась, хотя расчёт "
                "шёл. Причину по этому признаку определить нечем.")
    return f"{what}: исход «{outcome.kind}»."


@mcp.tool()
@runtime._com_threaded
def set_page_script(script: str) -> str:
    """Поставить скрипт в текущую страницу проекта и проверить, что он собрался.

    **Прежний скрипт в ответе.** `SetPageScript` затирает скрипт страницы, не
    сообщая, что там было; инструмент возвращает прежний текст, чтобы клиент мог
    его восстановить. Проверка «собрался ли» — единственная доступная: среда об
    ошибке компиляции молчит, а признак один — расчёт сдвинул модельное время.

    **Как это устроено.** Контурный режим (`run_page_script`) всегда возвращает
    прежний скрипт — иначе он не был бы безопасным. Поэтому работа идёт в два
    шага: сначала скрипт исполняется контуром (проверка компиляции) и прежний
    текст возвращается на место, затем — только если проверка прошла — скрипт
    ставится в страницу насовсем.

    **Чего инструмент не делает.** Не правит скрипт отдельного блока и не
    работает с `.inc`-файлами — это отдельная тема. Не чистит скрипт: пустой
    текст отвергается, потому что `SetPageScript` с пустой строкой стирает
    прежний скрипт молча.

    Args:
        script: текст скрипта встроенного языка для текущей страницы.
    """
    if not script.strip():
        raise ToolError(
            "скрипт пуст. `SetPageScript` с пустой строкой **стирает** прежний "
            "скрипт страницы, поэтому пустой текст отвергается: если цель — "
            "очистить скрипт, сделайте это осознанно и передайте скрипт с одним "
            "комментарием.")
    before = _object_names()
    outcome, restored = _run(script)
    if outcome.kind in (OUTCOME_NOT_COMPILED, OUTCOME_ABORTED):
        reason = (
            "скрипт не собрался (среда об ошибке молчит; текст ошибки — в окне "
            "сообщений редактора SimInTech)"
            if outcome.kind == OUTCOME_NOT_COMPILED else
            "скрипт оборвался на исполнении; последняя записанная строка: "
            + repr(outcome.lines[-1] if outcome.lines else "")
        )
        raise ToolError(
            f"скрипт не поставлен: {reason}. Прежний скрипт страницы возвращён "
            "на место, проект не изменён.")
    _install_script(script)
    after = _object_names()
    return (
        f"Скрипт поставлен в текущую страницу.\n"
        f"{_describe_outcome(outcome, what='Вердикт')}\n"
        f"{_change_report(before, after, restored)}\n"
        f"Чтобы вернуть прежний скрипт — передайте его текст в "
        f"`set_page_script`.\n"
        f"---- прежний скрипт ----\n{restored}"
    )
