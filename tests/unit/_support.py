"""Общие фейки, помощники и эталонные данные тестов."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.server import mcp

from simintech_mcp import session
from simintech_mcp.tools import model_text
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


class _OwnedClientStub:
    """Клиент, моделирующий библиотечный `COMClient` (v0.11.0).

    Несёт то, чем пользуется сервер: владение (`ownership`), PID сессии
    (`session_pid`), пробу живучести (`get_process_id`), отпускание ссылки
    (`disconnect`) и управляемое завершение (`shutdown`). Подделка обязана
    моделировать весь этот набор: гейт владения читает `ownership`, а
    инструмент `disconnect` — `session_pid` и `shutdown`.
    """

    connected = True

    def __init__(self, ownership=None, pid=4242, shutdown_result=True):
        from simintech_api import SessionOwnership
        self.ownership = ownership or SessionOwnership.OWNED
        self.session_pid = pid
        #: Исход снятия, который вернёт `shutdown` (0.14.0: bool — процесс
        #: исчез / остался жив). Подделка обязана отдавать его: инструмент
        #: `disconnect` называет процесс «завершённым» именно по нему.
        self.shutdown_result = shutdown_result
        self.probes = 0
        self.disconnected = False
        self.shutdown_called = False

    def connect(self):
        return self

    def get_process_id(self):
        self.probes += 1
        return self.session_pid

    def disconnect(self):
        self.disconnected = True

    def shutdown(self, kill_pids=None):
        self.shutdown_called = True
        self.connected = False
        return self.shutdown_result


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

    def __init__(self, name, block_id, class_name=""):
        self._name = name
        self._id = block_id
        #: Класс: по нему layout_place смыкает стопки порт-блоков.
        self.class_name = class_name
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

    def get_points(self):
        """Строка `Points` — как у среды (замер 02.10.2026).

        Первая точка — **центр** блока, вторая — выходной порт
        (центр + (16, 0)), дальше точки полилинии. Проверка наложений
        строит габарит из центра и размера — min/max полилинии габаритом
        не является (у «Константы» 32×16 размах 16×32).
        """
        cx, cy = self.center if self.center is not None else (0.0, 0.0)
        _w, h = self.SIZE
        return (f"[({cx:g} , {cy:g}), ({cx + 16:g} , {cy:g}), "
                f"({cx:g} , {cy - h / 2:g}), ({cx:g} , {cy + 24:g})]")

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

    def __init__(self, name, block_id, events=None, wire_id=None):
        self._name = name
        self._id = block_id
        self.center = None
        self.wires = []
        self.events = events
        #: Что вернёт `connect` в поле `id` линии. `None` — как у настоящего
        #: успешного `CreateWire`: id ненулевой и растёт. `0` — отказ среды
        #: создать линию (ноль у соседнего `create_block` значит «не создано»).
        self._wire_id = wire_id

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

    def get_points(self):
        """Строка `Points` — как у среды: первая точка — центр блока."""
        cx, cy = self.center if self.center is not None else (0.0, 0.0)
        _w, h = self.get_size()
        return (f"[({cx:g} , {cy:g}), ({cx + 16:g} , {cy:g}), "
                f"({cx:g} , {cy - h / 2:g}), ({cx:g} , {cy + 24:g})]")

    def set_center(self, cx, cy):
        self.center = (cx, cy)
        return self

    def connect(self, other, out_index=0, in_index=0):
        wire_id = len(self.wires) + 1 if self._wire_id is None else self._wire_id
        wire = _FakeWire(wire_id, events=self.events)
        self.wires.append((wire, other, out_index, in_index))
        return wire


class _WireProject:
    """Проект с одной страницей — минимум для connect/layout_place.

    `id` — не украшение: по нему ответы правок называют проект («Изменения
    внесены в: … (id=7)», issue #18), и подделка обязана моделировать то,
    что код читает.
    """

    def __init__(self, blocks, events=None, project_id=7):
        self.page = _FakePage(blocks)
        self.closed = False
        self._events = events
        self.repaints = 0
        self.id = project_id

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
    session.WIRES.clear()
    events = []
    for block in blocks.values():
        block.events = events
    monkeypatch.setattr(session, "_project", _WireProject(blocks, events))
    monkeypatch.setattr(model_text, "page_export_text", _no_page_export)
    return events


class _FakeBlock:
    """Подделка блока: свойства хранятся в словаре."""

    #: Имя, которое настоящий COM отдаёт всегда: `Name` — общее свойство
    #: каталога, и оно читается даже у класса вне каталога.
    AUTO_NAME = "k_0"

    def __init__(self, class_name, props=None):
        self._class_name = class_name
        self._props = {"Name": self.AUTO_NAME, **dict(props or {})}
        self.inited = False

    @property
    def class_name(self):
        return self._class_name

    def get_property(self, name):
        """Как библиотека: свойство строкой, отсутствующего — исключение."""
        return self._props[name]

    def get_properties(self, catalog=None):
        """Как библиотека: имена из каталога, нечитаемые пропускаются.

        `props_for` для класса вне каталога отдаёт ОБЩИЕ свойства (там `Name`),
        поэтому такой блок и у настоящего COM отвечает непустым словарём.
        Прежняя подделка возвращала пустой словарь, и тест «класс вне каталога»
        был зелён по ложной причине: он проверял поведение подделки, а не
        инструмента.
        """
        from simintech_api.catalog import load_default_catalog
        source = catalog or load_default_catalog()
        result = {}
        for prop in source.props_for(self._class_name):
            try:
                result[prop] = self.get_property(prop)
            except Exception:
                continue
        return result

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
        #: Идентификатор страницы (у реальной — COM-id): нужен обходу
        #: субмоделей, который защищается от повторного входа.
        self.id = id(self)
        #: Сколько раз страницу делали текущей (`SetCurrentPage`): по счётчику
        #: проверяется, что обход субмоделей возвращает активной главную.
        self.activations = 0

    def activate(self):
        """Сделать страницу текущей — подделка считает вызовы."""
        self.activations += 1

    def find_block(self, name):
        return self._blocks.get(name)

    def get_blocks(self):
        return list(self._blocks.values())

    def get_wires(self):
        """Линии страницы — всё, что блоки запомнили в `connect`.

        Как и в библиотеке (`Page.get_wires`), перечисляются все линии
        страницы, а не только созданные этой сессией.
        """
        return [wire for block in self._blocks.values()
                for wire, _other, _out, _in in getattr(block, "wires", [])]

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
        # id читают ответы правок («Изменения внесены в: … (id=7)», #18).
        self.id = 7

    def get_main_page(self):
        return self._page

    def repaint(self):
        """Перерисовка редактора: layout_place зовёт её перед трассировкой."""
        self.repaints += 1
        return self


def _no_page_export():
    """В юнитах контура нет: выгрузка графа недоступна, как на пустом мосте."""
    raise ToolError("выгрузка текста модели в тестах недоступна")


def _install_fake_project(monkeypatch, blocks):
    """Подменить открытый проект на подделку с заданными блоками."""
    monkeypatch.setattr(session, "_project", _FakeProject(blocks))
    monkeypatch.setattr(model_text, "page_export_text", _no_page_export)


class _FakeProjectWithCreate:
    def __init__(self):
        self.page = _FakePage({})
        self.id = 7  # ответы правок называют проект (issue #18)

    def get_main_page(self):
        return self.page


class _ClosableProject:
    def __init__(self, raises=False, project_id=7):
        self.closed = False
        self._raises = raises
        self.id = project_id

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
        self.id = 7  # ответы правок называют проект (issue #18)

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
    monkeypatch.setattr(session, "ensure_client", lambda: object())
    monkeypatch.setattr(session, "_project", None)
    return project, opened


class _SavableProject:
    """Проект, запоминающий, каким методом его сохранили.

    При `writes=True` запись **действительная** — файл создаётся: инструмент
    проверяет факт записи по диску (залипшая сессия сообщает об успехе без
    файла, simintech-code#21), и подделка обязана моделировать переход, а не
    удобный ответ. `writes=False` моделирует ту самую залипшую сессию.
    """

    def __init__(self, raises: bool = False, writes: bool = True):
        self.calls = []
        self._raises = raises
        self._writes = writes

    def _record(self, kind: str, path=None) -> None:
        self.calls.append((kind, path))
        if self._raises:
            raise RuntimeError("диск переполнен")
        if self._writes and kind in ("xml", "binary"):
            Path(path).write_bytes(b"<stub/>")

    def show_form(self) -> None:
        self._record("show_form")

    def save_xml(self, path: str) -> None:
        self._record("xml", path)

    def save_binary(self, path: str) -> None:
        self._record("binary", path)


def _install_savable(monkeypatch, raises: bool = False,
                     writes: bool = True) -> "_SavableProject":

    project = _SavableProject(raises=raises, writes=writes)
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


# ─── Габаритные фейки и контурная установка (общие: sizes/fits/wires) ────────


class _SizeBlock:
    """Блок с размером: два пути записи — оба в «Значение».

    `apply_body` моделирует переход по тексту тела контура (`setprop(…)`) —
    путь `set_block_size`; `set_size_value` — запись «Значения» для фитов
    (`fit_port_blocks`). Оба пути применяются к одному состоянию — как у
    среды, где габарит в итоге один; `graph_writes` («Формула») должен
    оставаться пустым, и это проверяется тестами обоих инструментов.

    Живёт в `_support.py`, потому что нужен и тестам размеров, и тестам
    фитов (issue #124): `_SubmodelBlock` фитов наследует этот фейк.
    """

    def __init__(self, name="kx_0", size=(32.0, 32.0)):
        self._name = name
        self._size = list(size)
        self.graph_writes = []
        self.value_writes = []
        self.class_name = "Усилитель"
        self.id = 3

    def get_name(self):
        return self._name

    def get_size(self):
        return tuple(self._size)

    def set_graph_prop(self, name, value):
        self.graph_writes.append((name, value))
        self._size[0 if name == "Width" else 1] = float(value)
        return self

    def set_size_value(self, name, value):
        """Языковая запись в «Значение» (не в «Формулу»)."""
        self.value_writes.append((name, value))
        self._size[0 if name == "Width" else 1] = float(value)
        return self

    def apply_body(self, body: str) -> None:
        """Применить `setprop(blk, "Width", N)`/`Height` из тела контура."""
        width = re.search(r'setprop\(blk, "Width", ([0-9.]+)\)', body)
        height = re.search(r'setprop\(blk, "Height", ([0-9.]+)\)', body)
        assert width and height, f"тело без записи Width/Height: {body!r}"
        self._size = [float(width.group(1)), float(height.group(1))]


class _PortBlock(_SizeBlock):
    """Порт-блок: класс «Порт входа», список сигналов читается из PortNames.

    Подделка моделирует замер 01.10.2026: имена приходят строкой с `\\r\\n`
    (`'in\\r\\n'` у однозначного порта, `'a1\\r\\na2\\r\\n'` у двухзначного).
    """

    def __init__(self, names="in\r\n", name="InputPort_0", size=(64.0, 16.0),
                 class_name="Порт входа"):
        super().__init__(name=name, size=size)
        self.class_name = class_name
        self._port_names = names

    def get_property(self, prop):
        if prop == "PortNames":
            return self._port_names
        raise AssertionError(
            f"подделка читает только PortNames, а спрошено {prop!r}")


class _UnreadableNamesPort(_PortBlock):
    """Порт-блок, у которого список сигналов не читается (отказ COM)."""

    def get_property(self, prop):
        raise OSError("COM недоступен")


class _FakeProcessClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


def _install_contour(monkeypatch, tmp_path, project, bridge) -> None:
    """Подменить каталог результатов, клиента, проект и мост контура.

    Прежде звалась `_install_fit_contour` и жила в тестах фитов, но годится
    любому контурному инструменту (issue #124): ставит мост
    `page_script.ScriptBridge` — общую точку входа контурного ядра.
    """
    from simintech_mcp.tools import page_script

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeProcessClient())
    monkeypatch.setattr(session, "_project", project)
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)
