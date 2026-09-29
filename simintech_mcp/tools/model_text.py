"""Текст модели: выгрузка и сборка — встроенным языком через контур моста.

COM API не отдаёт ни концов линий, ни иерархии контейнеров; их отдаёт выгрузка
текущего контейнера встроенным языком (`savemodeltofile`). Замеры 2026-09-28/29
(`simintech-code`, спецификации `2026-09-28-wire-level-and-coports.md` §1.3 и
`2026-09-29-submodel-script-and-model-text.md` §1.4): выгрузка несёт объекты,
провода с адресами концов `src = "block:out:N"` и `dst = "block:in:N"`, адрес
ветви `src = "wireName:K"` (K с нуля), вложенные страницы **на всю глубину** и
свойство `script` страницы.

**Оговорка про `script`.** В выгрузке это **временный скрипт контура**, а не
скрипт проекта: снимок делается, пока тело стоит в странице, а прежний скрипт
возвращается на место после прогона (живой прогон 2026-09-29). Читать из этого
поля чужой скрипт нельзя; поле полезно лишь как признак, что выгрузка снята с
живой страницы.

Тела обеих операций собирает библиотека (`simintech_api.model_operations`), а
доставляет контур (`ScriptBridge.run_page_script`): тело идёт в секцию
`initialization` — единственное место, где разрешено создавать объекты. Отсюда
две особенности, которые видит клиент: проект запускается на несколько шагов
расчёта, а исход различается на пять состояний (см. `page_script`), поэтому
«модель не считает» больше не выглядит как «скрипт не собрался».
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from fastmcp.exceptions import ToolError
from simintech_api.core.script_bridge import ScriptBridge
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.model_operations import (
    build_export_model_text_body,
    build_import_model_text_body,
)
from simintech_api.script_probe import (
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_OK,
    ContourOutcome,
)

from .. import runtime, sandbox, session
from ..app import mcp
from .page_script import RESULT_FILE, _change_report, _describe_outcome, _object_names

#: Имена файлов внутри каталога результатов. Клиенту они не нужны: путь
#: возвращается в ответе, а каталог — тот же, что у остальных инструментов.
MODEL_TEXT_FILE = "model-text.txt"
PROBE_RESULT_FILE = RESULT_FILE


def _bridge() -> ScriptBridge:
    """Мост для текущего проекта."""
    project = session._ensure_project()
    return ScriptBridge(session._ensure_client(), project.id)


def _run_contour(body: str, *, failed: str) -> Tuple[ContourOutcome, str]:
    """Выполнить тело контура и вернуть `(исход, прежний скрипт)`.

    Отказ моста (`ScriptBridgeError`) означает неопределённое состояние проекта,
    поэтому он выходит наружу отдельным `ToolError` с рецептом проверки; исходы
    же разбирает вызывающий — у выгрузки и сборки разный набор допустимых.
    """
    try:
        run = _bridge().run_page_script(body, _result_path())
    except ScriptBridgeError as exc:
        raise ToolError(
            f"{failed}: {exc}. Тело идёт в секцию `initialization`, поэтому "
            "расчёт должен сдвинуть модельное время: проверьте, что модель "
            "считает — неподключённый вход останавливает расчёт всей модели "
            "молча."
        ) from exc
    return run.outcome, run.restored_script


def _result_path() -> Path:
    """Путь файла результата внутри каталога результатов (песочница)."""
    return Path(os.path.join(sandbox.output_root(), PROBE_RESULT_FILE))


def _refuse_on_bad_outcome(outcome, *, action: str) -> None:
    """Отказать, если тело не отработало: `not-compiled` и `aborted` — не успех."""
    if outcome.kind in (OUTCOME_OK, OUTCOME_MODEL_NOT_RUNNING):
        return
    detail = (f", последняя строка тела: {outcome.lines[-1]!r}"
              if outcome.lines else "")
    raise ToolError(
        f"{action} не выполнена: исход «{outcome.kind}»{detail}. Текст ошибки "
        "компиляции — в окне сообщений редактора SimInTech: через COM он не "
        "читается."
    )


@mcp.tool()
@runtime._com_threaded
def export_model_text() -> str:
    """Выгрузить модель текущей страницы проекта в декларативный текст.

    Так читается то, чего COM не отдаёт: концы линий (какие блоки соединены),
    адреса ветвей, вложенные страницы субмоделей целиком и скрипт самой
    страницы. Для сборки модели из текста обратно служит `import_model_text`.

    **Порядок работы.** Инструмент ставит тело контура в текущую страницу
    (прежний скрипт возвращается на место), запускает расчёт на несколько шагов
    и читает файл выгрузки из каталога результатов. Поэтому у него две
    особенности: модельное время сдвинется (как после `step`), а на модели,
    которая не считает, приходит отказ с названной причиной.

    **Что в ответе.** Текст выгрузки: свойства страницы (`x_center`), объекты
    записями вида `Имя: (type = "Класс", points = […], свойства)`, провода
    (`type = "wire"`, `src`, `dst`) и вложенные `subsystem:`. Объём ограничен,
    как и у `read_output_file`: при превышении текст обрезается с пометкой.
    Поле `script` страницы содержит временный скрипт контура — не скрипт проекта
    (прежний возвращается на место после прогона).
    """
    root = sandbox.output_root()
    text_path = os.path.join(root, MODEL_TEXT_FILE)
    probe_path = Path(os.path.join(root, PROBE_RESULT_FILE))
    # Прежние файлы удаляем: иначе оборвавшийся прогон отдал бы прошлую
    # выгрузку как нынешнюю — ровно тот класс ошибки, который здесь дороже
    # всего (агент правит модель по устаревшему тексту).
    for stale in (text_path, str(probe_path)):
        try:
            os.remove(stale)
        except OSError:
            pass

    outcome, _restored = _run_contour(
        build_export_model_text_body(text_path),
        failed="выгрузка текста модели не удалась")
    _refuse_on_bad_outcome(outcome, action="выгрузка текста модели")

    data, truncated, error = sandbox._load_result_file(
        text_path, sandbox.MAX_OUTPUT_BYTES, sandbox._MISSING_RESULT_FILE
    )
    if error:
        raise ToolError(
            f"скрипт выгрузки отработал, но файла нет: {error}. Выгрузка идёт "
            "в каталог результатов, и туда же указывает путь в скрипте."
        )
    text = data.decode("utf-8", errors="replace")
    # BOM снимаем: `savemodeltofile` пишет его, а вклейка текста обратно через
    # `eval` файла с BOM не принимает (замер 2026-09-29) — текст, возвращённый
    # агенту, должен быть пригоден для обратного пути без правки.
    text = text.removeprefix("﻿")
    note = (
        f"\n… текст обрезан по пределу {sandbox.MAX_OUTPUT_BYTES} байт"
        if truncated
        else ""
    )
    return f"Текст модели (файл {text_path}):\n{text}{note}"


@mcp.tool()
@runtime._com_threaded
def import_model_text(model_text: str) -> str:
    """Собрать объекты модели из декларативного текста.

    Текст — того же формата, что возвращает `export_model_text`: записи
    `Имя: (type = "Класс", points = […], свойства)` и провода (`type = "wire"`
    с `src`/`dst`). Объекты добавляются в текущий контейнер; **старые не
    трогаются** — `createmodel` дополняет модель (вендор подтвердил 2026-09-28).

    **Кавычки.** Внутри текста кавычка задаётся `chr(34)`: удвоение `""` в этой
    сборке даёт пустую строку, а обратный слэш не экранирует. Текст с удвоенными
    кавычками при этом выглядит правдоподобно, объектов не создаёт и сообщений
    не оставляет — заметить это можно только по отчёту об изменениях.

    **Порядок работы** как у выгрузки: тело идёт в секцию `initialization`,
    расчёт делает несколько шагов, прежний скрипт страницы возвращается на
    место, изменения модели живут в памяти до `save_project`.

    Args:
        model_text: декларативный текст модели (как из `export_model_text`).
    """
    if not model_text.strip():
        raise ToolError(
            "текст модели пуст: собирать нечего. Пустой текст — не «ничего не "
            "сделает»: он означает, что на стороне клиента содержимое потеряно, "
            "и молчание здесь скрыло бы это.")
    before = _object_names()
    outcome, restored = _run_contour(
        build_import_model_text_body(model_text),
        failed="собрать модель из текста не удалось")
    _refuse_on_bad_outcome(outcome, action="сборка модели")
    after = _object_names()
    return (
        f"Модель собрана из текста.\n"
        f"{_describe_outcome(outcome, what='Вердикт')}\n"
        f"{_change_report(before, after, restored)}"
    )
