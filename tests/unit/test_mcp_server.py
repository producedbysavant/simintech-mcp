"""Тесты MCP-сервера: регистрация инструментов и логика без COM."""

import io
import json
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
        "read_output_file", "summarize_output_file", "inspect_project_file",
        "layout_place", "help_text",
    }
    assert expected <= names, f"Не хватает: {expected - names}"


@pytest.mark.anyio
async def test_read_output_file_reads_inside_sandbox(tmp_path, monkeypatch):
    """read_output_file читает результат блока «В файл» (COM не нужен)."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "result.txt"
    path.write_text("0\t6\n0.1\t6\n0.2\t6\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": str(path)})
    text = _text(result)

    assert "строк 3" in text
    assert "0.2\t6" in text


@pytest.mark.anyio
async def test_read_output_file_relative_path_resolves_in_sandbox(
        tmp_path, monkeypatch):
    """Относительный путь ищется внутри каталога результатов."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t6\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": "out.txt"})
    text = _text(result)

    assert "строк 1" in text
    assert "0\t6" in text


@pytest.mark.anyio
async def test_read_output_file_missing(tmp_path, monkeypatch):
    """Отсутствующий файл внутри песочницы — отказ с понятной причиной."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))

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

    assert "разрешено только из каталога" in text
    assert "секрет" not in text


@pytest.mark.anyio
async def test_read_output_file_defaults_to_standard_dir(tmp_path, monkeypatch):
    """Без переменной песочница тоже действует — по стандартному каталогу.

    Ограничение не опция: иначе инструмент читал бы любой файл по пути от
    клиента, а клиент может быть без доступа к файловой системе.
    """
    from simintech_mcp.server import default_output_dir

    monkeypatch.delenv("SIMINTECH_OUTPUT_DIR", raising=False)
    outside = tmp_path / "secret.txt"
    outside.write_text("секрет\n", encoding="utf-8")

    text = await _error("read_output_file", {"path": str(outside)})

    assert "секрет" not in text
    # Сравниваем канонизированный путь: Windows отдаёт gettempdir() в коротком
    # виде (C:\Users\A-SAVC~1\...), а сервер печатает long-форму после realpath.
    assert os.path.realpath(default_output_dir()) in text, \
        "отказ должен называть стандартный каталог"


def test_default_output_dir_is_created_private(tmp_path, monkeypatch):
    """Каталога нет — он создаётся, и права не раздают его всем."""
    if sys.platform == "win32":
        pytest.skip("права POSIX на Windows не проверяются")
    from simintech_mcp import server as server_module

    target = tmp_path / "новый"
    monkeypatch.setattr(server_module, "default_output_dir", lambda: str(target))

    root = server_module.output_root()

    assert os.path.isdir(root)
    assert (os.stat(root).st_mode & 0o777) == 0o700


def test_default_output_dir_rejects_symlink(tmp_path, monkeypatch):
    """Подменённый ссылкой стандартный каталог не принимается.

    Каталог результатов лежит в предсказуемом месте: если его заранее создать
    символической ссылкой, `realpath` увёл бы песочницу в выбранное атакующим
    место, и ограничение стало бы фиктивным.

    Проверяется подменой `os.path.islink`, а не настоящей ссылкой: создание
    ссылок на Windows требует привилегий, а сервер работает именно там — тест
    с `skip` на Windows не проверял бы ничего на целевой платформе.
    """
    from simintech_mcp import server as server_module

    target = tmp_path / "simintech-output"
    target.mkdir()
    monkeypatch.setattr(server_module, "default_output_dir", lambda: str(target))
    monkeypatch.setattr(server_module.os.path, "islink", lambda p: True)

    with pytest.raises(ToolError, match="символическая ссылка"):
        server_module.output_root()


def test_default_output_dir_rejects_non_directory(tmp_path, monkeypatch):
    """Путь существует, но это файл — читать из него нечего."""
    from simintech_mcp import server as server_module

    target = tmp_path / "simintech-output"
    target.write_text("не каталог", encoding="utf-8")
    monkeypatch.setattr(server_module, "default_output_dir", lambda: str(target))

    with pytest.raises(ToolError, match="не является каталогом"):
        server_module.output_root()


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

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
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

    #: Размер, который «отдаёт» блок: у SimInTech он свой у каждого класса.
    SIZE = (60.0, 40.0)

    def __init__(self, name, block_id):
        self._name = name
        self._id = block_id
        self.center = None
        self.size_reads = 0

    @property
    def id(self):
        return self._id

    def get_name(self):
        return self._name

    def get_size(self):
        self.size_reads += 1
        return self.SIZE

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
async def test_add_block_sets_standard_size_for_extra_inputs(monkeypatch):
    """Число входов меняет штатный размер: «Сумматор» 3 входа — 32x48.

    Замерено по эталонным моделям SimInTech (2026-09-15): 32x32 при двух
    входах, 32x48 при трёх. Блок нестандартного размера — нарушение правил
    разработки.
    """
    from simintech_mcp import server
    project = _FakeProjectWithCreate()
    monkeypatch.setattr(server, "_project", project)

    await mcp.call_tool("add_block", {"class_name": "Сумматор", "in_ports": 3})

    created = project.page._created[-1]
    assert created.in_ports == 3
    assert created.position == (0.0, 0.0, 32.0, 48.0)


@pytest.mark.anyio
async def test_layout_place_uses_block_sizes(monkeypatch):
    """Расстановка считается по размерам самих блоков, а не по константам.

    Размер блока задан правилами разработки SimInTech: подставлять свой
    (60x40) нельзя — блок нестандартного размера считается нарушением.
    """
    first = _PlacedBlock("k_0", 1)
    second = _PlacedBlock("kx_0", 2)
    first.SIZE = (120.0, 80.0)
    second.SIZE = (120.0, 80.0)
    _install_fake_project(monkeypatch, {"k_0": first, "kx_0": second})

    await mcp.call_tool("layout_place",
                        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"})

    assert first.size_reads == 1, "размер блока не запрошен"
    assert second.size_reads == 1, "размер блока не запрошен"


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


# ─── Трассировка линий (ортогональные провода) ────────────────────

class _FakeWire:
    """Линия, считающая вызовы нормализации."""

    def __init__(self, wire_id, events=None):
        self.id = wire_id
        self.normalized = 0
        self._events = events

    def normalize(self):
        self.normalized += 1
        if self._events is not None:
            self._events.append(("normalize", self.id))
        return self


class _FakePort:
    """Порт с координатами: по ним считается выравнивание блоков."""

    def __init__(self, block, is_output, index):
        self._block = block
        self._is_output = is_output
        self._index = index

    def get_coords(self):
        cx, cy = self._block.center or (0.0, 0.0)
        if self._is_output:
            return (cx + 16.0, cy)
        return (cx - 16.0, cy - self._block.in_port_offset)


class _ConnectingBlock:
    """Блок, который соединяется и двигается — как настоящий."""

    #: Насколько основной вход ниже центра блока. У «Сумматора» входы стоят на
    #: четверти и трёх четвертях высоты, поэтому вход не совпадает с центром.
    in_port_offset = 0.0

    def __init__(self, name, block_id, events=None):
        self._name = name
        self._id = block_id
        self.center = None
        self.wires = []
        self.events = events

    def get_out_port(self, index=0):
        return _FakePort(self, True, index)

    def get_in_port(self, index=0):
        return _FakePort(self, False, index)

    @property
    def id(self):
        return self._id

    def get_name(self):
        return self._name

    def get_size(self):
        return (60.0, 40.0)

    def set_center(self, cx, cy):
        self.center = (cx, cy)
        return self

    def connect(self, other, out_index=0, in_index=0):
        wire = _FakeWire(len(self.wires) + 1, events=self.events)
        self.wires.append((wire, other, out_index, in_index))
        return wire


class _WireProject:
    """Проект с одной страницей — минимум для connect/layout_place."""

    def __init__(self, blocks, events=None):
        self.page = _FakePage(blocks)
        self.closed = False
        self._events = events
        self.repaints = 0

    def get_main_page(self):
        return self.page

    def repaint(self):
        self.repaints += 1
        if self._events is not None:
            self._events.append(("repaint", None))
        return self

    def close(self):
        self.closed = True


def _install_wire_project(monkeypatch, blocks):
    """Подменить проект и очистить реестр линий (он общий для сессии).

    Возвращает журнал вызовов: по нему проверяется, что перерисовка идёт
    до трассировки, а не наоборот.
    """
    from simintech_mcp import server as server_module
    server_module._WIRES.clear()
    events = []
    for block in blocks.values():
        block.events = events
    monkeypatch.setattr(server_module, "_project", _WireProject(blocks, events))
    return events


@pytest.mark.anyio
async def test_connect_only_remembers_wire(monkeypatch):
    """connect запоминает линию, но НЕ трассирует её.

    Трассировать в этот момент нельзя: блоки стоят в (0,0) друг на друге, и
    NormalizeWire оставляет в геометрии точки вроде (-160,-1056), которые
    потом не пересчитываются. Проверено на SimInTech64 2026-09-15.
    """
    from simintech_mcp import server as server_module
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("Integrator_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "Integrator_0": dst})

    text = _text(await mcp.call_tool("connect",
                                     {"src": "k_0", "dst": "Integrator_0"}))

    assert "Соединено" in text
    wire = src.wires[0][0]
    assert wire.normalized == 0, "линия трассирована до расстановки блоков"
    assert server_module._WIRES == [(wire, "k_0", 0, "Integrator_0", 0)], \
        "линия не запомнена вместе с концами для выравнивания"


@pytest.mark.anyio
async def test_layout_place_aligns_port_heights(monkeypatch):
    """Блок сдвигается так, чтобы основной вход лёг на высоту выхода.

    Иначе линия идёт с лишним изломом: у «Сумматора» входы на четверти и трёх
    четвертях высоты, а выход «Усилителя» посередине.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    dst.in_port_offset = 8.0
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert dst.center[1] == src.center[1] + 8.0, "вход не выровнен с выходом"
    assert dst.center[0] > src.center[0], "блоки не разнесены по слоям"
    assert "ВНИМАНИЕ" not in text


@pytest.mark.anyio
async def test_layout_place_repaints_before_routing(monkeypatch):
    """Порядок: перемещение → перерисовка → трассировка.

    Без перерисовки SimInTech прокладывает провода по прежним прямоугольникам
    блоков (они ещё лежат в (0,0) друг на друге) и оставляет в геометрии точки
    вроде (-160,-1056), которые потом не пересчитываются. Проверено на
    SimInTech64 2026-09-15.
    """
    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    events = _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    await mcp.call_tool("connect", {"src": "k_0", "dst": "kx_0"})
    wire = src.wires[0][0]

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert events == [("repaint", None), ("normalize", 1)], \
        "перерисовка должна идти до трассировки"
    assert wire.normalized == 1, "после расстановки линия не трассирована"
    assert "нормализовано 1" in text


@pytest.mark.anyio
async def test_layout_place_reports_no_wires(monkeypatch):
    """Если линий не создавали, инструмент об этом говорит, а не молчит."""
    _install_wire_project(monkeypatch, {
        "k_0": _ConnectingBlock("k_0", 1),
        "kx_0": _ConnectingBlock("kx_0", 2),
    })

    text = _text(await mcp.call_tool(
        "layout_place",
        {"block_ids": "k_0,kx_0", "connections": "k_0->kx_0"}))

    assert "Линий связи в этой сессии нет" in text


def test_replace_project_clears_wire_registry(monkeypatch):
    """Смена проекта обнуляет реестр: чужие WireId указывают в никуда."""
    from simintech_mcp import server as server_module

    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    server_module._WIRES.append(_FakeWire(99))  # запись целиком не важна

    server_module._replace_project(_WireProject({}))

    assert server_module._WIRES == []


@pytest.mark.anyio
async def test_close_project_clears_wire_registry(monkeypatch):
    """close_project обнуляет реестр линий вместе с проектом."""
    from simintech_mcp import server as server_module
    project = _WireProject({})
    _install_wire_project(monkeypatch, {})
    monkeypatch.setattr(server_module, "_project", project)
    server_module._WIRES.append(_FakeWire(1))

    text = _text(await mcp.call_tool("close_project", {}))

    assert server_module._WIRES == []
    assert "закрыт" in text


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
        self.in_ports = 0
        self.position = None

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

    def set_in_port_count(self, count):
        self.in_ports = count
        return self

    def set_position(self, x, y, *, width=None, height=None):
        self.position = (x, y, width, height)
        return self

    def get_name(self):
        return self._props["Name"]


class _FakeProject:
    def __init__(self, blocks):
        self._page = _FakePage(blocks)
        self.repaints = 0

    def get_main_page(self):
        return self._page

    def repaint(self):
        """Перерисовка редактора: layout_place зовёт её перед трассировкой."""
        self.repaints += 1
        return self


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
async def test_set_block_param_rejects_unknown_param(monkeypatch):
    """Неизвестное имя параметра — отказ ДО записи, а не молчаливая запись.

    `SetBlockProp` неизвестные имена не отвергает: значение уходило в никуда,
    и по ответу нельзя было отличить применённый параметр от неприменённого.
    Проверено на SimInTech64: `Константа.y0 = 5` не меняет ничего.
    """
    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = await _error("set_block_param",
                        {"block": "Gain", "param": "неттакого", "value": "1"})

    assert "неттакого" in text
    assert "Известные параметры" in text
    assert "allow_unknown" in text, "отказ должен подсказывать обход"
    assert "неттакого" not in block._props, "запись не должна была пройти"
    assert block.inited is False, "при отказе блок не переинициализируется"


@pytest.mark.anyio
async def test_set_block_param_rejects_computed_param(monkeypatch):
    """Вычисляемый параметр — отказ: COM принимает запись, значение не меняется."""
    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = await _error(
        "set_block_param",
        {"block": "Gain", "param": "formula_visible", "value": "1"})

    assert "вычисляемый" in text
    assert block.inited is False


@pytest.mark.anyio
async def test_set_block_param_allow_unknown_writes(monkeypatch):
    """`allow_unknown=True` — запись проходит: параметр может не быть в каталоге."""
    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})

    await mcp.call_tool(
        "set_block_param", {"block": "Gain", "param": "неттакого", "value": "1",
                            "allow_unknown": True})

    assert block._props["неттакого"] == "1"
    assert block.inited is True


@pytest.mark.anyio
async def test_set_block_param_class_outside_catalog_writes_with_note(
        monkeypatch):
    """Класс вне каталога («В файл») не отвергается — проверять нечем."""
    block = _FakeBlock("В файл", {"filename": "старое.txt"})
    _install_fake_project(monkeypatch, {"ToFile": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param",
        {"block": "ToFile", "param": "filename", "value": "новое.txt"}))

    assert block._props["filename"] == "новое.txt"
    assert "отсутствует в каталоге" in text


@pytest.mark.anyio
async def test_add_block_rejects_unknown_prop_without_creating_block(
        monkeypatch):
    """Неизвестный параметр в add_block — отказ, и блок на схеме не остаётся."""
    from simintech_mcp import server

    _install_fake_project(monkeypatch, {})

    text = await _error("add_block",
                        {"class_name": "Константа", "props": "y0=5"})

    assert "y0" in text
    assert server._project.get_main_page()._created == [], (
        "отказ до создания блока — иначе блок остался бы на схеме"
    )


@pytest.mark.anyio
async def test_add_block_allow_unknown_props_creates_block(monkeypatch):
    """`allow_unknown_props=True` — блок создаётся с «некаталожным» параметром."""
    from simintech_mcp import server

    _install_fake_project(monkeypatch, {})

    await mcp.call_tool("add_block", {"class_name": "Константа", "props": "y0=5",
                                      "allow_unknown_props": True})

    created = server._project.get_main_page()._created
    assert len(created) == 1
    assert created[0]._props["y0"] == 5


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


# ─── Расчёт (без COM) ─────────────────────────────────────────────

class _FakeSimulation:
    """Расчёт с управляемой последовательностью модельного времени."""

    def __init__(self, times):
        self._times = list(times)
        self._last = 0.0
        self.run_to_calls = []
        self.stepped = 0
        self.started = 0
        self.run_to_result = True

    def start(self):
        self.started += 1
        return self

    def get_time(self):
        if self._times:
            self._last = self._times.pop(0)
        return self._last

    def step(self):
        self.stepped += 1
        return self

    def run(self):
        return self

    def stop(self):
        return self

    def run_to(self, target, timeout=None, stall=None):
        self.run_to_calls.append((target, timeout, stall))
        return self.run_to_result


class _SimProject:
    def __init__(self, sim):
        self._sim = sim

    def simulation(self):
        return self._sim


def _install_fake_simulation(monkeypatch, times):
    from simintech_mcp import server as server_module

    sim = _FakeSimulation(times)
    monkeypatch.setattr(server_module, "_project", _SimProject(sim))
    return sim


@pytest.mark.anyio
async def test_run_reports_failure_when_time_did_not_move(monkeypatch):
    """run честно сообщает, что расчёт не дошёл, а не «завершён»."""
    from simintech_mcp import server as server_module

    sim = _install_fake_simulation(monkeypatch, [0.0, 0.0])
    sim.run_to_result = False

    text = _text(await mcp.call_tool("run", {"to_time": 1.0}))

    assert "не дошёл" in text
    assert "0.000" in text
    assert sim.run_to_calls == [(1.0, server_module.CALC_WAIT_SECONDS,
                                 server_module.CALC_STALL_SECONDS)]


@pytest.mark.anyio
async def test_run_reports_success_with_actual_time(monkeypatch):
    """Успешный расчёт подтверждается фактическим временем."""
    _install_fake_simulation(monkeypatch, [1.0])

    text = _text(await mcp.call_tool("run", {"to_time": 1.0}))

    assert "завершён" in text
    assert "1.000" in text


@pytest.mark.anyio
async def test_run_passes_waits_to_library(monkeypatch):
    """Параметры ожидания уходят в библиотечный run_to, а не игнорируются."""
    sim = _install_fake_simulation(monkeypatch, [1.0])

    await mcp.call_tool("run", {"to_time": 1.0, "wait_timeout": 5.0,
                                "stall_seconds": 0.5})

    assert sim.run_to_calls == [(1.0, 5.0, 0.5)]


@pytest.mark.anyio
async def test_run_without_target_warns_no_confirmation(monkeypatch):
    """Неблокирующий запуск не выдаётся за подтверждённый результат."""
    _install_fake_simulation(monkeypatch, [0.0])

    text = _text(await mcp.call_tool("run", {}))

    assert "неблокирующий" in text


@pytest.mark.anyio
async def test_step_reports_stalled_time(monkeypatch):
    """step — тот же класс, что run: время не сдвинулось, значит расчёт стоит."""
    sim = _install_fake_simulation(monkeypatch, [0.0, 0.0])

    text = _text(await mcp.call_tool("step", {"count": 3}))

    assert "не сдвинулось" in text
    assert sim.stepped == 3


@pytest.mark.anyio
async def test_step_reports_time_delta(monkeypatch):
    """Успешные шаги подтверждаются дельтой времени."""
    _install_fake_simulation(monkeypatch, [0.0, 0.003])

    text = _text(await mcp.call_tool("step", {"count": 3}))

    assert "0.000 → 0.003" in text


@pytest.mark.anyio
async def test_step_rejects_non_positive_count(monkeypatch):
    _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("step", {"count": 0})

    assert "положительным" in text


# ─── Создание проекта и время расчёта (без COM) ───────────────────

class _TemplateProject:
    id = 5

    def __init__(self):
        self.end_time = None
        self.closed = False

    def set_calc_end_time(self, seconds):
        self.end_time = seconds
        return self

    def close(self):
        self.closed = True


def _install_fake_template(monkeypatch):
    from simintech_mcp import server as server_module

    opened = []
    project = _TemplateProject()

    def fake_from_template(client):
        opened.append(project)
        return project

    monkeypatch.setattr(server_module.Project, "from_template",
                        staticmethod(fake_from_template))
    monkeypatch.setattr(server_module, "_ensure_client", lambda: object())
    monkeypatch.setattr(server_module, "_project", None)
    return project, opened


@pytest.mark.anyio
async def test_create_project_uses_template_and_sets_end_time(monkeypatch):
    """create_project идёт через шаблон и применяет end_time."""
    from simintech_mcp import server as server_module

    project, opened = _install_fake_template(monkeypatch)

    text = _text(await mcp.call_tool("create_project", {"end_time": 2.5}))

    assert opened == [project], "проект должен создаваться из шаблона"
    assert project.end_time == 2.5
    assert "2.5 с" in text
    assert server_module._project is project


@pytest.mark.anyio
async def test_create_project_rejects_non_positive_end_time(monkeypatch):
    """Неверное время отвергается ДО открытия шаблона.

    Иначе созданный проект остался бы висеть в mmain.exe: он не попал бы ни в
    `_project`, ни в закрытие.
    """
    _project_obj, opened = _install_fake_template(monkeypatch)

    text = await _error("create_project", {"end_time": 0})

    assert "положительным" in text
    assert opened == [], "шаблон открывать было нельзя"


@pytest.mark.anyio
async def test_set_calc_time_delegates_to_project(monkeypatch):
    """set_calc_time передаёт значение в проект."""
    from simintech_mcp import server as server_module

    project = _TemplateProject()
    monkeypatch.setattr(server_module, "_project", project)

    text = _text(await mcp.call_tool("set_calc_time", {"seconds": 7.0}))

    assert project.end_time == 7.0
    assert "7.0 с" in text


# ─── Формат сохранения проекта ────────────────────────────────────

class _SavableProject:
    """Проект, запоминающий, каким методом его сохранили."""

    def __init__(self, raises: bool = False):
        self.calls = []
        self._raises = raises

    def _record(self, kind: str, path=None) -> None:
        self.calls.append((kind, path))
        if self._raises:
            raise RuntimeError("диск переполнен")

    def show_form(self) -> None:
        self._record("show_form")

    def save_xml(self, path: str) -> None:
        self._record("xml", path)

    def save_binary(self, path: str) -> None:
        self._record("binary", path)


def _install_savable(monkeypatch, raises: bool = False) -> "_SavableProject":
    from simintech_mcp import server as server_module

    project = _SavableProject(raises=raises)
    monkeypatch.setattr(server_module, "_project", project)
    return project


@pytest.mark.anyio
async def test_save_project_defaults_to_xml(monkeypatch):
    """По умолчанию сохраняется XML, и перед записью показывается форма.

    Без показа формы в файл уходит признак «окно скрыто», и GUI открывает
    проект, не показывая окно модели.
    """
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool("save_project", {"path": r"C:\m.xprt"}))

    assert project.calls == [("show_form", None), ("xml", r"C:\m.xprt")]
    assert "XML" in text
    assert "Форма проекта показана" in text


@pytest.mark.anyio
async def test_save_project_binary_flag_writes_prt(monkeypatch):
    """binary=True пишет нативный .prt — его открывает GUI SimInTech."""
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": r"C:\m.prt", "binary": True}))

    assert project.calls == [("show_form", None), ("binary", r"C:\m.prt")]
    assert ".prt" in text


@pytest.mark.anyio
async def test_save_project_can_skip_showing_form(monkeypatch):
    """show_form=False — безоконное сохранение: форму не показываем."""
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": r"C:\m.prt", "binary": True,
                         "show_form": False}))

    assert project.calls == [("binary", r"C:\m.prt")]
    assert "Форму не показывали" in text


@pytest.mark.anyio
async def test_save_project_failure_is_error(monkeypatch):
    """Неудачная запись — отказ, а не ответ «проект сохранён»."""
    _install_savable(monkeypatch, raises=True)

    text = await _error("save_project", {"path": r"C:\m.prt", "binary": True})

    assert "диск переполнен" in text


# ─── Отчёт об отказах закрытия ────────────────────────────────────

def test_replace_project_reports_failed_close(monkeypatch):
    """Неудачное закрытие предыдущего проекта не выдаётся за успех."""
    from simintech_mcp import server as server_module

    monkeypatch.setattr(server_module, "_project",
                        _ClosableProject(raises=True))
    fresh = _ClosableProject()

    note = server_module._replace_project(fresh)

    assert "ВНИМАНИЕ" in note
    assert server_module._project is fresh


@pytest.mark.anyio
async def test_disconnect_reports_failed_close(monkeypatch):
    """disconnect сбрасывает состояние, но сообщает о неудачном закрытии."""
    from simintech_mcp import server as server_module

    class _Client:
        def disconnect(self):
            pass

    monkeypatch.setattr(server_module, "_project",
                        _ClosableProject(raises=True))
    monkeypatch.setattr(server_module, "_client", _Client())

    text = _text(await mcp.call_tool("disconnect", {}))

    assert server_module._project is None
    assert "ВНИМАНИЕ" in text


# ─── Прочие ветки read_output_file и add_block ────────────────────

@pytest.mark.anyio
async def test_read_output_file_empty_is_error(tmp_path, monkeypatch):
    """Пустой файл — отказ: это признак, что расчёт не шёл."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")

    text = await _error("read_output_file", {"path": str(empty)})

    assert "пуст" in text


@pytest.mark.anyio
async def test_read_output_file_limits_returned_lines(tmp_path, monkeypatch):
    """max_lines ограничивает выдачу, но число строк в файле сообщается."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "many.txt"
    path.write_text("\n".join(str(i) for i in range(10)) + "\n", encoding="utf-8")

    text = _text(await mcp.call_tool("read_output_file",
                                     {"path": str(path), "max_lines": 3}))

    assert "строк 10" in text
    assert "показаны первые 3" in text
    assert "\n9" not in text


@pytest.mark.anyio
async def test_add_block_reports_ignored_props(monkeypatch):
    """Части props без '=' не исчезают молча."""
    _install_fake_project(monkeypatch, {})

    text = _text(await mcp.call_tool(
        "add_block", {"class_name": "Константа", "props": "a=2,мусор"}))

    assert "мусор" in text
    assert "пропущены" in text


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


def test_stdout_guard_misses_descriptor_writes(monkeypatch, capfd):
    """Запись прямо в дескриптор 1 гард не перехватывает — это его граница.

    `_StdoutGuard` подменяет `sys.stdout.write`, а нативный код (или библиотека,
    пишущая в fd 1) идёт мимо. Тест фиксирует границу явно, чтобы страховку не
    читали как «протокол защищён от любой записи»: такое попадание в stdout
    порвёт JSON-RPC молча.
    """
    from simintech_mcp.server import isolate_stdout

    _install_fake_streams(monkeypatch)
    isolate_stdout()

    os.write(1, b"mimo-guarda\n")

    assert "mimo-guarda" in capfd.readouterr().out


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


# ─── Ресурсы: каталог блоков ──────────────────────────────────────

async def _resource_text(uri: str) -> str:
    """Прочитать ресурс через MCP — так же, как это делает клиент."""
    result = await mcp.read_resource(uri)
    return result.contents[0].content


@pytest.mark.anyio
async def test_static_resources_are_registered():
    """Каталог блоков и скиллы отдаются как ресурсы, а не только инструменты."""
    resources = await mcp.list_resources()
    uris = {str(item.uri) for item in resources}

    assert {"simintech://status", "simintech://project/blocks",
            "simintech://blocks/catalog", "simintech://skills"} <= uris


@pytest.mark.anyio
async def test_resource_blocks_catalog_lists_classes_and_params():
    """В каталоге видно классы и имена их параметров — их и ждёт COM."""
    text = await _resource_text("simintech://blocks/catalog")

    assert "Каталог блоков" in text
    assert "Константа" in text
    assert "a" in text


@pytest.mark.anyio
async def test_resource_blocks_catalog_marks_computed_params(monkeypatch):
    """Вычисляемые параметры помечены: запись в них ничего не меняет."""
    from simintech_api.catalog import BlockCatalog
    from simintech_mcp import server

    catalog = BlockCatalog(
        classes={"Усилитель": {"a": "1", "formula_visible": "0"}},
        readonly={"Усилитель": ["formula_visible"]},
    )
    monkeypatch.setattr(server, "load_default_catalog", lambda: catalog)

    text = await _resource_text("simintech://blocks/catalog")

    assert "formula_visible" in text
    assert "вычисляемые" in text


@pytest.mark.anyio
async def test_resource_blocks_catalog_reports_empty(monkeypatch):
    """Пустой каталог — это сообщение о причине, а не пустая строка."""
    from simintech_api.catalog import BlockCatalog
    from simintech_mcp import server

    monkeypatch.setattr(server, "load_default_catalog", lambda: BlockCatalog())

    text = await _resource_text("simintech://blocks/catalog")

    assert "Каталог блоков пуст" in text


# ─── Ресурсы: скиллы ──────────────────────────────────────────────

def _write_skill(root, name: str, body: str) -> None:
    """Положить скилл на диск так, как его ждёт сервер."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")


@pytest.mark.anyio
async def test_resource_skills_lists_and_reads_skill(monkeypatch, tmp_path):
    """Скиллы перечисляются, а их текст читается отдельным ресурсом."""
    _write_skill(tmp_path, "simintech-model-building",
                 "---\nname: simintech-model-building\ndescription: сборка\n"
                 "---\n# Сборка модели\n\nПорядок работы.\n")
    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))

    listing = await _resource_text("simintech://skills")

    assert "simintech-model-building" in listing
    assert "Сборка модели" in listing

    body = await _resource_text(
        "simintech://skills/simintech-model-building")

    assert "Порядок работы" in body


@pytest.mark.anyio
async def test_resource_skills_missing_dir_names_variable(
        monkeypatch, tmp_path):
    """Нет каталога скиллов — сказано, какой переменной его задать.

    И он **не** подменяется соседним checkout: иначе опечатка в переменной
    молча читала бы другой каталог, и агент получал бы чужие скиллы.
    """
    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path / "нет-такого"))

    text = await _resource_text("simintech://skills")

    assert "SIMINTECH_SKILLS_DIR" in text
    assert "не найден" in text
    assert "Скиллы (" not in text, "соседний checkout не должен подставляться"


@pytest.mark.anyio
async def test_resource_skills_empty_dir(monkeypatch, tmp_path):
    """Каталог есть, но скиллов в нём нет — отдельное сообщение."""
    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))

    text = await _resource_text("simintech://skills")

    assert "скиллов нет" in text


def test_resource_skill_rejects_path_traversal(monkeypatch, tmp_path):
    """Имя скилла приходит от клиента: «..» в путь не проходит."""
    from simintech_mcp import server

    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))

    assert "ERROR" in server.resource_skill("../../etc/passwd")
    assert "ERROR" in server.resource_skill("SimInTech")   # регистр не тот
    assert "ERROR" in server.resource_skill("")


def test_resource_skill_missing(monkeypatch, tmp_path):
    """Несуществующий скилл — отказ, а не пустой текст."""
    from simintech_mcp import server

    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))

    assert "ERROR" in server.resource_skill("нет-такого")


# ─── Сводка по файлу результата ───────────────────────────────────

@pytest.mark.anyio
async def test_summarize_output_file_stats(monkeypatch, tmp_path):
    """Сводка считает точки, диапазон, min/max, среднее и наклон."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t2\n1\t5\n2\t8\n3\t11\n",
                                      encoding="utf-8")

    text = _tool_text(await mcp.call_tool("summarize_output_file",
                                          {"path": "out.txt"}))

    assert "точек 4" in text
    assert "min 2" in text
    assert "max 11" in text
    assert "среднее 6.5" in text
    assert "средний наклон: 3" in text


@pytest.mark.anyio
async def test_summarize_output_file_selects_column(monkeypatch, tmp_path):
    """Колонка выбирается: 0 — время, 1..n — значения."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t1\t10\n1\t2\t20\n", encoding="utf-8")

    text = _tool_text(await mcp.call_tool(
        "summarize_output_file", {"path": "out.txt", "column": 1}))

    assert "min 1" in text
    assert "max 2" in text


@pytest.mark.anyio
async def test_summarize_output_file_outside_sandbox(monkeypatch, tmp_path):
    """Песочница та же, что у read_output_file."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "вне.txt"
    outside.write_text("0 1\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    text = await _error("summarize_output_file", {"path": str(outside)})

    assert "разрешено только из каталога" in text


@pytest.mark.anyio
async def test_summarize_output_file_reports_no_numbers(monkeypatch, tmp_path):
    """Нечисловой файл — причина, а не «ноль точек»."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("заголовок\nещё строка\n",
                                      encoding="utf-8")

    text = await _error("summarize_output_file", {"path": "out.txt"})

    assert "нет ни одной числовой строки" in text


@pytest.mark.anyio
async def test_summarize_output_file_rejects_bad_column(monkeypatch, tmp_path):
    """Колонки вне строки — отказ с указанием, сколько их в файле."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t1\n", encoding="utf-8")

    text = await _error("summarize_output_file",
                        {"path": "out.txt", "column": 5})

    assert "вне диапазона" in text


# ─── Разбор проекта без COM ───────────────────────────────────────

_XPRT_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<project>
  <object>
    <name>`k_0`</name>
    <class_name>`Константа`</class_name>
    <custom_props>
      <data><name>`a`</name><value>`[2]`</value><mode>`1`</mode></data>
      <data><name>`formula_visible`</name><value>`0`</value><mode>`0`</mode>
      </data>
    </custom_props>
  </object>
  <object>
    <name>`kx_0`</name>
    <class_name>`Усилитель`</class_name>
    <custom_props>
      <data><name>`a`</name><value>`3`</value><mode>`1`</mode></data>
    </custom_props>
  </object>
</project>
"""


@pytest.mark.anyio
async def test_inspect_project_file_without_com(monkeypatch, tmp_path):
    """Сохранённый проект разбирается без COM — работает и на Linux."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "model.xprt").write_text(_XPRT_FIXTURE, encoding="utf-8")

    text = _tool_text(await mcp.call_tool("inspect_project_file",
                                          {"path": "model.xprt"}))

    assert "классов 2, блоков 2" in text
    assert "k_0" in text
    assert "kx_0" in text
    assert "Константа" in text
    assert "formula_visible" in text
    assert "вычисляемые" in text


@pytest.mark.anyio
async def test_inspect_project_file_outside_sandbox(monkeypatch, tmp_path):
    """Песочница та же, что у результатов: чужой файл не читается."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "model.xprt"
    outside.write_text(_XPRT_FIXTURE, encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    text = await _error("inspect_project_file", {"path": str(outside)})

    assert "разрешено только из каталога" in text


@pytest.mark.anyio
async def test_inspect_project_file_missing(monkeypatch, tmp_path):
    """Нет файла — отказ с подсказкой, куда его сохранять."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))

    text = await _error("inspect_project_file", {"path": "нет.xprt"})

    assert "файла нет" in text
    assert "save_project" in text


# ─── Журнал вызовов ───────────────────────────────────────────────

def test_log_event_writes_json_lines(monkeypatch, tmp_path):
    """События журнала — по одной JSON-строке на событие."""
    from simintech_mcp import server

    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(server.LOG_ENV, str(log))

    server.log_event("проверка", ok=True, n=1)

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "проверка"
    assert record["ok"] is True
    assert record["n"] == 1
    assert "ts" in record


def test_log_event_is_off_without_env(monkeypatch, tmp_path):
    """Без переменной журнал не пишется — поведение по умолчанию."""
    from simintech_mcp import server

    monkeypatch.delenv(server.LOG_ENV, raising=False)
    log = tmp_path / "log.jsonl"

    server.log_event("не должно записаться")

    assert not log.exists()


def test_log_event_never_writes_to_stdout(monkeypatch, capsys):
    """Журнал идёт в stderr: в stdout живёт JSON-RPC, его засорять нельзя."""
    from simintech_mcp import server

    monkeypatch.setenv(server.LOG_ENV, "stderr")

    server.log_event("событие", ok=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "событие" in captured.err


def test_log_event_survives_unwritable_path(monkeypatch, tmp_path):
    """Недоступный файл журнала не роняет инструмент."""
    from simintech_mcp import server

    monkeypatch.setenv(server.LOG_ENV, str(tmp_path / "нет-каталога" / "l.log"))

    server.log_event("без падения", ok=True)      # не должно бросить


@pytest.mark.anyio
async def test_tool_call_is_logged(monkeypatch, tmp_path):
    """Вызов инструмента попадает в журнал с длительностью и исходом."""
    from simintech_mcp import server

    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(server.LOG_ENV, str(log))
    _install_fake_project(monkeypatch, {"Gain": _FakeBlock("Усилитель")})

    await mcp.call_tool("set_block_param",
                        {"block": "Gain", "param": "a", "value": "2"})

    records = [json.loads(line)
               for line in log.read_text(encoding="utf-8").splitlines()]
    call = [r for r in records if r.get("tool") == "set_block_param"]
    assert call and call[0]["ok"] is True
    assert "ms" in call[0]
    assert call[0]["args"]["param"] == "a"


# ─── Границы чтения ───────────────────────────────────────────────

def test_read_bounded_reads_no_more_than_limit(tmp_path):
    """Из файла читается не больше предела: проверка размера ДО чтения.

    «Прочитать целиком, потом отказать по размеру» защитой не является —
    память к моменту проверки уже израсходована. Здесь проверяется, что из
    файла действительно берётся ограниченный кусок.
    """
    from simintech_mcp import server

    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 5000)

    data, truncated = server._read_bounded(str(path), 100)

    assert truncated is True
    assert len(data) == 100, "прочитано должно быть ровно 100 байт"


def test_read_bounded_keeps_small_file_intact(tmp_path):
    """Файл меньше предела читается целиком и не помечается обрезанным."""
    from simintech_mcp import server

    path = tmp_path / "small.bin"
    path.write_bytes(b"abc")

    assert server._read_bounded(str(path), 100) == (b"abc", False)


@pytest.mark.anyio
async def test_inspect_project_file_refuses_oversized(monkeypatch, tmp_path):
    """Слишком большой .xprt отвергается, а не читается в память целиком."""
    from simintech_mcp import server

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "MAX_PROJECT_BYTES", 64)
    (tmp_path / "big.xprt").write_text("x" * 500, encoding="utf-8")

    text = await _error("inspect_project_file", {"path": "big.xprt"})

    assert "больше" in text


@pytest.mark.anyio
async def test_summarize_bounds_single_huge_line(monkeypatch, tmp_path):
    """Строка без переводов не поднимается в память целиком.

    Построчное чтение подняло бы её всю: предел по байтам сработал бы уже
    после выделения памяти.
    """
    from simintech_mcp import server

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "MAX_SUMMARY_BYTES", 200)
    (tmp_path / "huge.txt").write_text("1 " * 100000, encoding="utf-8")

    text = _tool_text(await mcp.call_tool("summarize_output_file",
                                          {"path": "huge.txt"}))

    assert "ВНИМАНИЕ" in text, "обрезка должна быть видна в сводке"


@pytest.mark.anyio
async def test_failed_tool_call_is_logged(monkeypatch, tmp_path):
    """Отказ тоже попадает в журнал — иначе причина не видна."""
    from simintech_mcp import server

    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(server.LOG_ENV, str(log))
    _install_fake_project(monkeypatch, {})

    await _error("get_block_params", {"block": "Нет"})

    records = [json.loads(line)
               for line in log.read_text(encoding="utf-8").splitlines()]
    failed = [r for r in records
              if r.get("tool") == "get_block_params" and not r.get("ok")]
    assert failed and "не найден" in failed[0]["error"]
