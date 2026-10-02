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
    """Завершить сессию: закрыть пакет, проект и отсоединиться от COM-сервера.

    Сбрасывается **всё** состояние сессии. Раньше обнулялся только клиент, а
    текущий проект оставался в глобальной переменной: следующие вызовы шли с
    мёртвым `ProjectId`.

    Пакет закрывается первым: его участники закрываются вместе с ним (живой
    замер 01.10.2026), поэтому текущий проект-участник отдельно не
    закрывается — он уже закрыт вместе с пакетом.
    """
    if (session._client is None and session._project is None
            and session._pack is None):
        return "Без изменений: соединения не было — сбрасывать нечего"
    project = session._project
    label = session._project_label()
    in_pack = session._pack_membership()
    pack = session._pack
    pack_label = session._pack_label()
    # Пакет, проект и линии сбрасываются до попыток закрыть: состояние сессии
    # не должно зависеть от того, ответил ли COM.
    session._set_pack(None)
    session._set_project(None)
    failed = ""
    closed = []
    pack_closed = False
    if pack is not None:
        try:
            pack.close()
            pack_closed = True
            closed.append(f"пакет {pack_label} закрыт")
        except Exception as exc:                              # noqa: BLE001
            failed += (f" ВНИМАНИЕ: пакет {pack_label} закрыть не удалось "
                       f"({type(exc).__name__}: {exc}) — он и его участники "
                       f"могли остаться открытыми в SimInTech.")
    # Прямое закрытие — только когда точно известно, что проект не участник
    # (False); при `None` (состав пакета не читается) проект не закрывается:
    # закрытие участника исключило бы его из состава (замер 01.10.2026), а
    # «неизвестно» — не разрешение.
    if project is not None and in_pack is False:
        try:
            project.close()
            closed.append(f"проект {label} закрыт")
        except Exception as exc:                              # noqa: BLE001
            # Не выдаём отказ за успех: `COMClient.disconnect()` проекты не
            # закрывает, поэтому при сбое `CloseProject` проект останется жить
            # в mmain.exe.
            failed += (f" ВНИМАНИЕ: проект закрыть не удалось "
                       f"({type(exc).__name__}: {exc}) — он мог остаться "
                       f"открытым в SimInTech.")
    elif project is not None and pack_closed:
        if in_pack:
            closed.append(f"проект {label} закрыт вместе с пакетом")
        else:
            closed.append(f"проект {label} — участие в пакете не подтверждено "
                          f"(состав не читался)")
    if session._client is not None:
        session._client.disconnect()
        session._client = None
    summary = ", ".join(closed)
    done = (f"Сессия завершена: {summary}, соединение разорвано"
            if summary else "Сессия завершена: соединение разорвано")
    return done + failed


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
    `mmain.exe`. Смена текущего проекта называется в ответе явно («было …
    → стало …», issue #18).

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
@runtime._com_threaded(mutates_project=True)
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

    Предыдущий открытый проект закрывается (см. `create_project`), и ответ
    называет это явно: «было … → стало …» — вместе с именем открытого файла.
    Смена текущего проекта — событие сессии, и молчание о ней стоило живой
    ошибки (issue #18: импорт ушёл в проект, снятый ради образца).
    """
    prj = Project.open(session._ensure_client(), path)
    replaced = session._replace_project(prj, source_path=path)
    return f"Проект открыт: {session._project_label()}" + replaced


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
@runtime._com_threaded(mutates_project=True)
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


def _save_mtime(path: str) -> Optional[int]:
    """Время правки файла до записи; None — файла нет.

    Нужно, чтобы отличить «файл появился» от «старый файл остался на месте»:
    залипшая сессия сообщает об успехе, не записывая ничего, и одна лишь
    проверка «файл есть» приняла бы прежний файл за результат записи.
    """
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _verify_saved(path: str, before_mtime: Optional[int]) -> None:
    """Проверить, что запись действительно состоялась.

    `SaveProjectXML` в залипшей сессии сообщает об успехе, **не создавая
    файл** (simintech-code#21: симптом повторялся трижды подряд, следом
    `CloseProject` падает с Access violation). Ответ «проект сохранён» по
    такому вызову — ложь, которую клиент обнаружит только на чтении, и
    выдавать её нельзя. Поэтому файл должен появиться (не было раньше) или
    обновиться (время правки изменилось).

    Отказ называет обе возможные причины: залипшую сессию и несуществующий
    каталог в пути, — и рецепт от первой: повтор в той же сессии бесполезен,
    помогает перезапуск.
    """
    try:
        after = os.stat(path).st_mtime_ns
    except OSError:
        after = None
    unchanged = (after is not None and before_mtime is not None
                 and after == before_mtime)
    if after is not None and not unchanged:
        return
    raise ToolError(
        f"Файл не записан: «{path}» "
        + ("не обновился" if after is not None else "не появился")
        + ", хотя вызов среды сообщил об успехе. Проверьте, что каталог в "
          "пути существует; если с путём всё в порядке — это признак залипшей "
          "сессии (simintech-code#21): сохранение в ней не проходит, и повтор "
          "бесполезен. Переподключитесь (`disconnect`) и откройте проект "
          "заново — рабочая сессия запись выполнит.")


def _refuse_format_extension_mismatch(path: str, binary: bool) -> None:
    """Отказ, если имя файла обещает не тот формат, которым записываем.

    Среда выбирает формат проекта **по расширению**: `.prt` открывается как
    нативный, `.xprt` — как XML. XML, записанный в файл с именем `.prt`, GUI
    показал «Ошибка загрузки страницы проекта: data error» (живой случай
    02.10.2026: тот же текст, сохранённый как `.xprt`, открылся) — то есть
    промах формата обнаруживается только у пользователя в GUI, а пишущий
    инструмент о нём молчит. Такой файл инструмент создавать не должен;
    отказ называет оба выхода.

    Проверка стоит до всякой работы с проектом: ни формы, ни COM-вызова.
    """
    lower = path.lower()
    if lower.endswith(".prt") and not binary:
        raise ToolError(
            f"«{path}» назван нативным форматом (.prt), а сохраняем XML: среда "
            "открывает проект по расширению и покажет «Ошибка загрузки "
            "страницы проекта: data error» (живой случай 02.10.2026 — тот же "
            "текст под именем .xprt открылся). Сохраните в .xprt или "
            "передайте binary=True.")
    if lower.endswith(".xprt") and binary:
        raise ToolError(
            f"«{path}» назван XML-форматом (.xprt), а сохраняем нативный "
            "бинарный: среда откроет файл как XML и не разберёт его. "
            "Сохраните в .prt или передайте binary=False.")


@mcp.tool()
@runtime._com_threaded
def save_project(path: str, binary: bool = False,
                 show_form: bool = True) -> str:
    """Сохранить текущий проект в файл.

    Два формата, и назначение у них разное:

    * XML (`.xprt`, по умолчанию) — обычный текст: читается глазами, диффится,
      переживает перенос между версиями;
    * бинарный (`.prt`, `binary=True`) — **нативный формат проекта**, тот самый,
      который открывает GUI SimInTech. XML тоже открывается GUI — под именем
      `.xprt`: формат среда выбирает по расширению, поэтому расхождение имени
      и формата — отказ (см. ниже).

    Перед записью показывается форма проекта (`FormShow`) — иначе файл
    получится «закрытым» для GUI: состояние окна хранится в самом проекте, и
    сессия, работающая через COM, записывает в него признак «окно скрыто». COM
    такой проект потом открывает и считает, а GUI восстанавливает сохранённое
    состояние окна и окна модели не показывает — выглядит как «проект не
    открылся».

    **Запись проверяется по файлу.** Залипшая сессия сообщает об успехе, не
    записывая ничего (simintech-code#21), а «сохранено» без файла — ложь,
    которая всплывёт только на чтении; поэтому файл обязан появиться или
    обновиться, иначе — отказ с диагнозом.

    **Расширение и формат — одно и то же.** Среда выбирает формат проекта по
    расширению (`.prt` — нативный, `.xprt` — XML), поэтому имя, обещающее не
    тот формат, которым пишем, — отказ до всякой работы: XML в `.prt` GUI
    показал бы «data error» (живой случай 02.10.2026).

    Args:
        path: путь к файлу (абсолютный).
        binary: True — нативный бинарный `.prt`; False — XML `.xprt`.
        show_form: показать форму проекта перед сохранением (см. выше).
            False — для безоконных машин: окно не появится, но и GUI потом
            не покажет окно модели этого проекта.
    """
    _refuse_format_extension_mismatch(path, binary)
    project = session._ensure_project()
    if show_form:
        project.show_form()
        tail = (" Форма проекта показана — без этого файл открывался бы в GUI "
                "без окна модели.")
    else:
        tail = (" Форму не показывали: GUI откроет файл без окна модели.")
    if binary:
        before = _save_mtime(path)
        project.save_binary(path)
        _verify_saved(path, before)
        return f"Проект сохранён в бинарный файл (.prt): {path}.{tail}"
    before = _save_mtime(path)
    project.save_xml(path)
    _verify_saved(path, before)
    return f"Проект сохранён в XML (.xprt): {path}.{tail}"


@mcp.tool()
@runtime._com_threaded
def close_project() -> str:
    """Закрыть текущий проект.

    Ответ называет, что именно закрыто, и что текущего проекта больше нет:
    следующий мутирующий вызов после закрытия откажет, и агент не должен
    искать причину (issue #18).

    Участник открытого пакета не закрывается: `CloseProject` исключил бы его
    из состава пакета (живой замер 01.10.2026 — состав 2 → 1), а это операция
    над пакетом, не над проектом. Отказ называет выход: закрыть пакет целиком
    (`close_pack`) или переключиться на другого участника
    (`select_pack_project`).
    """
    if session._project is None:
        return "Без изменений: проект не был открыт"
    membership = session._pack_membership()
    if membership is not False:
        if membership is None:
            # Состав пакета не читается — «неизвестно» не разрешение: если
            # проект участник, CloseProject исключит его из состава (замер
            # 01.10.2026), а отличить «участник» от «не участник» нечем.
            raise ToolError(
                "Принадлежность текущего проекта к открытому пакету "
                "проверить не удалось (состав пакета не читается): закрывать "
                "вслепую нельзя — если проект участник, закрытие исключит его "
                "из состава пакета (живой замер 01.10.2026). Повторите "
                "`list_pack_projects`; закрыть пакет целиком можно "
                "`close_pack`.")
        raise ToolError(
            f"Текущий проект — участник открытого пакета "
            f"{session._pack_label()}: его закрытие исключило бы проект из "
            f"состава пакета (живой замер 01.10.2026). Закройте пакет целиком "
            f"(`close_pack`) или выберите другой проект "
            f"(`select_pack_project`).")
    project = session._project
    label = session._project_label()
    # Порядок: сначала закрыть, потом сбросить состояние. Если `CloseProject`
    # не ответил, проект остаётся текущим — его видно и можно закрыть повторно,
    # а не потерять открытым внутри mmain.exe.
    project.close()
    session._set_project(None)
    return f"Проект закрыт: {label} — текущего проекта больше нет"
