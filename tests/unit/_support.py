"""Общие фейки, помощники и эталонные данные тестов."""

from __future__ import annotations

import io
import sys

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.server import mcp

from simintech_mcp import session
from simintech_mcp.tools import project as project_tools


def _text(result) -> str:
    """Извлечь текст из результата `mcp.call_tool`.

    Разные версии FastMCP отдают либо кортеж контента, либо объект
    `CallToolResult` с полем `.content`, поэтому обрабатываются обе формы, а
    отсутствие `.text` не роняет тест.
    """
    if isinstance(result, (list, tuple)) and result:
        return getattr(result[0], "text", str(result[0]))
    content = getattr(result, "content", None)
    if content:
        return getattr(content[0], "text", str(content[0]))
    return str(result)


#: Синоним: исторически в тестах жили две копии одного помощника.
_tool_text = _text


async def _error(tool: str, arguments: dict) -> str:
    """Вызвать инструмент, ожидая отказ; вернуть текст отказа.

    Отказ доставляется исключением `ToolError` — именно по нему MCP выставляет
    `isError` в ответе. Раньше инструменты возвращали строку «ERROR: …», и
    клиент, доверяющий флагу, видел успех.
    """
    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool(tool, arguments)
    return str(excinfo.value)


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


_XPRT_EMPTY_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<Header>
<project><objects />
</project>
</Header>
"""


_XPRT_GRAPHICS_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<Header>
<project>
  <object>
    <name>`Line_0`</name>
    <class_name>`Line`</class_name>
  </object>
</project>
</Header>
"""


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
    session._WIRES.clear()
    events = []
    for block in blocks.values():
        block.events = events
    monkeypatch.setattr(session, "_project", _WireProject(blocks, events))
    return events


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
    monkeypatch.setattr(session, "_project", _FakeProject(blocks))


class _FakeProjectWithCreate:
    def __init__(self):
        self.page = _FakePage({})

    def get_main_page(self):
        return self.page


class _ClosableProject:
    def __init__(self, raises=False):
        self.closed = False
        self._raises = raises

    def close(self):
        if self._raises:
            raise RuntimeError("проект уже закрыт средой")
        self.closed = True
        return self


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

    sim = _FakeSimulation(times)
    monkeypatch.setattr(session, "_project", _SimProject(sim))
    return sim


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

    opened = []
    project = _TemplateProject()

    def fake_from_template(client):
        opened.append(project)
        return project

    monkeypatch.setattr(project_tools.Project, "from_template",
                        staticmethod(fake_from_template))
    monkeypatch.setattr(session, "_ensure_client", lambda: object())
    monkeypatch.setattr(session, "_project", None)
    return project, opened


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

    project = _SavableProject(raises=raises)
    monkeypatch.setattr(session, "_project", project)
    return project


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


async def _resource_text(uri: str) -> str:
    """Прочитать ресурс через MCP — так же, как это делает клиент."""
    result = await mcp.read_resource(uri)
    return result.contents[0].content


def _write_skill(root, name: str, body: str) -> None:
    """Положить скилл на диск так, как его ждёт сервер."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
