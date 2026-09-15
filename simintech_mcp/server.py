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
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from simintech_api import COMClient, Project
from simintech_api.catalog import load_default_catalog

# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
    instructions=(
        "Управление SimInTech через COM API (Windows, mmain.exe /regserver). "
        "Сборка и расчёт модели: create_project → add_block → connect → run → "
        "read_output_file (результат пишет блок «В файл»). "
        "Полный список инструментов — в tools/list, он же источник истины. "
        "Особенности среды — в репозитории simintech-code: CLAUDE.md и "
        "docs/reference/com_api_inventory.md."
    ),
)

# ─── Состояние сессии ─────────────────────────────────────────────

_client: Optional[COMClient] = None
_project: Optional[Project] = None


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


def _replace_project(project: Project) -> None:
    """Сделать проект текущим, закрыв предыдущий.

    Без этого `create_project`/`open_project` копили бы открытые проекты внутри
    `mmain.exe`: старые оставались бы жить со своими слоями, настройками
    расчёта и базой сигналов, а инструменты молча работали бы с последним.
    """
    global _project
    previous, _project = _project, project
    if previous is not None and previous is not project:
        try:
            previous.close()
        except Exception:
            # Предыдущий мог быть уже закрыт средой — это не отказ операции.
            pass


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
    if isinstance(result, str) and result.startswith(ERROR_PREFIX):
        raise ToolError(result[len(ERROR_PREFIX):].strip())
    return result


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
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        future = _COM_EXECUTOR.submit(_call_guarded, fn, args, kwargs)
        try:
            return future.result(timeout=COM_CALL_TIMEOUT)
        except FutureTimeout as exc:
            raise ToolError(
                f"COM-вызов не ответил за {COM_CALL_TIMEOUT:.0f} с: SimInTech "
                f"занят или завис. Перезапустите mmain.exe и повторите."
            ) from exc

    return wrapper


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
        return f"SimInTech недоступен: {exc}"


@mcp.tool()
@_com_threaded
def disconnect() -> str:
    """Завершить сессию: закрыть проект и отсоединиться от COM-сервера.

    Сбрасывается **всё** состояние сессии. Раньше обнулялся только клиент, а
    текущий проект оставался в глобальной переменной: следующие вызовы шли с
    мёртвым `ProjectId`.
    """
    global _client, _project
    if _client is None and _project is None:
        return "Без изменений: соединения не было — сбрасывать нечего"
    if _project is not None:
        try:
            _project.close()
        except Exception:
            # Проект мог быть уже закрыт средой — состояние всё равно сбрасываем.
            pass
        _project = None
    if _client is not None:
        _client.disconnect()
        _client = None
    return "Сессия завершена: проект закрыт, соединение разорвано"


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
        project_hint: подсказка для сообщения. Имя проекта задаёт среда
            (в файле `Noname.prt`); переименование через COM недоступно.
        end_time: конечное время расчёта в секундах; по умолчанию — из шаблона
            (10 с). Меняется инструментом `set_calc_time`.
    """
    prj = Project.from_template(_ensure_client())
    if end_time is not None:
        prj.set_calc_end_time(end_time)
    _replace_project(prj)
    tail = (f", время расчёта {end_time} с" if end_time is not None
            else ", время расчёта — из шаблона (10 с)")
    return (f"Проект '{project_hint}' создан из шаблона (id={prj.id}){tail}.")


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
    _replace_project(prj)
    return f"Проект открыт (id={prj.id})"


@mcp.tool()
@_com_threaded
def save_project(path: str) -> str:
    """Сохранить текущий проект в XML (.xprt)."""
    _ensure_project().save_xml(path)
    return f"Проект сохранён: {path}"


@mcp.tool()
@_com_threaded
def close_project() -> str:
    """Закрыть текущий проект."""
    global _project
    if _project is None:
        return "Без изменений: проект не был открыт"
    _project.close()
    _project = None
    return "Проект закрыт"


# ─── Блоки и связи ────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def add_block(class_name: str, name_hint: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "", in_ports: int = 0) -> str:
    """Добавить блок на главную страницу проекта.

    Args:
        class_name: класс блока (русское имя, напр. 'Константа',
            'Усилитель', 'Сумматор', 'Интегратор', 'Синусоида',
            'Ступенька', 'Временной график', 'В файл').
        name_hint: желаемое имя. **Заведомо не применяется**: COM не
            переименовывает блоки, имя остаётся автоматическим (`k_0`, `kx_0`).
            Ответ вернёт фактическое имя — используйте его в `connect`,
            `get_block_params`, `layout_place`.
        x, y: координаты центра блока (можно не задавать — их расставит
            `layout_place`).
        props: параметры через запятую, напр. 'a=2' или 'a=[1, -1]'.
            Имена короткие и различаются по классам: у «Константы» — `a`
            (не `y0`), у «Сумматора» — `a` (веса входов). Неизвестное имя
            принимается без ошибки и ни на что не влияет.
        in_ports: число входных портов (0 — не менять). Нужно для блоков с
            настраиваемым числом входов: у «Сумматора» их по умолчанию два,
            и более длинный `a` сам по себе портов не добавляет.
    """
    page = _ensure_project().get_main_page()
    block = page.create_block(class_name, x, y)
    if name_hint:
        block.set_name(name_hint)
    if in_ports:
        block.set_in_port_count(in_ports)
    if props:
        for pair in _split_props(props):
            if "=" in pair:
                k, _, v = pair.partition("=")
                block.set_property(k.strip(), _parse_val(v.strip()))

    actual = block.get_name()
    if name_hint and actual != name_hint:
        # Проверено на SimInTech64: SetBlockProp("Name") НЕ переименовывает
        # блок — имя остаётся автоматическим (k_0, kx_0, ...), ни в
        # get_name(), ни в .xprt. Молчаливое расхождение опаснее отказа:
        # последующий connect по имени не найдёт блок.
        return (f"Блок '{class_name}' создан (id={block.id}); имя "
                f"'{name_hint}' НЕ применилось — блок называется '{actual}'. "
                f"Переименование через COM не поддерживается. Используйте "
                f"'{actual}' в connect/get_block_params/layout_place.")
    return f"Блок '{class_name}' создан (id={block.id}, name={actual})"


@mcp.tool()
@_com_threaded
def connect(src: str, dst: str,
            out_index: int = 0, in_index: int = 0) -> str:
    """Соединить выход блока src с входом блока dst линией связи.

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
def set_block_param(block: str, param: str, value: str) -> str:
    """Установить параметр блока и переинициализировать блок.

    Блок переинициализируется (`InitBlock`) — без этого изменение может не
    дойти до расчёта: карта COM API отмечает, что `SetBlockProp` не влияет
    на уже инициализированные блоки (например, «Константа»).

    Имя несуществующего параметра COM принимает молча — инструмент
    предупреждает об этом в ответе, поэтому читайте ответ, а не только факт
    отсутствия ошибки.

    Args:
        block: имя блока на главной странице (автоимя из `list_blocks`).
        param: имя параметра блока (см. `get_block_params`).
        value: значение строкой; массивы — в стиле SimInTech, напр. '[1, -1]'.
    """
    page = _ensure_project().get_main_page()
    target = page.find_block(block)
    if target is None:
        return f"ERROR: блок '{block}' не найден на странице"
    try:
        target.set_property(param, _coerce_param_value(value))
        target.init()
        class_name = target.class_name
    except Exception as exc:
        return f"ERROR: {exc}"

    # Ненайденное имя параметра — не ошибка COM: SetBlockProp не отвергает
    # неизвестные имена, поэтому предупреждаем явно (отказ был бы молчаливым).
    if param not in load_default_catalog().props_for(class_name):
        return (f"{block}.{param} = {value} — применено, но параметр "
                f"отсутствует в каталоге для класса '{class_name}'. "
                f"Проверьте, что он реально есть у блока.")
    return f"{block}.{param} = {value}"


# ─── Расчёт ───────────────────────────────────────────────────────

#: Период опроса модельного времени при ожидании и значения по умолчанию для
#: `run`. Вынесены в параметры инструмента: раньше были зашиты, из-за чего
#: поведение зависело от машины и не описывалось в контракте.
CALC_POLL_SECONDS = 0.05
CALC_WAIT_SECONDS = 30.0
CALC_STALL_SECONDS = 1.0


def _await_calc_time(sim, target: float, *, wait_timeout: float,
                     stall_seconds: float) -> float:
    """Дождаться модельного времени `target`, опрашивая `GetProjectTime`.

    Читать время один раз сразу после `RunTo` нельзя: `RunTo` возвращается
    раньше, чем расчёт дойдёт до отметки (проверено на реальном SimInTech —
    сразу после вызова 0.240 с, через мгновение уже 0.5 с).

    Ожидание ограничено двумя способом: общим `wait_timeout` и простоем —
    если время не растёт `stall_seconds`, расчёт не идёт и ждать бессмысленно.
    """
    deadline = time.monotonic() + wait_timeout
    stall_limit = max(1, int(stall_seconds / CALC_POLL_SECONDS))
    actual = sim.get_time()
    stalled = 0
    while actual + 1e-9 < target and time.monotonic() < deadline:
        time.sleep(CALC_POLL_SECONDS)
        new = sim.get_time()
        stalled = stalled + 1 if new <= actual else 0
        actual = new
        if stalled >= stall_limit:
            break
    return actual


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

    Args:
        to_time: время окончания расчёта в секундах (если указано). Больше
            `endtime` проекта расчёт не пройдёт — см. `set_calc_time`.
        wait_timeout: сколько секунд ждать выхода времени на `to_time`.
        stall_seconds: сколько секунд неизменного времени считать признаком
            остановившегося расчёта (после этого ждать не имеет смысла).
    """
    sim = _ensure_project().simulation()
    sim.start()
    if to_time is not None:
        sim.run_to(to_time)
        actual = _await_calc_time(sim, to_time, wait_timeout=wait_timeout,
                                  stall_seconds=stall_seconds)
        if actual + 1e-9 < to_time:
            return (f"Расчёт не дошёл до {to_time} с: модельное время "
                    f"{actual:.3f} (ждали {wait_timeout:.0f} с). Две частые "
                    f"причины: у какого-то блока не соединён вход — это молча "
                    f"останавливает расчёт всей модели; либо у проекта нет "
                    f"расчётного слоя и время не растёт вовсе. Проверьте "
                    f"соединения, затем `list_blocks`.")
        return f"Расчёт до {to_time} с завершён (время={actual:.3f})"
    sim.run()
    return "Расчёт запущен"


@mcp.tool()
@_com_threaded
def step(count: int = 1) -> str:
    """Выполнить указанное число шагов расчёта."""
    sim = _ensure_project().simulation()
    sim.start()
    for _ in range(count):
        sim.step()
    return f"Выполнено шагов: {count}"


@mcp.tool()
@_com_threaded
def stop() -> str:
    """Остановить расчёт."""
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
#: `read_output_file`. Не задана — путь не ограничивается.
OUTPUT_DIR_ENV = "SIMINTECH_OUTPUT_DIR"

#: Подкаталог стандартного каталога результатов внутри временного каталога.
DEFAULT_OUTPUT_SUBDIR = "simintech-output"

#: Предел объёма, отдаваемого в контекст: защита от чтения большого
#: двоичного файла вместо текстового результата.
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


def default_output_dir() -> str:
    """Стандартный каталог результатов: ``<временный каталог>/simintech-output``."""
    return os.path.join(tempfile.gettempdir(), DEFAULT_OUTPUT_SUBDIR)


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
                f"{OUTPUT_DIR_ENV}={raw!r} не является каталогом — чтение "
                f"результатов запрещено"
            )
        return root
    root = os.path.realpath(default_output_dir())
    os.makedirs(root, exist_ok=True)
    return root


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


@mcp.tool()
@_com_threaded
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
        max_lines: сколько первых строк вернуть (по умолчанию 200).
    """
    root = output_root()
    # Относительный путь ищется внутри каталога результатов — так запись и
    # чтение не расходятся.
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    resolved = os.path.realpath(candidate)
    if not _is_inside(root, resolved):
        raise ToolError(
            f"Чтение результатов разрешено только из каталога {root!r} "
            f"(переопределяется переменной {OUTPUT_DIR_ENV}). Блок «В файл» "
            f"должен писать внутрь него — задайте filename с этим каталогом."
        )
    if not os.path.isfile(resolved):
        return (f"ERROR: файла нет: {path}. Проверьте свойство `filename` блока "
                f"«В файл» и что расчёт действительно прошёл.")
    lines = []
    total = 0
    read_bytes = 0
    truncated = False
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                total += 1
                read_bytes += len(raw)
                if len(lines) < max_lines:
                    lines.append(raw.rstrip("\r\n"))
                if read_bytes >= MAX_OUTPUT_BYTES:
                    truncated = True
                    break
    except OSError as exc:
        return f"ERROR: {exc}"
    if total == 0:
        return (f"Файл {path} пуст — блок «В файл» ничего не записал. Обычно это "
                f"значит, что расчёт не шёл (проверьте `get_time()` после `run`).")
    if truncated:
        return (f"{path}: прочитано строк {total}, файл больше "
                f"{MAX_OUTPUT_BYTES} байт — чтение остановлено\n"
                + "\n".join(lines))
    head = f"{path}: строк {total}"
    if total > max_lines:
        head += f", показаны первые {max_lines}"
    return head + "\n" + "\n".join(lines)


# ─── Утилиты ──────────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def layout_place(block_ids: str, connections: str) -> str:
    """Расставить блоки по слоям без наложений — **с применением** координат.

    Координаты считает `LayeredPlacer`, и они тут же применяются к блокам
    (`set_center`). Раньше инструмент только возвращал координаты текстом, а
    блоки не двигал: агент получал подтверждение расстановки, которой не было.
    Позиция задаётся до расчёта — она влияет только на вид схемы.

    Args:
        block_ids: блоки через запятую — имена (`k_0`, `kx_0`, `ToFile_0`; их
            даёт `list_blocks`) или числовые id.
        connections: пары `src->dst` через запятую, напр. `k_0->kx_0`. Оба конца
            должны быть перечислены в `block_ids`.
    """
    from simintech_api.layout import LayeredPlacer

    page = _ensure_project().get_main_page()
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

    sizes = {token: (60.0, 40.0) for token in tokens}
    positions = LayeredPlacer().place(tokens, links, sizes=sizes)

    applied = []
    for token in tokens:
        cx, cy = positions[token]
        available[token].set_center(cx, cy)
        applied.append(f"  {token}: ({cx:.1f}, {cy:.1f})")
    return f"Расставлено блоков: {len(applied)}\n" + "\n".join(applied)


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
        "  4. layout_place(block_ids, connections) — расставить блоки\n"
        "  5. run(to_time=N) — расчёт; проверьте get_time() в ответе\n"
        f"  6. read_output_file(путь) — результат блока «В файл»\n"
        "\n"
        f"Результаты читаются только из каталога:\n"
        f"  {_safe_output_root()}\n"
        f"Блок «В файл» должен писать внутрь него (свойство filename); каталог\n"
        f"переопределяется переменной SIMINTECH_OUTPUT_DIR.\n"
        "\n"
        "Аргументы инструментов и их ограничения описаны в их docstring.\n"
        "Ресурсы (read-only): simintech://status, simintech://project/blocks\n"
        "Промпты (шаблоны): create_pid_model, create_rc_chain\n"
        "Среда и ограничения COM: репозиторий simintech-code — CLAUDE.md и\n"
        "docs/reference/com_api_inventory.md\n"
    )


# ─── Ресурсы (read-only) ──────────────────────────────────────────

@mcp.resource("simintech://status")
def resource_status() -> str:
    """Статус COM-сервера SimInTech (аналог инструмента status)."""
    return status()


@mcp.resource("simintech://project/blocks")
def resource_project_blocks() -> str:
    """Список блоков текущего проекта (read-only представление)."""
    try:
        return list_blocks()
    except Exception as exc:
        return f"ERROR: {exc}"


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
