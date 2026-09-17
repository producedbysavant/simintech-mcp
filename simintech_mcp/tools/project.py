"""Инструменты жизненного цикла проекта: открыть, сохранить, закрыть.

`create_project` и `open_project` идут через `session._replace_project`, который
закрывает предыдущий проект: иначе они копились бы внутри `mmain.exe`.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from fastmcp.exceptions import ToolError
from simintech_api import Project

from .. import runtime, session
from ..app import mcp


# ─── Подключение ──────────────────────────────────────────────────

@mcp.tool()
@runtime._com_threaded
def status() -> str:
    """Проверить доступность COM-сервера SimInTech (Windows)."""
    if sys.platform != "win32":
        return "COM SimInTech доступен только на Windows"
    try:
        c = session._ensure_client()
        pid = c.get_process_id()
        return f"SimInTech подключён (PID={pid})"
    except Exception as exc:
        # Отказ, а не текст: клиент, доверяющий `isError`, иначе увидел бы
        # «успех» там, где подключиться не удалось.
        raise ToolError(f"SimInTech недоступен: {exc}") from exc


@mcp.tool()
@runtime._com_threaded
def disconnect() -> str:
    """Завершить сессию: закрыть проект и отсоединиться от COM-сервера.

    Сбрасывается **всё** состояние сессии. Раньше обнулялся только клиент, а
    текущий проект оставался в глобальной переменной: следующие вызовы шли с
    мёртвым `ProjectId`.
    """
    if session._client is None and session._project is None:
        return "Без изменений: соединения не было — сбрасывать нечего"
    project = session._project
    # Проект и линии сбрасываются вместе (`session._set_project`) — до попытки
    # закрыть: состояние сессии не должно зависеть от того, ответил ли COM.
    session._set_project(None)
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
    if session._client is not None:
        session._client.disconnect()
        session._client = None
    return "Сессия завершена: проект закрыт, соединение разорвано" + failed


# ─── Проекты ──────────────────────────────────────────────────────

@mcp.tool()
@runtime._com_threaded
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
    # созданный проект висеть в mmain.exe — он не попал бы ни в session._project,
    # ни в закрытие.
    if end_time is not None and end_time <= 0:
        raise ToolError("end_time должен быть положительным числом секунд")

    prj = Project.from_template(session._ensure_client())
    replaced = session._replace_project(prj)
    if end_time is not None:
        prj.set_calc_end_time(end_time)
    tail = (f", время расчёта {end_time} с" if end_time is not None
            else ", время расчёта — из шаблона (10 с)")
    return (f"Проект '{project_hint}' создан из шаблона (id={prj.id}){tail}."
            + replaced)


@mcp.tool()
@runtime._com_threaded
def set_calc_time(seconds: float) -> str:
    """Задать конечное время расчёта проекта (`endtime` расчётного слоя).

    Расчёт идёт до этого момента; `run(to_time=…)` не может уйти за него.

    Args:
        seconds: конечное время расчёта в секундах (> 0).
    """
    session._ensure_project().set_calc_end_time(seconds)
    return f"Время расчёта: {seconds} с"


@mcp.tool()
@runtime._com_threaded
def open_project(path: str) -> str:
    """Открыть существующий проект SimInTech (.prt/.xprt).

    Предыдущий открытый проект закрывается (см. `create_project`).
    """
    prj = Project.open(session._ensure_client(), path)
    replaced = session._replace_project(prj, source_path=path)
    return f"Проект открыт (id={prj.id})" + replaced


@mcp.tool()
@runtime._plain_tool
def project_network_role() -> str:
    """Показать роль проекта в распределённом (сетевом) расчёте.

    Роль лежит не в проекте, а рядом с ним — в файле настроек базы сигналов
    `<имя проекта>.dblocalconf` (или `<имя проекта>.dbconf`), и читается без
    COM. Инструмент нужен, чтобы «какой это узел» перестало быть догадкой:
    от роли зависит, кто в сети задаёт модельное время.

    Файл принадлежит не нам: его создаёт SimInTech, и порт приёма данных
    (по умолчанию 19000 — по файлам поставки, в справке этого числа нет)
    поднимается у проекта с включённым приёмом. Открытие такого проекта в
    живой среде начинает слушать порт — знать об этом полезно до запуска.

    Читается только файл рядом с открытым из файла проектом: произвольный путь
    инструмент не принимает, как и остальные инструменты чтения.
    """
    source = session._opened_from()
    if not source:
        raise ToolError(
            "Текущий проект не открывался из файла (создан или ещё не открыт), "
            "поэтому настроек рядом с ним нет.")
    from simintech_api.dbconf import load_db_config
    from simintech_api.exceptions import SimInTechError

    stem = os.path.splitext(source)[0]
    candidates = [stem + ".dblocalconf", stem + ".dbconf"]
    found = [path for path in candidates if os.path.exists(path)]
    if not found:
        return (f"Настроек обмена рядом с проектом нет: искали "
                f"«{os.path.basename(candidates[0])}» и "
                f"«{os.path.basename(candidates[1])}» в каталоге проекта. "
                f"Это обычное состояние — файл появляется, когда настройки "
                f"базы сигналов сохраняли.")
    try:
        config = load_db_config(found[0])
    except SimInTechError as exc:
        raise ToolError(f"файл «{found[0]}» не разобран: {exc}")

    summary = config.as_dict()
    role = summary["role"]
    detail = [f"Файл: {os.path.basename(found[0])}", f"Роль узла: {role}"]
    if role != "не настроен":
        detail.append(f"Синхронизация модельного времени: "
                      f"{'да' if config.sync_time else 'нет'}")
        if config.server_port is not None:
            detail.append(f"Порт приёма данных: {config.server_port}")
        if config.host:
            detail.append(f"Удалённый сервер: {config.host}:{config.port}")
    else:
        detail.append("Сетевой обмен у этого проекта не включён.")
    return "\n".join(detail)


@mcp.tool()
@runtime._com_threaded
def get_project_config() -> str:
    """Показать параметры расчётного слоя проекта.

    Время и шаг расчёта (`starttime`, `endtime`, `hmin`, `hmax`), метод
    интегрирования (`intmet`) и служебные имена слоя — список шире, чем
    «настройки расчёта», и на живом проекте в нём есть, например, `comp_names`.

    Читаются из выгрузки проекта, а не через COM: метода чтения свойств слоя в
    интерфейсе нет, есть только запись. Заодно это список имён, которые в
    проекте есть, — по нему проверяется `set_project_config`.

    У проекта, созданного через `create_project`, параметры есть (он берётся из
    шаблона). Пустой ответ — у проекта без расчётного слоя: тогда расчёт в нём
    не идёт вообще, и это не «настройки по умолчанию», а их отсутствие.
    """
    settings = session._ensure_project().calc_settings()
    if not settings:
        raise ToolError(
            "В проекте нет параметров расчётного слоя: сам слой отсутствует. "
            "Так выглядит проект без шаблона — расчёт в нём не пойдёт.")
    return ("Параметры расчётного слоя:\n"
            + "\n".join(f"  {name} = {value}"
                        for name, value in sorted(settings.items())))


@mcp.tool()
@runtime._com_threaded
def set_project_config(param: str, value: str) -> str:
    """Записать параметр расчётного слоя проекта (`SetLayerProp`).

    Имя сверяется с настройками, которые уже есть в проекте (`get_project_config`).
    Незнакомое отвергается: запись в несуществующее имя среда принимает молча,
    значение не меняется, и отличить это от успеха потом нечем.

    Время расчёта удобнее задавать через `set_calc_time` — он делает то же
    самое для `endtime`.

    Args:
        param: имя параметра (`endtime`, `hmin`, `intmet`, …).
        value: значение строкой — так его принимает среда.
    """
    session._ensure_project().set_calc_setting(param, value)
    return f"{param} = {value}"


@mcp.tool()
@runtime._com_threaded
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
    project = session._ensure_project()
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
@runtime._com_threaded
def close_project() -> str:
    """Закрыть текущий проект."""
    if session._project is None:
        return "Без изменений: проект не был открыт"
    project = session._project
    # Порядок: сначала закрыть, потом сбросить состояние. Если `CloseProject`
    # не ответил, проект остаётся текущим — его видно и можно закрыть повторно,
    # а не потерять открытым внутри mmain.exe.
    project.close()
    session._set_project(None)
    return "Проект закрыт"
