"""Инструменты расчёта и живых сигналов: run, step, stop, время, сигналы.

Неудача не приходит текстом: `run`/`step`, не дождавшиеся роста модельного
времени, отказывают (`isError`) — клиент, доверяющий флагу, иначе счёл бы
расчёт выполненным.
"""

from __future__ import annotations

from typing import Optional

from fastmcp.exceptions import ToolError

from .. import runtime, session
from ..app import mcp


# ─── Расчёт ───────────────────────────────────────────────────────

#: Значения по умолчанию для ожидания расчёта. Само ожидание живёт в
#: библиотеке (`Simulation.run_to`): `RunTo` не блокирующий, и подтверждать
#: достижение отметки надо опросом `GetProjectTime` — это общий контракт, а не
#: деталь MCP.
CALC_WAIT_SECONDS = 30.0
CALC_STALL_SECONDS = 1.0

#: Предел числа шагов за один вызов `step`. Каждый шаг — отдельный COM-вызов, а
#: COM-вызов в своём потоке по таймауту **не прерывается** (см. `runtime`):
#: `count` в миллионы занял бы выделенный поток надолго, и после срабатывания
#: таймаута сервер был бы пригоден только до перезапуска `mmain.exe`. Ограничение
#: не про скорость, а про то, чтобы одним числом от клиента нельзя было вывести
#: сервер из строя. Продвигать время лучше через `run(to_time=…)`: он ждёт
#: расчёт, а не считает шаги вручную.
MAX_STEP_COUNT = 1000


@mcp.tool()
@runtime._com_threaded
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
        wait_timeout: сколько секунд ждать выхода времени на `to_time`
            (не больше `runtime.COM_CALL_TIMEOUT`).
        stall_seconds: сколько секунд неизменного времени считать признаком
            остановившегося расчёта (после этого ждать не имеет смысла).
    """
    if wait_timeout <= 0 or stall_seconds <= 0:
        raise ToolError(
            "wait_timeout и stall_seconds должны быть положительными")
    if wait_timeout > runtime.COM_CALL_TIMEOUT:
        # Ожидание длиннее таймаута COM-вызова проверяется **здесь**, а не в
        # библиотеке: ждать дольше бессмысленно и опасно. Инструмент выполняется
        # в единственном выделенном потоке, а сам COM-вызов по таймауту не
        # прерывается: клиент получил бы отказ, но поток остался бы занят
        # опросом до `deadline`, и все последующие COM-инструменты падали бы по
        # таймауту до перезапуска mmain.exe. Ровно от этого защищает предел
        # `step` — и `run` обязан быть ограничен так же, иначе защита обходится
        # его параметрами.
        raise ToolError(
            f"wait_timeout={wait_timeout:.0f} с больше предела "
            f"{runtime.COM_CALL_TIMEOUT:.0f} с: дольше ждать нельзя — "
            f"единственный COM-поток остался бы занят, и сервер был бы пригоден "
            f"только до перезапуска mmain.exe.")
    sim = session._ensure_project().simulation()
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
@runtime._com_threaded
def step(count: int = 1) -> str:
    """Выполнить указанное число шагов расчёта.

    Проверяется **фактический** рост модельного времени, а не только код
    возврата: `ProjectStep` сообщает об успехе и на проекте без расчётного
    слоя, и при неподключённом входе блока — время при этом стоит. Раньше
    инструмент безусловно отвечал «Выполнено шагов: N». Если время не
    сдвинулось, инструмент отказывает: шаги, которых не было, — не успех.

    Args:
        count: сколько шагов выполнить (> 0, не больше `MAX_STEP_COUNT`).
    """
    if count <= 0:
        raise ToolError("count должен быть положительным")
    if count > MAX_STEP_COUNT:
        raise ToolError(
            f"count={count} больше предела {MAX_STEP_COUNT} шагов за вызов: "
            f"каждый шаг — отдельный COM-вызов, и такой вызов надолго занял бы "
            f"выделенный поток. Разбейте на несколько вызовов `step` или "
            f"продвигайте время через `run(to_time=…)`."
        )
    sim = session._ensure_project().simulation()
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
@runtime._com_threaded
def stop() -> str:
    """Остановить расчёт.

    Вызов неблокирующий и не подтверждает, что расчёт шёл: `ProjectStop`
    сообщает об успехе и на стоящем проекте. Состояние проверяйте по
    `get_time`.
    """
    session._ensure_project().simulation().stop()
    return "Расчёт остановлен"


@mcp.tool()
@runtime._com_threaded
def get_time() -> float:
    """Вернуть текущее модельное время проекта."""
    return session._ensure_project().simulation().get_time()


# ─── Сигналы ──────────────────────────────────────────────────────

@mcp.tool()
@runtime._com_threaded
def list_signals() -> str:
    """Вывести читаемые сигналы проекта и имена блоков отдельно.

    Сигналы (source='com') имеют дескриптор и читаются через `get_signal`.
    Имена блоков из XML читать нельзя — это подсказка о содержимом схемы.
    """
    prj = session._ensure_project()
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
@runtime._com_threaded
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
        sig = session._ensure_project().signal(block)
        value = sig.read()
        return f"{block} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@runtime._com_threaded
def set_signal(block: str, value: float) -> str:
    """Записать значение в сигнал (адресуется именем блока).

    Записывать можно только сигналы проекта с подключённой базой сигналов —
    как и `get_signal`.

    Args:
        block: имя блока (автоимное, из `list_blocks`).
        value: значение (float).
    """
    try:
        sig = session._ensure_project().signal(block)
        sig.write(value)
        return f"{block} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"
