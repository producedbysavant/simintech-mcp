"""Экспериментальные свидетельства Model IR: ledger по обоим хешам снимка.

Запись привязана к полным `structure_hash` и `content_hash`, а не к
`model_ref`: ссылка вытесняется, а хеши описывают саму модель. Ledger живёт
в памяти сервера и переживает новый `load_model`, смену проекта и
`disconnect`; перезапуск процесса сервера его очищает.

Отрицательное наблюдение не является доказательством отсутствия влияния:
ledger хранит запись, а `influence` оставляет её ограничением.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Optional, cast

from simintech_api.model_ir import (
    ExperimentRecord,
    ModelQueryError,
    parse_experiment_record,
)
from simintech_api.model_ir.experiments import MAX_RECORDS

#: Схема ответов ledger, а не записи эксперимента: запись остаётся
#: `experiment-record/1` из ядра.
LEDGER_SCHEMA = "experiment-ledger/1"

#: Сколько моделей с свидетельствами держим в памяти. Вытеснения нет:
#: девятая модель получает отказ, чтобы не потерять доказательства молча.
MAX_BUCKETS = 8

#: Предел одной записи после JSON-канонизации. Он ограничивает лишние поля
#: `scenario`/`run`/`observation`, которые ядро сохраняет, но не ограничивает.
MAX_RECORD_BYTES = 16 * 1024

#: Пределы полей, которых нет в ядре: id и цели эксперимента.
MAX_ID_CHARS = 120
MAX_GOAL_CHARS = 500

#: Предел числа записей на одну пару хешей. Синхронен лимиту ядра:
#: `influence` отказывает на 101-й записи, поэтому ledger не должен их копить.
MAX_RECORDS_PER_MODEL = MAX_RECORDS


class LedgerError(Exception):
    """Отказ ledger: причина называется до записи, а не после."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Запись эксперимента вместе с метаданными привязки к снимку."""

    record: ExperimentRecord
    goal: str
    attached_ref: str
    project_id: Optional[int]
    bound: bool
    generation: int
    attached_at: str

    def stale(self, current_generation: int) -> bool:
        """Снимок привязки устарел относительно текущего поколения сессии."""
        return self.bound and self.generation != current_generation

    def to_dict(self, current_generation: int) -> dict[str, Any]:
        data = self.record.to_dict()
        data.update({
            "goal": self.goal,
            "attached_ref": self.attached_ref,
            "project_id": self.project_id,
            "bound": self.bound,
            "attached_at": self.attached_at,
            "stale": self.stale(current_generation),
        })
        return data


def is_negative(record: ExperimentRecord) -> bool:
    """Отрицательное свидетельство: changed=false и статус не unknown.

    Ядро отбрасывает `status=unknown` до проверки наблюдения, поэтому такая
    запись неприменима, а не отрицательна. Один предикат держит счётчики
    `record_experiment`, `query_model(experiments)` и `influence` согласованными.
    """
    return record.status != "unknown" and not record.observed


@dataclass(frozen=True, slots=True)
class _StoredEntry:
    """Канонический JSON записи: копия без общих вложенных словарей."""

    record_json: str
    goal: str
    attached_ref: str
    project_id: Optional[int]
    bound: bool
    generation: int
    attached_at: str

    def parse(self) -> ExperimentRecord:
        try:
            data = json.loads(self.record_json)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"запись повреждена: {exc}") from exc
        if not isinstance(data, dict):
            raise LedgerError("запись повреждена: не объект")
        try:
            return parse_experiment_record(cast(dict[str, Any], data))
        except ModelQueryError as exc:
            raise LedgerError(f"запись повреждена: {exc}") from exc

    def entry(self) -> LedgerEntry:
        return LedgerEntry(
            record=self.parse(),
            goal=self.goal,
            attached_ref=self.attached_ref,
            project_id=self.project_id,
            bound=self.bound,
            generation=self.generation,
            attached_at=self.attached_at,
        )


_buckets: dict[tuple[str, str], dict[str, _StoredEntry]] = {}
#: Глобальный индекс id: свидетельства нумеруются на процесс, а не на
#: модель. Иначе `influence` показывал бы `experiment:<id>` без хешей и две
#: записи разных моделей сливались бы в одну ссылку.
_ids: dict[str, tuple[str, str]] = {}
_lock = threading.RLock()


def _canonical(record: ExperimentRecord) -> str:
    """Канонический JSON записи; отказ на несериализуемые и огромные данные."""
    try:
        payload = json.dumps(record.to_dict(), sort_keys=True,
                             ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LedgerError(f"запись не сериализуется в JSON: {exc}") from exc
    size = len(payload.encode("utf-8"))
    if size > MAX_RECORD_BYTES:
        raise LedgerError(
            f"запись больше {MAX_RECORD_BYTES} байт ({size})")
    return payload


def attach(record: ExperimentRecord, *, goal: str,
           attached_ref: str = "", project_id: Optional[int] = None,
           bound: bool = False, generation: int = 0,
           attached_at: str = "") -> LedgerEntry:
    """Положить проверенную запись в bucket её хешей.

    Запись уже разобрана ядром: `structure_hash`, `content_hash` и `id`
    непусты. Здесь проверяются цель, глобальная уникальность id и лимиты.
    """
    clean_goal = goal.strip()
    if not clean_goal:
        raise LedgerError("goal пуст")
    if len(clean_goal) > MAX_GOAL_CHARS:
        raise LedgerError(
            f"goal длиннее {MAX_GOAL_CHARS} символов")
    if len(record.id) > MAX_ID_CHARS:
        raise LedgerError(
            f"id длиннее {MAX_ID_CHARS} символов")
    stored = _StoredEntry(
        record_json=_canonical(record),
        goal=clean_goal,
        attached_ref=attached_ref,
        project_id=project_id,
        bound=bound,
        generation=generation,
        attached_at=attached_at,
    )
    entry = stored.entry()
    key = (record.structure_hash, record.content_hash)
    with _lock:
        if record.id in _ids:
            raise LedgerError(
                f"повтор id {record.id!r}: идентификаторы свидетельств "
                f"уникальны в процессе")
        bucket = _buckets.get(key)
        if bucket is None:
            if len(_buckets) >= MAX_BUCKETS:
                raise LedgerError(
                    f"ledger полон: уже {MAX_BUCKETS} моделей; удалите "
                    f"ненужные записи через forget_experiment(all_records=True)")
            bucket = {}
            _buckets[key] = bucket
        if len(bucket) >= MAX_RECORDS_PER_MODEL:
            raise LedgerError(
                f"для этой модели уже {MAX_RECORDS_PER_MODEL} записей; "
                f"удалите ненужные через forget_experiment")
        bucket[record.id] = stored
        _ids[record.id] = key
    return entry


def entries_for(structure_hash: str, content_hash: str
                ) -> tuple[LedgerEntry, ...]:
    """Все записи точной пары хешей в порядке добавления."""
    key = (structure_hash, content_hash)
    with _lock:
        bucket = _buckets.get(key)
        if not bucket:
            return ()
        stored = tuple(bucket.values())
    return tuple(item.entry() for item in stored)


def records_for(structure_hash: str, content_hash: str
                ) -> tuple[ExperimentRecord, ...]:
    """Записи для передачи в `influence`; только точная пара хешей."""
    return tuple(entry.record for entry in entries_for(
        structure_hash, content_hash))


def entry_for(experiment_id: str) -> Optional[LedgerEntry]:
    """Запись по глобальному id; None, если id неизвестен."""
    with _lock:
        key = _ids.get(experiment_id)
        if key is None:
            return None
        stored = _buckets.get(key, {}).get(experiment_id)
    return stored.entry() if stored is not None else None


def forget(experiment_id: str, *, structure_hash: str = "",
           content_hash: str = "",
           all_records: bool = False) -> tuple[str, ...]:
    """Удалить одну запись по id или весь bucket пары хешей.

    `all_records=True` требует оба хеша. Неизвестный id даёт пустой кортеж;
    чужая пара хешей даёт `LedgerError`, а не молчаливое «не найдено».
    """
    if all_records:
        if not structure_hash or not content_hash:
            raise LedgerError("all_records требует оба хеша")
        key = (structure_hash, content_hash)
        with _lock:
            bucket = _buckets.pop(key, None)
            if not bucket:
                return ()
            removed = tuple(bucket)
            for ident in removed:
                _ids.pop(ident, None)
            return removed
    ident = experiment_id.strip()
    if not ident:
        raise LedgerError("нужен experiment_id или all_records=True")
    with _lock:
        found = _ids.get(ident)
        if found is None:
            return ()
        if structure_hash and structure_hash != found[0]:
            raise LedgerError(
                f"запись {ident!r} относится к другой structure_hash")
        if content_hash and content_hash != found[1]:
            raise LedgerError(
                f"запись {ident!r} относится к другой content_hash")
        stored = _buckets.get(found, {}).pop(ident, None)
        if stored is None:
            _ids.pop(ident, None)
            return ()
        _ids.pop(ident, None)
        if not _buckets.get(found):
            _buckets.pop(found, None)
        return (ident,)


def summary(structure_hash: str = "", content_hash: str = ""
            ) -> dict[str, Any]:
    """Счётчики ledger: сколько записей у пары хешей и у других версий."""
    with _lock:
        buckets = len(_buckets)
        if not structure_hash or not content_hash:
            return {"buckets": buckets, "limit": MAX_RECORDS_PER_MODEL}
        bucket = _buckets.get((structure_hash, content_hash))
        other = sum(1 for (structure, content) in _buckets
                    if structure == structure_hash
                    and content != content_hash)
        return {
            "stored": len(bucket) if bucket else 0,
            "limit": MAX_RECORDS_PER_MODEL,
            "other_content_versions": other,
            "buckets": buckets,
        }


def clear() -> None:
    """Полная очистка: тесты и явный сброс состояния сервера."""
    with _lock:
        _buckets.clear()
        _ids.clear()
