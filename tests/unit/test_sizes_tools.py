"""Габариты: `set_block_size` — правило высоты порт-блоков и «Значение».

Разложено из `test_blocks_tools.py` (issue #124): файл называется по модулю,
который проверяет (`tools/sizes.py`). Общие фейки — в `_support.py`:
`_SizeBlock`/`_PortBlock`/`_UnreadableNamesPort` нужны и тестам фитов.
"""

from __future__ import annotations

import pytest

from simintech_mcp import session
from simintech_mcp.server import mcp

from _support import (
    _FakePage,
    _FakeProject,
    _PortBlock,
    _SizeBlock,
    _UnreadableNamesPort,
    _error,
    _tool_text,
)


# ─── set_block_size (mcp#24, п.5) ─────────────────────────────────


class _ImmutableSizeBlock(_SizeBlock):
    """Блок, который размер не принимает вовсе (отступление от замера)."""

    def set_graph_prop(self, name, value):
        self.graph_writes.append((name, value))
        return self

    def apply_body(self, body: str) -> None:
        return


class _EvenOnlyBlock(_SizeBlock):
    """Блок, принимающий только чётные значения (трансформация записи)."""

    def set_graph_prop(self, name, value):
        self.graph_writes.append((name, value))
        even = float(value) - float(value) % 2
        self._size[0 if name == "Width" else 1] = even
        return self

    def apply_body(self, body: str) -> None:
        super().apply_body(body)
        self._size = [float(value) - float(value) % 2 for value in self._size]


class _SizeContourBridge:
    """Мост-подделка контура: тело записи размера применяет сам блок.

    Тело инструмента — пара `setpropformula(…, "")` + `setprop(…)`; подделка
    не исполняет язык, а передаёт тело блоку (`apply_body`), и тот применяет
    его по своим правилам — так «среда приняла иначе» и «не приняла вовсе»
    моделируются блоком, а инструмент читает габарит «до» и «после» сам.
    """

    kind = "ok"
    body = ""
    block = None

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body, result_path):
        from simintech_api.core.script_bridge import PageRunResult
        from simintech_api.script_probe import ContourOutcome

        type(self).body = body
        # «Не собралось» и «секция не выполнилась» — тело не исполнялось
        # вовсе: эффекта нет, как у среды.
        if (type(self).block is not None
                and type(self).kind not in ("not-compiled", "section-not-run")):
            type(self).block.apply_body(body)
        return PageRunResult(
            outcome=ContourOutcome(kind=type(self).kind, lines=[]),
            restored_script="// прежний")


class _FakeClient:
    """COM-клиент: контуру достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


def _size_bridge(block=None, kind="ok"):
    """Свежий подкласс моста на тест: настройка не течёт между прогонами.

    Тем же приёмом закрыта утечка классового состояния в
    `test_block_script_tools` (#83): общий класс делал настройку одного теста
    частью другого при двойном прогоне anyio.
    """
    return type("_ConfiguredSizeBridge", (_SizeContourBridge,),
                {"block": block, "kind": kind})


def _install_size_contour(monkeypatch, tmp_path, blocks, bridge):
    """Подменить проект, клиента и мост контурной записи размера."""
    from simintech_mcp.tools import page_script

    project = _FakeProject(blocks)
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_project", project)
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)
    return project


class _UnactivatablePage(_FakePage):
    """Страница, которую сделать текущей не удаётся (деградация сцены)."""

    def activate(self):
        raise RuntimeError("SetCurrentPage отказал")


class _UnactivatableProject(_FakeProject):
    """Проект, чья главная страница не активируется."""

    def __init__(self, blocks):
        super().__init__(blocks)
        self._page = _UnactivatablePage(blocks)


@pytest.mark.anyio
async def test_set_block_size_activates_page_before_contour(monkeypatch,
                                                            tmp_path):
    """Запись по id идёт с активной страницей: скрипт ставится в текущую.

    `SetPageScript` пишет в `GetCurentPage`, и `blk = <id>` ищется на ней:
    без активации запись из GUI, уведённого в субмодель, ушла бы мимо блока
    (находка ревью PR #94).
    """
    block = _SizeBlock()
    project = _install_size_contour(monkeypatch, tmp_path, {"kx_0": block},
                                    _size_bridge(block))

    text = _tool_text(await mcp.call_tool(
        "set_block_size", {"block": "kx_0", "width": 140, "height": 80}))

    assert project.get_main_page().activations >= 1, (
        "контурный прогон без активной страницы: блок по id может не найтись")
    assert "140x80" in text


@pytest.mark.anyio
async def test_set_block_size_refuses_when_page_activation_fails(monkeypatch,
                                                                 tmp_path):
    """Не удалось активировать страницу — отказ, а не запись вслепую."""
    from simintech_mcp.tools import page_script

    block = _SizeBlock()
    bridge = _size_bridge(block)
    project = _UnactivatableProject({"kx_0": block})
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_project", project)
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)

    text = await _error("set_block_size",
                        {"block": "kx_0", "width": 140, "height": 80})

    assert "активной" in text
    assert bridge.body == "", "контур звали, хотя страница не активирована"
    assert block.get_size() == (32.0, 32.0), "проект не изменён"


@pytest.mark.anyio
async def test_set_block_size_applies_and_repaints(monkeypatch, tmp_path):
    """Размер пишется в «Значение» (не в формулу), перечитывается, схема обновляется."""
    block = _SizeBlock()
    bridge = _size_bridge(block)
    project = _install_size_contour(monkeypatch, tmp_path,
                                    {"kx_0": block}, bridge)

    text = _tool_text(await mcp.call_tool(
        "set_block_size", {"block": "kx_0", "width": 140, "height": 80}))

    assert 'setpropformula(blk, "Width", "");' in bridge.body, \
        "формула не снята — размер ляжет в неё, как у SetGraphBlockProp"
    assert 'setprop(blk, "Width", 140);' in bridge.body
    assert 'setpropformula(blk, "Height", "");' in bridge.body
    assert 'setprop(blk, "Height", 80);' in bridge.body
    assert block.get_size() == (140.0, 80.0)
    assert "32x32 → 140x80" in text
    assert project.repaints == 1, "после смены размера схема перерисовывается"


@pytest.mark.anyio
async def test_set_block_size_accepts_odd_values(monkeypatch, tmp_path):
    """Нечётные значения принимаются (замер 01.10.2026)."""
    block = _SizeBlock()
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"kx_0": block}, bridge)

    text = _tool_text(await mcp.call_tool(
        "set_block_size", {"block": "kx_0", "width": 141, "height": 79}))

    assert block.get_size() == (141.0, 79.0)
    assert "32x32 → 141x79" in text


@pytest.mark.anyio
async def test_set_block_size_refuses_when_nothing_changed(monkeypatch, tmp_path):
    """Размер не изменился — отказ: размера, которого нет, — не успех."""
    block = _ImmutableSizeBlock()
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"kx_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "kx_0", "width": 140, "height": 80})

    assert "не изменился" in text


@pytest.mark.anyio
async def test_set_block_size_notes_accepted_difference(monkeypatch, tmp_path):
    """Принятое средой значение, отличное от запрошенного, — примечание."""
    block = _EvenOnlyBlock()
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"kx_0": block}, bridge)

    text = _tool_text(await mcp.call_tool(
        "set_block_size", {"block": "kx_0", "width": 141, "height": 79}))

    assert "среда приняла 140x78" in text


@pytest.mark.anyio
@pytest.mark.parametrize("width,height", [(0, 10), (-5, 10), (10, 0),
                                          (10001, 10), (10, 10001)])
async def test_set_block_size_bounds(monkeypatch, tmp_path, width, height):
    """Пределы проверяются до контура: ни одной записи в блок."""
    block = _SizeBlock()
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"kx_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "kx_0", "width": width, "height": height})

    assert "вне пределов" in text
    assert bridge.body == "", "запись ушла, хотя размер вне пределов"


@pytest.mark.anyio
async def test_set_block_size_missing_block(monkeypatch, tmp_path):
    """Нет блока — отказ с общим текстом «не найден»."""
    bridge = _size_bridge()
    _install_size_contour(monkeypatch, tmp_path, {}, bridge)

    text = await _error("set_block_size",
                        {"block": "нетакого", "width": 10, "height": 10})

    assert "не найден" in text
    assert bridge.body == ""


@pytest.mark.anyio
async def test_set_block_size_port_accepts_rule_height(monkeypatch, tmp_path):
    """Порт из двух сигналов: высота 32 (16 px × 2 строки) принимается.

    Правило владельца 01.10.2026: высота «Порта входа»/«Порта выхода» —
    16 px на строку сигнала; число строк читается из `PortNames`.
    Ширина правилом не ограничена.
    """
    block = _PortBlock(names="a\r\nb\r\n")
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = _tool_text(await mcp.call_tool(
        "set_block_size", {"block": "InputPort_0", "width": 200, "height": 32}))

    assert block.get_size() == (200.0, 32.0)
    assert "64x16 → 200x32" in text


@pytest.mark.anyio
@pytest.mark.parametrize("height", [16, 20, 48])
async def test_set_block_size_port_refuses_other_height(monkeypatch, tmp_path,
                                                        height):
    """Высота не 16×N — отказ до записи: такой записью отображение ломается.

    Среда габарит сама не подгоняет (замер 01.10.2026: порт с двумя именами
    остаётся 64×16), поэтому и 16, и 48 при двух сигналах неверны, и в блок
    не уходит ни одной записи.
    """
    block = _PortBlock(names="a\r\nb\r\n")
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "InputPort_0", "width": 200, "height": height})

    assert "16 px" in text and "строк 2" in text and "32 px" in text
    assert bridge.body == "", "нарушающая правило высота записана в блок"


@pytest.mark.anyio
async def test_set_block_size_single_signal_port_accepts_16(monkeypatch, tmp_path):
    """Однозначный порт: правильная высота — 16, и она принимается."""
    block = _PortBlock(names="in\r\n")
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    _tool_text(await mcp.call_tool(
        "set_block_size", {"block": "InputPort_0", "width": 120, "height": 16}))

    assert block.get_size() == (120.0, 16.0)


@pytest.mark.anyio
async def test_set_block_size_port_refuses_when_names_unreadable(monkeypatch,
                                                                 tmp_path):
    """Список сигналов не читается — высота не задаётся: проверить нечем."""
    block = _UnreadableNamesPort()
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "InputPort_0", "width": 200, "height": 32})

    assert "PortNames" in text
    assert bridge.body == ""


class _BlankClassNamePort(_PortBlock):
    """Порт-блок, у которого имя класса прочиталось пустым."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.class_name = ""


class _RaisingClassNamePort(_PortBlock):
    """Порт-блок, у которого чтение класса падает (отказ COM)."""

    @property
    def class_name(self):
        raise OSError("COM недоступен")

    @class_name.setter
    def class_name(self, value):
        pass


class _HeightSnappingPort(_PortBlock):
    """Порт-блок, «преобразующий» высоту при записи (как `_EvenOnlyBlock`)."""

    def set_graph_prop(self, name, value):
        self.graph_writes.append((name, value))
        if name == "Height":
            self._size[1] = float(value) - 1
        else:
            self._size[0] = float(value)
        return self

    def apply_body(self, body: str) -> None:
        super().apply_body(body)
        self._size[1] = self._size[1] - 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "block", [_BlankClassNamePort(), _RaisingClassNamePort()],
    ids=["пустой класс", "сбой чтения класса"])
async def test_set_block_size_refuses_when_class_unreadable(monkeypatch,
                                                            tmp_path, block):
    """Класс блока не читается — высота не задаётся: правило нечем проверить.

    Fail-open здесь был бы дырой: блок-порт с нечитаемым классом обошёл бы
    правило, и ломающая высота записалась бы «с успехом» (находка ревью).
    """
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "InputPort_0", "width": 200, "height": 32})

    assert "не читается" in text or "пустым" in text
    assert bridge.body == ""


@pytest.mark.anyio
async def test_set_block_size_port_refuses_height_transformed_by_env(
        monkeypatch, tmp_path):
    """Среда «преобразовала» высоту — отказ, а не успех с примечанием.

    Принять её значило бы оставить порт со сломанным отображением строк и
    отчитаться успехом (находка ревью).
    """
    block = _HeightSnappingPort(names="a\r\nb\r\n")
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "InputPort_0", "width": 200, "height": 32})

    assert "испорчено" in text and "32 px" in text


@pytest.mark.anyio
async def test_set_block_size_port_refuses_unsatisfiable_rule(monkeypatch,
                                                              tmp_path):
    """Столько строк, что правило превышает предел размера, — сказано прямо.

    Иначе отказ правила и отказ «вне пределов» выглядели бы по отдельности
    капризами, а вместе — тупиком без объяснения (находка ревью).
    """
    names = "\r\n".join(f"sig{i}" for i in range(700)) + "\r\n"
    block = _PortBlock(names=names)
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "InputPort_0", "width": 200, "height": 5000})

    assert "задать нельзя вовсе" in text
    assert bridge.body == ""


@pytest.mark.anyio
async def test_set_block_size_port_unsat_also_at_bounds(monkeypatch, tmp_path):
    """Тупик назван и на непредельной ветке: высота правила — сама за пределом.

    Запрос ровно правила (11200 при 700 строках) не доходит до отказов
    правила и упирается в предел размера — и тот обязан объяснить, что
    задать нельзя вовсе (находка ревью: предельная проверка стояла выше
    правила и молчала об этом пути).
    """
    names = "\r\n".join(f"sig{i}" for i in range(700)) + "\r\n"
    block = _PortBlock(names=names)
    bridge = _size_bridge(block)
    _install_size_contour(monkeypatch, tmp_path, {"InputPort_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "InputPort_0", "width": 200, "height": 11200})

    assert "нельзя вовсе" in text
    assert bridge.body == ""


@pytest.mark.anyio
async def test_set_block_size_refuses_when_body_did_not_compile(monkeypatch,
                                                                tmp_path):
    """Тело контура не собралось — отказ, проект не изменён."""
    block = _SizeBlock()
    bridge = _size_bridge(block, kind="not-compiled")
    _install_size_contour(monkeypatch, tmp_path, {"kx_0": block}, bridge)

    text = await _error("set_block_size",
                        {"block": "kx_0", "width": 140, "height": 80})

    assert "не собралось" in text
    assert block.get_size() == (32.0, 32.0), "размер изменился при отказе"
