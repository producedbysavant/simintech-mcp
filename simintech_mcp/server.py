"""FastMCP-сервер для управления SimInTech.

Оборачивает библиотеку simintech-api (COM API) в MCP-инструменты, чтобы
ИИ-агент мог создавать модели, соединять блоки, запускать расчёт и читать
сигналы через текстовые вызовы.

Требуется Windows с зарегистрированным COM-сервером (mmain.exe /regserver).

Запуск:
    simintech-mcp
    python -m simintech_mcp.server
"""

from __future__ import annotations

import functools
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

import defusedxml.ElementTree as DefusedET
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from simintech_api import COMClient, Project, Wire
from simintech_api.catalog import (
    decode_xprt,
    load_default_catalog,
    parse_xprt_block_props,
    parse_xprt_readonly,
)
from simintech_api.constants import (
    default_output_dir as simintech_default_output_dir,
    standard_block_size,
)
from simintech_api.utils.xprt_signals import XprtSignalReader

# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
    instructions=(
        "Управление SimInTech через COM API (Windows, mmain.exe /regserver). "
        "Сборка и расчёт модели: create_project → add_block → connect → run → "
        "read_output_file (результат пишет блок «В файл»). "
        "Имена параметров блоков берите из ресурса simintech://blocks/catalog: "
        "неизвестное имя — отказ, запись в вычисляемый параметр — тоже. "
        "Полный список инструментов — в tools/list, он же источник истины. "
        "Особенности среды — в репозитории simintech-code: CLAUDE.md и "
        "docs/reference/com_api_inventory.md."
    ),
)

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


def _call_guarded(fn, args, kwargs):
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


def _log_args(fn, args, kwargs) -> Dict[str, str]:
    """Аргументы вызова для журнала: имена из сигнатуры, значения урезаны."""
    try:
        names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
        pairs = dict(zip(names, args))
        pairs.update(kwargs)
        return {name: str(value)[:120] for name, value in pairs.items()}
    except Exception:                                          # noqa: BLE001
        return {}


def _instrumented(fn, invoke):
    """Общая обвязка инструмента: журнал вызова плюс контракт отказа.

    Отказ превращается в `ToolError` (см. `_call_guarded`) — только по нему
    MCP выставляет `isError`; сюда же пишется запись журнала с длительностью
    и исходом, поэтому все инструменты логируются одинаково.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
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


def _com_threaded(fn):
    """Выполнить инструмент в выделенном COM-потоке.

    Делает три вещи, каждая из которых обязательна:
    * сериализует вызовы в одном потоке (иначе CO_E_OBJNOTCONNECTED);
    * ограничивает ожидание (`COM_CALL_TIMEOUT`) — зависание COM не должно
      подвешивать сервер молча;
    * приводит отказ к `ToolError`, то есть к `isError` в ответе.

    Ограничение таймаута: сам COM-вызов в потоке не прерывается, поэтому после
    срабатывания таймаута сервер, как правило, пригоден только до перезапуска —
    инструмент об этом честно сообщает.
    """
    def invoke(*args, **kwargs):
        future = _COM_EXECUTOR.submit(_call_guarded, fn, args, kwargs)
        try:
            return future.result(timeout=COM_CALL_TIMEOUT)
        except FutureTimeout as exc:
            raise ToolError(
                f"COM-вызов не ответил за {COM_CALL_TIMEOUT:.0f} с: SimInTech "
                f"занят или завис. Перезапустите mmain.exe и повторите."
            ) from exc

    return _instrumented(fn, invoke)


def _plain_tool(fn):
    """Как `_com_threaded`, но без COM-потока — для инструментов без COM.

    Разбор сохранённого проекта, каталог и справка COM не трогают, поэтому
    выделенный поток им не нужен (и на Linux его нет). Контракт отказа при
    этом общий: иначе MCP не выставил бы `isError`.
    """
    def invoke(*args, **kwargs):
        return _call_guarded(fn, args, kwargs)

    return _instrumented(fn, invoke)


# ─── Подключение ──────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def status() -> str:
    """Проверить доступность COM-сервера SimInTech (Windows)."""
    if sys.platform != "win32":
        return "COM SimInTech доступен только на Windows"
    try:
        c = _ensure_client()
        pid = c.get_process_id()
        return f"SimInTech подключён (PID={pid})"
    except Exception as exc:
        # Отказ, а не текст: клиент, доверяющий `isError`, иначе увидел бы
        # «успех» там, где подключиться не удалось.
        raise ToolError(f"SimInTech недоступен: {exc}") from exc


@mcp.tool()
@_com_threaded
def disconnect() -> str:
    """Завершить сессию: закрыть проект и отсоединиться от COM-сервера.

    Сбрасывается **всё** состояние сессии. Раньше обнулялся только клиент, а
    текущий проект оставался в глобальной переменной: следующие вызовы шли с
    мёртвым `ProjectId`.
    """
    global _client
    if _client is None and _project is None:
        return "Без изменений: соединения не было — сбрасывать нечего"
    project = _project
    # Проект и линии сбрасываются вместе (`_set_project`) — до попытки
    # закрыть: состояние сессии не должно зависеть от того, ответил ли COM.
    _set_project(None)
    failed = ""
    if project is not None:
        try:
            project.close()
        except Exception as exc:                              # noqa: BLE001
            # Не выдаём отказ за успех: `COMClient.disconnect()` проекты не
            # закрывает, поэтому при сбое `CloseProject` проект останется жить
            # в mmain.exe.
            failed = (f" ВНИМАНИЕ: проект закрыть не удалось "
                      f"({type(exc).__name__}: {exc}) — он мог остаться "
                      f"открытым в SimInTech.")
    if _client is not None:
        _client.disconnect()
        _client = None
    return "Сессия завершена: проект закрыт, соединение разорвано" + failed


# ─── Проекты ──────────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def create_project(project_hint: str = "model",
                   end_time: Optional[float] = None) -> str:
    """Создать новый проект SimInTech из шаблона «пустой модели».

    Проект создаётся из шаблона поставки (`Схема модели общего вида.prt`), а не
    через `NewProject`: пустой проект не считает — в нём нет расчётного слоя и
    настроек расчёта, поэтому модельное время не растёт ни через `run`, ни
    через `step`, хотя вызовы и возвращают успех. Особенности среды —
    `simintech-code/docs/reference/com_api_inventory.md`, §18.

    Предыдущий открытый проект закрывается: иначе они копились бы внутри
    `mmain.exe`.

    Args:
        project_hint: подсказка для сообщения. Имя проекта задаёт среда,
            переименование через COM недоступно.
        end_time: конечное время расчёта в секундах (> 0); по умолчанию — из
            шаблона (10 с). Меняется инструментом `set_calc_time`.
    """
    # Проверяем до открытия шаблона: иначе неверное значение оставило бы
    # созданный проект висеть в mmain.exe — он не попал бы ни в _project,
    # ни в закрытие.
    if end_time is not None and end_time <= 0:
        raise ToolError("end_time должен быть положительным числом секунд")

    prj = Project.from_template(_ensure_client())
    replaced = _replace_project(prj)
    if end_time is not None:
        prj.set_calc_end_time(end_time)
    tail = (f", время расчёта {end_time} с" if end_time is not None
            else ", время расчёта — из шаблона (10 с)")
    return (f"Проект '{project_hint}' создан из шаблона (id={prj.id}){tail}."
            + replaced)


@mcp.tool()
@_com_threaded
def set_calc_time(seconds: float) -> str:
    """Задать конечное время расчёта проекта (`endtime` расчётного слоя).

    Расчёт идёт до этого момента; `run(to_time=…)` не может уйти за него.

    Args:
        seconds: конечное время расчёта в секундах (> 0).
    """
    _ensure_project().set_calc_end_time(seconds)
    return f"Время расчёта: {seconds} с"


@mcp.tool()
@_com_threaded
def open_project(path: str) -> str:
    """Открыть существующий проект SimInTech (.prt/.xprt).

    Предыдущий открытый проект закрывается (см. `create_project`).
    """
    prj = Project.open(_ensure_client(), path)
    replaced = _replace_project(prj)
    return f"Проект открыт (id={prj.id})" + replaced


@mcp.tool()
@_com_threaded
def save_project(path: str, binary: bool = False,
                 show_form: bool = True) -> str:
    """Сохранить текущий проект в файл.

    Два формата, и назначение у них разное:

    * XML (`.xprt`, по умолчанию) — обычный текст: читается глазами, диффится,
      переживает перенос между версиями;
    * бинарный (`.prt`, `binary=True`) — **нативный формат проекта**, тот самый,
      который открывает GUI SimInTech. XML в GUI тоже открывается, но двойным
      щелчком по файлу проекта запускается именно `.prt`.

    Перед записью показывается форма проекта (`FormShow`) — иначе файл
    получится «закрытым» для GUI: состояние окна хранится в самом проекте, и
    сессия, работающая через COM, записывает в него признак «окно скрыто». COM
    такой проект потом открывает и считает, а GUI восстанавливает сохранённое
    состояние окна и окна модели не показывает — выглядит как «проект не
    открылся».

    Args:
        path: путь к файлу (абсолютный).
        binary: True — нативный бинарный `.prt`; False — XML `.xprt`.
        show_form: показать форму проекта перед сохранением (см. выше).
            False — для безоконных машин: окно не появится, но и GUI потом
            не покажет окно модели этого проекта.
    """
    project = _ensure_project()
    if show_form:
        project.show_form()
        tail = (" Форма проекта показана — без этого файл открывался бы в GUI "
                "без окна модели.")
    else:
        tail = (" Форму не показывали: GUI откроет файл без окна модели.")
    if binary:
        project.save_binary(path)
        return f"Проект сохранён в бинарный файл (.prt): {path}.{tail}"
    project.save_xml(path)
    return f"Проект сохранён в XML (.xprt): {path}.{tail}"


@mcp.tool()
@_com_threaded
def close_project() -> str:
    """Закрыть текущий проект."""
    if _project is None:
        return "Без изменений: проект не был открыт"
    project = _project
    # Порядок: сначала закрыть, потом сбросить состояние. Если `CloseProject`
    # не ответил, проект остаётся текущим — его видно и можно закрыть повторно,
    # а не потерять открытым внутри mmain.exe.
    project.close()
    _set_project(None)
    return "Проект закрыт"


# ─── Проверка имён параметров ─────────────────────────────────────

def _check_params(class_name: str, names: Iterable[str], *,
                  allow_unknown: bool, notes: List[str]) -> None:
    """Проверить имена параметров до записи; примечание дописать в `notes`.

    `SetBlockProp` не отвергает неизвестное имя: запись уходит в никуда **без
    ошибки**, и агент считает параметр заданным. Проверено на SimInTech64:
    `Константа.y0 = 5` не меняет ничего, а отказа нет. Поэтому имена
    сверяются с каталогом блоков (`simintech_api/data/block_catalog.json`)
    **до** вызова COM.

    Класс, которого в каталоге нет (например, «В файл»), не проверяется:
    каталог собран не для всех классов, и отказ сломал бы рабочий сценарий.
    Об этом говорится в примечании — и оно пишется в переданный список, а не
    возвращается строкой: возвращённую строку вызывающий может потерять, и
    тогда предупреждение о непроверенной записи исчезнет незаметно. Список —
    тот же, из которого затем собирается ответ инструмента.

    Args:
        class_name: класс блока («Константа», «Усилитель», ...).
        names: проверяемые имена параметров.
        allow_unknown: True — не проверять (каталог может отставать).
        notes: список, куда дописывается примечание.

    Raises:
        ToolError: параметра у класса нет либо он вычисляемый.
    """
    if allow_unknown:
        return
    catalog = load_default_catalog()
    # Каталог может отсутствовать целиком: `BlockCatalog.load` на пропавший
    # файл возвращает ПУСТОЙ каталог, а не ошибку. Тогда «класса нет в
    # каталоге» — это не свойство класса, а отказ проверки вообще, и молчать
    # об этом нельзя: защита тихо выключилась бы для всех классов, а
    # примечание обвиняло бы конкретный класс.
    if len(catalog) == 0:
        notes.append("ВНИМАНИЕ: каталог блоков недоступен целиком — имена "
                     "параметров не проверяются ни для одного класса. "
                     "Проверьте установку simintech-api (файл "
                     "data/block_catalog.json).")
        return
    if not catalog.has(class_name):
        notes.append(f"Класс '{class_name}' отсутствует в каталоге блоков — "
                     f"имена параметров проверить нечем.")
        return
    known = catalog.props_for(class_name)
    for name in names:
        # `Name` в каталоге есть (общее свойство), но COM запись игнорирует:
        # `SetBlockProp("Name", …)` блок НЕ переименовывает — имя остаётся
        # автоматическим. Без этой ветки проверка пропускала бы ровно ту
        # запись, ради которой она и делалась: успешный ответ без эффекта.
        if name == "Name":
            raise ToolError(
                "Блоки не переименовываются через COM: запись в 'Name' "
                "проходит без ошибки, но имя остаётся автоматическим (его "
                "даёт list_blocks). Если запись всё же нужна — "
                "allow_unknown=True."
            )
        if catalog.is_readonly(class_name, name):
            raise ToolError(
                f"'{name}' — вычисляемый параметр блока '{class_name}': COM "
                f"принимает запись, но значение не меняется — отказ "
                f"молчаливый. Задаваемые параметры: {', '.join(known)}."
            )
        if name not in known:
            raise ToolError(
                f"У блока '{class_name}' нет параметра '{name}'. Известные "
                f"параметры: {', '.join(known)}. Если параметр существует, "
                f"но не попал в каталог — повторите вызов с "
                f"allow_unknown=True."
            )


# ─── Блоки и связи ────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def add_block(class_name: str, name_hint: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "", in_ports: int = 0,
              allow_unknown_props: bool = False) -> str:
    """Добавить блок на главную страницу проекта.

    Args:
        class_name: класс блока (русское имя, напр. 'Константа',
            'Усилитель', 'Сумматор', 'Интегратор', 'Синусоида',
            'Ступенька', 'Временной график', 'В файл').
        name_hint: желаемое имя. **Заведомо не применяется**: COM не
            переименовывает блоки, имя остаётся автоматическим (`k_0`, `kx_0`).
            Ответ вернёт фактическое имя — используйте его в `connect`,
            `get_block_params`, `layout_place`.
        x, y: координаты **левого верхнего угла** блока, не центра (так их
            трактует SimInTech: `SetBlockPosition` — это Left/Top). Можно не
            задавать — их расставит `layout_place`, он центрирует сам.
        props: параметры через запятую, напр. 'a=2' или 'a=[1, -1]'.
            Имена короткие и различаются по классам: у «Константы» — `a`
            (не `y0`), у «Сумматора» — `a` (веса входов). Имена сверяются с
            каталогом блоков **до** создания блока: неизвестное имя — отказ
            со списком известных, а не молчаливая запись в никуда.
        in_ports: число входных портов (0 — не менять). Нужно для блоков с
            настраиваемым числом входов: у «Сумматора» их по умолчанию два,
            и более длинный `a` сам по себе портов не добавляет.
        allow_unknown_props: True — не сверять имена с каталогом. Нужно, если
            параметр у блока есть, а в каталог не попал (каталог собран не
            для всех классов).
    """
    project = _ensure_project()
    # Параметры разбираются и проверяются ДО создания блока: иначе отказ
    # оставил бы на схеме блок, которого нет в ответе инструмента.
    pairs = []
    ignored = []
    if props:
        for pair in _split_props(props):
            if "=" in pair:
                k, _, v = pair.partition("=")
                pairs.append((k.strip(), _parse_val(v.strip())))
            else:
                # Молча выбросить нельзя: «a=2 мусор» применил бы `a` и не
                # сказал, что вторая часть потеряна.
                ignored.append(pair)
    notes: List[str] = []
    _check_params(class_name, [name for name, _ in pairs],
                  allow_unknown=allow_unknown_props, notes=notes)

    page = project.get_main_page()
    block = page.create_block(class_name, x, y)
    if name_hint:
        block.set_name(name_hint)
    if in_ports:
        block.set_in_port_count(in_ports)
        # Число входов меняет штатный размер блока: у «Сумматора» 32x32 при
        # двух входах и 32x48 при трёх (замерено по эталонным моделям).
        size = standard_block_size(class_name, in_ports)
        if size:
            block.set_position(x, y, width=size[0], height=size[1])
    for name, value in pairs:
        block.set_property(name, value)

    actual = block.get_name()
    if name_hint and actual != name_hint:
        # Проверено на SimInTech64: SetBlockProp("Name") НЕ переименовывает
        # блок — имя остаётся автоматическим (k_0, kx_0, ...), ни в
        # get_name(), ни в .xprt. Молчаливое расхождение опаснее отказа:
        # последующий connect по имени не найдёт блок.
        notes.append(f"имя '{name_hint}' НЕ применилось — блок называется "
                     f"'{actual}'; переименование через COM недоступно")
    if ignored:
        notes.append(f"параметры без '=' пропущены: {', '.join(ignored)}")
    tail = (" " + "; ".join(notes) + ".") if notes else ""
    return (f"Блок '{class_name}' создан (id={block.id}, name={actual})."
            f"{tail}")


@mcp.tool()
@_com_threaded
def connect(src: str, dst: str,
            out_index: int = 0, in_index: int = 0) -> str:
    """Соединить выход блока src с входом блока dst линией связи.

    Созданная линия запоминается, но **не трассируется здесь**: трассировка
    (`layout_place`) делается, когда блоки займут свои места. Нормализовать
    сразу нельзя — блоки в этот момент стоят в (0,0) друг на друге, и
    `NormalizeWire` прокладывает маршрут в обход наложенных блоков, оставляя в
    геометрии точки вида (-160,-1056). Проверено на SimInTech64 2026-09-15:
    такие точки потом не пересчитываются, и линия остаётся кривой даже после
    расстановки.

    Args:
        src: имя/алиас блока-источника.
        dst: имя/алиас блока-приёмника.
        out_index: номер выходного порта источника (0-based).
        in_index: номер входного порта приёмника (0-based).
    """
    page = _ensure_project().get_main_page()
    b1 = page.find_block(src)
    b2 = page.find_block(dst)
    if b1 is None:
        return f"ERROR: блок '{src}' не найден на странице"
    if b2 is None:
        return f"ERROR: блок '{dst}' не найден на странице"
    wire = b1.connect(b2, out_index=out_index, in_index=in_index)
    # Храним и концы связи: по ним `layout_place` выравнивает блоки так, чтобы
    # линия шла без лишнего излома.
    _WIRES.append((wire, src, out_index, dst, in_index))
    return f"Соединено {src} -> {dst} (wire={wire.id})"


@mcp.tool()
@_com_threaded
def list_blocks() -> str:
    """Вывести список блоков текущей страницы проекта."""
    blocks = _ensure_project().get_main_page().get_blocks()
    if not blocks:
        return "Блоков на странице нет"
    lines = []
    for b in blocks[:50]:
        try:
            nm = b.get_name()
        except Exception:
            nm = ""
        lines.append(f"  {nm or '(без имени)'} [{b.class_name}] (id={b.id})")
    more = f"\n  ... и ещё {len(blocks) - 50}" if len(blocks) > 50 else ""
    return "Блоки:\n" + "\n".join(lines) + more


@mcp.tool()
@_com_threaded
def get_block_params(block: str) -> str:
    """Прочитать параметры блока.

    COM API не умеет перечислять свойства блока, поэтому читаются имена из
    каталога блоков (`simintech_api/data/block_catalog.json`). Имена короткие
    и различаются по классам: у «Константы» `a`, у «Ступеньки» `t`/`y0`/`yk`,
    у «Интегратора» `k`/`x0`.

    Args:
        block: имя блока на главной странице — автоматическое (их даёт
            `list_blocks`); переименование через COM недоступно.
    """
    page = _ensure_project().get_main_page()
    target = page.find_block(block)
    if target is None:
        return f"ERROR: блок '{block}' не найден на странице"
    try:
        props = target.get_properties()
        class_name = target.class_name
    except Exception as exc:
        return f"ERROR: {exc}"

    if not props:
        return (f"Блок '{block}' [{class_name}]: параметры неизвестны — "
                f"класс отсутствует в каталоге блоков")
    lines = [f"  {k} = {v}" for k, v in sorted(props.items())]
    return f"Блок '{block}' [{class_name}]:\n" + "\n".join(lines)


@mcp.tool()
@_com_threaded
def set_block_param(block: str, param: str, value: str,
                    allow_unknown: bool = False) -> str:
    """Установить параметр блока и переинициализировать блок.

    Блок переинициализируется (`InitBlock`) — без этого изменение может не
    дойти до расчёта: карта COM API отмечает, что `SetBlockProp` не влияет
    на уже инициализированные блоки (например, «Константа»).

    Имя параметра сверяется с каталогом блоков **до** записи. Раньше здесь
    было предупреждение уже после записи, а сам `SetBlockProp` неизвестные
    имена не отвергает: значение уходило в никуда, и по ответу нельзя было
    отличить применённый параметр от неприменённого.

    Отдельно отвергается `Name`: он в каталоге есть (общее свойство), но
    блок не переименовывает — COM такой записи не применяет.

    Args:
        block: имя блока на главной странице (автоимя из `list_blocks`).
        param: имя параметра блока (см. `get_block_params`).
        value: значение строкой; массивы — в стиле SimInTech, напр. '[1, -1]'.
        allow_unknown: True — не сверять имя с каталогом (для параметров,
            которых в каталоге нет).
    """
    page = _ensure_project().get_main_page()
    target = page.find_block(block)
    if target is None:
        return f"ERROR: блок '{block}' не найден на странице"
    notes: List[str] = []
    try:
        _check_params(target.class_name, [param],
                      allow_unknown=allow_unknown, notes=notes)
        target.set_property(param, _coerce_param_value(value))
        target.init()
    except ToolError:
        # Отказ проверки (нет параметра, вычисляемый, `Name`) — уже готовый
        # `ToolError`; возвращать его текстом с префиксом значило бы гонять
        # типизированный отказ через строку и восстанавливать тип обратно.
        raise
    except Exception as exc:
        return f"ERROR: {exc}"
    tail = f" {'; '.join(notes)}" if notes else ""
    return f"{block}.{param} = {value}" + tail


# ─── Расчёт ───────────────────────────────────────────────────────

#: Значения по умолчанию для ожидания расчёта. Само ожидание живёт в
#: библиотеке (`Simulation.run_to`): `RunTo` не блокирующий, и подтверждать
#: достижение отметки надо опросом `GetProjectTime` — это общий контракт, а не
#: деталь MCP.
CALC_WAIT_SECONDS = 30.0
CALC_STALL_SECONDS = 1.0


@mcp.tool()
@_com_threaded
def run(to_time: Optional[float] = None,
        wait_timeout: float = CALC_WAIT_SECONDS,
        stall_seconds: float = CALC_STALL_SECONDS) -> str:
    """Запустить расчёт проекта (опционально до момента времени).

    Проверяется **фактическое** модельное время, а не код возврата: на проекте
    без расчётного слоя или с неподключённым входом `ProjectRun`/`RunTo`/
    `ProjectStep` возвращают успех, а модельное время не растёт. Раньше
    инструмент в этом случае сообщал «Расчёт завершён» — ложное подтверждение.
    Недостижение отметки — **отказ** (`isError`), а не текст в успешном ответе:
    клиент, доверяющий флагу, иначе счёл бы расчёт дошедшим.

    Расчёт идёт до `endtime` проекта, поэтому `to_time` больше него недостижим
    — поднимите время расчёта через `set_calc_time`.

    Args:
        to_time: время окончания расчёта в секундах (если указано).
        wait_timeout: сколько секунд ждать выхода времени на `to_time`.
        stall_seconds: сколько секунд неизменного времени считать признаком
            остановившегося расчёта (после этого ждать не имеет смысла).
    """
    sim = _ensure_project().simulation()
    sim.start()
    if to_time is None:
        sim.run()
        return ("Расчёт запущен (неблокирующий вызов: подтвердить ход можно "
                "через `get_time`)")
    reached = sim.run_to(to_time, timeout=wait_timeout, stall=stall_seconds)
    actual = sim.get_time()
    if not reached:
        # Отказ, а не текст: недостижение отметки — это неудача, и клиент,
        # доверяющий `isError`, иначе увидел бы успех (ровно то, против чего
        # весь контракт). Текст причины сохраняется целиком.
        raise ToolError(
            f"Расчёт не дошёл до {to_time} с: модельное время "
            f"{actual:.3f} (ждали {wait_timeout:.0f} с). Три частые "
            f"причины: у какого-то блока не соединён вход — это молча "
            f"останавливает расчёт всей модели; у проекта нет расчётного "
            f"слоя и время не растёт вовсе; либо `to_time` больше `endtime`"
            f" — поднимите его через `set_calc_time`. Проверьте соединения "
            f"и `list_blocks`."
        )
    return f"Расчёт до {to_time} с завершён (время={actual:.3f})"


@mcp.tool()
@_com_threaded
def step(count: int = 1) -> str:
    """Выполнить указанное число шагов расчёта.

    Проверяется **фактический** рост модельного времени, а не только код
    возврата: `ProjectStep` сообщает об успехе и на проекте без расчётного
    слоя, и при неподключённом входе блока — время при этом стоит. Раньше
    инструмент безусловно отвечал «Выполнено шагов: N». Если время не
    сдвинулось, инструмент отказывает: шаги, которых не было, — не успех.

    Args:
        count: сколько шагов выполнить (> 0).
    """
    if count <= 0:
        raise ToolError("count должен быть положительным")
    sim = _ensure_project().simulation()
    sim.start()
    before = sim.get_time()
    for _ in range(count):
        sim.step()
    after = sim.get_time()
    if after <= before:
        # Отказ, а не текст: время не сдвинулось — это неудача; клиент,
        # доверяющий `isError`, иначе счёл бы шаги выполненными.
        raise ToolError(
            f"Время не сдвинулось после {count} шагов (осталось "
            f"{after:.3f} с). Обычно это значит, что расчёт не идёт: "
            f"у какого-то блока не соединён вход либо у проекта нет "
            f"расчётного слоя (создайте его через `create_project`)."
        )
    return f"Выполнено шагов: {count} (время: {before:.3f} → {after:.3f})"


@mcp.tool()
@_com_threaded
def stop() -> str:
    """Остановить расчёт.

    Вызов неблокирующий и не подтверждает, что расчёт шёл: `ProjectStop`
    сообщает об успехе и на стоящем проекте. Состояние проверяйте по
    `get_time`.
    """
    _ensure_project().simulation().stop()
    return "Расчёт остановлен"


@mcp.tool()
@_com_threaded
def get_time() -> float:
    """Вернуть текущее модельное время проекта."""
    return _ensure_project().simulation().get_time()


# ─── Сигналы ──────────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def list_signals() -> str:
    """Вывести читаемые сигналы проекта и имена блоков отдельно.

    Сигналы (source='com') имеют дескриптор и читаются через `get_signal`.
    Имена блоков из XML читать нельзя — это подсказка о содержимом схемы.
    """
    prj = _ensure_project()
    signals = prj.list_signals()
    if not signals:
        return ("Сигналов нет. Обмен идёт через базу сигналов проекта; у этой "
                "модели она не подключена, поэтому читать нечего.")

    readable = [s for s in signals if s.readable]
    names_only = [s for s in signals if not s.readable]

    parts = []
    if readable:
        parts.append("Читаемые сигналы:\n" + "\n".join(
            f"  {s.name}" for s in readable[:50]))
    else:
        parts.append("Читаемых сигналов нет: у проекта не подключена база "
                     "сигналов, поэтому get_signal работать не будет.")
    if names_only:
        parts.append("Имена блоков на схеме (НЕ читаются через get_signal):\n"
                     + "\n".join(f"  {s.name}" for s in names_only[:50]))
    return "\n\n".join(parts)


@mcp.tool()
@_com_threaded
def get_signal(block: str) -> str:
    """Прочитать значение сигнала — он адресуется именем блока.

    Работает только у проекта с подключённой базой сигналов. У модели,
    созданной через `create_project`, базы нет: `list_signals` скажет об этом
    прямо, а этот инструмент вернёт ошибку. Результат самодельной модели
    забирайте блоком «В файл» и `read_output_file`.

    Args:
        block: имя блока (автоимя из `list_blocks`).
    """
    try:
        sig = _ensure_project().signal(block)
        value = sig.read()
        return f"{block} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@_com_threaded
def set_signal(block: str, value: float) -> str:
    """Записать значение в сигнал (адресуется именем блока).

    Записывать можно только сигналы проекта с подключённой базой сигналов —
    как и `get_signal`.

    Args:
        block: имя блока (автоимное, из `list_blocks`).
        value: значение (float).
    """
    try:
        sig = _ensure_project().signal(block)
        sig.write(value)
        return f"{block} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


# ─── Результаты расчёта ───────────────────────────────────────────

#: Переменная окружения: каталог, за пределы которого не выходит
#: `read_output_file`.
OUTPUT_DIR_ENV = "SIMINTECH_OUTPUT_DIR"

#: Предел объёма, отдаваемого в контекст: защита от чтения большого
#: двоичного файла вместо текстового результата.
MAX_OUTPUT_BYTES = 2 * 1024 * 1024

#: Стандартный каталог результатов и его подкаталог берутся из библиотеки:
#: это общее соглашение, а не деталь MCP. Пример `examples/run_to_file.py`
#: пишет туда же, поэтому файл удаётся прочитать через `read_output_file`.
default_output_dir = simintech_default_output_dir


def _ensure_default_output_dir() -> str:
    """Создать стандартный каталог результатов, не следуя по чужой ссылке.

    Каталог лежит в предсказуемом месте, поэтому его мог заранее создать другой
    процесс — **символической ссылкой** на выбранный им каталог. `realpath` тогда
    увёл бы песочницу туда, и ограничение стало бы фиктивным. Поэтому каталог
    создаётся только когда его нет (`os.mkdir` без `exist_ok`), с правами 0o700,
    и подмена ссылкой отвергается явно.
    """
    path = default_output_dir()
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ToolError(
            f"Не удалось создать каталог результатов «{path}»: {exc}"
        ) from exc
    if os.path.islink(path):
        raise ToolError(
            f"«{path}» — символическая ссылка (возможна подмена каталога "
            f"результатов) — чтение запрещено"
        )
    if not os.path.isdir(path):
        raise ToolError(f"«{path}» не является каталогом — чтение запрещено")
    return os.path.realpath(path)


def output_root() -> str:
    """Каталог, из которого разрешено читать результаты. Ограничение всегда есть.

    Порядок: ``SIMINTECH_OUTPUT_DIR``, иначе стандартный каталог
    (`default_output_dir()`, создаётся при необходимости).

    Если переменная задана, но каталога нет — это ошибка конфигурации и чтение
    запрещено (fail closed): иначе инструмент отказывал бы с невнятной причиной
    «путь вне разрешённого каталога», хотя проблема в настройке.
    """
    raw = os.environ.get(OUTPUT_DIR_ENV)
    if raw:
        root = os.path.realpath(raw)
        if not os.path.isdir(root):
            raise ToolError(
                f"{OUTPUT_DIR_ENV}=«{raw}» не является каталогом — чтение "
                f"результатов запрещено"
            )
        return root
    return _ensure_default_output_dir()


def _safe_output_root() -> str:
    """Каталог результатов для справки: не падает на ошибке конфигурации."""
    try:
        return output_root()
    except ToolError as exc:
        return f"<ошибка конфигурации {OUTPUT_DIR_ENV}: {exc}>"


def _is_inside(root: str, path: str) -> bool:
    """Лежит ли `path` внутри `root` (оба уже realpath'нуты)."""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:                  # разные диски (Windows)
        return False


def _read_bounded(resolved: str, max_bytes: int) -> Tuple[bytes, bool]:
    """Прочитать файл не более `max_bytes` байт: `(данные, обрезано)`.

    Ограничение обязательно именно **до** чтения. Проверка «прочитать целиком,
    потом отказать по размеру» защитой не является: файл в каталоге
    результатов мог создать кто угодно, и память к моменту проверки уже
    израсходована. По той же причине нельзя опираться на построчное чтение:
    одна строка без переводов поднялась бы в память целиком.

    Обрезка может прийтись на середину многобайтового символа: текстовые
    читатели этого модуля декодируют с `errors="replace"`, а разбор `.xprt`
    отвергает обрезанный файл ещё до декодирования.
    """
    with open(resolved, "rb") as fh:
        raw = fh.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return raw[:max_bytes], True
    return raw, False


#: Отказ, когда файла результата нет. Один и тот же текст у всех, кто читает
#: файл блока «В файл»: расхождение формулировок путало бы агента.
_MISSING_RESULT_FILE = ("ERROR: файла нет: {path}. Проверьте свойство "
                        "`filename` блока «В файл» и что расчёт действительно "
                        "прошёл.")

#: Отказ, когда нет сохранённого проекта: подсказка здесь своя.
_MISSING_PROJECT_FILE = ("ERROR: файла нет: {path}. Сохраните проект через "
                         "`save_project` в каталог результатов.")


def _load_result_file(path: str, max_bytes: int,
                      missing: str) -> Tuple[bytes, bool, str]:
    """Разрешить путь в песочнице и прочитать файл не больше `max_bytes`.

    Читатели файлов (`read_output_file`, `summarize_output_file`,
    `inspect_project_file`) делают одно и то же — разрешают путь, проверяют
    наличие, читают ограниченно и переводят сбой в текст отказа. Пока это
    было скопировано трижды, копии успели разойтись в формулировках и в том,
    что считать отказом.

    Args:
        path: путь от клиента (абсолютный или относительный — к каталогу).
        max_bytes: предел чтения; применяется **до** чтения (см.
            `_read_bounded`).
        missing: текст отказа при отсутствующем файле; в него подставляется
            `path` (у разных инструментов подсказка разная).

    Returns:
        `(data, truncated, error)`: `error` непуст, если читать нечего; иначе
        `data` — прочитанное (возможно, обрезанное) содержимое.
    """
    resolved = _resolve_output_path(path)
    if not os.path.isfile(resolved):
        return b"", False, missing.format(path=path)
    try:
        data, truncated = _read_bounded(resolved, max_bytes)
    except OSError as exc:
        return b"", False, f"ERROR: {exc}"
    return data, truncated, ""


def _resolve_output_path(path: str) -> str:
    """Разрешить путь внутри каталога результатов (см. `read_output_file`).

    Относительный путь ищется внутри каталога результатов — так запись и
    чтение не расходятся. `realpath` выполняется до проверки, поэтому `..`
    и символические ссылки наружу не выводят.

    Raises:
        ToolError: путь ведёт за пределы каталога результатов.
    """
    root = output_root()
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    resolved = os.path.realpath(candidate)
    if not _is_inside(root, resolved):
        raise ToolError(
            f"Чтение разрешено только из каталога «{root}» (переопределяется "
            f"переменной {OUTPUT_DIR_ENV}). Блок «В файл» должен писать "
            f"внутрь него — задайте filename с этим каталогом."
        )
    return resolved


@mcp.tool()
@_plain_tool
def read_output_file(path: str, max_lines: int = 200) -> str:
    """Прочитать текстовый файл с результатами расчёта.

    Основной способ получить результаты: `get_signal` работает только у проекта
    с подключённой базой сигналов, а блок «В файл» пишет результат в текстовый
    файл независимо от базы. Каждая строка — один момент времени:
    «<время> <значение 1> … <значение n>».

    Порядок работы: `add_block("В файл", props="filename=<путь>,count=1,step=[0.1]")`
    → соединить с выходом модели → `run(to_time=…)` → `read_output_file(<путь>)`.

    **Читается только каталог результатов.** По умолчанию это
    ``<временный каталог>/simintech-output`` (переопределяется переменной
    `SIMINTECH_OUTPUT_DIR`), и блок «В файл» должен писать **внутрь** него —
    иначе инструмент откажет. Это стандартное ограничение, а не опция: иначе
    инструмент превращается в «прочитать произвольный файл по пути от клиента».
    Относительный путь ищется внутри каталога результатов, символические ссылки
    раскрываются до проверки — поэтому `..` и ссылки выйти наружу не помогают.
    Текущий каталог печатает `help_text`.

    Args:
        path: путь к файлу внутри каталога результатов (абсолютный или
            относительный — тогда он ищется в этом каталоге).
        max_lines: сколько первых строк вернуть; значение по умолчанию — из
            сигнатуры.
    """
    # Именно байты, а не символы: кириллица в UTF-8 весит вдвое больше, и по
    # символам предел объёма занижался бы. Чтение ограничено заранее — иначе
    # предел срабатывал бы уже после того, как файл занял память.
    data, truncated, error = _load_result_file(path, MAX_OUTPUT_BYTES,
                                               _MISSING_RESULT_FILE)
    if error:
        return error
    text = data.decode("utf-8", errors="replace")
    lines = []
    total = 0
    for raw in io.StringIO(text):
        total += 1
        if len(lines) < max_lines:
            lines.append(raw.rstrip("\r\n"))
    if total == 0:
        # Пустой результат — не «данных нет», а признак, что расчёт не шёл:
        # блок «В файл» создаёт файл, но без вычислений не пишет ни строки.
        return (f"ERROR: файл {path} пуст — блок «В файл» ничего не записал. "
                f"Обычно это значит, что расчёт не шёл (проверьте `get_time()` "
                f"после `run` и соединения блоков).")
    if truncated:
        return (f"{path}: прочитано строк {total} — файл больше "
                f"{MAX_OUTPUT_BYTES} байт, чтение остановлено\n"
                + "\n".join(lines))
    head = f"{path}: строк {total}"
    if total > max_lines:
        head += f", показаны первые {max_lines}"
    return head + "\n" + "\n".join(lines)


#: Пределы сводки: строк и объёма. Файл результата — не более нескольких
#: мегабайт, но сводка читает его целиком, поэтому границы нужны явные.
MAX_SUMMARY_ROWS = 500_000
MAX_SUMMARY_BYTES = 32 * 1024 * 1024

#: Предел колонок в одной строке. Предела по байтам мало: строка из «1 1 1 …»
#: на каждой единице текста даёт объект строки и объект float. Замерено:
#: разбор 2 МБ такого текста занимает 39 МБ (коэффициент 20), то есть на
#: пределе сводки это ≈ `MAX_SUMMARY_BYTES` × 20 (~640 МБ при текущих 32 МБ)
#: из файла, который создаёт клиент. Модель с тысячами выходных сигналов до
#: этого предела не дотягивается.
MAX_SUMMARY_COLUMNS = 4096


class NumericTable(NamedTuple):
    """Разбор файла результата: строки плюс учёт того, что в них не попало.

    Именованные поля, а не кортеж: значений стало пять, и три из них —
    счётчики одного типа, поэтому позиционная распаковка молча путала бы
    «пропущено» с «широких», меняя смысл примечания в ответе.
    """

    rows: List[List[float]]
    skipped: int            # нечисловые строки
    too_wide: int           # строки шире MAX_SUMMARY_COLUMNS
    truncated: bool         # упёрлись в предел строк или байт
    partial_dropped: bool   # последняя строка обрезана и отброшена


def _read_numeric_table(data: bytes, truncated: bool) -> NumericTable:
    """Разобрать содержимое файла результата — чистый разбор, без ввода-вывода.

    Разделитель — любой пробельный (SimInTech пишет табуляцию, но таблица
    может прийти и с пробелами). Нечисловые строки (заголовок, мусор)
    считаются, а не роняют разбор. «Широкие» строки (больше
    `MAX_SUMMARY_COLUMNS` колонок) пропускаются со счётчиком: разбирать их
    значило бы материализовать все токены строки. Обрезанная на середине
    последняя строка не разбирается — см. `partial_dropped`.

    Args:
        data: прочитанное содержимое (см. `_load_result_file`).
        truncated: файл обрезан пределом чтения — тогда последняя строка
            может быть неполной.
    """
    rows: List[List[float]] = []
    skipped = 0
    too_wide = 0
    dropped = False
    # Обрезка по пределу байт приходится на середину строки — и даже на
    # середину числа. Разбирать её нельзя: `2.123456`, обрезанное до `2.0`,
    # попало бы в min/max/среднее/наклон как настоящее измерение. Признак
    # неполноты — отсутствие перевода строки в конце прочитанного.
    partial_line = truncated and not data.endswith(b"\n")
    for raw in io.StringIO(data.decode("utf-8", errors="replace")):
        if len(rows) >= MAX_SUMMARY_ROWS:
            truncated = True
            break
        if partial_line and not raw.endswith("\n"):
            dropped = True
            break
        line = raw.strip()
        if not line:
            continue
        # maxsplit обязателен: `line.split()` без него материализует все
        # токены строки, и предел объёма файла перестаёт ограничивать память.
        parts = line.split(None, MAX_SUMMARY_COLUMNS + 1)
        if len(parts) > MAX_SUMMARY_COLUMNS:
            too_wide += 1
            continue
        try:
            rows.append([float(part) for part in parts])
        except ValueError:
            skipped += 1
    return NumericTable(rows, skipped, too_wide, truncated, dropped)


@mcp.tool()
@_plain_tool
def summarize_output_file(path: str, column: int = -1) -> str:
    """Свести результат расчёта к числам: диапазон, min/max, среднее, наклон.

    Дополняет `read_output_file`, который отдаёт строки как есть: проверять
    модель по двумстам строкам текста неудобно, а по сводке видно, попала ли
    кривая в ожидание. Работает **без COM** — сохранённый файл результата
    разбирается и на машине без SimInTech.

    Колонки файла блока «В файл»: `0` — время, `1..n` — значения.
    По умолчанию берётся последняя колонка (выход модели).

    Args:
        path: путь внутри каталога результатов (как у `read_output_file`).
        column: номер колонки значения; отрицательный — с конца строки
            (`-1` — последняя). `0` — время.
    """
    data, truncated, error = _load_result_file(path, MAX_SUMMARY_BYTES,
                                               _MISSING_RESULT_FILE)
    if error:
        return error
    table = _read_numeric_table(data, truncated)
    rows = table.rows
    if not rows:
        if table.partial_dropped:
            # Единственная «строка» файла не уместилась в предел сводки. Это
            # не пустой результат расчёта, и говорить «нет данных» нельзя:
            # числа в файле есть, но это не таблица (нет переводов строк).
            return (f"ERROR: в файле {path} нет ни одной полной числовой "
                    f"строки: строка не умещается в предел сводки "
                    f"({MAX_SUMMARY_BYTES} байт). Похоже, это не таблица — "
                    f"в файле нет переводов строк.")
        return (f"ERROR: в файле {path} нет ни одной числовой строки"
                + (f" (нечисловых строк: {table.skipped})"
                   if table.skipped else "")
                + ". Пустой результат — признак, что расчёт не шёл.")

    width = len(rows[0])
    if not -width <= column < width:
        raise ToolError(
            f"В файле {width} колонок (0 — время, далее значения), "
            f"column={column} вне диапазона."
        )
    usable = [row for row in rows if len(row) == width]
    ragged = len(rows) - len(usable)
    times = [row[0] for row in usable]
    series = [row[column] for row in usable]
    count = len(series)
    vmin, vmax = min(series), max(series)
    mean = sum(series) / count
    span = times[-1] - times[0]

    label = "время" if column == 0 else f"значение (колонка {column})"
    lines = [
        f"{path}: точек {count}, колонок {width}",
        f"  {label}: первое {series[0]:g}, последнее {series[-1]:g}",
        f"  min {vmin:g} при t={times[series.index(vmin)]:g}, "
        f"max {vmax:g} при t={times[series.index(vmax)]:g}, среднее {mean:g}",
        f"  время: {times[0]:g} … {times[-1]:g}",
    ]
    if span:
        lines.append(f"  средний наклон: {(series[-1] - series[0]) / span:g} "
                     f"за секунду (по концам ряда)")
    if table.skipped or ragged or table.too_wide or table.partial_dropped:
        notes = []
        if table.skipped:
            notes.append(f"нечисловых {table.skipped}")
        if ragged:
            notes.append(f"с другим числом колонок {ragged}")
        if table.too_wide:
            notes.append(f"слишком широких (больше {MAX_SUMMARY_COLUMNS} "
                         f"колонок) {table.too_wide}")
        if table.partial_dropped:
            notes.append("последняя строка обрезана и отброшена")
        lines.append("  пропущено строк: " + ", ".join(notes))
    if table.truncated:
        lines.append(f"  ВНИМАНИЕ: файл больше предела сводки "
                     f"({MAX_SUMMARY_ROWS} строк или "
                     f"{MAX_SUMMARY_BYTES} байт) — посчитаны первые {count}.")
    return "\n".join(lines)


# ─── Разбор проекта без COM (в том числе на Linux) ────────────────

#: Предел объёма разбираемого .xprt: файл проекта читается целиком.
MAX_PROJECT_BYTES = 64 * 1024 * 1024


def _xprt_block_names(text: str) -> List[str]:
    """Имена блоков из XML проекта.

    Имена — вспомогательная часть разбора: если список не собрался, об этом
    честнее сказать пустым результатом, чем отказать в разборе целиком.
    """
    try:
        return list(XprtSignalReader(text).parse())
    except Exception:                                          # noqa: BLE001
        return []


@mcp.tool()
@_plain_tool
def inspect_project_file(path: str) -> str:
    """Разобрать сохранённый проект (.xprt) **без COM** — годится и для Linux.

    SimInTech работает только на Windows, но XML-экспорт проекта (его пишет
    `save_project`) читается где угодно: видно, какие блоки в модели и с
    какими параметрами. Это способ проверить чужую модель, не поднимая среду.

    Что даёт разбор: классы блоков с именами их параметров (включая
    вычисляемые — запись в них ничего не меняет) и имена блоков, по которым
    адресуются `connect`/`get_signal`.

    Чего не даёт: связей и координат — по XML они не восстанавливаются
    надёжно, — и расчёта: без Windows он не идёт. Значения параметров
    показаны не будут: в файле они у каждого экземпляра свои.

    Args:
        path: путь к `.xprt` внутри каталога результатов (как у
            `read_output_file`): файл должен лежать в нём.
    """
    raw, truncated, error = _load_result_file(path, MAX_PROJECT_BYTES,
                                              _MISSING_PROJECT_FILE)
    if error:
        return error
    if truncated:
        raise ToolError(
            f"Файл {path} больше {MAX_PROJECT_BYTES} байт — разбор проекта "
            f"такого объёма не выполняется"
        )

    text = decode_xprt(raw)
    # Корректность проверяется ДО разбора. Без этой проверки повреждённый,
    # обрезанный или вовсе не-XML файл давал бы «классов 0, блоков 0» —
    # ровно тот же ответ, что у пустой, но исправной схемы: провал разбора
    # выглядел бы как «модель пуста». Парсер — defusedxml, как и в
    # библиотеке: обычный `xml.etree` на чужом файле открывает XXE и
    # «бомбы». Проверено на реальном .xprt из SimInTech (693 КБ): структура
    # корректна, 32 объекта.
    try:
        DefusedET.fromstring(text)
    except Exception as exc:                                   # noqa: BLE001
        raise ToolError(
            f"Файл {path} не является корректным экспортом SimInTech "
            f"(.xprt): {type(exc).__name__}: {exc}"
        ) from exc

    classes = parse_xprt_block_props(text)
    readonly = parse_xprt_readonly(text)
    names = _xprt_block_names(text)

    parts = [f"{path}: классов {len(classes)}, блоков {len(names)}"]
    if names:
        shown = names[:50]
        parts.append("Блоки:\n" + "\n".join(f"  {name}" for name in shown)
                     + (f"\n  ... и ещё {len(names) - 50}"
                        if len(names) > 50 else ""))
    else:
        # Отличить «нечего показывать» от «разбор имён не справился» важно:
        # при найденных параметрах классов пустой список имён означает
        # ограничение разбора, а не отсутствие объектов в схеме.
        parts.append(
            "Имена блоков не найдены — " + (
                "разбор имён ограничен, хотя параметры блоков в файле есть."
                if classes else "в файле нет блоков схемы.")
        )
    if classes:
        lines = []
        for cls in sorted(classes):
            computed = readonly.get(cls) or []
            tail = (f" [вычисляемые, задавать нельзя: {', '.join(computed)}]"
                    if computed else "")
            lines.append(f"  {cls}: {', '.join(sorted(classes[cls]))}{tail}")
        parts.append("Параметры по классам:\n" + "\n".join(lines))
    else:
        # Объекты считаются отдельно: и парсер параметров, и извлекатель имён
        # отбрасывают графику (`Line`, `PolyLine`, ...), поэтому схема из одной
        # графики выглядит как пустая — а это разные вещи.
        objects = text.count("<object>")
        parts.append(
            "Параметры блоков не найдены: в файле нет секций <custom_props>"
            + (f" (объектов в файле: {objects} — возможно, это только "
               f"графика)." if objects else ".")
        )
    return "\n".join(parts)


# ─── Утилиты ──────────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def layout_place(block_ids: str, connections: str) -> str:
    """Расставить блоки по слоям без наложений — **с применением** координат.

    Координаты считает `LayeredPlacer`, и они тут же применяются к блокам
    (`set_center`). Раньше инструмент только возвращал координаты текстом, а
    блоки не двигал: агент получал подтверждение расстановки, которой не было.
    Позиция задаётся до расчёта — она влияет только на вид схемы.

    Размеры блоков не задаются: `set_center` сохраняет родной размер каждого
    блока (он задан правилами разработки SimInTech, и подменять его нельзя), а
    расстановка считается по фактическим габаритам из `get_size`.

    Здесь же трассируются линии, созданные `connect` в этой сессии: после
    сдвига блоков геометрия пересчитывается `NormalizeWire`, иначе провода
    остаются диагональными (по прямой между портами). Это единственное место,
    где трассировка возможна: до расстановки блоки лежат в (0,0) друг на
    друге, и маршрут получается в обход наложенных блоков. Линии, созданные
    не в этой сессии, недоступны: COM не умеет перечислять линии страницы.

    Args:
        block_ids: блоки через запятую — имена (`k_0`, `kx_0`, `ToFile_0`; их
            даёт `list_blocks`) или числовые id.
        connections: пары `src->dst` через запятую, напр. `k_0->kx_0`. Оба конца
            должны быть перечислены в `block_ids`.
    """
    from simintech_api.layout import LayeredPlacer

    project = _ensure_project()
    page = project.get_main_page()
    available = {}
    for block in page.get_blocks():
        available[str(block.id)] = block
        try:
            available[block.get_name()] = block
        except Exception:
            continue

    tokens = [t.strip() for t in block_ids.split(",") if t.strip()]
    if not tokens:
        raise ToolError("Не указаны блоки: передайте block_ids через запятую")
    missing = [t for t in tokens if t not in available]
    if missing:
        raise ToolError(
            f"Блоки не найдены на странице: {', '.join(missing)}. "
            f"Актуальные имена и id даёт list_blocks."
        )

    links = []
    for pair in connections.split(","):
        pair = pair.strip()
        if "->" not in pair:
            continue
        src, _, dst = pair.partition("->")
        src, dst = src.strip(), dst.strip()
        unknown = [t for t in (src, dst) if t not in tokens]
        if unknown:
            raise ToolError(
                f"В connections упомянуты блоки вне block_ids: "
                f"{', '.join(unknown)} — расстановка невозможна"
            )
        links.append((src, dst))

    # Размеры берём у самих блоков, а не подставляем свои: размер задан
    # правилами разработки SimInTech, и `set_center` не должен его менять.
    sizes = {token: available[token].get_size() for token in tokens}
    positions = LayeredPlacer().place(tokens, links, sizes=sizes)

    centers = {token: positions[token] for token in tokens}
    for token in tokens:
        available[token].set_center(*centers[token])

    # Перерисовка ДО чтения портов: пока проект не перерисован, порты отдают
    # координаты блоков на прежних местах, и выравнивание посчиталось бы по
    # устаревшей геометрии (проверено на SimInTech64 2026-09-15: без этого
    # шага «Сумматор» уехал на тысячу пикселей вниз).
    project.repaint()

    # Выравнивание по вертикали: основной вход блока (in_index=0) ставим на
    # одну высоту с выходом источника. Иначе линия идёт с лишним изломом — у
    # «Сумматора» входы на четверти и трёх четвертях высоты, а выход
    # «Усилителя» посередине, и прямой участок не получается. Координаты
    # берём у самих портов, поэтому считаем по фактической геометрии.
    unaligned = []
    # Смещения портов относительно центров читаем один раз: дальше блоки
    # двигаются, а COM отдаёт координаты портов только после перерисовки —
    # повторное чтение вернуло бы устаревшие значения.
    offsets = {}
    for _wire, src_name, out_index, dst_name, in_index in _WIRES:
        for name, index, is_output in ((src_name, out_index, True),
                                       (dst_name, in_index, False)):
            key = (name, index, is_output)
            if key in offsets or name not in centers:
                continue
            block = available.get(name)
            if block is None:
                continue
            try:
                port = (block.get_out_port(index) if is_output
                        else block.get_in_port(index))
                offsets[key] = port.get_coords()[1] - centers[name][1]
            except Exception as exc:                          # noqa: BLE001
                unaligned.append(f"{name}[{index}] ({type(exc).__name__})")

    shifted = False
    for _wire, src_name, out_index, dst_name, in_index in _WIRES:
        if in_index != 0:
            continue
        out_key = (src_name, out_index, True)
        in_key = (dst_name, in_index, False)
        if out_key not in offsets or in_key not in offsets:
            continue
        dy = ((centers[src_name][1] + offsets[out_key])
              - (centers[dst_name][1] + offsets[in_key]))
        if abs(dy) < 0.5:
            continue
        cx, cy = centers[dst_name]
        centers[dst_name] = (cx, cy + dy)
        available[dst_name].set_center(cx, cy + dy)
        shifted = True

    applied = [f"  {token}: ({centers[token][0]:.1f}, {centers[token][1]:.1f})"
               for token in tokens]

    # Порядок обязателен и проверен на SimInTech64: перемещение блоков →
    # перерисовка → трассировка. Без перерисовки SimInTech прокладывает
    # провода по прежним прямоугольникам блоков (они ещё лежат в (0,0) друг на
    # друге) и оставляет в геометрии точки вида (-160,-1056), которые потом не
    # пересчитываются. Если блоки сдвинулись на выравнивании — перерисовка
    # нужна ещё раз, уже перед трассировкой.
    if shifted:
        project.repaint()
    for wire, _src, _out, _dst, _in in _WIRES:
        wire.normalize()
    routes = (f"\nЛинии связи: нормализовано {len(_WIRES)} — участки "
              f"ортогональные" if _WIRES
              else "\nЛиний связи в этой сессии нет — трассировать нечего")
    if unaligned:
        routes += (f"\nВНИМАНИЕ: выровнять не удалось для {len(unaligned)} "
                   f"связей: {', '.join(unaligned)}")
    return f"Расставлено блоков: {len(applied)}\n" + "\n".join(applied) + routes


@mcp.tool()
@_com_threaded
def help_text() -> str:
    """Справка: порядок работы и где взять список инструментов.

    Перечня команд здесь намеренно нет: он дублировал бы `tools/list` и
    расходился бы с ним при каждом добавлении инструмента. Источник истины по
    составу — `tools/list`.
    """
    return (
        "Состав инструментов — в `tools/list` (единственный источник истины).\n"
        "\n"
        "Порядок работы:\n"
        "  1. create_project(end_time=N) — проект из шаблона пустой модели\n"
        "  2. add_block(class_name, props=\"a=2\") — блоки; имена не задаются,\n"
        "     фактическое имя возвращает сам вызов\n"
        "  3. connect(src, dst) — связи; соединяйте ВСЕ входы: блок с висящим\n"
        "     входом молча останавливает расчёт всей модели\n"
        "  4. layout_place(block_ids, connections) — расставить блоки и\n"
        "     трассировать линии (иначе провода идут по диагонали)\n"
        "  5. run(to_time=N) — расчёт; проверьте get_time() в ответе\n"
        f"  6. read_output_file(путь) или summarize_output_file(путь) —\n"
        f"     результат блока «В файл» (сводка: min/max/среднее/наклон)\n"
        "\n"
        f"Результаты читаются только из каталога:\n"
        f"  {_safe_output_root()}\n"
        f"Блок «В файл» должен писать внутрь него (свойство filename); каталог\n"
        f"переопределяется переменной SIMINTECH_OUTPUT_DIR.\n"
        "\n"
        "Блоков библиотеки «Конечные автоматы» через COM нет: «Состояние\n"
        "автомата» и родственные классы не создаются (отказ, а не пустой\n"
        "блок). Автомат собирается из доступных блоков — например, переходы\n"
        "по времени задаёт «Ступенька», а состояние складывает «Сумматор».\n"
        "\n"
        "Разобрать сохранённый проект (.xprt) можно и без SimInTech:\n"
        "inspect_project_file(путь) — классы, параметры и имена блоков\n"
        "(файл должен лежать в каталоге результатов).\n"
        "\n"
        "Аргументы инструментов и их ограничения описаны в их docstring.\n"
        "Ресурсы (read-only): simintech://status, simintech://project/blocks,\n"
        "  simintech://blocks/catalog — классы и имена параметров блоков;\n"
        "  simintech://skills и simintech://skills/<имя> — инструкции из\n"
        "  репозитория simintech-skill (каталог задаёт SIMINTECH_SKILLS_DIR)\n"
        "Промпты (шаблоны): create_pid_model, create_rc_chain\n"
        "Среда и ограничения COM: репозиторий simintech-code — CLAUDE.md и\n"
        "docs/reference/com_api_inventory.md\n"
    )


# ─── Ресурсы (read-only) ──────────────────────────────────────────

@mcp.resource("simintech://status")
def resource_status() -> str:
    """Статус COM-сервера SimInTech (аналог инструмента status).

    Инструмент при недоступном COM отказывает (это и есть отказ), а ресурс —
    только читаемое представление, поэтому причину возвращает текстом:
    исключение при чтении ресурса клиенту ничего не объясняет.
    """
    try:
        return status()
    except ToolError as exc:
        return f"SimInTech недоступен: {exc}"


@mcp.resource("simintech://project/blocks")
def resource_project_blocks() -> str:
    """Список блоков текущего проекта (read-only представление)."""
    try:
        return list_blocks()
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.resource("simintech://blocks/catalog")
def resource_blocks_catalog() -> str:
    """Каталог блоков: классы, их параметры и вычисляемые имена.

    Источник — `simintech_api/data/block_catalog.json` из `simintech-code`.
    Агенту он нужен, чтобы не угадывать имена параметров: имена короткие и
    различаются по классам (у «Константы» — `a`, а не `y0`), а запись в
    неизвестное имя COM принимает молча.
    """
    catalog = load_default_catalog()
    classes = catalog.classes()
    if not classes:
        return ("Каталог блоков пуст: `simintech_api/data/block_catalog.json` "
                "не найден. Генерируется командой `simintech-generate-catalog` "
                "(Windows, mmain.exe /regserver).")
    lines = []
    for cls in classes:
        props = ", ".join(catalog.props_for(cls))
        readonly = catalog.readonly_for(cls)
        tail = f" [вычисляемые, задавать нельзя: {', '.join(readonly)}]" \
            if readonly else ""
        lines.append(f"  {cls}: {props}{tail}")
    return f"Каталог блоков ({len(classes)} классов):\n" + "\n".join(lines)


# ─── Скиллы (инструкции агента) ───────────────────────────────────

#: Переменная окружения: каталог скиллов — репозиторий `simintech-skill`,
#: подкаталог `skills-catalog`. При разработке подхватывается и соседний
#: checkout, но в установленном виде сервер видит только её.
SKILLS_DIR_ENV = "SIMINTECH_SKILLS_DIR"

#: Файл скилла внутри его каталога.
SKILL_FILE = "SKILL.md"

#: Скилл адресуется именем каталога: строчные буквы, цифры, дефис. Имя
#: приходит от клиента и подставляется в путь, поэтому «..» и разделители
#: сюда не проходят by design.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Предел объёма текста скилла, отдаваемого в контекст.
MAX_SKILL_BYTES = 64 * 1024


def skills_root() -> Optional[str]:
    """Каталог скиллов или None, если его нет.

    ``SIMINTECH_SKILLS_DIR`` — явный выбор пользователя, поэтому при заданной
    переменной берётся **только** она. Подставлять вместо несуществующего
    каталога соседний checkout нельзя: читался бы не тот каталог, который
    назвали (проверено — так и происходило, пока переменная указывала на
    опечатку, а рядом лежал рабочий checkout). Без переменной соседний
    checkout ``simintech-skill/skills-catalog`` ищется — это удобно при
    разработке, когда репозитории лежат рядом.
    """
    raw = os.environ.get(SKILLS_DIR_ENV)
    if raw:
        return raw if os.path.isdir(raw) else None
    here = Path(__file__).resolve()
    for base in list(here.parents)[:4]:
        candidate = base / "simintech-skill" / "skills-catalog"
        if candidate.is_dir():
            return str(candidate)
    return None


def _skills_missing_message() -> str:
    """Почему скиллов нет — с именем переменной, если она задана."""
    raw = os.environ.get(SKILLS_DIR_ENV)
    if raw:
        return (f"Каталог скиллов из {SKILLS_DIR_ENV}=«{raw}» не найден. "
                f"Укажите существующий каталог.")
    return (f"Скиллы не найдены. Задайте каталог репозитория simintech-skill "
            f"переменной {SKILLS_DIR_ENV} (например, "
            f".../simintech-skill/skills-catalog).")


def _skill_summary(path: Path) -> Optional[str]:
    """Краткое описание скилла — первая содержательная строка SKILL.md.

    `None` — файл не прочитался. Это не то же самое, что скилл без описания,
    и вызывающий обязан показать разницу: иначе ошибка ввода-вывода
    выглядела бы как отсутствие описания.
    """
    try:
        # Читается ограниченно — описание берётся из начала файла, а сам файл
        # может быть большим; предел тот же, что у тела скилла.
        data, _ = _read_bounded(str(path), MAX_SKILL_BYTES)
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    in_frontmatter = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        return line.lstrip("#").strip()[:200]
    return ""


def _list_skills(root: str) -> List[Tuple[str, Optional[str]]]:
    """Скиллы каталога: пары (имя, краткое описание или None при ошибке)."""
    result = []
    for entry in sorted(Path(root).iterdir()):
        if not entry.is_dir() or not _SKILL_NAME_RE.match(entry.name):
            continue
        skill_file = entry / SKILL_FILE
        if skill_file.is_file():
            result.append((entry.name, _skill_summary(skill_file)))
    return result


@mcp.resource("simintech://skills")
def resource_skills() -> str:
    """Список скиллов: что агент может подгрузить про SimInTech.

    Скиллы живут в отдельном репозитории `simintech-skill`; сервер их только
    читает (искать в этом репозитории нечего).
    """
    root = skills_root()
    if root is None:
        return _skills_missing_message()
    skills = _list_skills(root)
    if not skills:
        return (f"В каталоге «{root}» скиллов нет: нужны подкаталоги с файлом "
                f"{SKILL_FILE}.")
    lines = []
    for name, summary in skills:
        if summary is None:
            # Ошибку чтения показываем явно: иначе она неотличима от скилла
            # без описания, и агент не поймёт, почему описания нет.
            lines.append(f"  {name} — (описание недоступно: ошибка чтения)")
        elif summary:
            lines.append(f"  {name} — {summary}")
        else:
            lines.append(f"  {name}")
    return (f"Скиллы ({root}):\n" + "\n".join(lines)
            + "\n\nСодержимое скилла — ресурс simintech://skills/<имя>.")


@mcp.resource("simintech://skills/{name}")
def resource_skill(name: str) -> str:
    """Текст скилла (`SKILL.md`) — инструкции по работе с SimInTech."""
    root = skills_root()
    if root is None:
        return _skills_missing_message()
    if not _SKILL_NAME_RE.match(name):
        return (f"ERROR: недопустимое имя скилла «{name}»: разрешены строчные "
                f"латинские буквы, цифры и дефис")
    path = Path(root) / name / SKILL_FILE
    if not path.is_file():
        return f"ERROR: скилла «{name}» нет в «{root}»"
    try:
        # Чтение ограничено ЗАРАНЕЕ, как и у файлов результатов: проверка
        # размера после чтения защитой не является — файл уже в памяти.
        raw, truncated = _read_bounded(str(path), MAX_SKILL_BYTES)
    except OSError as exc:
        return f"ERROR: {exc}"
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        return text + f"\n... (скилл обрезан: больше {MAX_SKILL_BYTES} байт)"
    return text


# ─── Промпты (шаблоны) ────────────────────────────────────────────

@mcp.prompt()
def create_pid_model(kp: float = 1.0, ki: float = 0.5,
                     kd: float = 0.1, setpoint: float = 1.0) -> str:
    """Шаблон создания ПИД-регулятора в SimInTech.

    Возвращает последовательность команд для сборки ПИД-контура
    с обратной связью через инструменты MCP-сервера.

    Args:
        kp, ki, kd: коэффициенты ПИД-регулятора.
        setpoint: уставка (значение ступеньки).
    """
    return (
        f"Создай ПИД-регулятор в SimInTech:\n"
        f"1. create_project(project_hint=\"pid\", end_time=20)\n"
        f"2. add_block(class_name=\"Ступенька\", props=\"yk={setpoint}\")\n"
        f"3. add_block(class_name=\"Сумматор\", props=\"a=[1.0,-1.0]\")\n"
        f"4. add_block(class_name=\"Усилитель\", props=\"a={kp}\")\n"
        f"5. add_block(class_name=\"Усилитель\", props=\"a={ki}\")\n"
        f"6. add_block(class_name=\"Усилитель\", props=\"a={kd}\")\n"
        f"7. add_block(class_name=\"Сумматор\", in_ports=3, "
        f"props=\"a=[1.0,1.0,1.0]\")\n"
        f"8. add_block(class_name=\"Интегратор\", props=\"k=1.0,x0=0.0\")\n"
        f"9. connect вход→сумматор, сумматор→усилители, усиливающие→PID\n"
        f"10. connect PID → Интегратор, Интегратор → сумматор (обратная связь)\n"
        f"11. layout_place по фактическим именам блоков\n"
        f"12. run(to_time=20) и проверь get_time() в ответе\n"
        f"\n"
        f"Блоки НЕ переименовываются: `name_hint` не применяется, а фактические\n"
        f"имена (`k_0`, `kx_0`, …) возвращает add_block — по ним и соединяй.\n"
        f"Соедини ВСЕ входы: блок с висящим входом молча останавливает расчёт.\n"
    )


@mcp.prompt()
def create_rc_chain(rc: float = 1.0, amplitude: float = 5.0) -> str:
    """Шаблон создания RC-цепи (ступенька → усилитель → интегратор)."""
    return (
        f"Создай RC-цепь в SimInTech:\n"
        f"1. create_project(project_hint=\"rc\", end_time={5.0 * rc})\n"
        f"2. add_block(class_name=\"Ступенька\", props=\"yk={amplitude}\")\n"
        f"3. add_block(class_name=\"Усилитель\", props=\"a={_gain_for_rc(rc)}\")\n"
        f"4. add_block(class_name=\"Интегратор\", props=\"k=1.0,x0=0.0\")\n"
        f"5. соедини блоки по фактическим именам из ответов add_block\n"
        f"   (Ступенька → Усилитель → Интегратор; все входы заняты)\n"
        f"6. run(to_time={5.0 * rc}) и проверь get_time() в ответе\n"
        f"\n"
        f"Переименование через COM недоступно — используй автоимена.\n"
    )


# ─── Изоляция stdout ──────────────────────────────────────────────

class _StdoutGuard:
    """Прокси sys.stdout, защищающий JSON-RPC-транспорт от постороннего вывода.

    Транспорт MCP (`mcp/server/stdio.py`) пишет протокол через
    `sys.stdout.buffer`. Любая запись в `sys.stdout.write()` — `print()` из
    библиотеки, логов или предупреждений — уйдёт в тот же поток и повредит
    JSON-RPC (симптом плавающий: транспорт ломается на ровном месте).

    Прокси отводит `write()` в stderr, но оставляет `.buffer` настоящим,
    поэтому протокол продолжает работать.

    Ограничение: не перехватывает запись напрямую в файловый дескриптор 1
    из нативного кода. Для SimInTech это неактуально: `mmain.exe` —
    out-of-proc COM-сервер со своей консолью.
    """

    def __init__(self, real: Any, err: Any):
        self._buffer = getattr(real, "buffer", None)
        self._err = err

    @property
    def buffer(self):
        """Настоящий бинарный буфер stdout — им пользуется транспорт MCP."""
        return self._buffer

    def write(self, text: str) -> int:
        return self._err.write(text)

    def writelines(self, lines) -> None:
        return self._err.writelines(lines)

    def flush(self) -> None:
        return self._err.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._err.fileno()

    def __getattr__(self, name: str):
        # Прочее (encoding, errors, ...) — как у stderr. Приватные имена не
        # делегируем, иначе возможна рекурсия при неинициализированном _err.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._err, name)


def isolate_stdout() -> None:
    """Отвести посторонний вывод из stdout в stderr (см. `_StdoutGuard`).

    Идемпотентна: повторный вызов не оборачивает прокси второй раз.
    """
    real = sys.stdout
    if isinstance(real, _StdoutGuard):
        return
    sys.stdout = _StdoutGuard(real, sys.stderr)


# ─── Внутреннее ───────────────────────────────────────────────────

def _gain_for_rc(rc: float) -> float:
    """Коэффициент усилителя для RC-цепи: 1/RC (защита от деления на 0)."""
    return 1.0 / rc if rc else 1.0


def _split_props(text: str):
    """Разделить `props` по запятым, не трогая запятые внутри `[...]`.

    Без этого документированный пример `a=[1, -1]` разваливался на `a=[1`
    и `-1]`: первая часть уходила в свойство как обрезанный массив, вторая
    молча отбрасывалась (в ней нет `=`).
    """
    parts = []
    depth = 0
    current = []
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _parse_val(text: str):
    text = text.strip()
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        return text


def _coerce_param_value(text: str):
    """Разобрать значение параметра блока из строки.

    Массивы в стиле SimInTech ('[1, -1]') → list; иначе — число или строка.
    """
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        body = stripped[1:-1].strip()
        if not body:
            return []
        return [_parse_val(p) for p in body.split(",") if p.strip()]
    return _parse_val(stripped)


def main() -> None:
    """Точка входа MCP-сервера (stdio).

    Изоляция stdout выполняется ДО запуска транспорта: иначе первый же
    `print()` из библиотеки повредит JSON-RPC.
    """
    isolate_stdout()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
