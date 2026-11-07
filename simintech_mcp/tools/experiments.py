"""Инструменты ledger: запись и удаление экспериментальных свидетельств.

Инструменты не трогают COM и проект: они читают снимок IR из памяти и
пишут в `experiment_ledger`. Запись не повышает поколение снимков, иначе
сразу после неё `query_model` по тому же `model_ref` стал бы устаревшим.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastmcp.exceptions import ToolError
from simintech_api.model_ir import ModelQueryError, parse_experiment_record
from simintech_api.model_ir.experiments import EXPERIMENT_SCHEMA

from .. import experiment_ledger, model_store, runtime
from ..app import mcp


def _now() -> str:
    """Серверное UTC-время записи в ISO 8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _generated_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"exp-{stamp}-{uuid.uuid4().hex[:8]}"


def _hash_error(tool: str, name: str, provided: str,
                actual: str) -> ToolError:
    return ToolError(
        f"{tool}: свидетельство относится к другой модели: "
        f"{name} {provided[:12]} не совпадает со снимком {actual[:12]}")


def _resolve_hashes(snapshot: model_store.Snapshot, structure_hash: str,
                    content_hash: str) -> tuple[str, str]:
    """Сверить явные хеши со снимком и заполнить отсутствующие."""
    provided_structure = structure_hash.strip()
    provided_content = content_hash.strip()
    actual_structure = snapshot.model.meta.structure_hash
    actual_content = snapshot.model.meta.content_hash
    if provided_structure and provided_structure != actual_structure:
        raise _hash_error("record_experiment", "structure_hash",
                          provided_structure, actual_structure)
    if provided_content and provided_content != actual_content:
        raise _hash_error("record_experiment", "content_hash",
                          provided_content, actual_content)
    return (provided_structure or actual_structure,
            provided_content or actual_content)


@mcp.tool(
    title="Записать эксперимент",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.plain_tool
def record_experiment(
        model_ref: str,
        goal: str,
        scenario: Dict[str, Any],
        run: Dict[str, Any],
        observation: Dict[str, Any],
        experiment_id: str = "",
        limitations: Optional[List[str]] = None,
        status: str = "observed",
        recorded_at: str = "",
        structure_hash: str = "",
        content_hash: str = "") -> str:
    """Прикрепить наблюдение эксперимента к снимку Model IR.

    Запись хранится в ledger по полным `structure_hash` и `content_hash`
    снимка и переживает новый `load_model`. `goal` - цель эксперимента
    (текст), `scenario` - изменённый вход, параметр или состояние, `run` -
    baseline и условия прогона, `observation` - наблюдение. Обязательные
    поля `scenario` (`changed`, `from`, `to`, `at`), `run` (`step`,
    `to_time`, `baseline`) и `observation` (`target`, `changed`) проверяет
    ядро `experiment-record/1`; лишние ключи в этих объектах сохраняются.
    `goal` в ядро не передаётся: это поле ledger.

    `experiment_id` по умолчанию генерируется, `recorded_at` по умолчанию
    ставится серверным UTC. Если `structure_hash` или `content_hash` не
    заданы, они берутся из снимка; явно заданные обязаны совпасть со
    снимком, иначе отказ: свидетельство относится к другой модели.
    `status` - `observed`, `experiment` или `unknown`; `limitations` - до
    20 строк по 500 символов. Запись после JSON-канонизации ограничена
    16 КиБ, id - 120 символами; на модель хранится не больше 100 записей.

    Снимок может быть устаревшим: запись принимается по его неизменяемым
    хешам, ответ несёт `stale: true`. Отрицательное наблюдение
    (`observation.changed=false`) не доказывает отсутствие влияния.
    `model_ref` нужен только для получения хешей и метаданных; ключ ledger -
    пара хешей.

    Args:
        model_ref: снимок IR, к которому привязывается запись.
        goal: цель эксперимента, непустая строка до 500 символов.
        scenario: `changed`, `from`, `to`, `at` и дополнительные ключи.
        run: `step`, `to_time`, `baseline` и условия прогона.
        observation: `target`, `changed`, необязательный `first_change_at`.
        experiment_id: id записи; пусто - сгенерировать.
        limitations: ограничения эксперимента.
        status: статус свидетельства ядра.
        recorded_at: время записи; пусто - серверное UTC.
        structure_hash: ожидаемый хеш структуры; пусто - из снимка.
        content_hash: ожидаемый хеш содержимого; пусто - из снимка.
    """
    ref = model_ref.strip()
    if not ref:
        raise ToolError("record_experiment: нужен model_ref")
    snapshot = model_store.get(ref)
    expected_structure, expected_content = _resolve_hashes(
        snapshot, structure_hash, content_hash)
    data: Dict[str, Any] = {
        "schema": EXPERIMENT_SCHEMA,
        "id": experiment_id.strip() or _generated_id(),
        "structure_hash": expected_structure,
        "content_hash": expected_content,
        "scenario": scenario,
        "run": run,
        "observation": observation,
        "status": status,
        "recorded_at": recorded_at.strip() or _now(),
        "limitations": list(limitations or []),
    }
    try:
        record = parse_experiment_record(data)
    except ModelQueryError as exc:
        raise ToolError(f"record_experiment: {exc}") from exc
    try:
        entry = experiment_ledger.attach(
            record, goal=goal, attached_ref=ref,
            project_id=snapshot.project_id, bound=snapshot.bound,
            generation=snapshot.generation, attached_at=_now())
    except experiment_ledger.LedgerError as exc:
        raise ToolError(f"record_experiment: {exc}") from exc
    current = model_store.generation()
    stale = entry.stale(current)
    notes: List[str] = []
    if stale:
        notes.append(
            "Снимок устарел: хеши относятся к состоянию на момент "
            "load_model, а проект уже менялся.")
    if not record.observed:
        notes.append(
            "Эффект не наблюдался; один сценарий не доказывает отсутствие "
            "влияния.")
    if record.status == "unknown":
        notes.append(
            "status=unknown: запись сохранена, но influence её не применит.")
    payload = {
        "schema": experiment_ledger.LEDGER_SCHEMA,
        "recorded": True,
        "model_ref": ref,
        "id": record.id,
        "goal": entry.goal,
        "stale": stale,
        "negative": not record.observed,
        "experiment": entry.to_dict(current),
        "ledger": experiment_ledger.summary(
            expected_structure, expected_content),
        "notes": notes,
    }
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool(
    title="Удалить эксперимент",
    annotations={
        "read_only_hint": False,
        "destructive_hint": True,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.plain_tool
def forget_experiment(
        experiment_id: str = "",
        model_ref: str = "",
        all_records: bool = False,
        structure_hash: str = "",
        content_hash: str = "") -> str:
    """Удалить запись эксперимента из ledger.

    Без `all_records` нужен `experiment_id`. `model_ref` или пара
    `structure_hash`/`content_hash` сужают удаление до конкретной модели и
    проверяются: чужой id даёт отказ. Без них id удаляется глобально, потому
    что идентификаторы свидетельств уникальны в процессе.

    `all_records=True` удаляет все записи модели; тогда обязательны
    `model_ref` или оба хеша. `experiment_id` и `all_records` не сочетаются.

    Args:
        experiment_id: id записи; пусто только при `all_records=True`.
        model_ref: снимок, к хешам которого относится удаление.
        all_records: удалить все записи пары хешей.
        structure_hash: хеш структуры, если снимка нет.
        content_hash: хеш содержимого, если снимка нет.
    """
    ref = model_ref.strip()
    provided_structure = structure_hash.strip()
    provided_content = content_hash.strip()
    if ref:
        snapshot = model_store.get(ref)
        actual_structure = snapshot.model.meta.structure_hash
        actual_content = snapshot.model.meta.content_hash
        if provided_structure and provided_structure != actual_structure:
            raise _hash_error("forget_experiment", "structure_hash",
                              provided_structure, actual_structure)
        if provided_content and provided_content != actual_content:
            raise _hash_error("forget_experiment", "content_hash",
                              provided_content, actual_content)
        provided_structure = actual_structure
        provided_content = actual_content
    elif bool(provided_structure) != bool(provided_content):
        raise ToolError(
            "forget_experiment: задайте оба хеша или model_ref")
    ident = experiment_id.strip()
    if all_records:
        if ident:
            raise ToolError(
                "forget_experiment: experiment_id и all_records не сочетаются")
        if not provided_structure or not provided_content:
            raise ToolError(
                "forget_experiment: all_records требует model_ref или оба хеша")
    elif not ident:
        raise ToolError(
            "forget_experiment: нужен experiment_id или all_records=True")
    try:
        removed = experiment_ledger.forget(
            ident, structure_hash=provided_structure,
            content_hash=provided_content, all_records=all_records)
    except experiment_ledger.LedgerError as exc:
        raise ToolError(f"forget_experiment: {exc}") from exc
    if not removed:
        target = ("записи этой модели" if all_records
                  else f"запись {ident!r}")
        raise ToolError(
            f"forget_experiment: {target} не найдена в ledger")
    payload = {
        "schema": experiment_ledger.LEDGER_SCHEMA,
        "forgotten": list(removed),
        "removed": len(removed),
        "cleared": all_records,
        "model_ref": ref,
        "structure_hash": provided_structure,
        "content_hash": provided_content,
        "ledger": experiment_ledger.summary(
            provided_structure, provided_content),
    }
    return json.dumps(payload, ensure_ascii=False)
