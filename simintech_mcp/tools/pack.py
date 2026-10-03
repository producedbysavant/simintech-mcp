"""Инструменты пакета проектов (`.pak`): открыть, состав, выбор проекта, расчёт.

Пакет — несколько проектов, считающих вместе (модельное время у них общее —
минимум по участникам), с общей базой сигналов. Сессия держит один пакет (см.
`session.set_pack`); его участники — обычные открытые проекты среды, и
`select_pack_project` делает один из них текущим проектом сессии.

Форму инструментов определил живой замер, а не предположение (копия
демо-пакета «Тест синхронной записи сигналов/Пакет.pak» в Temp, поставка
2.26.6.23, 01.10.2026):

* участники открытого пакета видны как открытые проекты (`GetProjectCount`
  0 → 2), и обёртка `Project` по id участника работает (страницы, настройки);
* время участников растёт пошагово: `PackStep` × 5 → 6e-05/0.001 с;
* `PackStart` время **не обнуляет**: после `PackStop` повторные
  `PackStart`+`PackRun` оставили времена на прежнем уровне (0.0099/0.01) —
  поэтому `pack_step` читает «до» рядом со `start()` без риска сравнить с
  нулём после перезапуска;
* `PackStart`+`PackRun` в COM-инстансе поднимают время до первого синхрошага
  (0.0099/0.01) и дальше **не продвигают** — плато ≥2 с, и после перезапуска
  тоже. Поэтому `pack_run` не обещает непрерывный прогон, а `pack_step` —
  рабочая форма продвижения;
* `RunToPack` без PackRun-цикла — то же плато, а `WaitForTimePack` (внутри
  `Pack.run_to`) в пробе не вернулся за ~4 минуты — поэтому
  `pack_run(to_time=…)` в наборе **нет**: подтверждать достижение отметки
  нечем, а отдавать непроверяемое ожидание — против контракта;
* `CloseProject` участника **исключает его из пакета** (состав 2 → 1) — отсюда
  защита участника; решение «участник / не участник / неизвестно» принимает
  `session.pack_membership`, и «неизвестно» трактуется как «нельзя
  исключить»:
  `replace_project` такого проекта не закрывает, `close_project` отказывает,
  `close_pack`/`replace_pack`/`disconnect` сбрасывают состояние текущего
  проекта после **удавшегося** закрытия пакета (неудавшееся — только
  предупреждение: уверять «закрыт вместе с пакетом» рядом с «закрыть не
  удалось» — противоречие);
* `ClosePack` закрывает участников; `ClosePack(-1)` роняет `mmain.exe`
  (Access violation, замер) — поэтому id ≤ 0 в него не подаётся никогда;
* открытие одного файла дважды открывает **второй** пакет (дедупа нет) —
  поэтому `open_pack` закрывает прежний пакет сессии;
* идентификаторы участников среда выдаёт заново после изменений состава —
  адресовать участника надо **индексом состава**, а не сохранённым id.

Состав читается **один раз** на вызов (`_member_ids`), а имена файлов и
времена — по уже прочитанным id: два независимых чтения состава могли
разойтись (состав меняется из GUI, id нестабильны), и пары «участник ↔
время» собирались бы по позиции из разных списков (ревью mcp#25).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api import Pack, Project
from simintech_api.pak import PackEntry, write_pack

from .. import runtime, session
from ..app import mcp
from .simulation import MAX_STEP_COUNT


def _member_ids(pack: Pack) -> List[int]:
    """Идентификаторы участников — единственное чтение состава на вызов.

    Может отказать (подпорченный пакет) — вызывающий решает, отказ это или
    текст; главное, что и имена, и времена дальше берутся по ЭТИМ id, а не
    по второму чтению состава.
    """
    return list(pack.project_ids())


def _member_name(pack: Pack, pid: int) -> Optional[str]:
    """Имя файла участника; `None` — прочитать не удалось.

    Сбой чтения имени не валит перечисление: `list_pack_projects` — в том
    числе диагностика повреждённого пакета, и падать на том, что он
    диагностирует, ему нельзя (ревью mcp#25). Пустая строка — «участник не
    связан с файлом», `None` — «имя не прочитано»; это разные состояния.
    """
    try:
        return pack.client.get_opened_file_name(pid)
    except Exception:                                         # noqa: BLE001
        return None


def _member_times(pack: Pack, member_ids: List[int]) -> List[Optional[float]]:
    """Модельное время участников по уже прочитанным id; `None` — не прочиталось.

    Имена здесь не нужны — они уже прочитаны тем же вызовом, что и id, —
    поэтому этот помощник не трогает состав вовсе.
    """
    times: List[Optional[float]] = []
    for pid in member_ids:
        try:
            times.append(float(pack.client.call("GetProjectTime", pid)))
        except Exception:                                     # noqa: BLE001
            times.append(None)
    return times


def _pack_time(times: List[Optional[float]]) -> Optional[float]:
    """Время пакета — минимум по **всем** участникам; None — если хоть один неизвестен.

    Минимум по читаемому подмножеству — не время пакета: если между замерами
    «до» и «после» менялось само подмножество, разность фабриковала бы рост
    (или ложный отказ «не сдвинулось»). Поэтому неизвестное время одного
    участника делает неизвестным время пакета (ревью mcp#25).
    """
    if not times or any(t is None for t in times):
        return None
    return min(times)                                         # type: ignore[type-var]


def _fmt_time(value: Optional[float]) -> str:
    return "время недоступно" if value is None else f"{value:g}"


def _file_label(name: Optional[str]) -> str:
    """Имя файла участника для перечисления: три состояния — три текста."""
    if name is None:
        return "файл не прочитан"
    if not name:
        return "не связан с файлом"
    return os.path.basename(name.replace("\\", "/"))


def _composition_lines(members: List[Tuple[int, Optional[str]]]) -> str:
    """Перечисление состава: индекс, id, файл — по строке на участника."""
    return "\n".join(
        f"  [{index}] id={pid} «{_file_label(name)}»"
        for index, (pid, name) in enumerate(members))


# ─── Жизненный цикл ───────────────────────────────────────────────


@mcp.tool()
@runtime.plain_tool
def create_pack(path: str, projects: List[str],
                inactive: Optional[List[int]] = None,
                no_sync: Optional[List[int]] = None,
                synchronize: bool = True) -> str:
    """Собрать пакет проектов (`.pak`) текстом — COM не нужен.

    `.pak` — плоский INI-файл со списком проектов `.prt` (`[Files]`; порядок
    строк — порядок запуска): собрать его — файловая операция, без COM.
    Проекты рядом с пакетом указываются **голыми именами** (`a.prt` — так
    пишет и сама среда; при другом каталоге — путь относительно `.pak`).
    Относительная запись не может выходить за каталог пакета: среда
    развернула бы её мимо пакета, а читатель такие записи не выдаёт (замер
    01.10.2026) — проект за каталогом указывается **абсолютным** путём, как
    это делает и сама среда. Файл записывается UTF-8 с BOM и CRLF, как файлы
    поставки, и тут же перечитывается разборщиком: расхождение формата стало
    бы отказом здесь, а не отказом среды при `open_pack`.

    Живой замер 01.10.2026 (поставка 2.26.6.23): собранный так пакет среда
    открывает (`OpenPack` отвечает, состав читается). Файлы проектов должны
    существовать на момент записи — это проверяется заранее, вместе с
    каталогом пути. Существующий файл перезаписывается целиком.

    Args:
        path: путь к файлу `.pak` (должен оканчиваться на `.pak`).
        projects: пути к проектам; порядок задаёт порядок запуска.
        inactive: позиции (0-based) в `projects`, исключаемые из расчёта
            (`[Active]` = 0). От `no_sync` не зависит: в примерах поставки
            эти флаги и совпадают, и расходятся.
        no_sync: позиции, у которых выключается пер-проектная синхронизация
            реального времени (`[TimeSync]` = 0); из расчёта такой проект не
            исключается.
        synchronize: `Synchronize` — объединение списков сигналов проектов
            (по умолчанию включено, как у большинства файлов поставки).
    """
    if not projects:
        raise ToolError(
            "projects пуст: пакет без проектов — файл без состава; если "
            "нужен именно такой, соберите его текстом вручную.")
    if not path.lower().endswith(".pak"):
        raise ToolError(
            f"«{path}» не оканчивается на `.pak`: среда открывает как пакет "
            f"только файл пакета — `open_pack` на другом расширении вернёт 0.")
    base = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(base):
        raise ToolError(
            f"Каталог «{base}» не существует — файл пакета записывать некуда.")
    off = set(inactive or [])
    unsynced = set(no_sync or [])
    for label, positions in (("inactive", off), ("no_sync", unsynced)):
        bad = sorted(i for i in positions if not 0 <= i < len(projects))
        if bad:
            raise ToolError(
                f"{label}: позиции {bad} вне списка projects (в нём "
                f"{len(projects)} записей, допустимо 0…{len(projects) - 1}).")
    missing: list[str] = []
    entries: list[PackEntry] = []
    for index, project in enumerate(projects):
        candidate = project
        if not os.path.isabs(project):
            candidate = os.path.join(base, project.replace("\\", "/"))
        if not os.path.isfile(candidate):
            missing.append(f"[{index}] {project}")
        entries.append(PackEntry(project, active=index not in off,
                                 time_sync=index not in unsynced))
    if missing:
        raise ToolError(
            "Проекты не найдены (относительные пути ищутся от каталога "
            "пакета «" + base + "»):\n  " + "\n  ".join(missing)
            + "\nСначала сохраните их (`save_project` с `binary=True`) или "
              "поправьте пути.")
    try:
        pack = write_pack(Path(path), entries, synchronize=synchronize)
    except Exception as exc:                                  # noqa: BLE001
        raise ToolError(
            f"пакет не собран: {type(exc).__name__}: {exc}") from exc
    lines: list[str] = []
    for index, item in enumerate(pack.projects):
        marks: list[str] = []
        if not item.active:
            marks.append("неактивен")
        if not item.time_sync:
            marks.append("без синхронизации")
        tail = f" — {', '.join(marks)}" if marks else ""
        lines.append(f"  [{index}] {item.path}{tail}")
    # Единственное разрешённое писателем расхождение разбора — абсолютные
    # записи: среда их принимает (так пишет и сама за каталогом пакета), но
    # переносимость ниже — клиент обязан это видеть, а не узнавать при переносе.
    note = ""
    if pack.problems:
        note = "\nПримечание: " + "; ".join(pack.problems)
    return (f"Пакет собран и перечитан: «{os.path.basename(path)}» — "
            f"проектов {pack.project_count} (перезаписан целиком).\n"
            + "\n".join(lines)
            + note
            + f"\nОткрыть: `open_pack(\"{path}\")`.")


@mcp.tool()
@runtime.com_threaded
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
    client = session.ensure_client()
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
    replaced = session.replace_pack(pack, source_path=path)
    # Состав — после установки пакета в сессию: даже если он не читается,
    # пакет уже текущий, и клиент должен узнать это из ответа, а не из отказа
    # с потерянным состоянием (ревью mcp#25).
    try:
        ids = _member_ids(pack)
        members = [(pid, _member_name(pack, pid)) for pid in ids]
    except Exception as exc:                                  # noqa: BLE001
        return (f"Пакет {session.pack_label()} открыт, но состав прочитать не "
                f"удалось ({type(exc).__name__}: {exc}). Пакет стал текущим "
                f"пакетом сессии: повторите `list_pack_projects`, а закрыть "
                f"пакет можно `close_pack`." + replaced)
    return (f"Пакет {session.pack_label()} открыт: проектов {len(members)}\n"
            + _composition_lines(members) + replaced)


@mcp.tool()
@runtime.com_threaded
def close_pack() -> str:
    """Закрыть текущий пакет сессии.

    Участники пакета закрываются вместе с ним (замер 01.10.2026); если
    текущий проект сессии — один из участников, состояние текущего проекта
    сбрасывается, и ответ это называет.
    """
    if session.current_pack() is None:
        return "Без изменений: пакет не был открыт"
    pack = session.ensure_pack()
    label = session.pack_label()
    # Принадлежность проверяется ДО закрытия: после него состав уже не
    # прочитать. `None` (состав не читается) — тот же сброс, что и «участник»:
    # неизвестно, жив ли текущий проект, а мёртвый id в сессии хуже
    # сброшенного.
    membership = session.pack_membership()
    # Порядок как у `close_project`: сначала закрыть, потом сбросить
    # состояние. Сбой `ClosePack` оставляет пакет текущим — его видно, и
    # закрытие можно повторить, а не потерять открытым внутри mmain.exe.
    pack.close()
    session.set_pack(None)
    tail = ""
    if membership is not False:
        session.set_project(None)
        tail = (" Текущий проект — участник пакета — закрыт вместе с ним: "
                "текущего проекта больше нет." if membership else
                " Принадлежность текущего проекта к пакету проверить не "
                "удалось; текущий проект сброшен на всякий случай: текущего "
                "проекта больше нет.")
    return f"Пакет закрыт: {label}" + tail


# ─── Состав ───────────────────────────────────────────────────────


@mcp.tool()
@runtime.com_threaded
def list_pack_projects() -> str:
    """Вывести состав пакета: индекс, id, файл и модельное время участников.

    Индекс — адрес участника для `select_pack_project`: идентификаторы среда
    выдаёт заново после изменений состава (замер 01.10.2026), поэтому
    сохранённый id ненадёжен, а индекс — позиция в составе.

    Модельное время участников здесь единственный способ увидеть ход расчёта
    пакета: отдельного «времени пакета» у COM API нет, а время пакета равно
    минимуму времён участников (общее время, канон библиотеки) — оно печатается
    отдельной строкой и недоступно, если хоть одно время участника не
    прочиталось.
    """
    pack = session.ensure_pack()
    try:
        ids = _member_ids(pack)
    except Exception as exc:                                  # noqa: BLE001
        raise ToolError(
            f"Состав пакета {session.pack_label()} прочитать не удалось "
            f"({type(exc).__name__}: {exc}): идентификаторы участников не "
            f"получены, перечислять нечего. Закрыть пакет можно `close_pack`; "
            f"если состав не читается и дальше, пакет, вероятно, повреждён.")
    members = [(pid, _member_name(pack, pid)) for pid in ids]
    times = _member_times(pack, ids)
    lines: list[str] = []
    for index, (pid, name) in enumerate(members):
        lines.append(f"  [{index}] id={pid} «{_file_label(name)}» — модельное "
                     f"время {_fmt_time(times[index])}")
    total = _pack_time(times)
    return (f"Пакет {session.pack_label()}: проектов {len(members)}\n"
            + "\n".join(lines)
            + f"\nВремя пакета (минимум по участникам): {_fmt_time(total)}")


@mcp.tool()
@runtime.com_threaded
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
    pack = session.ensure_pack()
    ids = _member_ids(pack)
    if not 0 <= index < len(ids):
        raise ToolError(
            f"index={index} вне состава пакета {session.pack_label()}: "
            f"участников {len(ids)}"
            + (f", допустимо 0…{len(ids) - 1}" if ids else "")
            + ". Состав — `list_pack_projects`.")
    pid = ids[index]
    current = session.current_project()
    if current is not None and current.id == pid:
        return (f"Без изменений: проект [{index}] уже текущий — "
                f"{session.project_label()}")
    name = _member_name(pack, pid)
    # Пустая строка (и непрочитанное имя) — не то же, что «проект из шаблона»:
    # метка обязана называть неизвестный источник неизвестным, а не выдумывать
    # шаблон (ревью mcp#25).
    source = name or ""
    project = Project(pack.client, pid)
    replaced = session.replace_project(project, source_path=source)
    return f"Текущий проект: {session.project_label()}" + replaced


# ─── Расчёт ───────────────────────────────────────────────────────


@mcp.tool()
@runtime.com_threaded
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
    pack = session.ensure_pack()
    ids = _member_ids(pack)
    before = _pack_time(_member_times(pack, ids))
    pack.start()
    pack.run()
    after = _pack_time(_member_times(pack, ids))
    if after is None:
        return ("Расчёт пакета запущен (неблокирующий вызов). Модельное время "
                "участников прочиталось не полностью — подтвердить ход можно "
                "через `list_pack_projects`.")
    return (f"Расчёт пакета запущен (неблокирующий вызов). Время пакета: "
            f"{_fmt_time(before)} → {after:g}. В COM-инстансе непрерывный "
            f"прогон не наблюдался: время поднимается до первого синхрошага "
            f"и дальше не растёт (замер 01.10.2026) — подтверждать ход и "
            f"продвигать время надёжнее через `pack_step`.")


@mcp.tool()
@runtime.com_threaded
def pack_step(count: int = 1) -> str:
    """Выполнить указанное число шагов расчёта пакета.

    Проверяется **фактический** рост времени пакета (минимум по участникам):
    иначе шаги, которых расчёт не выполнил, выглядели бы успехом — тот же
    контракт, что у `step`. Если время хотя бы одного участника не прочиталось
    или не сдвинулось, инструмент отказывает: неполное чтение — не рост, а
    его отсутствие — не шаги. `PackStart` время не обнуляет (замер
    01.10.2026), поэтому «до» читается рядом со `start()` без сравнения с
    нулём после перезапуска.

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
    pack = session.ensure_pack()
    ids = _member_ids(pack)
    before = _pack_time(_member_times(pack, ids))
    pack.start()
    for _ in range(count):
        pack.step()
    after = _pack_time(_member_times(pack, ids))
    if before is None or after is None:
        raise ToolError(
            f"Модельное время участников пакета прочиталось не полностью — "
            f"подтвердить, что {count} шагов выполнились, нечем. Разность "
            f"минимумов по читаемому подмножеству была бы фабрикацией: "
            f"участник, чьё время не читается, из минимума выпадает. "
            f"Повторите `list_pack_projects`: если время и там недоступно, "
            f"пакет, вероятно, повреждён или закрыт средой.")
    if after <= before:
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
@runtime.com_threaded
def pack_stop() -> str:
    """Остановить расчёт пакета.

    Вызов неблокирующий и не подтверждает, что расчёт шёл (`PackStop`
    сообщает об успехе и на остановленном пакете — как `ProjectStop`).
    """
    session.ensure_pack().stop()
    return "Расчёт пакета остановлен"
