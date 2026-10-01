"""Инструменты пакета проектов (`.pak`): открыть, состав, выбор проекта, расчёт.

Пакет — несколько проектов, считающих вместе (модельное время у них общее —
минимум по участникам), с общей базой сигналов. Сессия держит один пакет (см.
`session._set_pack`); его участники — обычные открытые проекты среды, и
`select_pack_project` делает один из них текущим проектом сессии.

Форму инструментов определил живой замер, а не предположение (копия
демо-пакета «Тест синхронной записи сигналов/Пакет.pak» в Temp, поставка
2.26.6.23, 01.10.2026):

* участники открытого пакета видны как открытые проекты (`GetProjectCount`
  0 → 2), и обёртка `Project` по id участника работает (страницы, настройки);
* время участников растёт пошагово: `PackStep` × 5 → 6e-05/0.001 с;
* `PackStart`+`PackRun` поднимают время до первого синхрошага (0.0099/0.01) и
  в COM-инстансе дальше **не продвигают** — плато ≥2 с, и после перезапуска
  тоже. Поэтому `pack_run` не обещает непрерывный прогон, а `pack_step` —
  рабочая форма продвижения;
* `PackRun` без PackRun-цикла, `RunToPack` без ожидания — то же плато, а
  `WaitForTimePack` (внутри `Pack.run_to`) в пробе не вернулся за ~4 минуты —
  поэтому `pack_run(to_time=…)` в наборе **нет**: подтверждать достижение
  отметки нечем, а отдавать непроверяемое ожидание — против контракта;
* `CloseProject` участника **исключает его из пакета** (состав 2 → 1) — отсюда
  защита участника в `session._replace_project` и отказ `close_project`;
* `ClosePack` закрывает участников; `ClosePack(-1)` роняет `mmain.exe`
  (Access violation, замер) — поэтому id ≤ 0 в него не подаётся никогда;
* открытие одного файла дважды открывает **второй** пакет (дедупа нет) —
  поэтому `open_pack` закрывает прежний пакет сессии;
* идентификаторы участников среда выдаёт заново после изменений состава —
  адресовать участника надо **индексом состава**, а не сохранённым id.
"""

from __future__ import annotations

import os
from typing import List, Optional

from fastmcp.exceptions import ToolError
from simintech_api import Pack, Project

from .. import runtime, session
from ..app import mcp
from .simulation import MAX_STEP_COUNT


def _member_list(pack: Pack) -> List[List]:
    """Участники как строки состава: `[id, файл]` по порядку запуска.

    Имя файла даёт `GetOpenedFileName` — на живом пакете это полный путь
    участника (замер 01.10.2026). Пустая строка — участник не связан с файлом;
    это не отказ: состав всё равно перечисляется.
    """
    return [[pid, pack.client.get_opened_file_name(pid)]
            for pid in pack.project_ids()]


def _member_times(pack: Pack) -> List[Optional[float]]:
    """Модельное время участников; `None` — не прочиталось.

    Недоступное время не отказ само по себе (состав важнее), но скрывать его
    нельзя: `None` печатается явно, а `pack_step` на нём отказывает — шаги,
    которых нельзя подтвердить, не успех.
    """
    times: List[Optional[float]] = []
    for pid in pack.project_ids():
        try:
            times.append(float(pack.client.call("GetProjectTime", pid)))
        except Exception:                                     # noqa: BLE001
            times.append(None)
    return times


def _pack_time(times: List[Optional[float]]) -> Optional[float]:
    """Время пакета — минимум по участникам; `None` — если ни одно не прочитано."""
    known = [t for t in times if t is not None]
    return min(known) if known else None


def _fmt_time(value: Optional[float]) -> str:
    return "время недоступно" if value is None else f"{value:g}"


def _composition_lines(members: List[List]) -> str:
    """Перечисление состава: индекс, id, имя файла — по строке на участника."""
    lines = []
    for index, (pid, file_name) in enumerate(members):
        name = (os.path.basename(file_name.replace("\\", "/"))
                if file_name else "не связан с файлом")
        lines.append(f"  [{index}] id={pid} «{name}»")
    return "\n".join(lines)


# ─── Жизненный цикл ───────────────────────────────────────────────


@mcp.tool()
@runtime._com_threaded
def open_pack(path: str) -> str:
    """Открыть пакет проектов SimInTech (`.pak`).

    Пакет — несколько проектов с общим модельным временем и общей базой
    сигналов; они открываются как обычные проекты среды, но **текущий проект
    сессии не меняется**: выбрать участника для работы — `select_pack_project`.

    Прежний открытый пакет закрывается: открытие того же файла дважды создаёт
    **второй** пакет, а не возвращает первый (замер 01.10.2026), поэтому без
    этого пакеты копились бы в `mmain.exe`. Если текущий проект сессии —
    участник закрываемого пакета, он закрывается вместе с ним, и об этом
    сказано в ответе.

    Args:
        path: путь к файлу `.pak` (абсолютный).
    """
    client = session._ensure_client()
    pack_id = client.open_pack(path)
    if pack_id <= 0:
        # Ноль документирован библиотекой («не открылся»); отрицательный id
        # встречается у `GetPackIdByFileName` для неоткрытого пакета, и подать
        # его в `ClosePack` нельзя — падает mmain (замер 01.10.2026). Поэтому
        # проверяются оба конца: любой неположительный id — отказ.
        raise ToolError(
            f"Пакет не открыт: `OpenPack` вернул {pack_id}. Файл «{path}» не "
            f"прочитан средой (не .pak, повреждён или недоступен). Прежний "
            f"пакет сессии не тронут.")
    pack = Pack(client, pack_id)
    replaced = session._replace_pack(pack, source_path=path)
    members = _member_list(pack)
    return (f"Пакет {session._pack_label()} открыт: проектов {len(members)}\n"
            + _composition_lines(members) + replaced)


@mcp.tool()
@runtime._com_threaded
def close_pack() -> str:
    """Закрыть текущий пакет сессии.

    Участники пакета закрываются вместе с ним (замер 01.10.2026); если
    текущий проект сессии — один из участников, состояние текущего проекта
    сбрасывается, и ответ это называет.
    """
    if session._pack is None:
        return "Без изменений: пакет не был открыт"
    pack = session._ensure_pack()
    label = session._pack_label()
    member = session._project_is_pack_member()
    # Порядок как у `close_project`: сначала закрыть, потом сбросить
    # состояние. Сбой `ClosePack` оставляет пакет текущим — его видно, и
    # закрытие можно повторить, а не потерять открытым внутри mmain.exe.
    pack.close()
    session._set_pack(None)
    tail = ""
    if member:
        session._set_project(None)
        tail = (" Текущий проект — участник пакета — закрыт вместе с ним: "
                "текущего проекта больше нет.")
    return f"Пакет закрыт: {label}" + tail


# ─── Состав ───────────────────────────────────────────────────────


@mcp.tool()
@runtime._com_threaded
def list_pack_projects() -> str:
    """Вывести состав пакета: индекс, id, файл и модельное время участников.

    Индекс — адрес участника для `select_pack_project`: идентификаторы среда
    выдаёт заново после изменений состава (замер 01.10.2026), поэтому
    сохранённый id ненадёжен, а индекс — позиция в составе.

    Модельное время участников здесь единственный способ увидеть ход расчёта
    пакета: отдельного «времени пакета» у COM API нет, а время пакета равно
    минимуму времён участников (общее время, канон библиотеки) — оно печатается
    отдельной строкой.
    """
    pack = session._ensure_pack()
    members = _member_list(pack)
    times = _member_times(pack)
    lines = []
    for index, (pid, file_name) in enumerate(members):
        name = (os.path.basename(file_name.replace("\\", "/"))
                if file_name else "не связан с файлом")
        lines.append(f"  [{index}] id={pid} «{name}» — модельное время "
                     f"{_fmt_time(times[index])}")
    total = _pack_time(times)
    return (f"Пакет {session._pack_label()}: проектов {len(members)}\n"
            + "\n".join(lines)
            + f"\nВремя пакета (минимум по участникам): {_fmt_time(total)}")


@mcp.tool()
@runtime._com_threaded
def select_pack_project(index: int) -> str:
    """Сделать проект пакета текущим проектом сессии.

    Дальше участник доступен обычными инструментами проекта: `list_blocks`,
    `get_block_params`, `run`/`step`… Сессия при этом **не закрывает** прежний
    текущий проект, если тот — тоже участник этого пакета: `CloseProject`
    исключил бы его из состава (замер 01.10.2026). Прежний проект, не
    входящий в пакет, закрывается как при `open_project`.

    Args:
        index: позиция участника в составе (`list_pack_projects`); именно
            индекс, а не id — идентификаторы среда выдаёт заново после
            изменений состава.
    """
    pack = session._ensure_pack()
    ids = pack.project_ids()
    if not 0 <= index < len(ids):
        raise ToolError(
            f"index={index} вне состава пакета {session._pack_label()}: "
            f"участников {len(ids)}, допустимо 0…{len(ids) - 1}. Состав — "
            f"`list_pack_projects`.")
    pid = ids[index]
    if session._project is not None and session._project.id == pid:
        return (f"Без изменений: проект [{index}] уже текущий — "
                f"{session._project_label()}")
    source = pack.client.get_opened_file_name(pid) or None
    project = Project(pack.client, pid)
    replaced = session._replace_project(project, source_path=source)
    return f"Текущий проект: {session._project_label()}" + replaced


# ─── Расчёт ───────────────────────────────────────────────────────


@mcp.tool()
@runtime._com_threaded
def pack_run() -> str:
    """Запустить расчёт пакета (`PackStart` + `PackRun`, неблокирующий).

    Честная форма запуска: на живом COM-инстансе непрерывный прогон
    **не наблюдался** — время поднимается до первого синхрошага и замирает
    (плато ≥2 с, замер 01.10.2026). Поэтому ответ печатает время участников
    до и после, а подтверждать ход расчёта следует через `list_pack_projects`;
    для гарантированного продвижения служит `pack_step`.

    Ожидания «до времени» у пакета нет: `WaitForTimePack` (внутри
    `Pack.run_to`) в пробе не вернулся, а `RunToPack` без PackRun-цикла время
    не двигает — подтверждать достижение отметки нечем.
    """
    pack = session._ensure_pack()
    before = _pack_time(_member_times(pack))
    pack.start()
    pack.run()
    after = _pack_time(_member_times(pack))
    if after is None:
        return ("Расчёт пакета запущен (неблокирующий вызов; модельное время "
                "участников не прочиталось — подтвердить ход через "
                "`list_pack_projects`)")
    return (f"Расчёт пакета запущен (неблокирующий вызов). Время пакета: "
            f"{_fmt_time(before)} → {after:g}. В COM-инстансе непрерывный "
            f"прогон не наблюдался: время поднимается до первого синхрошага "
            f"и дальше не растёт (замер 01.10.2026) — подтверждать ход и "
            f"продвигать время надёжнее через `pack_step`.")


@mcp.tool()
@runtime._com_threaded
def pack_step(count: int = 1) -> str:
    """Выполнить указанное число шагов расчёта пакета.

    Проверяется **фактический** рост времени пакета (минимум по участникам):
    иначе шаги, которых расчёт не выполнил, выглядели бы успехом — тот же
    контракт, что у `step`. Если время не сдвинулось, инструмент отказывает.

    Args:
        count: сколько шагов выполнить (> 0, не больше `MAX_STEP_COUNT`).
    """
    if count <= 0:
        raise ToolError("count должен быть положительным")
    if count > MAX_STEP_COUNT:
        raise ToolError(
            f"count={count} больше предела {MAX_STEP_COUNT} шагов за вызов: "
            f"каждый шаг — отдельный COM-вызов, и такой вызов надолго занял бы "
            f"выделенный поток. Разбейте на несколько вызовов `pack_step`.")
    pack = session._ensure_pack()
    pack.start()
    before = _pack_time(_member_times(pack))
    for _ in range(count):
        pack.step()
    after = _pack_time(_member_times(pack))
    if after is None:
        raise ToolError(
            f"Модельное время участников пакета не прочиталось — "
            f"подтвердить, что {count} шагов выполнились, нечем. Повторите "
            f"`list_pack_projects`: если время и там недоступно, пакет, "
            f"вероятно, повреждён или закрыт средой.")
    if before is None or after <= before:
        raise ToolError(
            f"Время пакета не сдвинулось после {count} шагов (осталось "
            f"{after:g} с). Обычно это значит, что расчёт не идёт: у "
            f"какого-то блока не соединён вход — это молча останавливает "
            f"расчёт; либо участники не инициализируются. Проверьте состав "
            f"(`list_pack_projects`) и модели участников "
            f"(`select_pack_project` → `list_blocks`).")
    return (f"Выполнено шагов: {count} (время пакета: "
            f"{_fmt_time(before)} → {after:g})")


@mcp.tool()
@runtime._com_threaded
def pack_stop() -> str:
    """Остановить расчёт пакета.

    Вызов неблокирующий и не подтверждает, что расчёт шёл (`PackStop`
    сообщает об успехе и на остановленном пакете — как `ProjectStop`).
    """
    session._ensure_pack().stop()
    return "Расчёт пакета остановлен"
