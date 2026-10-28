"""Снимки Model IR: model_ref, поколение сессии и вытеснение.

Снимок неизменяем. Свежесть проектного снимка определяется поколением
сессии: любая успешная правка или смена проекта делает прежний ref
устаревшим. Файловый снимок не привязан к проекту и не стареет от правок.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from fastmcp.exceptions import ToolError
from simintech_api.model_ir import ModelIR, ModelIndex

#: Сколько снимков держим в памяти. Два нужны для compare_models.
MAX_SNAPSHOTS = 2

_generation = 0
_sequence = 0
_snapshots: dict[str, "Snapshot"] = {}
_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class Snapshot:
    ref: str
    model: ModelIR
    index: ModelIndex
    bound: bool
    project_id: Optional[int]
    generation: int


def generation() -> int:
    with _lock:
        return _generation


def bump_generation() -> int:
    """Новая правка или смена проекта: проектные снимки становятся stale."""
    global _generation
    with _lock:
        _generation += 1
        return _generation


def invalidate_project() -> None:
    """Убрать снимки, привязанные к проекту; файловые оставить."""
    with _lock:
        for ref in [key for key, item in _snapshots.items() if item.bound]:
            _snapshots.pop(ref, None)


def clear() -> None:
    """Полная очистка (тесты и disconnect)."""
    with _lock:
        _snapshots.clear()


def store(model: ModelIR, *, bound: bool,
          project_id: Optional[int] = None) -> str:
    """Положить снимок в LRU и вернуть opaque model_ref."""
    global _sequence
    index = ModelIndex(model)
    with _lock:
        _sequence += 1
        ref = (f"mir1.{model.meta.structure_hash[:12]}."
               f"{_generation}.{_sequence}")
        _snapshots[ref] = Snapshot(
            ref=ref, model=model, index=index, bound=bound,
            project_id=project_id, generation=_generation)
        while len(_snapshots) > MAX_SNAPSHOTS:
            oldest = next(iter(_snapshots))
            _snapshots.pop(oldest, None)
        return ref


def get(ref: str) -> Snapshot:
    """Найти снимок; неизвестная ссылка отличается от устаревшей."""
    with _lock:
        snapshot = _snapshots.get(ref)
        if snapshot is None:
            raise ToolError(
                f"Ссылка {ref} неизвестна: снимок вытеснен или сервер "
                f"перезапущен. Вызовите load_model заново.")
        _snapshots.pop(ref, None)
        _snapshots[ref] = snapshot
        return snapshot


def require_fresh(snapshot: Snapshot) -> None:
    if snapshot.bound and snapshot.generation != generation():
        raise ToolError(
            f"Снимок {snapshot.ref} устарел: проект менялся после загрузки. "
            f"Вызовите load_model заново.")
