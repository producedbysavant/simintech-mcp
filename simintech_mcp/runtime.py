"""Исполнение инструментов: COM-поток, контракт отказа, журнал вызовов.

Три вещи, которые обязан разделять каждый инструмент:

* **COM привязан к потоку.** Все обращения к COM идут через один выделенный
  поток (`_COM_EXECUTOR`, `max_workers=1`): FastMCP выполняет синхронные
  инструменты в пуле и чередует потоки, а чужой поток даёт
  `CO_E_OBJNOTCONNECTED`. Параллелить COM нельзя — `max_workers=1` здесь не
  запас, а условие работоспособности.
* **Отказ приходит как `isError`, а не текстом.** `_call_guarded` превращает и
  исключение, и строку `"ERROR: …"` в `ToolError`: только по исключению MCP
  выставляет `isError`. Инструмент, вернувший отказ текстом, выглядит для
  клиента успехом.
* **Журнал — только в stderr или файл.** stdout занят JSON-RPC, и одна строка
  лога рвёт транспорт.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from types import FunctionType
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple, cast

from fastmcp.exceptions import ToolError


# ─── Поток для COM ────────────────────────────────────────────────

# COM-объект привязан к апартаменту создавшего его потока. Использование его
# из другого потока даёт «Объект не подключен к серверу» (CO_E_OBJNOTCONNECTED),
# а то и зависание. FastMCP выполняет синхронные инструменты в пуле потоков и
# чередует их, поэтому все обращения к COM идут через ОДИН выделенный поток:
# клиент и проект создаются и используются в нём же.
_COM_EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                   thread_name_prefix="simintech-com")

#: Предел ожидания ответа COM-вызова. Без него подвисание COM останавливает
#: сервер целиком: транспорт перестаёт отвечать, и клиент не получает даже
#: отказа.
COM_CALL_TIMEOUT = 120.0

#: Префикс, которым инструменты исторически помечали отказ в тексте ответа.
ERROR_PREFIX = "ERROR:"

#: Метка на обёртке инструмента: `True` — вызов идёт через COM-поток
#: (`com_threaded`), `False` — без него (`plain_tool`). Наличия `__wrapped__`
#: для проверки мало: инструмент, ошибочно помеченный `plain_tool`, обёрнут
#: ровно так же, но COM-вызов из чужого потока даёт `CO_E_OBJNOTCONNECTED`, а
#: на Linux-тестах (COM нет вовсе) не проявляется. Метку ставит сам декоратор,
#: поэтому тест поверхности читает фактически применённый, а не любой.
COM_THREAD_MARK = "_simintech_com_threaded"

#: Метка мутирующего инструмента: `True` — к успешному ответу обвязка
#: добавляет адрес правки («Изменения внесены в: …»,
#: `session.mutation_note`). Метку ставит декоратор по флагу
#: `mutates_project`, поэтому тест поверхности видит фактический контракт:
#: забытый флаг вернул бы слепые ответы — ровно дыра issue #18 (импорт ушёл
#: не в тот проект, а ответ не назвал, куда именно).
MUTATES_PROJECT_MARK = "_simintech_mutates_project"

#: Метка спасательного инструмента (`com_threaded(rescue=True)`): в состоянии
#: «COM-поток занят зависшим вызовом» ему **не** отвечают мгновенным отказом —
#: он встаёт в очередь и выполнится, когда поток освободится. Единственный
#: такой инструмент — `disconnect`: им и сбрасывается зависшая сессия.
RESCUE_MARK = "_simintech_rescue"


class _StuckCall(NamedTuple):
    """Зависший COM-вызов: таймаут сработал, а future не завершился.

    Поток один (`_COM_EXECUTOR`, max_workers=1): пока он занят, каждый
    следующий вызов стоял бы в очереди и падал бы по своему 120-с таймауту —
    «залипание» каскадом (репорт fdd002 08.10.2026: `add_block` залип на
    120 с, mmain мёртв). Помним такой вызов и отказываем следующим
    **немедленно**, не занимая очередь.
    """

    tool: str
    at: float
    future: "Future[Any]"


#: Текущий зависший вызов (последний, чей таймаут сработал). Снимается, когда
#: его future всё-таки завершился: следующий вызов увидит `done()` и пойдёт
#: как обычно.
_stuck_call: "Optional[_StuckCall]" = None
_STUCK_LOCK = threading.Lock()


def _mmain_state() -> str:
    """Живость процесса mmain сессии — **вне COM** (зависший поток не трогаем).

    `get_mmain_pids` библиотеки — wmic/tasklist/PowerShell, без COM, поэтому
    работает и при занятом потоке. Незнание не выдаётся за «мёртв»: если PID
    сессии или список процессов недоступны, возвращается пустая строка.
    """
    try:
        from . import session as _session
        client = getattr(_session, "_client", None)
        pid = None if client is None else getattr(client, "session_pid", None)
    except Exception:                                         # noqa: BLE001
        pid = None
    try:
        from simintech_api.utils.processes import get_mmain_pids
        live = get_mmain_pids()
    except Exception:                                         # noqa: BLE001
        return ""
    if pid is None or int(pid) <= 0:
        if live:
            return f"Живые процессы mmain.exe: {sorted(live)}."
        return "Ни одного процесса mmain.exe не найдено."
    if int(pid) in live:
        return (f"Процесс сессии mmain.exe (PID {pid}) жив, но COM не "
                f"отвечает.")
    return (f"Процесс сессии mmain.exe (PID {pid}) уже завершён — "
            f"перезапускать его не нужно: `disconnect` сбросит мёртвое "
            f"соединение, следующий вызов поднимет новый процесс.")


def _stuck_gate(tool: str, *, rescue: bool) -> "Optional[str]":
    """Мгновенный отказ, если COM-поток занят зависшим вызовом; иначе `None`.

    Спасательный (`rescue=True`) мгновенного отказа не получает — он должен
    дойти до очереди: иначе сессию нечем сбросить.
    """
    global _stuck_call
    with _STUCK_LOCK:
        stuck = _stuck_call
        if stuck is None:
            return None
        if stuck.future.done():
            _stuck_call = None
            return None
    if rescue:
        return None
    waited = time.monotonic() - stuck.at
    state = _mmain_state()
    tail = f" {state}" if state else ""
    return (f"COM-поток занят: вызов `{stuck.tool}` не ответил за "
            f"{COM_CALL_TIMEOUT:.0f} с (висит уже {waited:.0f} с) — mmain не "
            f"отвечает.{tail} Перезапустите mmain.exe, вызовите `disconnect` "
            f"и повторите: до этого `{tool}` и другие вызовы отказывают "
            f"сразу, 120-с очередь не копится (`disconnect` исключён — он "
            f"выполнится, когда поток освободится).")


def _remember_stuck(tool: str, future: "Future[Any]") -> None:
    """Запомнить зависший вызов (future уже завершился — не запоминать)."""
    global _stuck_call
    if future.done():
        return
    with _STUCK_LOCK:
        _stuck_call = _StuckCall(tool=tool, at=time.monotonic(), future=future)


def _call_guarded(fn: Callable[..., Any], args: Tuple[Any, ...],
                  kwargs: Dict[str, Any]) -> Any:
    """Вызвать тело инструмента, превратив отказ в `ToolError`.

    Инструменты сообщали об ошибке строкой «ERROR: …», а MCP помечает отказ
    **только по исключению**. Клиент, доверяющий флагу `isError`, видел 100%
    успеха и продолжал работать по недостоверной модели состояния. Здесь это
    принуждается кодом, а не соглашением: сюда попадает любой инструмент,
    включая будущие.
    """
    try:
        result = fn(*args, **kwargs)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc
    if isinstance(result, str):
        if result.startswith(ERROR_PREFIX):
            raise ToolError(result[len(ERROR_PREFIX):].strip())
        _report_contract_drift(result)
    return result


def _report_contract_drift(result: str) -> None:
    """Заметить текст, похожий на отказ, но не оформленный как `ERROR:`.

    Контракт держится на точном префиксе, а клиент доверяет только `isError`:
    текст вида «Error: …» ушёл бы ему как успех — молча и невозвратимо.
    Поведение здесь не меняется (иначе пришлось бы угадывать, что автор имел
    в виду), но в журнале остаётся след. Журнал выключен по умолчанию, так
    что цена проверки — сравнение восьми символов.
    """
    head = result.lstrip()[:8].lower()
    if head.startswith(("error", "ошибка", "отказ")):
        log_event("contract-drift", text=result[:120])


def _append_mutation_note(result: Any) -> Any:
    """Дописать к успешному ответу правки адресата: «Изменения внесены в: …».

    Здесь же сессия узнаёт, что у текущего проекта есть **несохранённые**
    правки (`session.mark_mutated`): успешная правка — единственный признак,
    и точка эта одна на все мутирующие инструменты (issue #18) — заведи её
    инструменты сами, новый мутирующий выпал бы из счёта.

    Импорт `session` ленивый: обвязке незачем тянуть домен при импорте модуля
    (тесты runtime не обязаны знать `simintech_api`), а «какому проекту
    принадлежит правка» всё равно решает сессия — единственное место, где
    проект меняется.
    """
    if not isinstance(result, str):
        return result
    from . import session
    session.mark_mutated()
    note = session.mutation_note()
    return result + note if note else result


# ─── Структурированный журнал ─────────────────────────────────────

#: Переменная окружения: куда писать журнал вызовов. Пусто/0 — выключено,
#: `stderr` (или 1/true) — в stderr, иначе — путь к файлу.
#:
#: stdout для журнала непригоден: там JSON-RPC, и одна строка лога ломает
#: транспорт (см. `_StdoutGuard`) — поэтому «stdout» здесь не принимается.
LOG_ENV = "SIMINTECH_MCP_LOG"

_LOG_OFF = frozenset({"", "0", "false", "off", "no", "none"})
#: `stdout` здесь намеренно: писать журнал в stdout нельзя (там JSON-RPC),
#: поэтому такое значение понимается как «в stderr», а не как имя файла
#: `stdout` в рабочем каталоге сервера.
_LOG_STDERR = frozenset({"1", "true", "yes", "on", "stderr", "stdout"})

#: Журнал пишут и рабочий COM-поток, и поток транспорта — строки не должны
#: перемешиваться между собой.
_LOG_LOCK = threading.Lock()


def log_event(event: str, **fields: Any) -> None:
    """Дописать событие в журнал — одна JSON-строка на событие.

    Журнал вспомогательный: его отказ не должен превращаться в отказ
    инструмента, поэтому ошибки записи глушатся, а не поднимаются.
    """
    target = (os.environ.get(LOG_ENV) or "").strip()
    if target.lower() in _LOG_OFF:
        return
    record: Dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "event": event}
    record.update(fields)
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return
    try:
        with _LOG_LOCK:
            if target.lower() in _LOG_STDERR:
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
            else:
                with open(target, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
    except OSError:
        pass


def _log_args(fn: Callable[..., Any], args: Tuple[Any, ...],
              kwargs: Dict[str, Any]) -> Dict[str, str]:
    """Аргументы вызова для журнала: имена из сигнатуры, значения урезаны."""
    try:
        # `__code__` есть у функции (инструменты — обычные `def`), а не у
        # произвольного `Callable` — сужаем до `FunctionType` явно.
        code = cast(FunctionType, fn).__code__
        names = code.co_varnames[:code.co_argcount]
        pairs = dict(zip(names, args))
        pairs.update(kwargs)
        return {name: str(value)[:120] for name, value in pairs.items()}
    except Exception:                                          # noqa: BLE001
        return {}


def _instrumented(fn: Callable[..., Any],
                  invoke: Callable[..., Any]) -> Callable[..., Any]:
    """Общая обвязка инструмента: журнал вызова плюс контракт отказа.

    Отказ превращается в `ToolError` (см. `_call_guarded`) — только по нему
    MCP выставляет `isError`; сюда же пишется запись журнала с длительностью
    и исходом, поэтому все инструменты логируются одинаково.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            result = invoke(*args, **kwargs)
        except ToolError as exc:
            log_event("tool", tool=fn.__name__, ok=False,
                      ms=round((time.monotonic() - started) * 1000, 1),
                      args=_log_args(fn, args, kwargs), error=str(exc)[:300])
            raise
        log_event("tool", tool=fn.__name__, ok=True,
                  ms=round((time.monotonic() - started) * 1000, 1),
                  args=_log_args(fn, args, kwargs))
        return result

    return wrapper


def com_threaded(fn: Callable[..., Any] | None = None, *,
                 mutates_project: bool = False,
                 rescue: bool = False) -> Callable[..., Any]:
    """Выполнить инструмент в выделенном COM-потоке.

    Делает три вещи, каждая из которых обязательна:
    * сериализует вызовы в одном потоке (иначе CO_E_OBJNOTCONNECTED);
    * ограничивает ожидание (`COM_CALL_TIMEOUT`) — зависание COM не должно
      подвешивать сервер молча;
    * приводит отказ к `ToolError`, то есть к `isError` в ответе.

    Плюс контракт «ответ правки называет проект»: при `mutates_project=True`
    к успешному ответу добавляется адрес (`session.mutation_note`). Хвост
    ставит обвязка, а не сам инструмент: текущий проект — скрытое состояние
    сессии, и новый мутирующий инструмент иначе молча выпал бы из контракта
    (issue #18 — импорт ушёл не в тот проект, а ответ не назвал, куда).

    **Зависший вызов не копит очередь** (репорт fdd002 08.10.2026: `add_block`
    залип на 120 с, mmain мёртв). Поток один, и без памяти о зависшем вызове
    каждый следующий клиентский вызов стоял бы в очереди и падал бы по
    своему 120-с таймауту — каскадом. Поэтому зависший future запоминается
    (`_remember_stuck`): последующие вызовы получают **мгновенный** отказ с
    диагнозом живости процесса mmain (`_mmain_state`, вне COM), пока future
    не завершится. Спасательный инструмент (`rescue=True`, это `disconnect`)
    мгновенного отказа не получает: он встаёт в очередь и выполняется, когда
    поток освободится, — иначе зависшую сессию нечем сбросить.

    Ограничение таймаута: сам COM-вызов в потоке **не прерывается**, поэтому
    после срабатывания таймаута работа может ещё продолжаться — измерено на
    SimInTech64 (2026-09-17): `step(count=1000)` при `COM_CALL_TIMEOUT=1.0`
    отдал клиенту отказ на 1.14 с, а шаги досчитались (модельное время дошло до
    0.001). Но поток при этом освобождается сам (пробный `GetProcessID` сразу
    после отказа ждал 0.000 с), и перезапуск `mmain.exe` обычно **не нужен**:
    при внешнем дедлайне на долгом штатном `run` сессия восстанавливается одним
    `disconnect` + `create_project` (проверено). Перезапуск обязателен только
    тогда, когда COM-вызов не вернулся вовсе, — а в этом случае занят
    единственный выделенный поток, и один перезапуск `mmain.exe` без
    переподключения ничего не даёт: флаг `connected` не отражает живучесть COM,
    и повтор подхватил бы тот же мёртвый прокси, пока не вызван `disconnect`.
    Поэтому рецепт в тексте отказа называет оба шага, а диагноз добавляет,
    жив ли процесс сессии (завершён — перезапускать нечего).
    """
    if fn is None:
        # Декоратор вызван с флагами (`@com_threaded(mutates_project=True)`):
        # вернуть обёртку, которая дождётся самой функции.
        return functools.partial(com_threaded,
                                 mutates_project=mutates_project,
                                 rescue=rescue)

    def invoke(*args: Any, **kwargs: Any) -> Any:
        refusal = _stuck_gate(fn.__name__, rescue=rescue)
        if refusal is not None:
            raise ToolError(refusal)
        future = _COM_EXECUTOR.submit(_call_guarded, fn, args, kwargs)
        try:
            result = future.result(timeout=COM_CALL_TIMEOUT)
        except FutureTimeout as exc:
            _remember_stuck(fn.__name__, future)
            state = _mmain_state()
            tail = f" {state}" if state else ""
            raise ToolError(
                f"COM-вызов не ответил за {COM_CALL_TIMEOUT:.0f} с: SimInTech "
                f"занят или завис.{tail} Перезапустите mmain.exe, затем "
                f"вызовите `disconnect` (он сбросит мёртвое соединение и "
                f"текущий проект) и повторите: без `disconnect` повтор "
                f"подхватил бы тот же нерабочий COM-прокси, и отказывали бы "
                f"все COM-инструменты, а не только этот. Если вызов не "
                f"вернулся вовсе, следующие будут отказывать сразу — очередь "
                f"120-с таймаутов не копится (`disconnect` исключён)."
            ) from exc
        if mutates_project:
            result = _append_mutation_note(result)
        return result

    wrapper = _instrumented(fn, invoke)
    setattr(wrapper, COM_THREAD_MARK, True)
    if mutates_project:
        setattr(wrapper, MUTATES_PROJECT_MARK, True)
    if rescue:
        setattr(wrapper, RESCUE_MARK, True)
    return wrapper


def plain_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Как `com_threaded`, но без COM-потока — для инструментов без COM.

    Разбор сохранённого проекта, каталог и справка COM не трогают, поэтому
    выделенный поток им не нужен (и на Linux его нет). Контракт отказа при
    этом общий: иначе MCP не выставил бы `isError`.
    """
    def invoke(*args: Any, **kwargs: Any) -> Any:
        return _call_guarded(fn, args, kwargs)

    wrapper = _instrumented(fn, invoke)
    setattr(wrapper, COM_THREAD_MARK, False)
    return wrapper
