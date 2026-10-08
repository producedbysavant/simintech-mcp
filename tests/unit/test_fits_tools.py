"""Подгонки габаритов: `fit_port_blocks`/`fit_value_labels` — план, бюджет, курсор.

Разложено из `test_blocks_tools.py` (issue #124): файл называется по модулю,
который проверяет (`tools/fits.py`). Общие фейки — в `_support.py`.
"""

from __future__ import annotations

import re

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
    _install_contour,
    _tool_text,
)


# ─── fit_port_blocks: ширина порт-блоков по подписям (mcp#19) ──────


class _SubmodelBlock(_SizeBlock):
    """Блок-субмодель: страница берётся у проекта по id блока."""

    def __init__(self, name="sub_1", block_id=5):
        super().__init__(name=name)
        self.class_name = "Субмодель"
        self.id = block_id


class _SubmodelProject(_FakeProject):
    """Проект с субмоделью: `submodel_page(id)` отдаёт её страницу."""

    def __init__(self, blocks, subs):
        super().__init__(blocks)
        self._subs = subs

    def submodel_page(self, block_id):
        return self._subs[block_id]


class _FitClient:
    """COM-клиент: контуру достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _FitBridge:
    """Мост-подделка: применяет `setprop` из тела контура к блокам по id.

    Тело контура фитов — пары «`setpropformula(blk, "P", "")` + `setprop(blk,
    "P", V)`» по строкам `blk = <id>;`. Подделка моделирует переход: правит
    «Значение» подделки (`set_size_value`), а `graph_writes` («Формула») не
    трогает — этим и проверяется смена пути. Конфигурация — через фабрику
    подкласса (`_fit_bridge`): общий класс протекал бы между сериями anyio
    (урок #83).
    """

    kind = "ok"
    kinds: list | None = None    # исход по вызову: [1-й, 2-й, дальше — последний]
    registry: dict = {}          # id блока -> блок (на тест)
    body = ""
    calls = 0

    def __init__(self, client, project_id):
        self.project_id = project_id

    def run_page_script(self, body, result_path):
        from simintech_api.core.script_bridge import PageRunResult
        from simintech_api.script_probe import ContourOutcome

        type(self).body = body
        type(self).calls += 1
        kind = type(self).kind
        if type(self).kinds:
            index = min(type(self).calls - 1, len(type(self).kinds) - 1)
            kind = type(self).kinds[index]
        if kind == "ok":
            pair = re.compile(
                r'blk = (\d+);\nsetpropformula\(blk, "(\w+)", ""\);\n'
                r'setprop\(blk, "(\w+)", ([-\d.eE]+)\);')
            for match in pair.finditer(body):
                block = type(self).registry[int(match.group(1))]
                block.set_size_value(match.group(2), match.group(4))
        return PageRunResult(
            outcome=ContourOutcome(kind=kind, lines=[]),
            restored_script="")


def _fit_bridge(registry, kind="ok", kinds=None):
    """Мост с конфигурацией на один тест: класс не мутируется (урок #83)."""
    return type("_ConfiguredFitBridge", (_FitBridge,), {
        "registry": registry, "kind": kind, "kinds": kinds,
        "calls": 0, "body": ""})


@pytest.mark.anyio
async def test_fit_port_blocks_widens_long_labels(monkeypatch, tmp_path):
    """Длинная подпись: ширина растёт записью в «Значение», не в «Формулу»."""
    long_name = "CoolTT_C_CoolSt_WorkSt"
    block = _PortBlock(names=long_name + "\r\n")
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert block.value_writes == [("Width", str(len(long_name) * 8)),
                                  ("Height", "16")]
    assert block.graph_writes == [], "правка ушла в «Формулу» вместо «Значения»"
    assert block.get_size()[0] == len(long_name) * 8.0
    assert bridge.calls == 1
    assert "Габариты подогнаны: 1" in text
    assert "высота 16 — приведена к «Значению»" in text
    assert long_name in text


@pytest.mark.anyio
async def test_fit_port_blocks_widens_memory_blocks(monkeypatch, tmp_path):
    """«В память»/«Из памяти»: ширина по подписям, высота — в «Значение».

    Замер 07.10.2026: импорт ставит блокам памяти 64×16 при любой длине
    имени, и длинная надпись вылезает за рамку — прежние фиты эти классы не
    трогали (в боевой модели таких блоков десятки). Правило высоты у них
    не измерено, поэтому высота только приводится к «Значению», а не
    подгоняется — в отличие от порт-блоков.
    """
    long_name = "Long_Memory_Value_Name_Alpha"      # 28 символов → 224
    to_mem = _PortBlock(names=long_name + "\r\n", name="ToMem_0",
                        class_name="В память")
    from_mem = _PortBlock(names=long_name + "\r\n", name="FromMem_0",
                          class_name="Из памяти")
    from_mem.id = 5                                 # id поделок должны различаться
    project = _FakeProject({"ToMem_0": to_mem, "FromMem_0": from_mem})
    bridge = _fit_bridge({to_mem.id: to_mem, from_mem.id: from_mem})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    want = str(len(long_name) * 8)
    for block in (to_mem, from_mem):
        assert block.value_writes == [("Width", want), ("Height", "16")], \
            "блок памяти не расширен или высота ушла из «Значения»"
        assert block.graph_writes == []
    assert "Габариты подогнаны: 2" in text
    assert "ToMem_0" in text and "FromMem_0" in text


class _UnacceptingPort(_PortBlock):
    """Порт-блок, не принимающий запись габарита: «среда не применила»."""

    def set_size_value(self, name, value):
        self.value_writes.append((name, value))
        return self                        # размер не меняется — запись мимо


@pytest.mark.anyio
async def test_fit_port_blocks_reports_rejected_write(monkeypatch, tmp_path):
    """Среда не приняла запись — заголовок не говорит «подогнаны» (ревью #122).

    Блок, чья рабочая ось отвергнута, не считается изменённым и не получает
    строку «приведена к „Значению"»: раньше touch-ветка добавляла его в
    изменённые, и ответ «Габариты подогнаны: 1» противоречил собственной
    строке «среда не приняла запись».
    """
    block = _UnacceptingPort(names="Very_Long_Signal_Name\r\n")
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert "среда не приняла запись" in text
    assert "Габариты не изменились" in text
    assert "Габариты подогнаны" not in text, "отвергнутая запись выдана успехом"
    assert "приведена к «Значению»" not in text, \
        "приведение утверждается при отвергнутой рабочей оси"


@pytest.mark.anyio
async def test_fit_port_blocks_keeps_fitting_labels(monkeypatch, tmp_path):
    """Подпись в рамке — блок не трогается, контур не зовётся; главная активна."""
    block = _PortBlock(names="in\r\n")
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert block.graph_writes == [] and block.value_writes == []
    assert bridge.calls == 0, "контур звали, хотя менять нечего"
    assert "менять нечего" in text
    assert project.get_main_page().activations >= 1, (
        "обход оставил активной субмодель, а не главную")


@pytest.mark.anyio
async def test_fit_port_blocks_walks_submodels(monkeypatch, tmp_path):
    """Порт-блок ВНУТРИ субмодели расширяется этой же страницей."""
    sub_block = _SubmodelBlock(block_id=5)
    inner_name = "In_WorkSt_Channel_1"
    inner = _PortBlock(name="in_1", names=inner_name + "\r\n")
    sub_page = _FakePage({"in_1": inner})
    project = _SubmodelProject({"sub_1": sub_block}, {5: sub_page})
    bridge = _fit_bridge({inner.id: inner})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert inner.value_writes == [("Width", str(len(inner_name) * 8)),
                                  ("Height", "16")]
    assert sub_page.activations >= 1, (
        "страница правки не активирована перед контурным прогоном")
    assert "субмодель 'sub_1'" in text
    assert project.get_main_page().activations >= 1, (
        "после обхода субмоделей главная не возвращена активной")


@pytest.mark.anyio
async def test_fit_port_blocks_names_unreadable_names(monkeypatch, tmp_path):
    """Нечитаемый PortNames — примечанием, а не молчанием и не отказом."""
    block = _UnreadableNamesPort()
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert "PortNames не читается" in text
    assert block.graph_writes == [] and block.value_writes == []
    assert bridge.calls == 0


class _PortedSubmodelBlock(_SizeBlock):
    """Блок-субмодель с читаемым числом внешних ВХОДОВ.

    Правило высоты — по входным контактам (владелец 07.10.2026): выход
    уходит на правую сторону рамки и строки не занимает. У боевых субмоделей
    на входов-минус-один и разошлась прежняя формула «× все порты».
    """

    def __init__(self, name="sub_1", block_id=5, ports=6, in_ports=None,
                 total_ports=None, size=(48.0, 32.0)):
        super().__init__(name=name, size=size)
        self.class_name = "Субмодель"
        self.id = block_id
        self._in_ports = in_ports if in_ports is not None else ports
        self._total = total_ports

    def get_in_port_count(self):
        return self._in_ports

    def get_port_count(self):
        # Всего портов — для контроля расхождения формул (входы + выход).
        return (self._total if self._total is not None
                else self._in_ports + 1)


@pytest.mark.anyio
async def test_fit_port_blocks_fits_submodel_height(monkeypatch, tmp_path):
    """Высота субмодели — 16 px на входной контакт (6 входов → 96)."""
    block = _PortedSubmodelBlock(ports=6)
    project = _FakeProject({"sub_1": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert block.value_writes == [("Height", "96"), ("Width", "48")]
    assert block.graph_writes == []
    assert "высота 32 → 96 (6 вход(ов) × 16)" in text
    assert "ширина 48 — приведена к «Значению»" in text


@pytest.mark.anyio
async def test_fit_submodel_height_counts_in_ports_only(monkeypatch, tmp_path):
    """Правило владельца: (nport − 1) × 16 при единственном выходе.

    Блок с 7 портами (6 входов + выход) обязан получить 96, не 112 —
    именно на +16 у всех субмоделей боевой модели владелец откатывал вручную.
    """
    block = _PortedSubmodelBlock(ports=6, in_ports=6, total_ports=7,
                                 size=(120.0, 112.0))
    project = _FakeProject({"sub_1": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert ("Height", "96") in block.value_writes
    assert "высота 112 → 96 (6 вход(ов) × 16)" in text


class _MultiplexerBlock(_PortedSubmodelBlock):
    """Мультиплексор: входов ровно `nport`, выход один (замер 08.10.2026)."""

    def __init__(self, name="mx_0", block_id=7, ports=8, size=(32.0, 32.0)):
        super().__init__(name=name, block_id=block_id, ports=ports, size=size)
        self.class_name = "Мультиплексор"


@pytest.mark.anyio
async def test_fit_port_blocks_fits_multiplexer_height(monkeypatch, tmp_path):
    """Мультиплексор: высота = входы × 16 — шаг пинов 16 вместо 4 (nport=8).

    Импорт ставит 32×32 при любом `nport`, и входы ложатся с шагом
    `32 / nport` (замер 08.10.2026). Ширина не меняется, но приводится к
    «Значению» — как у субмодели.
    """
    block = _MultiplexerBlock(ports=8, size=(32.0, 32.0))
    project = _FakeProject({"mx_0": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert block.value_writes == [("Height", "128"), ("Width", "32")]
    assert block.graph_writes == []
    assert "высота 32 → 128 (8 вход(ов) × 16)" in text


@pytest.mark.anyio
async def test_fit_multiplexer_already_fitted_is_quiet(monkeypatch, tmp_path):
    """Подогнанный мультиплексор (128 при 8 входах) не трогается."""
    block = _MultiplexerBlock(ports=8, size=(32.0, 128.0))
    project = _FakeProject({"mx_0": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert block.value_writes == []
    assert bridge.calls == 0


class _PinPort:
    """Порт-пин: координаты считаются от центра и ширины блока (пин справа)."""

    def __init__(self, block):
        self._block = block

    def get_coords(self):
        center = self._block.center or (0.0, 0.0)
        width = self._block.get_size()[0]
        return (center[0] + width / 2.0, center[1])


class _PinBlock(_PortBlock):
    """Порт-блок с пином и центром: ширина меняется «влево от пина»."""

    def __init__(self, names="in\r\n", name="InputPort_0", size=(64.0, 16.0),
                 center=(0.0, 0.0)):
        super().__init__(names=names, name=name, size=size)
        self.center = center
        self.centers = []

    def get_in_port(self, index=0):
        return _PinPort(self)

    def get_points(self):
        cx, cy = self.center
        return f"[({cx:g} , {cy:g})]"

    def set_center(self, cx, cy):
        self.centers.append((cx, cy))
        self.center = (cx, cy)
        return self


@pytest.mark.anyio
async def test_fit_port_blocks_restores_pin_x(monkeypatch, tmp_path):
    """Пин не съезжает: центр компенсирует смену ширины.

    Владелец держит пин на фиксированном X и меняет ширину «влево от пина»
    (замечание 07.10.2026: прежняя подгонка от центра изламывала связи).
    """
    long_name = "CoolTT_C_CoolSt_WorkSt"
    block = _PinBlock(names=long_name + "\r\n", size=(64.0, 16.0),
                      center=(0.0, 0.0))
    pin_x_before = block.get_in_port(0).get_coords()[0]
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({block.id: block})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    new_w = float(len(long_name) * 8)
    assert block.get_size()[0] == new_w
    assert block.centers == [(-new_w / 2.0 + 64.0 / 2.0, 0.0)], \
        "центр не компенсировал сдвиг пина"
    assert block.get_in_port(0).get_coords()[0] == pin_x_before, \
        "пин съехал: линии изломаются"
    assert "пин возвращён на место" in text


@pytest.mark.anyio
async def test_fit_port_blocks_applies_plan_in_one_contour_run(monkeypatch,
                                                               tmp_path):
    """Правки страницы — одним контурным прогоном, а не прогоном на блок.

    Контур перезапускает расчёт: у страницы с десятками порт-блоков прогон
    на каждый стоил бы его десятки раз. План собирается целиком и
    применяется один раз.
    """
    port = _PortBlock(name="InputPort_0", names="Very_Long_Signal_Name\r\n")
    sub = _PortedSubmodelBlock(name="sub_1", block_id=5, ports=6)
    project = _FakeProject({"InputPort_0": port, "sub_1": sub})
    bridge = _fit_bridge({port.id: port, 5: sub})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert bridge.calls == 1, (
        "прогон на блок вместо одного прогона на страницу")
    assert port.value_writes == [("Width", "168"), ("Height", "16")]
    assert sub.value_writes == [("Height", "96"), ("Width", "48")]
    assert "Габариты подогнаны: 2" in text


@pytest.mark.anyio
async def test_fit_port_blocks_refuses_on_not_compiled(monkeypatch, tmp_path):
    """Несобравшееся тело — отказ, а не «подогнано»: проект не изменён."""
    long_name = "CoolTT_C_CoolSt_WorkSt"
    block = _PortBlock(names=long_name + "\r\n")
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({block.id: block}, kind="not-compiled")
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("fit_port_blocks", {})

    assert "не собралось" in text
    assert block.graph_writes == [] and block.value_writes == []


class _JournalPage(_FakePage):
    """Страница, отмечающая активации в общем журнале теста."""

    def __init__(self, blocks, journal, label):
        super().__init__(blocks)
        self._journal = journal
        self._label = label

    def activate(self):
        self._journal.append(self._label)
        return super().activate()


class _JournalProject(_FakeProject):
    """Проект с журнальной главной страницей и страницами субмоделей."""

    def __init__(self, main_page, sub_pages):
        super().__init__({})
        self._page = main_page
        self._subs = sub_pages

    def submodel_page(self, block_id):
        return self._subs[block_id]


@pytest.mark.anyio
async def test_fit_port_blocks_returns_main_active_after_midwalk_failure(
        monkeypatch, tmp_path):
    """Отказ на странице субмодели не оставляет её активной — главная возвращается.

    Находка ревью PR #102: `main.activate()` стоял после цикла обхода, а
    отказ `_apply_size_plan` вылетал раньше — субмодель осталась бы текущей,
    и следующая контурная операция (выгрузка, снимок) сняла бы её текст.
    """
    journal: list = []
    port = _PortBlock(name="InputPort_0", names="Very_Long_Signal_Name\r\n")
    sub_port = _PortBlock(name="in_1", names="Inner_Long_Name_Here\r\n")
    sub_port.id = 4                       # id не должен совпадать с блоком главной
    sub_page = _JournalPage({"in_1": sub_port}, journal, "субмодель")
    main_page = _JournalPage({"InputPort_0": port}, journal, "главная")
    main_page._blocks["sub_1"] = _SubmodelBlock(block_id=5)
    project = _JournalProject(main_page, {5: sub_page})
    bridge = _fit_bridge({port.id: port, sub_port.id: sub_port},
                         kinds=["ok", "not-compiled"])
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("fit_port_blocks", {})

    assert "не собралось" in text
    assert journal[-1] == "главная", (
        "отказ на субмодели оставил активной её, а не главную")


class _UnactivatableFitPage(_FakePage):
    """Страница, которую сделать текущей не удаётся (деградация сцены)."""

    def activate(self):
        raise RuntimeError("SetCurrentPage отказал")


@pytest.mark.anyio
async def test_fit_port_blocks_refuses_when_page_activation_fails(monkeypatch,
                                                                  tmp_path):
    """Не удалось активировать страницу правки — отказ, а не прогон вслепую.

    Находка ревью PR #102: провал активации проглатывался, прогон шёл по id,
    и промах записи выглядел как «среда не приняла запись» — диагноз уходил
    в сторону среды.
    """
    port = _PortBlock(name="InputPort_0", names="Very_Long_Signal_Name\r\n")
    project = _FakeProject({})
    project._page = _UnactivatableFitPage({"InputPort_0": port})
    bridge = _fit_bridge({port.id: port})
    _install_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("fit_port_blocks", {})

    assert "активной" in text
    assert bridge.calls == 0, "контур звали без активной страницы"
    assert port.value_writes == [] and port.graph_writes == []


@pytest.mark.anyio
async def test_fit_port_blocks_defers_pages_over_budget(monkeypatch, tmp_path):
    """Лимит COM-вызова: обход порциями, отложенные названы (issue #117).

    Каждая страница — контурный прогон (~20 с), и на многопстраничной модели
    одна порция перекрывала `COM_CALL_TIMEOUT`: клиент получал «перезапустите
    mmain.exe» (неверно — COM не завис), а правки доигрывали в фоне. Здесь
    бюджет исчерпан после первой страницы: вторая отложена и названа, а
    повтор её добирает (идемпотентность).
    """
    main_block = _PortBlock(name="in_main", names="Main_Long_Signal_Name\r\n")
    sub_block = _SubmodelBlock(name="sub_1", block_id=5)
    inner = _PortBlock(name="in_1", names="Inner_Long_Signal_Name\r\n")
    inner.id = 7                      # иначе id поделок совпадут (оба 3)
    sub_page = _FakePage({"in_1": inner})
    project = _SubmodelProject({"in_main": main_block, "sub_1": sub_block},
                               {5: sub_page})
    bridge = _fit_bridge({main_block.id: main_block, inner.id: inner})
    _install_contour(monkeypatch, tmp_path, project, bridge)
    # Бюджет ужимается запасом, а НЕ `runtime.COM_CALL_TIMEOUT`: тот же
    # глобал — и таймаут обёртки вызова (`future.result`), и его правка
    # делала тест чувствительным ко времени (находка ревью PR #118).
    from simintech_mcp.tools import fits as fits_tools
    monkeypatch.setattr(fits_tools, "_CONTOUR_BUDGET_MARGIN_SECONDS", 1e6)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert main_block.value_writes, "первая страница обязана обойтись всегда"
    assert inner.value_writes == [], "вторая страница должна быть отложена"
    assert "страниц обойдено: 1 из 2" in text
    assert "Обошёл не все страницы: 1 отложено" in text
    assert "Повторите `fit_port_blocks`" in text
    assert "субмодель 'sub_1'" in text, "отложенная страница не названа"
    assert session.fit_resume("fit_port_blocks") == sub_page.id, (
        "курсор не запомнил отложенную страницу")

    # Повтор с нормальным бюджетом продолжает С ОТЛОЖЕННОЙ: без курсора
    # (находка ревью PR #118) порция снова оплачивала бы начальные страницы,
    # и хвост не обошёлся бы никогда.
    writes_before = len(main_block.value_writes)
    monkeypatch.setattr(fits_tools, "_CONTOUR_BUDGET_MARGIN_SECONDS", 0.0)
    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert inner.value_writes, "повтор не добрал отложенную страницу"
    assert len(main_block.value_writes) == writes_before, (
        "повтор переобработал страницу до курсора")
    assert "Обход продолжен с отложенной страницы" in text
    assert "страниц обойдено: 2" in text
    assert "отложено" not in text
    assert session.fit_resume("fit_port_blocks") is None, (
        "курсор не сброшен после полного прохода")


class _AnchorBlock(_SizeBlock):
    """Блок с читаемой точкой центра (как у настоящего блока)."""

    def __init__(self, name="k_0", center=(456.0, 72.0), size=(32.0, 16.0)):
        super().__init__(name=name, size=size)
        self._center = center

    def get_points(self):
        return f"[({self._center[0]:g} , {self._center[1]:g})]"


class _ValueLabelBlock(_SizeBlock):
    """Подпись значения: якорь — левый верх карточки 60×40 (живой замер)."""

    def __init__(self, name="TextLabel3", anchor=(248.0, 192.0)):
        super().__init__(name=name, size=(60.0, 40.0))
        self.class_name = "constLabel"
        self._anchor = list(anchor)
        self.centers = []

    def get_points(self):
        return f"[({self._anchor[0]:g} , {self._anchor[1]:g})]"

    def set_center(self, cx, cy):
        self.centers.append((cx, cy))
        self._anchor = [cx - 30.0, cy - 20.0]
        return self


def _install_export(monkeypatch, text):
    from simintech_mcp.tools import fits as fits_tools
    monkeypatch.setattr(fits_tools, "page_export_text",
                        lambda: (text, False, None, None))


def test_constlabel_parents_reads_pairs():
    """Карта «подпись → родитель» из выгрузки (constLabel + parentblock)."""
    from simintech_mcp.tools.fits import _constlabel_parents

    text = ('(\n  k_0: (\n    type = "Константа",\n'
            '    points=[(456 , 72)]\n  ),\n'
            '  TextLabel3: (\n    type = "constLabel",\n'
            '    points=[(440 , 46)],\n    parentblock = "k_0"\n  )\n)')
    assert _constlabel_parents(text) == {"TextLabel3": "k_0"}
    assert _constlabel_parents("(\n)") == {}


def test_constlabel_parents_skips_submodel_pairs():
    """Пары вложенных страниц не попадают в карту страницы-владельца.

    Выгрузка главной несёт субмодели целиком (`subsystem:` + скобочный
    блок): без фильтра по глубине одноимённая подпись главной могла бы
    «подтянуться» по паре субмодели (живой случай: `TextLabel7` есть и на
    главной, и внутри `sub3`).
    """
    from simintech_mcp.tools.fits import _constlabel_parents

    text = (
        '(\n'
        '  TextLabel7: (\n'
        '    type = "constLabel",\n'
        '    points=[(184 , -34)],\n'
        '    parentblock = "inner"\n'
        '  ),\n'
        '  sub3: (\n'
        '    type = "Субмодель",\n'
        '    points=[(600 , 0)],\n'
        '    subsystem:\n'
        '        (\n'
        '          gain5: (\n'
        '            type = "Усилитель",\n'
        '            points=[(60 , 60)],\n'
        '            a = 2\n'
        '          ),\n'
        '          TextLabel7: (\n'
        '            type = "constLabel",\n'
        '            points=[(248 , 192)],\n'
        '            parentblock = "gain5"\n'
        '          )\n'
        '        )\n'
        '  )\n'
        ')'
    )

    assert _constlabel_parents(text) == {"TextLabel7": "inner"}


def test_constlabel_parents_ignores_brackets_in_quoted_values():
    """Скобки в кавычечных значениях не сбивают счёт глубины.

    Выгрузка несёт `script` страницы одной строкой с экранированными `\\n`
    (живой пример — `test_language_contour_live`), и одиночная `(` в тексте
    скрипта уводила счётчик вложенности: `top_level` перестал совпадать с
    записями, карта возвращалась пустой — и `fit_value_labels` молча отвечал
    «подписи на месте» (находка ревью PR #96).
    """
    from simintech_mcp.tools.fits import _constlabel_parents

    text = (
        '(\n'
        '  script = "writelnutf8(fid, \\"тест (1\\");",\n'
        '  TextLabel3: (\n'
        '    type = "constLabel",\n'
        '    points=[(248 , 192)],\n'
        '    parentblock = "k_0"\n'
        '  )\n'
        ')'
    )

    assert _constlabel_parents(text) == {"TextLabel3": "k_0"}


@pytest.mark.anyio
async def test_fit_value_labels_moves_label_to_parent(monkeypatch):
    """Подпись из (248,192) подтягивается к левому верхнему углу родителя."""
    parent = _AnchorBlock()             # центр (456, 72), 32×16
    label = _ValueLabelBlock()          # якорь (248, 192)
    project = _FakeProject({"k_0": parent, "TextLabel3": label})
    monkeypatch.setattr(session, "_project", project)
    _install_export(monkeypatch,
                    '(\n  TextLabel3: (\n    type = "constLabel",\n'
                    '    points=[(248 , 192)],\n    parentblock = "k_0"\n  )\n)')

    text = _tool_text(await mcp.call_tool("fit_value_labels", {}))

    # Цель якоря (440, 46) = (456−16, 72−8−18); set_center — центром карточки.
    assert label.centers == [(470.0, 66.0)]
    assert "подпись (248, 192) → (440, 46)" in text
    assert "к блоку «k_0»" in text
    assert project.get_main_page().activations >= 1, (
        "выгрузка пойдёт не по той странице: главная не активирована")


@pytest.mark.anyio
async def test_fit_value_labels_keeps_label_in_place(monkeypatch):
    """Подпись уже у блока — центры не трогаются."""
    parent = _AnchorBlock()
    label = _ValueLabelBlock(anchor=(440.0, 46.0))
    monkeypatch.setattr(session, "_project",
                        _FakeProject({"k_0": parent, "TextLabel3": label}))
    _install_export(monkeypatch,
                    '(\n  TextLabel3: (\n    type = "constLabel",\n'
                    '    points=[(440 , 46)],\n    parentblock = "k_0"\n  )\n)')

    text = _tool_text(await mcp.call_tool("fit_value_labels", {}))

    assert label.centers == []
    assert "на месте" in text


@pytest.mark.anyio
async def test_fit_value_labels_walks_submodels(monkeypatch):
    """Подписи внутри субмоделей подтягиваются тем же обходом (mcp#19, фаза 2).

    Выгрузка снимается активной страницей, поэтому обход обязан активировать
    субмодель перед съёмкой, а в конце вернуть активной главную (иначе
    следующая контурная операция снимет текст субмодели).
    """
    parent = _AnchorBlock()                      # главная — без подписей
    sub_parent = _AnchorBlock(name="k_1", center=(100.0, 100.0))
    sub_label = _ValueLabelBlock(name="TextLabel5", anchor=(10.0, 10.0))
    sub_page = _FakePage({"k_1": sub_parent, "TextLabel5": sub_label})
    sub_block = _SubmodelBlock(name="sub_1", block_id=5)
    project = _SubmodelProject({"k_0": parent, "sub_1": sub_block}, {5: sub_page})
    monkeypatch.setattr(session, "_project", project)

    texts = [
        '(\n  k_0: (\n    type = "Константа",\n    points=[(456 , 72)]\n  )\n)',
        '(\n  TextLabel5: (\n    type = "constLabel",\n'
        '    points=[(10 , 10)],\n    parentblock = "k_1"\n  )\n)',
    ]
    calls = {"n": 0}

    def fake_export():
        # Порядок обхода — как у `_walk_pages`: главная, затем субмодель.
        text = texts[min(calls["n"], len(texts) - 1)]
        calls["n"] += 1
        return (text, False, None, None)

    from simintech_mcp.tools import fits as fits_tools
    monkeypatch.setattr(fits_tools, "page_export_text", fake_export)

    text = _tool_text(await mcp.call_tool("fit_value_labels", {}))

    # Цель якоря (84, 74) = (100−16, 100−8−18) у k_1; set_center — центром.
    assert sub_label.centers == [(114.0, 94.0)], \
        "подпись внутри субмодели не подтянута"
    assert "страниц обойдено: 2" in text
    assert "субмодель 'sub_1'" in text
    assert project.get_main_page().activations >= 1, \
        "главная не возвращена активной"


@pytest.mark.anyio
async def test_fit_value_labels_defers_pages_over_budget(monkeypatch):
    """Бюджет порций — и у подписей: страница отложена и названа (issue #117).

    Выгрузка страницы — тоже контурный прогон (~20 с): на многопстраничной
    модели обход перекрывал `COM_CALL_TIMEOUT`, и клиент вместо честного
    «продолжите повтором» видел отказ про зависший mmain.
    """
    parent = _AnchorBlock()                      # главная — без подписей
    sub_parent = _AnchorBlock(name="k_1", center=(100.0, 100.0))
    sub_label = _ValueLabelBlock(name="TextLabel5", anchor=(10.0, 10.0))
    sub_page = _FakePage({"k_1": sub_parent, "TextLabel5": sub_label})
    sub_block = _SubmodelBlock(name="sub_1", block_id=5)
    project = _SubmodelProject({"k_0": parent, "sub_1": sub_block}, {5: sub_page})
    monkeypatch.setattr(session, "_project", project)

    # Тексты по вызову: первый экспорт — главной, второй — субмодели (как
    # при обходе: до отложенной страницы дело дошло только на повторе).
    texts = [
        '(\n  k_0: (\n    type = "Константа",\n'
        '    points=[(456 , 72)]\n  )\n)',
        '(\n  TextLabel5: (\n    type = "constLabel",\n'
        '    points=[(10 , 10)],\n    parentblock = "k_1"\n  )\n)',
    ]
    calls = {"n": 0}

    def fake_export():
        export = texts[min(calls["n"], len(texts) - 1)]
        calls["n"] += 1
        return (export, False, None, None)

    from simintech_mcp.tools import fits as fits_tools
    monkeypatch.setattr(fits_tools, "page_export_text", fake_export)
    monkeypatch.setattr(fits_tools, "_CONTOUR_BUDGET_MARGIN_SECONDS", 1e6)

    text = _tool_text(await mcp.call_tool("fit_value_labels", {}))

    assert "страниц обойдено: 1 из 2" in text
    assert "Обошёл не все страницы: 1 отложено" in text
    assert "Повторите `fit_value_labels`" in text
    assert "субмодель 'sub_1'" in text, "отложенная страница не названа"
    assert session.fit_resume("fit_value_labels") == sub_page.id, (
        "курсор не запомнил отложенную страницу")

    # Повтор продолжает с отложенной: у подписей это критично — экспорт
    # платен на любой странице, и без курсора хвост не достижим (находка
    # ревью PR #118). Цель якоря (84, 74) у k_1 — как в walks-тесте.
    monkeypatch.setattr(fits_tools, "_CONTOUR_BUDGET_MARGIN_SECONDS", 0.0)
    text = _tool_text(await mcp.call_tool("fit_value_labels", {}))

    assert sub_label.centers == [(114.0, 94.0)], (
        "повтор не добрал отложенную страницу")
    assert "Обход продолжен с отложенной страницы" in text
    assert session.fit_resume("fit_value_labels") is None, (
        "курсор не сброшен после полного прохода")
