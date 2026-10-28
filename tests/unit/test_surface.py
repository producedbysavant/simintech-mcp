"""Регистрация инструментов, ресурсов и промптов: клиент должен видеть цельный набор."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from simintech_mcp import runtime, surface
from simintech_mcp.server import mcp

import simintech_mcp

from _support import _text, all_tools

#: Отрицание создания блока: утверждения вида «такой класс не создаётся».
_DENIALS = ("не создаются", "не создаётся", "не создаёт", "через COM нет")

#: Инструменты, которым COM не нужен: файлы результатов, разбор сохранённого
#: проекта, справка и роль узла в сетевом расчёте — они работают и на Linux.
#: Остальные обязаны идти в COM-поток. Это утверждение о назначении, а не
#: вывод из кода: будь оно выводом, проверка повторяла бы декоратор.
_PLAIN_TOOLS = {
    "read_output_file", "summarize_output_file", "inspect_project_file",
    "get_model_manifest", "query_model",
    "help_text", "project_network_role", "create_pack",
    "search_language_functions", "get_language_function",
}


def _constants_text() -> str:
    """Исходник `constants.py` библиотеки — как текст, без импорта имён."""
    spec = importlib.util.find_spec("simintech_api")
    assert spec is not None and spec.origin, "пакет simintech_api не найден"
    return (Path(spec.origin).parent / "constants.py").read_text(
        encoding="utf-8")


def _fsm_names() -> set[str]:
    """Записи библиотеки FSM: полные имена и короткие заголовки с палитры."""
    src = _constants_text()
    prefix = re.search(r'FSM_RECORD_PREFIX\s*=\s*"([^"]*)"', src)
    block = re.search(r"FSM_BLOCK_RECORDS\s*=\s*\{(.*?)\}", src, re.S)
    assert prefix is not None and block is not None, (
        "не разобрали константы FSM из constants.py")
    head = prefix.group(1)
    # Имя записи — последний литерал строки словаря («ключ»: ПРЕФИКС + «имя»),
    # поэтому разбор не зависит от того, как записано значение.
    values = [re.findall(r'"([^"]*)"', line)[-1]
              for line in block.group(1).splitlines() if '"' in line]
    assert values and not any(v.startswith(head) for v in values), (
        "вид FSM_BLOCK_RECORDS изменился — разбор надо поправить")
    records = {head + name for name in values}
    # Заголовок с палитры — имя записи без префикса: именно его отрицал старый
    # текст справки, и именно он в модели не работает, а запись работает.
    return records | {name[len(head):] for name in records}


def _denied_names(text: str) -> set[str]:
    """Имена в кавычках, стоящие в предложении с отрицанием создания."""
    denied: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)):
        if any(marker in sentence for marker in _DENIALS):
            denied.update(re.findall(r"«([^»]+)»", sentence))
            denied.update(re.findall(r"`([^`]+)`", sentence))
    return denied


#: Инструменты, чей вызов меняет проект: их ответы обязаны называть, куда
#: внесены изменения (`mutates_project=True`). Список — контракт поверхности:
#: пропавший флаг вернёт слепые ответы (issue #18 — импорт ушёл не в тот
#: проект), лишний — соврёт о правке у инструмента чтения.
_MUTATING_TOOLS = {
    "add_block", "remove_block", "connect", "connect_branch",
    "disconnect_wire",
    "set_block_param", "fit_port_blocks", "fit_value_labels",
    "set_block_size", "set_block_script", "layout_place", "fit_view",
    "set_block_center",
    "import_model_text", "set_page_script", "run_page_script",
    "inject_submodel_script", "set_signal", "set_calc_time",
    "set_project_config",
    "build_model", "build_and_verify", "format_model",
}

#: Чистые чтения — аннотация `readOnlyHint: True`: ни проект, ни сессия, ни
#: расчёт не меняются. Список — контракт поверхности: хост по этой аннотации
#: авто-одобряет вызов, и лишняя метка открыла бы без подтверждения
#: инструмент с побочным эффектом (худший класс ошибки — авто-одобрение
#: мутации и необратимо, и незаметно). Контурные чтения (`export_model_text`,
#: `check_model_layout`, `audit_routing`) сюда не входят: они перезапускают
#: расчёт и сдвигают модельное время. `save_screenshot` — тоже не чтение:
#: пишет файл и кадр страницы (побочные эффекты названы в его ответе), но и
#: не разрушительный — файл уникален, перезаписи чужого нет.
_READ_ONLY_TOOLS = {
    "status", "get_time", "list_blocks", "list_wires", "get_block_params",
    "get_page_script", "get_block_script", "get_project_config",
    "list_signals", "get_signal", "read_output_file", "list_annotations",
    "summarize_output_file", "inspect_project_file", "project_network_role",
    "list_pack_projects", "help_text", "search_language_functions",
    "get_language_function",
    "doctor", "inspect_model", "get_model_manifest", "query_model",
}

#: Удаление/перезапись/сброс — `destructiveHint: True` (хост показывает
#: подтверждение): удаление блока и линии, закрытие сессии и пакета,
#: перезапись файла проекта, откат несохранённых правок.
_DESTRUCTIVE_TOOLS = {
    "remove_block", "disconnect_wire", "disconnect", "close_project",
    "close_pack", "save_project", "reload_project",
}


@pytest.mark.anyio
async def test_mutating_tools_are_marked():
    """Правки помечены флагом — метку ставит только `runtime`."""

    tools = await all_tools()
    marked = {t.name for t in tools
              if getattr(t.fn, runtime.MUTATES_PROJECT_MARK, False)}

    assert marked == _MUTATING_TOOLS, (
        f"без метки: {sorted(_MUTATING_TOOLS - marked)}; "
        f"лишняя метка: {sorted(marked - _MUTATING_TOOLS)}")


@pytest.mark.anyio
async def test_tool_annotations_contract():
    """У каждого инструмента — заголовок и честный `readOnlyHint`.

    Аннотации — то, по чему хост решает об авто-одобрении чтения и
    подтверждении разрушительных вызовов. Отсутствие аннотации читается
    хостом как «худший случай» (ничего не авто-одобряется), а вот лишний
    `readOnlyHint: True` — ошибка необратимая: инструмент с побочным
    эффектом вызвался бы без спроса (аудит 16.10.2026: аннотаций не было ни
    у одного из 59). Наборы сверяются в обе стороны, как у мутирующих.
    """
    tools = await all_tools()
    by_name = {t.name: t for t in tools}

    no_title = [name for name, t in by_name.items() if not t.title]
    assert not no_title, f"без заголовка: {sorted(no_title)}"

    unset = [name for name, t in by_name.items()
             if t.annotations is None or t.annotations.read_only_hint is None]
    assert not unset, f"readOnlyHint не задан: {sorted(unset)}"

    read_only = {name for name, t in by_name.items()
                 if t.annotations is not None
                 and t.annotations.read_only_hint is True}
    assert read_only == _READ_ONLY_TOOLS, (
        f"лишний readOnlyHint: {sorted(read_only - _READ_ONLY_TOOLS)}; "
        f"пропал readOnlyHint: {sorted(_READ_ONLY_TOOLS - read_only)}")

    destructive = {name for name, t in by_name.items()
                   if t.annotations is not None
                   and t.annotations.destructive_hint is True}
    assert destructive == _DESTRUCTIVE_TOOLS, (
        f"лишний destructiveHint: {sorted(destructive - _DESTRUCTIVE_TOOLS)}; "
        f"пропал destructiveHint: {sorted(_DESTRUCTIVE_TOOLS - destructive)}")

    # Мутирующий инструмент не бывает чистым чтением; разрушительные —
    # не чтения по построению (пересечение наборов пусто).
    assert not (_MUTATING_TOOLS & _READ_ONLY_TOOLS)
    assert not (_DESTRUCTIVE_TOOLS & _READ_ONLY_TOOLS)


@pytest.mark.anyio
async def test_all_tools_registered():
    """Зарегистрированы ровно ожидаемые инструменты — набор в обе стороны.

    Прежде проверка была включением (56 из 59): `save_screenshot`, `fit_view`
    и `set_block_center` в наборе не перечислялись, и о любом инструменте вне
    списка тест молчал бы. Число состава держит README
    (`test_readme_tool_count_matches_registry`), имена — здесь.
    """
    tools = await all_tools()
    names = {t.name for t in tools}
    expected = {
        "status", "disconnect",
        "create_project", "open_project", "reload_project", "save_project",
        "close_project", "set_calc_time", "project_network_role",
        "create_pack", "open_pack", "close_pack", "list_pack_projects",
        "select_pack_project", "pack_run", "pack_step", "pack_stop",
        "get_project_config", "set_project_config",
        "add_block", "remove_block", "connect", "connect_branch",
        "disconnect_wire", "list_blocks", "list_wires",
        "get_block_params", "set_block_param", "set_block_size",
        "fit_port_blocks", "fit_value_labels",
        "run", "step", "stop", "get_time",
        "list_signals", "get_signal", "set_signal", "export_signal_db",
        "read_output_file", "summarize_output_file", "inspect_project_file",
        "audit_routing", "layout_place", "check_model_layout", "help_text",
        "search_language_functions", "get_language_function",
        "export_model_text", "import_model_text", "list_annotations",
        "get_page_script", "set_page_script", "run_page_script",
        "inject_submodel_script",
        "get_block_script", "set_block_script",
        "save_screenshot", "fit_view", "set_block_center",
        "doctor", "build_model", "build_and_verify", "inspect_model",
        "simulate", "format_model", "load_model", "get_model_manifest",
        "query_model",
    }
    assert expected == names, (
        f"не хватает: {sorted(expected - names)}; "
        f"лишние: {sorted(names - expected)}")


@pytest.mark.anyio
async def test_readme_tool_count_matches_registry():
    """Число инструментов в README совпадает с фактическим (находка ревью).

    Тест на перечисление имён (ниже) не ловит числа: README успел пожить с
    «44 при 45» и «47 при 48». Число — обещание полноты состава, и оно
    обязано быть правдой или отсутствовать (в `help_text` числа нет намеренно).
    """
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(
        encoding="utf-8")
    found = re.search(r"Всего инструментов: (\d+)", readme)
    assert found, "в README нет строки «Всего инструментов — N»"

    tools = await all_tools()

    assert int(found.group(1)) == len(tools), (
        f"README говорит {found.group(1)}, а инструментов {len(tools)}")


@pytest.mark.anyio
async def test_readme_lists_every_tool():
    """Таблица «Инструменты» в README не отстаёт от `tools/list`.

    Расхождение уже случалось: из таблицы пропали `list_wires`,
    `project_network_role`, `get_project_config`, `set_project_config` и
    `export_signal_db` — 29 инструментов выглядели как 24, и клиент не знал о
    существовании пяти. Проверка держит список полным, а не полагается на то,
    что автор правки вспомнит про README.
    """
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(
        encoding="utf-8")
    section = readme.split("## Инструменты", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"`([a-z][a-z0-9_]*)`", section))
    names = {t.name for t in await all_tools()}

    assert not names - listed, f"README не перечисляет: {names - listed}"


def test_server_version_is_package_version():
    """Клиент видит версию пакета, а не версию fastmcp.

    Без `version=` `FastMCP` объявляет в `serverInfo.version` версию
    библиотеки: своя версия в протокол не попадает вовсе, а после апгрейда
    зависимости объявленное число меняется без единой правки в репозитории.
    """
    assert mcp.version == simintech_mcp.__version__


@pytest.mark.anyio
async def test_all_tools_have_failure_contract():
    """У каждого инструмента — декоратор контракта отказа, а не голая функция.

    Инструмент без декоратора регистрируется, попадает в `tools/list` и
    проходит `test_all_tools_registered`, но отказ возвращает обычной строкой:
    клиент, доверяющий `isError`, видит успех там, где работа не выполнена, а
    вызова нет в журнале.
    """
    tools = await all_tools()

    unwrapped = [t.name for t in tools if not hasattr(t.fn, "__wrapped__")]

    assert not unwrapped, f"инструменты без декоратора: {sorted(unwrapped)}"

    # `__wrapped__` есть у любой обёртки, в том числе чужой: инструмент,
    # обёрнутый не `runtime`, прошёл бы проверку выше, но `_call_guarded` в нём
    # нет, и отказ снова стал бы текстом. Метку ставит только `runtime`,
    # поэтому её отсутствие — тоже отказ.
    unmarked = [t.name for t in tools
                if getattr(t.fn, runtime.COM_THREAD_MARK, None) is None]

    assert not unmarked, f"инструменты вне контракта runtime: {sorted(unmarked)}"


@pytest.mark.anyio
async def test_plain_tools_are_exactly_the_com_free_ones():
    """`plain_tool` стоит там, где COM не нужен, и только там.

    `__wrapped__` различает «обёрнут / не обёрнут», но не сам декоратор:
    COM-инструмент под `plain_tool` обёрнут ровно так же. Между тем это
    опаснее — на Linux-тестах (COM нет вовсе) он отработает, а на Windows
    упадёт с `CO_E_OBJNOTCONNECTED`: COM-объект в чужом потоке. Поэтому
    сверяем метку, которую оставил фактически применённый декоратор.
    """
    tools = await all_tools()

    plain = {t.name for t in tools
             if getattr(t.fn, runtime.COM_THREAD_MARK, None) is False}

    assert plain == _PLAIN_TOOLS, (
        f"plain_tool лишний у: {sorted(plain - _PLAIN_TOOLS)}; "
        f"plain_tool пропал у: {sorted(_PLAIN_TOOLS - plain)}")


@pytest.mark.anyio
async def test_help_text_tool():
    """help_text возвращает справку с ключевыми командами."""
    text = _text(await mcp.call_tool("help_text", {}))
    assert "add_block" in text
    assert "create_project" in text
    # Перечень инструментов живёт в tools/list, а не в справке.
    assert "tools/list" in text
    for name in surface.CORE_TOOLS:
        assert name in text, name


@pytest.mark.anyio
async def test_help_text_does_not_use_com_thread(monkeypatch):
    """Справка не трогает COM — и не должна вставать в очередь COM-потока.

    Под `com_threaded` она занимала бы единственный выделенный поток: при
    занятом `mmain.exe` справка упиралась бы в `COM_CALL_TIMEOUT`, хотя ей это
    не нужно. Проверка сторожит именно выбор декоратора.
    """
    def boom(*args, **kwargs):
        raise AssertionError("справка ушла в COM-поток")

    monkeypatch.setattr(runtime._COM_EXECUTOR, "submit", boom)

    text = _text(await mcp.call_tool("help_text", {}))

    assert "create_project" in text


@pytest.mark.anyio
async def test_help_text_does_not_deny_creatable_classes():
    """Справка не отрицает создаваемость класса, который есть в каталоге.

    Неверный факт в справке дороже ошибки: агент читает её до первого вызова
    инструмента, и ни отказ, ни расчёт его не диагностируют. Отрицались ровно
    записи библиотеки «Конечные автоматы» — они создаются по **полному имени
    записи** (`constants.FSM_BLOCK_RECORDS`), а не по заголовку с палитры.
    """
    from simintech_api.catalog import load_default_catalog

    catalog = load_default_catalog()
    assert len(catalog) > 0, "каталог блоков пуст — сверять не с чем"
    available = set(catalog.classes()) | _fsm_names()

    denied = _denied_names(_text(await mcp.call_tool("help_text", {})))

    assert not denied & available, (
        f"справка отрицает создаваемые классы: {sorted(denied & available)}")


@pytest.mark.anyio
async def test_resources_registered():
    """Зарегистрированы ресурсы simintech://."""
    res = await mcp.list_resources()
    uris = {str(getattr(r, "uri", r)) for r in res}
    assert "simintech://status" in uris
    assert "simintech://project/blocks" in uris


@pytest.mark.anyio
async def test_prompts_registered():
    """Зарегистрированы промпты-шаблоны."""
    prompts = await mcp.list_prompts()
    names = {getattr(p, "name", str(p)) for p in prompts}
    assert "create_pid_model" in names
    assert "create_rc_chain" in names


@pytest.mark.anyio
async def test_render_prompt_pid():
    """Промпт create_pid_model подставляет параметры."""
    rendered = await mcp.render_prompt(
        "create_pid_model",
        arguments={"kp": "1.5", "setpoint": "2.0"},
    )
    text = str(rendered)
    assert "create_project" in text
    assert "yk=2.0" in text


@pytest.mark.anyio
async def test_render_prompt_rc():
    """Промпт create_rc_chain работает."""
    rendered = await mcp.render_prompt(
        "create_rc_chain",
        arguments={"rc": "1.0", "amplitude": "3.0"},
    )
    text = str(rendered)
    assert "create_project" in text
    assert "yk=3.0" in text


@pytest.mark.anyio
async def test_static_resources_are_registered():
    """Каталог блоков и скиллы отдаются как ресурсы, а не только инструменты."""
    resources = await mcp.list_resources()
    uris = {str(item.uri) for item in resources}

    assert {"simintech://status", "simintech://project/blocks",
            "simintech://blocks/catalog", "simintech://skills"} <= uris
