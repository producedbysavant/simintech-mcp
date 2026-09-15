"""Тесты MCP-сервера: регистрация инструментов и логика без COM."""

import io
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from simintech_mcp.server import mcp  # noqa: E402


def _text(result) -> str:
    """Достать текст из результата call_tool (форма зависит от версии MCP)."""
    if isinstance(result, (list, tuple)):
        return result[0].text
    content = getattr(result, "content", None)
    if content:
        return content[0].text
    return str(result)


async def _error(tool: str, arguments: dict) -> str:
    """Вызвать инструмент, ожидая отказ; вернуть текст отказа.

    Отказ доставляется исключением `ToolError` — именно по нему MCP выставляет
    `isError` в ответе. Раньше инструменты возвращали строку «ERROR: …», и
    клиент, доверяющий флагу, видел успех.
    """
    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool(tool, arguments)
    return str(excinfo.value)


@pytest.mark.anyio
async def test_all_tools_registered():
    """Зарегистрированы все ожидаемые инструменты."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "status", "disconnect",
        "create_project", "open_project", "save_project", "close_project",
        "set_calc_time",
        "add_block", "connect", "list_blocks",
        "get_block_params", "set_block_param",
        "run", "step", "stop", "get_time",
        "list_signals", "get_signal", "set_signal",
        "read_output_file",
        "layout_place", "help_text",
    }
    assert expected <= names, f"Не хватает: {expected - names}"


@pytest.mark.anyio
async def test_read_output_file_without_com(tmp_path):
    """read_output_file читает результат блока «В файл» (COM не нужен)."""
    path = tmp_path / "result.txt"
    path.write_text("0\t6\n0.1\t6\n0.2\t6\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": str(path)})
    text = _text(result)

    assert "строк 3" in text
    assert "0.2\t6" in text


@pytest.mark.anyio
async def test_read_output_file_missing(tmp_path):
    """Отсутствующий файл — отказ с понятной причиной."""
    text = await _error("read_output_file", {"path": str(tmp_path / "нет.txt")})

    assert "файла нет" in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_blocks_outside(tmp_path, monkeypatch):
    """С SIMINTECH_OUTPUT_DIR читается только этот каталог и его подкаталоги."""
    sandbox = tmp_path / "results"
    sandbox.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("секрет\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    text = await _error("read_output_file", {"path": str(outside)})

    assert "вне разрешённого каталога" in text
    assert "секрет" not in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_root_must_be_dir(tmp_path, monkeypatch):
    """Несуществующий каталог в SIMINTECH_OUTPUT_DIR — ошибка конфигурации.

    Проверка «fail closed»: иначе инструмент молча отказывал бы с невнятной
    причиной «путь вне разрешённого каталога».
    """
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path / "нет-такого"))

    text = await _error("read_output_file", {"path": str(tmp_path / "x.txt")})

    assert "не является каталогом" in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_allows_inside(tmp_path, monkeypatch):
    """Файл внутри разрешённого каталога читается."""
    sandbox = tmp_path / "results"
    sandbox.mkdir()
    inside = sandbox / "out.txt"
    inside.write_text("0\t6\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    result = await mcp.call_tool("read_output_file", {"path": str(inside)})
    text = _text(result)

    assert "строк 1" in text
    assert "0\t6" in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_blocks_parent_escape(tmp_path, monkeypatch):
    """`..` не помогает выйти из песочницы: путь раскрывается до проверки."""
    sandbox = tmp_path / "results"
    sandbox.mkdir()
    (tmp_path / "secret.txt").write_text("секрет\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    escape = str(sandbox / ".." / "secret.txt")
    text = await _error("read_output_file", {"path": escape})

    assert "секрет" not in text


@pytest.mark.anyio
async def test_read_output_file_stops_on_size_limit(tmp_path, monkeypatch):
    """Объём чтения ограничен — большой файл не уходит в контекст целиком."""
    from simintech_mcp import server as server_module

    monkeypatch.setattr(server_module, "MAX_OUTPUT_BYTES", 30)
    big = tmp_path / "big.txt"
    big.write_text("\n".join(["строка"] * 100) + "\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": str(big)})
    text = _text(result)

    assert "чтение остановлено" in text
    assert text.count("строка") < 100


def test_split_props_keeps_array_commas():
    """Запятые внутри `[...]` не считаются разделителями параметров."""
    from simintech_mcp.server import _split_props

    assert _split_props("a=[1, -1], b=2") == ["a=[1, -1]", "b=2"]
    assert _split_props("a=2") == ["a=2"]
    assert _split_props("") == []
    assert _split_props("filename=C:\\Temp\\out.txt,count=1,step=[0.2]") == [
        "filename=C:\\Temp\\out.txt", "count=1", "step=[0.2]"]


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
    text = _text(await mcp.call_tool("help_text", {}))
    assert "add_block" in text
    assert "create_project" in text
    # Перечень инструментов живёт в tools/list, а не в справке.
    assert "tools/list" in text


class _PlacedBlock:
    """Блок с координатами — для проверки, что расстановка применяется."""

    def __init__(self, name, block_id):
        self._name = name
        self._id = block_id
        self.center = None

    @property
    def id(self):
        return self._id

    def get_name(self):
        return self._name

    def set_center(self, cx, cy):
        self.center = (cx, cy)
        return self


@pytest.mark.anyio
async def test_layout_place_applies_coordinates(monkeypatch):
    """layout_place не только считает координаты, но и применяет их.

    Раньше инструмент возвращал координаты текстом, а блоки не двигал: агент
    получал подтверждение расстановки, которой не было.
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    _install_fake_project(monkeypatch, {"k_0": first, "kx_0": second})

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert "Расставлено блоков: 2" in text
    assert first.center is not None, "координаты не применены к блоку"
    assert second.center is not None, "координаты не применены к блоку"
    assert first.center != second.center, "блоки не разнесены по слоям"


@pytest.mark.anyio
async def test_layout_place_rejects_unknown_block(monkeypatch):
    """Несуществующий блок — отказ, а не «успешная» расстановка."""
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = await _error("layout_place",
                        {"block_ids": "k_0,нет_такого", "connections": ""})

    assert "не найдены" in text


@pytest.mark.anyio
async def test_layout_place_rejects_connection_outside_block_ids(monkeypatch):
    """Связь ссылается на блок вне block_ids — расстановка невозможна."""
    _install_fake_project(monkeypatch, {
        "k_0": _PlacedBlock("k_0", 1),
        "kx_0": _PlacedBlock("kx_0", 2),
    })

    text = await _error("layout_place",
                        {"block_ids": "k_0", "connections": "k_0->kx_0"})

    assert "вне block_ids" in text


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

    def get_blocks(self):
        return list(self._blocks.values())

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

    text = _tool_text(await mcp.call_tool("get_block_params", {"block": "Gain"}))

    assert "Усилитель" in text
    assert "a = 2.5" in text


@pytest.mark.anyio
async def test_get_block_params_missing_block(monkeypatch):
    """Несуществующий блок — отказ."""
    _install_fake_project(monkeypatch, {})

    text = await _error("get_block_params", {"block": "Нет"})

    assert "не найден" in text


@pytest.mark.anyio
async def test_get_block_params_unknown_class(monkeypatch):
    """Класс вне каталога — явное сообщение, а не пустой список."""
    _install_fake_project(monkeypatch, {
        "X": _FakeBlock("Неизвестный класс", {"a": "1"}),
    })

    text = _tool_text(await mcp.call_tool("get_block_params", {"block": "X"}))

    assert "неизвестны" in text


@pytest.mark.anyio
async def test_set_block_param_applies_and_inits(monkeypatch):
    """set_block_param меняет свойство и переинициализирует блок."""
    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param", {"block": "Gain", "param": "a", "value": "3.5"}))

    assert block._props["a"] == "3.5"
    assert block.inited is True
    assert "ERROR" not in text


@pytest.mark.anyio
async def test_set_block_param_accepts_array(monkeypatch):
    """Массив в стиле SimInTech разбирается в список."""
    block = _FakeBlock("Сумматор", {})
    _install_fake_project(monkeypatch, {"Sum": block})

    await mcp.call_tool(
        "set_block_param", {"block": "Sum", "param": "a", "value": "[1, -1]"})

    assert block._props["a"] == "[1, -1]"


@pytest.mark.anyio
async def test_set_block_param_warns_on_unknown_param(monkeypatch):
    """Неизвестный параметр — предупреждение (отказ был бы молчаливым)."""
    block = _FakeBlock("Усилитель", {})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param", {"block": "Gain", "param": "неттакого", "value": "1"}))

    assert "неттакого" in text
    assert "отсутствует в каталоге" in text


@pytest.mark.anyio
async def test_set_block_param_missing_block(monkeypatch):
    """Несуществующий блок — отказ, а не тихий «успех»."""
    _install_fake_project(monkeypatch, {})

    text = await _error(
        "set_block_param", {"block": "Нет", "param": "a", "value": "1"})

    assert "не найден" in text


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
        "add_block", {"class_name": "Константа", "name_hint": "Src"}))

    assert "НЕ применилось" in text
    assert "k_0" in text


@pytest.mark.anyio
async def test_add_block_reports_auto_name(monkeypatch):
    """Без name_hint= инструмент возвращает фактическое автоимя."""
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


def test_com_threaded_returns_result_and_wraps_errors():
    """Отказ доставляется как `ToolError` — по нему MCP выставляет `isError`.

    Раньше инструменты сообщали об ошибке строкой «ERROR: …», и клиент,
    доверяющий флагу `isError`, видел 100% успеха.
    """
    from simintech_mcp.server import _com_threaded

    @_com_threaded
    def add(a, b):
        return a + b

    @_com_threaded
    def boom():
        raise ValueError("нет проекта")

    @_com_threaded
    def prose_error():
        return "ERROR: блока нет"

    @_com_threaded
    def ok_message():
        return "всё хорошо"

    assert add(2, 3) == 5
    assert ok_message() == "всё хорошо", "обычный текст не должен стать ошибкой"
    with pytest.raises(ToolError, match="нет проекта"):
        boom()
    with pytest.raises(ToolError, match="блока нет"):
        prose_error()


def test_com_threaded_times_out_instead_of_hanging(monkeypatch):
    """Зависание COM даёт отказ, а не молчаливое подвисание сервера."""
    import time as _time

    from simintech_mcp import server

    monkeypatch.setattr(server, "COM_CALL_TIMEOUT", 0.05)

    @server._com_threaded
    def slow():
        _time.sleep(0.3)
        return "поздно"

    with pytest.raises(ToolError, match="не ответил"):
        slow()


def test_com_threaded_preserves_signature():
    """functools.wraps сохраняет сигнатуру — иначе FastMCP не увидит аргументы."""
    from simintech_mcp.server import _com_threaded

    @_com_threaded
    def sample(name: str, count: int = 1) -> str:
        return name * count

    import inspect
    assert list(inspect.signature(sample).parameters) == ["name", "count"]


# ─── Жизненный цикл сессии ────────────────────────────────────────

class _ClosableProject:
    def __init__(self, raises=False):
        self.closed = False
        self._raises = raises

    def close(self):
        if self._raises:
            raise RuntimeError("проект уже закрыт средой")
        self.closed = True
        return self


def test_replace_project_closes_previous(monkeypatch):
    """Смена проекта закрывает предыдущий.

    Иначе create_project/open_project копили бы открытые проекты внутри
    mmain.exe, а инструменты молча работали бы с последним.
    """
    from simintech_mcp import server

    previous, fresh = _ClosableProject(), _ClosableProject()
    monkeypatch.setattr(server, "_project", previous)

    server._replace_project(fresh)

    assert previous.closed, "предыдущий проект не закрыт"
    assert server._project is fresh


def test_replace_project_tolerates_already_closed(monkeypatch):
    """Уже закрытый средой проект не должен ломать смену."""
    from simintech_mcp import server

    monkeypatch.setattr(server, "_project", _ClosableProject(raises=True))
    fresh = _ClosableProject()

    server._replace_project(fresh)

    assert server._project is fresh


def test_replace_project_without_previous(monkeypatch):
    """Первый проект в сессии — закрывать нечего."""
    from simintech_mcp import server

    monkeypatch.setattr(server, "_project", None)
    fresh = _ClosableProject()

    server._replace_project(fresh)

    assert server._project is fresh
    assert not fresh.closed


@pytest.mark.anyio
async def test_disconnect_resets_project_and_client(monkeypatch):
    """disconnect() сбрасывает и проект: иначе остаётся мёртвый ProjectId."""
    from simintech_mcp import server

    project = _ClosableProject()

    class _Client:
        connected = True

        def __init__(self):
            self.disconnected = False

        def disconnect(self):
            self.disconnected = True

    client = _Client()
    monkeypatch.setattr(server, "_project", project)
    monkeypatch.setattr(server, "_client", client)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert project.closed
    assert client.disconnected
    assert server._project is None
    assert server._client is None
    assert "Сессия завершена" in text


@pytest.mark.anyio
async def test_disconnect_without_session_is_noop(monkeypatch):
    """Нечего сбрасывать — сообщение «без изменений», а не вид действия."""
    from simintech_mcp import server

    monkeypatch.setattr(server, "_project", None)
    monkeypatch.setattr(server, "_client", None)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert "Без изменений" in text


@pytest.mark.anyio
async def test_close_project_without_project_is_noop(monkeypatch):
    """Закрытие без проекта — тоже «без изменений»."""
    from simintech_mcp import server

    monkeypatch.setattr(server, "_project", None)

    text = _text(await mcp.call_tool("close_project", {}))

    assert "Без изменений" in text


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
