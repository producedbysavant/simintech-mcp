"""Состояние сессии: COM-клиент, текущий проект и созданные в нём линии.

Текущий проект меняется только через `_set_project`: линии принадлежат
проекту, и сбрасывать их надо вместе с ним. Пока проект присваивался бы в
нескольких местах, инвариант «линии живут ровно столько же, сколько проект»
держался бы на соглашении — и одного нового инструмента хватило бы, чтобы
`layout_place` начал двигать блоки по мёртвым COM-идентификаторам.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api import COMClient, Project, Wire


# ─── Состояние сессии ─────────────────────────────────────────────

_client: Optional[COMClient] = None
_project: Optional[Project] = None

#: Линии, созданные в текущей сессии. COM API не умеет перечислять линии
#: страницы (нет ни `GetWireCount`, ни `GetWire`), поэтому запоминаем их при
#: создании — иначе `layout_place` нечего трассировать, и провода остались бы
#: диагональными. Живут ровно столько же, сколько проект: сбрасываются вместе
#: с ним.
#:
#: Элемент — не сама линия, а `(линия, имя источника, номер выхода, имя
#: приёмника, номер входа)`: без концов `layout_place` не выровнял бы блоки
#: по портам.
_WIRES: List[Tuple[Wire, str, int, str, int]] = []


def _ensure_client() -> COMClient:
    """Подключиться к COM-серверу (лениво)."""
    global _client
    if _client is None or not _client.connected:
        _client = COMClient(silent_mode=True).connect()
    return _client


def _ensure_project() -> Project:
    if _project is None:
        raise ToolError("Нет открытого проекта. Сначала вызовите create_project")
    return _project


def _set_project(project: Optional[Project]) -> None:
    """Сделать проект текущим — единственное место, где он меняется.

    Линии принадлежат проекту: их COM-идентификаторы после смены проекта
    указывают в никуда, поэтому `_WIRES` сбрасывается здесь же. Пока проект
    присваивался в нескольких местах, инвариант «линии живут ровно столько
    же, сколько проект» держался на соглашении — и одного нового инструмента
    хватило бы, чтобы `layout_place` начал двигать блоки по мёртвым линиям.
    Переменная `_project` остаётся на месте: на неё опираются тесты.
    """
    global _project
    _project = project
    _WIRES.clear()


def _replace_project(project: Project) -> str:
    """Сделать проект текущим, закрыв предыдущий.

    Без этого `create_project`/`open_project` копили бы открытые проекты внутри
    `mmain.exe`: старые оставались бы жить со своими слоями, настройками
    расчёта и базой сигналов, а инструменты молча работали бы с последним.

    Returns:
        Пустая строка, если закрывать было нечего или всё закрылось, иначе —
        предупреждение для ответа инструмента: самоцель функции не достигнута,
        и об этом нельзя молчать (проект остался жить в `mmain.exe`).
    """
    previous = _project
    # Смена проекта и сброс линий — одна операция (`_set_project`): линии
    # принадлежат предыдущему проекту, их идентификаторы после смены
    # указывают в никуда.
    _set_project(project)
    if previous is None or previous is project:
        return ""
    try:
        previous.close()
    except Exception as exc:                                  # noqa: BLE001
        return (f" ВНИМАНИЕ: предыдущий проект закрыть не удалось "
                f"({type(exc).__name__}: {exc}) — он мог остаться открытым "
                f"в SimInTech.")
    return ""
