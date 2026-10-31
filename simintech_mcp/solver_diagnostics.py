"""Диагностика шага внешнего решателя: файл, схема и проверки.

Модуль не является инструментом: им пользуются ``simulate`` и ``step``.
Файл пишет модуль интегрирования, а MCP только читает его и проверяет, что
он относится к тому же процессу mmain и обновлён этим прогоном. Без такой
проверки старый файл от прошлого запуска выдал бы себя за текущий шаг.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Optional, cast

from fastmcp.exceptions import ToolError

from . import sandbox, session

#: Схема файла. Версия в имени позволяет менять поля, не угадывая формат.
SCHEMA = "solver-step-diagnostics/1"

#: Предел чтения: файл пишет внешний модуль, и он не обязан быть маленьким.
MAX_BYTES = 64 * 1024

#: Допуск на грубость mtime файловой системы: файл, записанный в начале
#: прогона, не должен отвергаться из-за округления времени.
STALE_TOLERANCE_SECONDS = 2.0

_REQUIRED = ("schema", "pid", "t", "h", "status", "Nbad", "Ngood")


class DiagnosticsError(ValueError):
    """Файл диагностики не соответствует схеме."""


def _number(data: Dict[str, Any], key: str,
            minimum: Optional[float] = None) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagnosticsError(f"{key} должен быть числом")
    try:
        number = float(value)
    except OverflowError as exc:
        raise DiagnosticsError(f"{key} должен быть конечным числом") from exc
    if not math.isfinite(number):
        raise DiagnosticsError(f"{key} должен быть конечным числом")
    if minimum is not None and number < minimum:
        raise DiagnosticsError(f"{key} должен быть не меньше {minimum:g}")
    return number


def _integer(data: Dict[str, Any], key: str, minimum: int = 0) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DiagnosticsError(f"{key} должен быть целым числом")
    if value < minimum:
        raise DiagnosticsError(f"{key} должен быть не меньше {minimum}")
    return value


def parse_diagnostics(raw: bytes) -> Dict[str, Any]:
    """Разобрать и проверить JSON-объект схемы ``solver-step-diagnostics/1``.

    Обязательные поля: ``schema``, ``pid``, ``t``, ``h``, ``status``,
    ``Nbad``, ``Ngood``. У отказа обязан быть ``reason`` или ``code``:
    ответ без причины не выполняет требование "отказ называет причину".
    Неизвестные поля сохраняются, но в машинную строку не попадают.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DiagnosticsError(f"файл не UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DiagnosticsError(
            f"не JSON: {exc.msg} (строка {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise DiagnosticsError("ожидается JSON-объект")
    payload = cast(Dict[str, Any], data)
    missing = [key for key in _REQUIRED if key not in payload]
    if missing:
        raise DiagnosticsError(f"нет полей: {', '.join(missing)}")
    if payload["schema"] != SCHEMA:
        raise DiagnosticsError(
            f"schema={payload['schema']!r}, ожидается {SCHEMA!r}")
    _integer(payload, "pid", minimum=1)
    _number(payload, "t")
    _number(payload, "h", minimum=0)
    if payload["status"] not in ("accepted", "rejected"):
        raise DiagnosticsError("status должен быть accepted или rejected")
    _integer(payload, "Nbad")
    _integer(payload, "Ngood")
    reason = payload.get("reason", "")
    if reason is not None and not isinstance(reason, str):
        raise DiagnosticsError("reason должен быть строкой")
    code = payload.get("code", "")
    if code is not None and not isinstance(code, str):
        raise DiagnosticsError("code должен быть строкой")
    if payload["status"] == "rejected" and not (reason or code):
        raise DiagnosticsError(
            "отказ шага должен называть причину: reason или code")
    return payload


def _line(data: Dict[str, Any]) -> str:
    """Одна машинная строка в формате, родственном ``timing:``."""
    keys = ("schema", "pid", "t", "h", "status", "reason", "code",
            "Nbad", "Ngood")
    ordered = {key: data[key] for key in keys if key in data}
    return "solver: " + json.dumps(
        ordered, ensure_ascii=False, separators=(",", ":"))


def _warning(message: str) -> str:
    return f"WARNING: диагностика шага недоступна: {message}"


def _session_pid() -> Optional[int]:
    client = session.current_client()
    return getattr(client, "session_pid", None) if client is not None else None


def read_diagnostics(path: str, run_started: float) -> str:
    """Прочитать файл и вернуть машинную строку или явное предупреждение.

    ``run_started`` - время начала прогона по тем же часам, что и mtime
    файла. Файл принимается только от процесса mmain текущей сессии и только
    если он обновлён после начала прогона. При любом сомнении значения не
    показываются: подставлять догадку вместо диагностики запрещено.
    """
    expected_pid = _session_pid()
    if expected_pid is None:
        return ("WARNING: диагностика шага недоступна: PID сессии неизвестен, "
                "принадлежность файла прогону не проверить")
    try:
        resolved = sandbox.resolve_output_path(path)
    except ToolError as exc:
        return _warning(str(exc))
    if not os.path.isfile(resolved):
        return _warning(
            f"файла нет: {path}. Модуль решателя должен писать JSON-объект "
            f"схемы {SCHEMA} в каталог результатов")
    try:
        mtime = os.stat(resolved).st_mtime
        raw, truncated = sandbox.read_bounded(resolved, MAX_BYTES)
    except OSError as exc:
        return _warning(str(exc))
    if truncated:
        return _warning(f"файл {path} больше {MAX_BYTES} байт")
    try:
        data = parse_diagnostics(raw)
    except DiagnosticsError as exc:
        return _warning(f"{path}: {exc}")
    if data["pid"] != expected_pid:
        return (f"WARNING: диагностика шага от другого процесса: файл "
                f"pid={data['pid']}, сессия pid={expected_pid}; значения "
                f"не приняты")
    if mtime < run_started - STALE_TOLERANCE_SECONDS:
        return (f"WARNING: файл диагностики не обновлён этим прогоном "
                f"(mtime={mtime:.3f}, старт={run_started:.3f}); значения "
                f"не приняты")
    return _line(data)
