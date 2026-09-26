"""Регистрация инструментов, ресурсов и промптов: клиент должен видеть цельный набор."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from simintech_mcp import runtime
from simintech_mcp.server import mcp

import simintech_mcp

from _support import _text

#: Отрицание создания блока: утверждения вида «такой класс не создаётся».
_DENIALS = ("не создаются", "не создаётся", "не создаёт", "через COM нет")

#: Инструменты, которым COM не нужен: файлы результатов, разбор сохранённого
#: проекта, справка и роль узла в сетевом расчёте — они работают и на Linux.
#: Остальные обязаны идти в COM-поток. Это утверждение о назначении, а не
#: вывод из кода: будь оно выводом, проверка повторяла бы декоратор.
_PLAIN_TOOLS = {
    "read_output_file", "summarize_output_file", "inspect_project_file",
    "help_text", "project_network_role",
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


@pytest.mark.anyio
async def test_all_tools_registered():
    """Зарегистрированы все ожидаемые инструменты."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "status", "disconnect",
        "create_project", "open_project", "save_project", "close_project",
        "set_calc_time", "project_network_role",
        "get_project_config", "set_project_config",
        "add_block", "connect", "list_blocks", "list_wires",
        "get_block_params", "set_block_param",
        "run", "step", "stop", "get_time",
        "list_signals", "get_signal", "set_signal", "export_signal_db",
        "read_output_file", "summarize_output_file", "inspect_project_file",
        "layout_place", "help_text",
    }
    assert expected <= names, f"Не хватает: {expected - names}"


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
    names = {t.name for t in await mcp.list_tools()}

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
    tools = await mcp.list_tools()

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
    """`_plain_tool` стоит там, где COM не нужен, и только там.

    `__wrapped__` различает «обёрнут / не обёрнут», но не сам декоратор:
    COM-инструмент под `_plain_tool` обёрнут ровно так же. Между тем это
    опаснее — на Linux-тестах (COM нет вовсе) он отработает, а на Windows
    упадёт с `CO_E_OBJNOTCONNECTED`: COM-объект в чужом потоке. Поэтому
    сверяем метку, которую оставил фактически применённый декоратор.
    """
    tools = await mcp.list_tools()

    plain = {t.name for t in tools
             if getattr(t.fn, runtime.COM_THREAD_MARK, None) is False}

    assert plain == _PLAIN_TOOLS, (
        f"_plain_tool лишний у: {sorted(plain - _PLAIN_TOOLS)}; "
        f"_plain_tool пропал у: {sorted(_PLAIN_TOOLS - plain)}")


@pytest.mark.anyio
async def test_help_text_tool():
    """help_text возвращает справку с ключевыми командами."""
    text = _text(await mcp.call_tool("help_text", {}))
    assert "add_block" in text
    assert "create_project" in text
    # Перечень инструментов живёт в tools/list, а не в справке.
    assert "tools/list" in text


@pytest.mark.anyio
async def test_help_text_does_not_use_com_thread(monkeypatch):
    """Справка не трогает COM — и не должна вставать в очередь COM-потока.

    Под `_com_threaded` она занимала бы единственный выделенный поток: при
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
