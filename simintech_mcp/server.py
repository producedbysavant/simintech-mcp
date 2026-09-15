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
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from fastmcp import FastMCP

from simintech_api import COMClient, Project
from simintech_api.catalog import load_default_catalog

# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
    instructions=(
        "Управление SimInTech через COM API. Сборка и расчёт модели: "
        "create_project(end_time) → add_block → connect → "
        "run(to_time) → read_output_file (результат пишет блок «В файл»). "
        "get_signal работает только у проекта с подключённой базой сигналов. "
        "Требуется Windows и mmain.exe /regserver."
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
        raise ValueError("Нет открытого проекта. Сначала вызовите create_project")
    return _project


# ─── Поток для COM ────────────────────────────────────────────────

# COM-объект привязан к апартаменту создавшего его потока. Использование его
# из другого потока даёт «Объект не подключен к серверу» (CO_E_OBJNOTCONNECTED),
# а то и зависание. FastMCP выполняет синхронные инструменты в пуле потоков и
# чередует их, поэтому все обращения к COM идут через ОДИН выделенный поток:
# клиент и проект создаются и используются в нём же.
_COM_EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                   thread_name_prefix="simintech-com")


def _com_threaded(fn):
    """Выполнить функцию в выделенном COM-потоке и дождаться результата."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return _COM_EXECUTOR.submit(fn, *args, **kwargs).result()

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
    """Отсоединиться от COM-сервера SimInTech (не закрывая приложение)."""
    global _client
    if _client:
        _client.disconnect()
        _client = None
        return "Отсоединено"
    return "Уже отсоединён"


# ─── Проекты ──────────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def create_project(name: str = "model",
                   end_time: Optional[float] = None) -> str:
    """Создать новый проект SimInTech из шаблона «пустой модели».

    Проект создаётся из шаблона поставки (`Схема модели общего вида.prt`), а не
    через `NewProject`: пустой проект не считает — в нём нет расчётного слоя и
    настроек расчёта, поэтому модельное время не растёт ни через `run`, ни
    через `step`, хотя вызовы и возвращают успех.

    Args:
        name: подсказка для сообщения; COM не переименовывает проект — имя
            остаётся автоматическим (в файле `Noname.prt`).
        end_time: конечное время расчёта в секундах; по умолчанию — из шаблона
            (10 с). Меняется инструментом `set_calc_time`.
    """
    global _project
    prj = Project.from_template(_ensure_client())
    if end_time is not None:
        prj.set_calc_end_time(end_time)
    _project = prj
    tail = (f", время расчёта {end_time} с" if end_time is not None
            else ", время расчёта — из шаблона (10 с)")
    return (f"Проект '{name}' создан из шаблона (id={prj.id}){tail}. "
            f"Имя проекта задаёт среда, переименование через COM недоступно.")


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
    """Открыть существующий проект SimInTech (.prt/.xprt)."""
    global _project
    prj = Project.open(_ensure_client(), path)
    _project = prj
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
    if _project:
        _project.close()
        _project = None
        return "Проект закрыт"
    return "Нет открытого проекта"


# ─── Блоки и связи ────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def add_block(class_name: str, name: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "", in_ports: int = 0) -> str:
    """Добавить блок на главную страницу проекта.

    Args:
        class_name: класс блока (русское имя, напр. 'Константа',
            'Усилитель', 'Сумматор', 'Интегратор', 'Синусоида',
            'Ступенька', 'Временной график').
        name: имя блока (опционально).
        x, y: координаты центра блока.
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
    if name:
        block.set_name(name)
    if in_ports:
        block.set_in_port_count(in_ports)
    if props:
        for pair in _split_props(props):
            if "=" in pair:
                k, _, v = pair.partition("=")
                block.set_property(k.strip(), _parse_val(v.strip()))

    actual = block.get_name()
    if name and actual != name:
        # Проверено на SimInTech64: SetBlockProp("Name") НЕ переименовывает
        # блок — имя остаётся автоматическим (k_0, kx_0, ...), ни в
        # get_name(), ни в .xprt. Молчаливое расхождение опаснее отказа:
        # последующий connect по имени не найдёт блок.
        return (f"Блок '{class_name}' создан (id={block.id}); имя '{name}' "
                f"НЕ применилось — блок называется '{actual}'. "
                f"Переименование через COM не поддерживается. Используйте "
                f"'{actual}' в connect/get_signal/get_block_params.")
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
def get_block_params(name: str) -> str:
    """Прочитать параметры блока по имени.

    COM API не умеет перечислять свойства блока, поэтому читаются имена из
    каталога блоков (`simintech_api/data/block_catalog.json`). Имена короткие
    и различаются по классам: у «Константы» `a`, у «Ступеньки» `t`/`y0`/`yk`,
    у «Интегратора» `k`/`x0`.

    Args:
        name: имя блока на главной странице проекта.
    """
    page = _ensure_project().get_main_page()
    block = page.find_block(name)
    if block is None:
        return f"ERROR: блок '{name}' не найден на странице"
    try:
        props = block.get_properties()
        class_name = block.class_name
    except Exception as exc:
        return f"ERROR: {exc}"

    if not props:
        return (f"Блок '{name}' [{class_name}]: параметры неизвестны — "
                f"класс отсутствует в каталоге блоков")
    lines = [f"  {k} = {v}" for k, v in sorted(props.items())]
    return f"Блок '{name}' [{class_name}]:\n" + "\n".join(lines)


@mcp.tool()
@_com_threaded
def set_block_param(name: str, param: str, value: str) -> str:
    """Установить параметр блока и переинициализировать блок.

    Блок переинициализируется (`InitBlock`) — без этого изменение может не
    дойти до расчёта: карта COM API отмечает, что `SetBlockProp` не влияет
    на уже инициализированные блоки (например, «Константа»).

    Args:
        name: имя блока на главной странице.
        param: имя параметра блока (см. `get_block_params`).
        value: значение строкой; массивы — в стиле SimInTech, напр. '[1, -1]'.
    """
    page = _ensure_project().get_main_page()
    block = page.find_block(name)
    if block is None:
        return f"ERROR: блок '{name}' не найден на странице"
    try:
        block.set_property(param, _coerce_param_value(value))
        block.init()
        class_name = block.class_name
    except Exception as exc:
        return f"ERROR: {exc}"

    # Ненайденное имя параметра — не ошибка COM: SetBlockProp не отвергает
    # неизвестные имена, поэтому предупреждаем явно (отказ был бы молчаливым).
    if param not in load_default_catalog().props_for(class_name):
        return (f"{name}.{param} = {value} — применено, но параметр "
                f"отсутствует в каталоге для класса '{class_name}'. "
                f"Проверьте, что он реально есть у блока.")
    return f"{name}.{param} = {value}"


# ─── Расчёт ───────────────────────────────────────────────────────

# Сколько ждать выхода модельного времени на заданную отметку и через сколько
# опросов без движения считать, что расчёт не идёт.
_CALC_WAIT_SECONDS = 30.0
_CALC_POLL_SECONDS = 0.05
_CALC_STALL_POLLS = 20          # ~1 секунда без движения времени


def _await_calc_time(sim, target: float) -> float:
    """Дождаться модельного времени `target`, опрашивая `GetProjectTime`.

    Читать время один раз сразу после `RunTo` нельзя: `RunTo` возвращается
    раньше, чем расчёт дойдёт до отметки (проверено на реальном SimInTech —
    сразу после вызова 0.240 с, через мгновение уже 0.5 с). Если время не
    растёт вовсе (у проекта не настроено время расчёта), выходим после
    ~секунды простоя, а не ждём весь таймаут.
    """
    deadline = time.monotonic() + _CALC_WAIT_SECONDS
    actual = sim.get_time()
    stalled = 0
    while actual + 1e-9 < target and time.monotonic() < deadline:
        time.sleep(_CALC_POLL_SECONDS)
        new = sim.get_time()
        stalled = stalled + 1 if new <= actual else 0
        actual = new
        if stalled >= _CALC_STALL_POLLS:
            break
    return actual


@mcp.tool()
@_com_threaded
def run(to_time: Optional[float] = None) -> str:
    """Запустить расчёт проекта (опционально до момента времени).

    Проверяется **фактическое** модельное время, а не код возврата: на проекте
    без настроенного времени расчёта `ProjectRun`/`RunTo`/`ProjectStep`
    возвращают успех, но модельное время не растёт (проверено на реальном
    SimInTech: проект из `Project.new()` не считает, `fsm_demo.prt` считается).
    Раньше инструмент в этом случае сообщал «Расчёт завершён» — ложное
    подтверждение.

    Args:
        to_time: время окончания расчёта в секундах (если указано).
    """
    sim = _ensure_project().simulation()
    sim.start()
    if to_time is not None:
        sim.run_to(to_time)
        actual = _await_calc_time(sim, to_time)
        if actual + 1e-9 < to_time:
            return (f"Расчёт не дошёл до {to_time} с: модельное время "
                    f"{actual:.3f}. Две частые причины: у какого-то блока не "
                    f"соединён вход (это молча останавливает расчёт всей "
                    f"модели) или у проекта нет расчётного слоя — тогда "
                    f"модельное время не растёт вовсе. Проверьте соединения, "
                    f"затем `list_blocks`.")
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
def get_signal(name: str) -> str:
    """Прочитать значение сигнала по имени блока.

    Args:
        name: имя сигнала (совпадает с именем блока).
    """
    try:
        sig = _ensure_project().signal(name)
        value = sig.read()
        return f"{name} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@_com_threaded
def set_signal(name: str, value: float) -> str:
    """Записать значение в сигнал (по имени блока).

    Args:
        name: имя сигнала.
        value: значение (float).
    """
    try:
        sig = _ensure_project().signal(name)
        sig.write(value)
        return f"{name} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


# ─── Результаты расчёта ───────────────────────────────────────────

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

    Args:
        path: путь к файлу, куда писал блок «В файл».
        max_lines: сколько первых строк вернуть (по умолчанию 200).
    """
    if not os.path.isfile(path):
        return (f"ERROR: файла нет: {path}. Проверьте свойство `filename` блока "
                f"«В файл» и что расчёт действительно прошёл.")
    lines = []
    total = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                total += 1
                if len(lines) < max_lines:
                    lines.append(raw.rstrip("\r\n"))
    except OSError as exc:
        return f"ERROR: {exc}"
    if total == 0:
        return (f"Файл {path} пуст — блок «В файл» ничего не записал. Обычно это "
                f"значит, что расчёт не шёл (проверьте `get_time()` после `run`).")
    head = f"{path}: строк {total}"
    if total > max_lines:
        head += f", показаны первые {max_lines}"
    return head + "\n" + "\n".join(lines)


# ─── Утилиты ──────────────────────────────────────────────────────

@mcp.tool()
@_com_threaded
def layout_place(block_ids: str, connections: str) -> str:
    """Автоматически расставить блоки без наложений (LayeredPlacer).

    Args:
        block_ids: список id блоков через запятую, напр. 'A,B,C'.
        connections: пары 'src->dst' через запятую, напр. 'A->B,B->C'.
    """
    from simintech_api.layout import LayeredPlacer
    ids = [b.strip() for b in block_ids.split(",") if b.strip()]
    links = []
    for pair in connections.split(","):
        pair = pair.strip()
        if "->" in pair:
            s, _, d = pair.partition("->")
            links.append((s.strip(), d.strip()))
    pos = LayeredPlacer().place(ids, links, sizes={k: (60.0, 40.0) for k in ids})
    return "\n".join(
        f"{bid}: ({cx:.1f}, {cy:.1f})" for bid, (cx, cy) in pos.items()
    )


@mcp.tool()
@_com_threaded
def help_text() -> str:
    """Справка: список доступных команд MCP-сервера."""
    return (
        "Команды:\n"
        "  status — проверить COM\n"
        "  create_project(end_time) / open_project / save_project / close_project\n"
        "  set_calc_time(секунды) — конечное время расчёта\n"
        "  add_block(класс, имя, x, y, props) — добавить блок\n"
        "  connect(src, dst) — соединить блоки\n"
        "  list_blocks / list_signals\n"
        "  get_block_params(имя) — прочитать параметры блока\n"
        "  set_block_param(имя, параметр, значение) — изменить параметр\n"
        "  run(to_time) / step(n) / stop / get_time\n"
        "  get_signal(имя) / set_signal(имя, значение) — только проект с базой\n"
        "  read_output_file(путь) — прочитать результат от блока «В файл»\n"
        "  layout_place(блоки, связи) — авто-расстановка (только расчёт координат)\n"
        "\n"
        "Ресурсы (read-only):\n"
        "  simintech://status, simintech://project/blocks\n"
        "\n"
        "Промпты (шаблоны):\n"
        "  create_pid_model, create_rc_chain\n"
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
        f"1. create_project \"pid\"\n"
        f"2. add_block \"Ступенька\" name=\"Step\" props=\"yk={setpoint}\"\n"
        f"3. add_block \"Сумматор\" name=\"Err\" props=\"a=[1.0,-1.0]\"\n"
        f"4. add_block \"Усилитель\" name=\"Kp\" props=\"a={kp}\"\n"
        f"5. add_block \"Усилитель\" name=\"Ki\" props=\"a={ki}\"\n"
        f"6. add_block \"Усилитель\" name=\"Kd\" props=\"a={kd}\"\n"
        f"7. add_block \"Сумматор\" name=\"PID\" in_ports=3 "
        f"props=\"a=[1.0,1.0,1.0]\"\n"
        f"8. add_block \"Интегратор\" name=\"Plant\" props=\"k=1.0,x0=0.0\"\n"
        f"9. connect \"Step\" to \"Err\"\n"
        f"10. connect \"Err\" to \"Kp\", \"Err\" to \"Ki\", \"Err\" to \"Kd\"\n"
        f"11. connect \"Kp\" to \"PID\", \"Ki\" to \"PID\", \"Kd\" to \"PID\"\n"
        f"12. connect \"PID\" to \"Plant\"\n"
        f"13. connect \"Plant\" to \"Err\" (обратная связь)\n"
        f"14. run to_time=20\n"
    )


@mcp.prompt()
def create_rc_chain(rc: float = 1.0, amplitude: float = 5.0) -> str:
    """Шаблон создания RC-цепи (ступенька → усилитель → интегратор)."""
    return (
        f"Создай RC-цепь в SimInTech:\n"
        f"1. create_project \"rc\"\n"
        f"2. add_block \"Ступенька\" name=\"Step\" props=\"yk={amplitude}\"\n"
        f"3. add_block \"Усилитель\" name=\"Gain\" props=\"a={_gain_for_rc(rc)}\"\n"
        f"4. add_block \"Интегратор\" name=\"Integrator\" props=\"k=1.0,x0=0.0\"\n"
        f"5. connect \"Step\" to \"Gain\"\n"
        f"6. connect \"Gain\" to \"Integrator\"\n"
        f"7. run to_time={5.0 * rc}\n"
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
