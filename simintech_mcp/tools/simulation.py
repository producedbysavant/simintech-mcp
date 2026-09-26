"""Инструменты расчёта и живых сигналов: run, step, stop, время, сигналы.

Неудача не приходит текстом: `run`/`step`, не дождавшиеся роста модельного
времени, отказывают (`isError`) — клиент, доверяющий флагу, иначе счёл бы
расчёт выполненным.
"""

from __future__ import annotations

from typing import Optional

from fastmcp.exceptions import ToolError

from .. import runtime, sandbox, session
from ..app import mcp


# ─── Расчёт ───────────────────────────────────────────────────────

#: Значения по умолчанию для ожидания расчёта. Само ожидание живёт в
#: библиотеке (`Simulation.run_to`): `RunTo` не блокирующий, и подтверждать
#: достижение отметки надо опросом `GetProjectTime` — это общий контракт, а не
#: деталь MCP.
CALC_WAIT_SECONDS = 30.0
CALC_STALL_SECONDS = 1.0

#: Запас между `wait_timeout` и `COM_CALL_TIMEOUT`: до внутреннего дедлайна
#: `run_to` проходят ещё COM-рейсы (`ProjectRun` в `start()` и сам `RunTo`),
#: поэтому при ожидании у самой границы внешний дедлайн срабатывает первым — и
#: клиент вместо фактического модельного времени получает ложное «SimInTech
#: занят или завис» с советом уничтожить сессию, хотя расчёт шёл штатно.
#: Замер на живом SimInTech64 (2026-09-17): ProjectStart 22.1 мс, ProjectRun
#: 29.4 мс, RunTo 47.2 мс, GetProjectTime 0.1 мс — то есть ≈0.1 с. Проверка
#: «wait_timeout >= COM_CALL_TIMEOUT» этот зазор не закрывала: при пределе 120
#: прогон с `wait_timeout=119.11` дал честное «модельное время 371.404», а с
#: `119.95` — ложное «COM-вызов не ответил за 120 с». Запас взят с десятикратным
#: перекрытием измеренного, чтобы граница не зависела от быстродействия машины.
CALC_WAIT_MARGIN_SECONDS = 1.0

#: Предел числа шагов за один вызов `step`. Каждый шаг — отдельный COM-вызов, а
#: COM-вызов в своём потоке по таймауту **не прерывается** (см. `runtime`):
#: `count` в миллионы занял бы выделенный поток надолго, и, пока вызов не
#: завершится, все COM-инструменты отказывали бы по таймауту — отказ клиенту
#: приходит раньше, чем освободится поток. Если вызов не вернётся вовсе, одного
#: перезапуска `mmain.exe` мало: нужен ещё `disconnect` — повтор иначе подхватил
#: бы тот же мёртвый прокси. Ограничение не про скорость, а про то, чтобы одним
#: числом от клиента нельзя было вывести сервер из строя. Продвигать время
#: лучше через `run(to_time=…)`: он ждёт расчёт, а не считает шаги вручную.
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
            (не больше `runtime.COM_CALL_TIMEOUT - CALC_WAIT_MARGIN_SECONDS`).
        stall_seconds: сколько секунд неизменного времени считать признаком
            остановившегося расчёта (после этого ждать не имеет смысла).
    """
    if wait_timeout <= 0 or stall_seconds <= 0:
        raise ToolError(
            "wait_timeout и stall_seconds должны быть положительными")
    max_wait = runtime.COM_CALL_TIMEOUT - CALC_WAIT_MARGIN_SECONDS
    if wait_timeout > max_wait:
        # Ожидание у самого предела таймаута COM-вызова отвергается **здесь**,
        # а не в библиотеке, по двум причинам.
        # Первая: до внутреннего дедлайна `run_to` проходят ещё COM-рейсы
        # (`ProjectRun` в `start()` и сам `RunTo`), и если ожидание ближе к
        # пределу, чем их стоимость, внешний дедлайн выигрывает
        # детерминированно — клиент вместо настоящей причины («не дошёл до
        # to_time, модельное время N») получил бы «SimInTech занят или завис»,
        # то есть совет уничтожить сессию с открытыми проектами, хотя расчёт шёл
        # штатно. Проверка «>= COM_CALL_TIMEOUT» этот зазор не закрывала (см.
        # `CALC_WAIT_MARGIN_SECONDS`), поэтому граница сдвинута на запас.
        # Вторая: сам COM-вызов по таймауту не прерывается, и ожидание дольше
        # таймаута оставило бы единственный выделенный поток занятым опросом до
        # `deadline` — все последующие COM-инструменты падали бы по таймауту,
        # пока вызов не завершится. Ровно от этого защищает предел `step`, и
        # `run` обязан быть ограничен так же, иначе защита обходится его
        # параметрами.
        raise ToolError(
            f"wait_timeout={wait_timeout:g} с больше достижимого максимума "
            f"{max_wait:g} с (COM_CALL_TIMEOUT {runtime.COM_CALL_TIMEOUT:g} с "
            f"минус запас {CALC_WAIT_MARGIN_SECONDS:g} с на COM-рейсы расчёта). "
            f"Запас нужен потому, что до внутреннего дедлайна `run_to` проходят "
            f"ещё два COM-вызова — запуск (`ProjectRun`) и сам `RunTo`; на "
            f"живом SimInTech64 (2026-09-17) они стоят ≈0.1 с, и при ожидании у "
            f"самой границы внешний таймаут срабатывает первым: вместо "
            f"фактического модельного времени клиент получает ложное «SimInTech "
            f"занят или завис» с советом уничтожить сессию с открытыми "
            f"проектами, хотя расчёт шёл штатно. Дольше ждать нельзя и потому, "
            f"что единственный COM-поток остался бы занят опросом, пока вызов "
            f"не завершится.")
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

#: Сколько имён показывать в одном списке `list_signals`. Список идёт текстом,
#: и на базе в сотни сигналов ответ раздулся бы без пользы. Предел ограничивает
#: **показ**, а не перечень: счёт и хвост «… и ещё N» остаются в ответе, иначе
#: пятьдесят показанных имён читались бы как полный список.
MAX_SIGNAL_NAMES = 50


def _signals_section(title: str, items: list) -> str:
    """Заголовок со счётом, первые `MAX_SIGNAL_NAMES` имён и хвост «… и ещё N».

    Счёт печатается всегда, чтобы «сигналов ровно 50» и «показано 50 из 300»
    не выглядели одинаково: по одному лишь срезу отличить одно от другого
    нельзя, и агент не знал бы, что перечисление неполно.
    """
    lines = "\n".join(f"  {s.name}" for s in items[:MAX_SIGNAL_NAMES])
    if len(items) > MAX_SIGNAL_NAMES:
        return (f"{title}: {MAX_SIGNAL_NAMES} из {len(items)}\n{lines}\n"
                f"  … и ещё {len(items) - MAX_SIGNAL_NAMES}")
    # У полного списка счёт — то же число, что и число показанных имён, но он
    # всё равно нужен: без него заголовок короткого списка выглядел бы так же,
    # как заголовок обрезанного без счёта, и обещание «рядом стоит, сколько их
    # всего» выполнялось бы только на длинных базах.
    return f"{title}: {len(items)}\n{lines}"


@mcp.tool()
@runtime._com_threaded
def list_signals() -> str:
    """Вывести читаемые сигналы проекта и имена блоков отдельно.

    Сигналы (source='com') имеют дескриптор и читаются через `get_signal`.
    Имена блоков из XML читать нельзя — это подсказка о содержимом схемы.
    Оба списка могут быть длиннее ответа: показываются первые
    `MAX_SIGNAL_NAMES` имён, а рядом стоит, сколько их всего.
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
        parts.append(_signals_section("Читаемые сигналы", readable))
    else:
        parts.append("Читаемых сигналов нет: у проекта не подключена база "
                     "сигналов, поэтому get_signal работать не будет.")
    if names_only:
        parts.append(_signals_section(
            "Имена блоков на схеме (НЕ читаются через get_signal)", names_only))
    return "\n\n".join(parts)


#: Сколько элементов массива читать за один вызов `get_signal`. Каждый элемент —
#: отдельный COM-вызов (`GetExtArrayElement`), а COM-поток один: число элементов
#: приходит из параметра, то есть задаётся клиентом, и без предела длинный
#: массив занял бы поток надолго — тот же класс опасности, что у `MAX_STEP_COUNT`.
MAX_ARRAY_ITEMS = 100


def _array_types() -> tuple:
    """Типы-массивы (`ARRAY`, `INT_ARRAY`) — импорт здесь, а не наверху."""
    from simintech_api.constants import DataType

    return (DataType.ARRAY, DataType.INT_ARRAY)


@mcp.tool()
@runtime._com_threaded
def get_signal(block: str, max_items: int = 20,
               index: Optional[int] = None) -> str:
    """Прочитать значение сигнала — он адресуется именем блока.

    Работает только у проекта с подключённой базой сигналов. У модели,
    созданной через `create_project`, базы нет: `list_signals` скажет об этом
    прямо, а этот инструмент вернёт ошибку. Результат самодельной модели
    забирайте блоком «В файл» и `read_output_file`.

    Массивы читаются **не целиком**: библиотека тянет массив поэлементно, а
    каждый элемент — отдельный COM-вызов и единственный COM-поток. За один
    вызов отдаются первые `max_items` значений и полный размер; остальное —
    по индексу в `index`.

    Args:
        block: имя блока (автоимя из `list_blocks`).
        max_items: сколько элементов массива показать (1…`MAX_ARRAY_ITEMS`).
        index: прочитать один элемент массива по индексу.
    """
    if not 0 < max_items <= MAX_ARRAY_ITEMS:
        raise ToolError(
            f"max_items={max_items} вне предела 1…{MAX_ARRAY_ITEMS}: каждый "
            f"элемент массива — отдельный COM-вызов, и такой запрос надолго "
            f"занял бы единственный COM-поток.")
    try:
        sig = session._ensure_project().signal(block)
        if sig.data_type not in _array_types():
            if index is not None:
                raise ToolError(
                    f"«{block}» — не массив, индекс {index} к нему неприменим")
            return f"{block} = {sig.read()}"

        size = sig.array_count()
        if index is not None:
            if not 0 <= index < size:
                raise ToolError(
                    f"индекс {index} вне массива «{block}» (размер {size})")
            return f"{block}[{index}] = {sig.read_array_element(index)}"

        shown = min(size, max_items)
        values = [sig.read_array_element(i) for i in range(shown)]
        tail = "" if shown == size else f" … ещё {size - shown}"
        return f"{block} [{size}] = [{', '.join(str(v) for v in values)}{tail}]"
    except ToolError:
        raise
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@runtime._com_threaded
def set_signal(block: str, value: float, index: Optional[int] = None) -> str:
    """Записать значение в сигнал (адресуется именем блока).

    Записывать можно только сигналы проекта с подключённой базой сигналов —
    как и `get_signal`.

    Целому массиву значение не присваивается: запись идёт либо в скаляр, либо
    в один элемент по индексу. Массив целиком — это уже не «одно число от
    клиента», и такой записи здесь нет намеренно.

    Args:
        block: имя блока (автоимное, из `list_blocks`).
        value: значение (float).
        index: индекс элемента массива; без него — скалярная запись.
    """
    try:
        sig = session._ensure_project().signal(block)
        if index is None:
            sig.write(value)
            return f"{block} = {value}"

        if sig.data_type not in _array_types():
            raise ToolError(
                f"«{block}» — не массив, запись по индексу {index} неприменима")
        size = sig.array_count()
        if not 0 <= index < size:
            raise ToolError(
                f"индекс {index} вне массива «{block}» (размер {size})")
        sig.set_array_element(index, value)
        return f"{block}[{index}] = {value}"
    except ToolError:
        raise
    except Exception as exc:
        return f"ERROR: {exc}"


# ─── База сигналов ────────────────────────────────────────────────


@mcp.tool()
@runtime._com_threaded
def export_signal_db(path: str = "signals.xml") -> str:
    """Выгрузить базу сигналов проекта в XML и показать сводку.

    База отдаётся COM-методом `ExportDBToXML` — без командной строки и без
    макроса `dbexporttoxml`, которым её выгружали раньше. Файл пишет сам
    SimInTech; место — каталог результатов (та же песочница, что у
    `read_output_file`), поэтому относительный путь ищется внутри неё.

    Нужен проект с подключённой базой: у модели из `create_project` базы нет,
    и сводка скажет об этом прямо, а не покажет пустой файл за успех.

    Args:
        path: куда положить XML, относительно каталога результатов.
    """
    from simintech_api.sdb import SignalDatabase

    destination = sandbox._resolve_output_path(path)
    session._ensure_project().export_db_to_xml(destination)
    try:
        database = SignalDatabase.from_xml(destination)
    except Exception as exc:
        raise ToolError(
            f"база выгружена в «{destination}», но не разобрана: {exc}")

    groups = sum(len(cat.groups) for cat in database.categories)
    signals = sum(len(group.signals)
                  for cat in database.categories for group in cat.groups)
    if not signals:
        return (f"База пуста: файл «{destination}» записан, но сигналов в нём "
                f"нет. Нужен проект с подключённой базой сигналов.")
    categories = ", ".join(cat.name for cat in database.categories[:5])
    # Шаблонные описания категории — прототип её сигналов, а не сигналы
    # (см. `simintech_api.sdb.CategoryInfo`): в счёт сигналов они не идут, но
    # их число полезно видеть — по нему понятно, что база типизированная.
    templates = sum(len(cat.template_signals) for cat in database.categories)
    template_note = (f" Прототипов категорий (не сигналы): {templates}."
                     if templates else "")
    return (f"База сигналов выгружена в «{destination}»: категорий "
            f"{len(database.categories)}, групп {groups}, сигналов {signals}. "
            f"Категории: {categories}{template_note}")
