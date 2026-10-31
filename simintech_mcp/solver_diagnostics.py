"""Диагностика шага внешнего решателя: журнал JSON Lines и проверки.

Модуль не является инструментом: им пользуются ``simulate`` и ``step``.
Модуль интегрирования пишет по одной JSON-строке на попытку шага, а MCP
читает хвост журнала и берёт последний исход. Записи ``call``/``entry`` -
якоря входа в ``im_run``; они не являются исходом и не выдаются за него.
Без проверок pid и mtime старый журнал от прошлого запуска выдал бы себя за
текущий шаг.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

from fastmcp.exceptions import ToolError

from . import sandbox, session

#: Схема записи. Версия в имени позволяет менять поля, не угадывая формат.
SCHEMA = "solver-step-diagnostics/1"

#: Предел чтения хвоста: журнал растёт с каждой попыткой, а нужен его конец.
MAX_BYTES = 64 * 1024

#: Допуск на разрешение mtime. NTFS на целевой платформе даёт 100 нс,
#: поэтому 1 мс хватает на округление и не превращает журнал прошлого
#: прогона в текущий (раньше двухсекундный допуск принимал предыдущий
#: отказ за новый). На FAT/сетевом каталоге возможно ложное предупреждение;
#: значения тогда не показываются, догадка не подставляется.
STALE_TOLERANCE_SECONDS = 0.001

#: Текст отказа, когда журнала нет; `{path}` подставляет `load_result_tail`.
MISSING_LOG = ("файла нет: {path}. Модуль решателя должен писать JSON Lines "
               f"схемы {SCHEMA} в каталог результатов")

#: Исходы шага. Только они участвуют в выборе последней записи.
_OUTCOMES = ("accepted", "rejected")

#: Якоря входа в im_run. ``entry`` принят как синоним ``call``.
_ENTRIES = ("call", "entry")


class DiagnosticsError(ValueError):
    """Запись журнала не соответствует схеме."""


@dataclass(frozen=True)
class DiagnosticsResult:
    """Результат чтения журнала: машинная строка или предупреждение.

    ``status`` - статус последнего исхода (``accepted``/``rejected``), а не
    последней строки: после исхода могла остаться запись ``call``. Если
    исхода в хвосте нет, ``status`` - статус последней записи (``call`` или
    ``entry``), а ``text`` содержит явное предупреждение. ``last_status``
    хранит статус именно последней записи, ``pending_entry`` - был ли после
    последнего исхода вход без исхода.
    """

    text: str
    status: Optional[str] = None
    record: Optional[Dict[str, Any]] = None
    warning: bool = False
    last_status: Optional[str] = None
    pending_entry: bool = False


def accepted_stall_note(result: DiagnosticsResult) -> str:
    """Текст для ``Δt=0``, когда последний исход ``accepted``.

    Не выдаёт догадку: если после исхода был вход без исхода, так и говорит;
    в обоих случаях точная причина не устанавливается.
    """
    if result.pending_entry:
        return ("Журнал решателя: последний исход принят, но после него был "
                "вход в im_run без исхода. Модельное время не сдвинулось; "
                "последняя попытка могла выйти без записи исхода (например, "
                "h<4*hmin). Точная причина не установлена.")
    return ("Журнал решателя заканчивается принятым шагом, но модельное "
            "время не сдвинулось. Это не отказ решателя по журналу: "
            "последняя попытка могла выйти без записи (например, "
            "h<4*hmin). Точная причина не установлена.")


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
    if isinstance(value, bool):
        raise DiagnosticsError(f"{key} должен быть целым числом")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise DiagnosticsError(f"{key} должен быть целым числом")
        value = int(value)
        data[key] = value
    if not isinstance(value, int):
        raise DiagnosticsError(f"{key} должен быть целым числом")
    if value < minimum:
        raise DiagnosticsError(f"{key} должен быть не меньше {minimum}")
    return value


def _record(data: Any, line_no: int = 0) -> Dict[str, Any]:
    """Проверить одну запись журнала.

    Обязательны ``schema``, ``pid``, ``t`` и ``status``. Для исхода нужны
    ещё ``h``, ``Nbad`` и ``Ngood``; у отказа обязателен ``reason`` или
    ``code``. Для ``call``/``entry`` шаг и счётчики необязательны: это
    якорь входа, а не исход.
    """
    where = f"строка {line_no}: " if line_no else ""
    if not isinstance(data, dict):
        raise DiagnosticsError(where + "ожидается JSON-объект")
    payload = cast(Dict[str, Any], data)
    if payload.get("schema") != SCHEMA:
        raise DiagnosticsError(
            where + f"schema={payload.get('schema')!r}, ожидается {SCHEMA!r}")
    for key in ("pid", "t", "status"):
        if key not in payload:
            raise DiagnosticsError(where + f"нет поля: {key}")
    _integer(payload, "pid", minimum=1)
    _number(payload, "t")
    status = payload["status"]
    if status not in _OUTCOMES + _ENTRIES:
        raise DiagnosticsError(
            where + "status должен быть accepted, rejected, call или entry")
    reason = payload.get("reason", "")
    if reason is not None and not isinstance(reason, str):
        raise DiagnosticsError(where + "reason должен быть строкой")
    code = payload.get("code", "")
    if code is not None and not isinstance(code, str):
        raise DiagnosticsError(where + "code должен быть строкой")
    if status in _ENTRIES:
        if "h" in payload:
            _number(payload, "h", minimum=0)
        for key in ("Nbad", "Ngood"):
            if key in payload:
                _integer(payload, key)
        return payload
    for key in ("h", "Nbad", "Ngood"):
        if key not in payload:
            raise DiagnosticsError(where + f"нет поля: {key}")
    _number(payload, "h", minimum=0)
    _integer(payload, "Nbad")
    _integer(payload, "Ngood")
    if status == "rejected" and not (reason or code):
        raise DiagnosticsError(
            where + "отказ шага должен называть причину: reason или code")
    return payload


def parse_diagnostics(raw: bytes) -> Dict[str, Any]:
    """Разобрать одну запись (один JSON-объект) схемы."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DiagnosticsError(f"файл не UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        detail = getattr(exc, "msg", str(exc))
        line = getattr(exc, "lineno", None)
        where = f" (строка {line})" if line is not None else ""
        raise DiagnosticsError(f"не JSON: {detail}{where}") from exc
    return _record(data)


def parse_log(raw: bytes, *, partial_first_line: bool = False
              ) -> List[Dict[str, Any]]:
    """Разобрать журнал JSON Lines.

    ``partial_first_line`` - хвост начался с середины строки; неполная
    первая строка отбрасывается. Пустые строки пропускаются.
    """
    lines = raw.split(b"\n")
    if partial_first_line and lines:
        lines = lines[1:]
    records: List[Dict[str, Any]] = []
    first_content = True
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            text = line.decode(
                "utf-8-sig" if first_content else "utf-8").strip()
        except UnicodeDecodeError as exc:
            raise DiagnosticsError(
                f"строка {number}: не UTF-8: {exc}") from exc
        first_content = False
        try:
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:
            detail = getattr(exc, "msg", str(exc))
            raise DiagnosticsError(
                f"строка {number}: не JSON: {detail}") from exc
        records.append(_record(data, number))
    if not records:
        raise DiagnosticsError("в файле нет записей")
    return records


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


def read_diagnostics_result(path: str, run_started: float) -> DiagnosticsResult:
    """Прочитать хвост журнала и вернуть последний исход или предупреждение.

    ``run_started`` - время начала прогона по тем же часам, что и mtime
    файла. Журнал принимается только от процесса mmain текущей сессии и
    только если он обновлён после начала прогона. При любом сомнении
    значения не показываются: подставлять догадку вместо диагностики
    запрещено.
    """
    expected_pid = _session_pid()
    if expected_pid is None:
        return DiagnosticsResult(
            text=("WARNING: диагностика шага недоступна: PID сессии неизвестен, "
                  "принадлежность файла прогону не проверить"),
            warning=True)
    try:
        raw, partial, mtime, error = sandbox.load_result_tail(
            path, MAX_BYTES, MISSING_LOG)
    except ToolError as exc:
        return DiagnosticsResult(text=_warning(str(exc)), warning=True)
    if error:
        return DiagnosticsResult(text=_warning(error), warning=True)
    try:
        records = parse_log(raw, partial_first_line=partial)
    except DiagnosticsError as exc:
        if partial and "нет записей" in str(exc):
            return DiagnosticsResult(
                text=_warning(
                    f"файл {path} больше {MAX_BYTES} байт; последняя строка "
                    f"не помещается"),
                warning=True)
        return DiagnosticsResult(
            text=_warning(f"{path}: {exc}"), warning=True)
    foreign = next(
        (item for item in records if item["pid"] != expected_pid), None)
    if foreign is not None:
        return DiagnosticsResult(
            text=(f"WARNING: диагностика шага от другого процесса: в "
                  f"журнале pid={foreign['pid']}, сессия pid={expected_pid}; "
                  f"значения не приняты"),
            warning=True)
    if mtime < run_started - STALE_TOLERANCE_SECONDS:
        return DiagnosticsResult(
            text=(f"WARNING: файл диагностики не обновлён этим прогоном "
                  f"(mtime={mtime:.3f}, старт={run_started:.3f}); значения "
                  f"не приняты"),
            warning=True)
    if mtime > time.time() + STALE_TOLERANCE_SECONDS:
        return DiagnosticsResult(
            text=(f"WARNING: mtime файла диагностики в будущем "
                  f"(mtime={mtime:.3f}); значения не приняты"),
            warning=True)
    last = records[-1]
    outcomes = [item for item in records if item["status"] in _OUTCOMES]
    if not outcomes:
        return DiagnosticsResult(
            text=("WARNING: в хвосте журнала только записи входа в im_run; "
                  "исхода шага нет"),
            status=last["status"], record=last, warning=True,
            last_status=last["status"])
    outcome = outcomes[-1]
    pending = last["status"] in _ENTRIES
    text = _line(outcome)
    if pending:
        text += ("\nWARNING: после последнего исхода был вход в im_run "
                 "без исхода")
    return DiagnosticsResult(
        text=text, status=outcome["status"], record=outcome, warning=False,
        last_status=last["status"], pending_entry=pending)


def read_diagnostics(path: str, run_started: float) -> str:
    """Строковая форма `read_diagnostics_result` для ответов инструментов."""
    return read_diagnostics_result(path, run_started).text
