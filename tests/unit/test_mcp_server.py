"""Тесты MCP-сервера: регистрация инструментов и логика без COM."""

import io
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from simintech_mcp.server import mcp


@pytest.mark.anyio
async def test_all_tools_registered():
    """Зарегистрированы все ожидаемые инструменты."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "status", "disconnect",
        "create_project", "open_project", "save_project", "close_project",
        "add_block", "connect", "list_blocks",
        "get_block_params", "set_block_param",
        "run", "step", "stop", "get_time",
        "list_signals", "get_signal", "set_signal",
        "layout_place", "help_text",
    }
    assert expected <= names, f"Не хватает: {expected - names}"


@pytest.mark.anyio
async def test_status_on_linux():
    """status() на Linux сообщает о несовместимости платформы."""
    if sys.platform == "win32":
        pytest.skip("Тест для не-Windows окружения")
    result = await mcp.call_tool("status", {})
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "Windows" in text


@pytest.mark.anyio
async def test_help_text_tool():
    """help_text возвращает справку с ключевыми командами."""
    result = await mcp.call_tool("help_text", {})
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "add_block" in text
    assert "create_project" in text
    assert "get_signal" in text


@pytest.mark.anyio
async def test_layout_place_works_without_com():
    """layout_place работает без COM (чистый алгоритм)."""
    result = await mcp.call_tool(
        "layout_place",
        {"block_ids": "A,B,C", "connections": "A->B,B->C"},
    )
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "A:" in text
    assert "B:" in text
    assert "C:" in text


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


# ─── Параметры блоков (без COM) ───────────────────────────────────

class _FakeBlock:
    """Подделка блока: свойства хранятся в словаре."""

    def __init__(self, class_name, props=None):
        self._class_name = class_name
        self._props = dict(props or {})
        self.inited = False

    @property
    def class_name(self):
        return self._class_name

    def get_properties(self, catalog=None):
        from simintech_api.catalog import load_default_catalog
        source = catalog or load_default_catalog()
        return {p: self._props[p] for p in source.props_for(self._class_name)
                if p in self._props}

    def set_property(self, name, value):
        from simintech_api.utils.converters import value_to_prop_string
        self._props[name] = value_to_prop_string(value)
        return self

    def init(self):
        self.inited = True
        return self


class _FakePage:
    def __init__(self, blocks, created=None):
        self._blocks = blocks
        self._created = created if created is not None else []

    def find_block(self, name):
        return self._blocks.get(name)

    def create_block(self, class_name, x, y):
        block = _RenamingBlock(class_name)
        self._created.append(block)
        return block


class _RenamingBlock:
    """Блок, который НЕ переименовывается — как реальный SimInTech."""

    AUTO_NAME = "k_0"

    def __init__(self, class_name):
        self._class_name = class_name
        self._props = {"Name": self.AUTO_NAME}

    @property
    def id(self):
        return 1

    @property
    def class_name(self):
        return self._class_name

    def set_name(self, name):
        # Проверено на SimInTech64: SetBlockProp("Name") не переименовывает
        # блок — имя остаётся автоматическим.
        return self

    def set_property(self, name, value):
        self._props[name] = value
        return self

    def get_name(self):
        return self._props["Name"]


class _FakeProject:
    def __init__(self, blocks):
        self._page = _FakePage(blocks)

    def get_main_page(self):
        return self._page


def _install_fake_project(monkeypatch, blocks):
    """Подменить открытый проект на подделку с заданными блоками."""
    from simintech_mcp import server
    monkeypatch.setattr(server, "_project", _FakeProject(blocks))


def _tool_text(result):
    """Извлечь текст из результата mcp.call_tool() (устойчиво к версиям).

    Разные версии FastMCP возвращают либо кортеж контента, либо объект
    CallToolResult с полем `.content`.
    """
    if isinstance(result, (list, tuple)) and result:
        return getattr(result[0], "text", str(result[0]))
    content = getattr(result, "content", None)
    if content:
        return getattr(content[0], "text", str(content[0]))
    return str(result)


@pytest.mark.anyio
async def test_get_block_params_returns_known_props(monkeypatch):
    """get_block_params читает свойства из каталога."""
    _install_fake_project(monkeypatch, {
        "Gain": _FakeBlock("Усилитель", {"Name": "Gain", "a": "2.5"}),
    })

    text = _tool_text(await mcp.call_tool("get_block_params", {"name": "Gain"}))

    assert "Усилитель" in text
    assert "a = 2.5" in text


@pytest.mark.anyio
async def test_get_block_params_missing_block(monkeypatch):
    """Несуществующий блок — сообщение, а не исключение."""
    _install_fake_project(monkeypatch, {})

    text = _tool_text(await mcp.call_tool("get_block_params", {"name": "Нет"}))

    assert text.startswith("ERROR")
    assert "не найден" in text


@pytest.mark.anyio
async def test_get_block_params_unknown_class(monkeypatch):
    """Класс вне каталога — явное сообщение, а не пустой список."""
    _install_fake_project(monkeypatch, {
        "X": _FakeBlock("Неизвестный класс", {"a": "1"}),
    })

    text = _tool_text(await mcp.call_tool("get_block_params", {"name": "X"}))

    assert "неизвестны" in text


@pytest.mark.anyio
async def test_set_block_param_applies_and_inits(monkeypatch):
    """set_block_param меняет свойство и переинициализирует блок."""
    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param", {"name": "Gain", "param": "a", "value": "3.5"}))

    assert block._props["a"] == "3.5"
    assert block.inited is True
    assert "ERROR" not in text


@pytest.mark.anyio
async def test_set_block_param_accepts_array(monkeypatch):
    """Массив в стиле SimInTech разбирается в список."""
    block = _FakeBlock("Сумматор", {})
    _install_fake_project(monkeypatch, {"Sum": block})

    await mcp.call_tool(
        "set_block_param", {"name": "Sum", "param": "a", "value": "[1, -1]"})

    assert block._props["a"] == "[1, -1]"


@pytest.mark.anyio
async def test_set_block_param_warns_on_unknown_param(monkeypatch):
    """Неизвестный параметр — предупреждение (отказ был бы молчаливым)."""
    block = _FakeBlock("Усилитель", {})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param", {"name": "Gain", "param": "неттакого", "value": "1"}))

    assert "неттакого" in text
    assert "отсутствует в каталоге" in text


@pytest.mark.anyio
async def test_set_block_param_missing_block(monkeypatch):
    _install_fake_project(monkeypatch, {})

    text = _tool_text(await mcp.call_tool(
        "set_block_param", {"name": "Нет", "param": "a", "value": "1"}))

    assert text.startswith("ERROR")


def test_coerce_param_value_parses_scalars():
    from simintech_mcp.server import _coerce_param_value

    assert _coerce_param_value("2") == 2
    assert _coerce_param_value("2.5") == 2.5
    assert _coerce_param_value(" -3 ") == -3
    assert _coerce_param_value("текст") == "текст"


def test_coerce_param_value_parses_arrays():
    from simintech_mcp.server import _coerce_param_value

    assert _coerce_param_value("[1, -1]") == [1, -1]
    assert _coerce_param_value("[1.5,2]") == [1.5, 2]
    assert _coerce_param_value("[]") == []


# ─── add_block: имя не применяется ────────────────────────────────

class _FakeProjectWithCreate:
    def __init__(self):
        self.page = _FakePage({})

    def get_main_page(self):
        return self.page


@pytest.mark.anyio
async def test_add_block_warns_when_rename_ignored(monkeypatch):
    """Если имя не применилось, инструмент сообщает об этом и даёт автоимя.

    Иначе агент получит «name=Src», а блок будет называться k_0 — и
    последующий connect по имени не найдёт блок.
    """
    from simintech_mcp import server
    monkeypatch.setattr(server, "_project", _FakeProjectWithCreate())

    text = _tool_text(await mcp.call_tool(
        "add_block", {"class_name": "Константа", "name": "Src"}))

    assert "НЕ применилось" in text
    assert "k_0" in text


@pytest.mark.anyio
async def test_add_block_reports_auto_name(monkeypatch):
    """Без name= инструмент возвращает фактическое автоимя."""
    from simintech_mcp import server
    monkeypatch.setattr(server, "_project", _FakeProjectWithCreate())

    text = _tool_text(await mcp.call_tool(
        "add_block", {"class_name": "Константа"}))

    assert "k_0" in text
    assert "НЕ применилось" not in text


def test_com_threaded_uses_single_dedicated_thread():
    """Все COM-вызовы идут через один поток.

    COM-объект привязан к апартаменту создавшего его потока; использование
    из другого потока даёт «Объект не подключен к серверу». Проверено на
    реальном SimInTech.
    """
    from simintech_mcp.server import _com_threaded

    @_com_threaded
    def whoami():
        import threading
        return threading.get_ident()

    assert whoami() == whoami() == whoami()


def test_com_threaded_returns_result_and_propagates_error():
    from simintech_mcp.server import _com_threaded

    @_com_threaded
    def add(a, b):
        return a + b

    @_com_threaded
    def boom():
        raise ValueError("нет проекта")

    assert add(2, 3) == 5
    with pytest.raises(ValueError, match="нет проекта"):
        boom()


def test_com_threaded_preserves_signature():
    """functools.wraps сохраняет сигнатуру — иначе FastMCP не увидит аргументы."""
    from simintech_mcp.server import _com_threaded

    @_com_threaded
    def sample(name: str, count: int = 1) -> str:
        return name * count

    import inspect
    assert list(inspect.signature(sample).parameters) == ["name", "count"]


# ─── Изоляция stdout ──────────────────────────────────────────────

class _FakeStdout(io.StringIO):
    """Подделка stdout: текстовый поток плюс отдельный бинарный «буфер».

    Повторяет структуру настоящего sys.stdout, у которого есть `.buffer` —
    именно через него транспорт MCP пишет JSON-RPC.
    """

    def __init__(self):
        super().__init__()
        self.buffer = io.StringIO()


def _install_fake_streams(monkeypatch):
    """Подменить sys.stdout/sys.stderr и вернуть (stdout, stderr)."""
    real = _FakeStdout()
    err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", real)
    monkeypatch.setattr(sys, "stderr", err)
    return real, err


def test_stdout_guard_routes_text_to_stderr(monkeypatch):
    """Посторонний вывод уходит в stderr, а .buffer остаётся настоящим."""
    from simintech_mcp.server import isolate_stdout

    real, err = _install_fake_streams(monkeypatch)
    isolate_stdout()

    # Транспорт MCP пишет протокол через sys.stdout.buffer.
    sys.stdout.buffer.write('{"jsonrpc": "2.0"}\n')
    # Посторонний вывод — print() из библиотеки, логов, предупреждений.
    print("диагностика")

    assert real.buffer.getvalue() == '{"jsonrpc": "2.0"}\n'
    assert "диагностика" not in real.buffer.getvalue()
    assert "диагностика" in err.getvalue()
    assert real.getvalue() == ""  # в текстовый stdout не попало ничего


def test_stdout_guard_keeps_buffer_identity(monkeypatch):
    """`.buffer` прокси — тот же объект, что и до изоляции."""
    from simintech_mcp.server import isolate_stdout

    real, _ = _install_fake_streams(monkeypatch)
    buffer_before = sys.stdout.buffer

    isolate_stdout()

    assert sys.stdout.buffer is buffer_before


def test_isolate_stdout_idempotent(monkeypatch):
    """Повторный вызов не оборачивает прокси второй раз."""
    from simintech_mcp.server import isolate_stdout

    _install_fake_streams(monkeypatch)
    isolate_stdout()
    first = sys.stdout
    isolate_stdout()

    assert sys.stdout is first


def test_stdout_guard_survives_flush_and_isatty(monkeypatch):
    """Служебные методы потока не падают и не пишут в stdout."""
    from simintech_mcp.server import isolate_stdout

    real, _ = _install_fake_streams(monkeypatch)
    isolate_stdout()

    sys.stdout.flush()
    assert sys.stdout.isatty() is False
    assert real.getvalue() == ""


def test_stdout_guard_private_attr_raises_without_recursion(monkeypatch):
    """Приватные имена не делегируются — иначе возможна бесконечная рекурсия."""
    from simintech_mcp.server import isolate_stdout

    _install_fake_streams(monkeypatch)
    isolate_stdout()

    with pytest.raises(AttributeError):
        sys.stdout._nonexistent_attr


def test_main_isolates_stdout_before_run(monkeypatch):
    """main() включает изоляцию ДО запуска транспорта."""
    from simintech_mcp import server

    _install_fake_streams(monkeypatch)
    seen = {}

    def fake_run(**kwargs):
        seen["guard_installed"] = isinstance(sys.stdout, server._StdoutGuard)

    monkeypatch.setattr(server.mcp, "run", fake_run)
    server.main()

    assert seen["guard_installed"] is True
