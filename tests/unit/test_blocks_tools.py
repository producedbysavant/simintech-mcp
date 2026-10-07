"""Блоки и параметры: проверка имён до вызова COM."""

from __future__ import annotations

import re

import pytest
from simintech_api import ComCallError

from simintech_mcp.server import mcp

from simintech_mcp import catalog, session
from simintech_mcp.tools import page_script
from simintech_mcp.tools.blocks import (
    MAX_BLOCK_IN_PORTS,
    MAX_BLOCK_PROPS,
    MAX_LIST_BLOCKS,
)

from _support import (
    _FakeBlock,
    _FakePage,
    _FakeProject,
    _FakeProjectWithCreate,
    _PlacedBlock,
    _error,
    _install_fake_project,
    _text,
    _tool_text,
)


def test_split_props_keeps_array_commas():
    """Запятые внутри `[...]` не считаются разделителями параметров."""
    from simintech_mcp.tools.blocks import _split_props

    assert _split_props("a=[1, -1], b=2") == ["a=[1, -1]", "b=2"]
    assert _split_props("a=2") == ["a=2"]
    assert _split_props("") == []
    assert _split_props("filename=C:\\Temp\\out.txt,count=1,step=[0.2]") == [
        "filename=C:\\Temp\\out.txt", "count=1", "step=[0.2]"]


@pytest.mark.anyio
async def test_add_block_sets_standard_size_for_extra_inputs(monkeypatch):
    """Число входов меняет штатный размер: «Сумматор» 3 входа — 32x48.

    Замерено по эталонным моделям SimInTech (2026-09-15): 32x32 при двух
    входах, 32x48 при трёх. Блок нестандартного размера — нарушение правил
    разработки.
    """
    project = _FakeProjectWithCreate()
    monkeypatch.setattr(session, "_project", project)

    await mcp.call_tool("add_block", {"class_name": "Сумматор", "in_ports": 3})

    created = project.page._created[-1]
    assert created.in_ports == 3
    assert created.position == (0.0, 0.0, 32.0, 48.0)


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
    """Класс вне каталога — общие свойства прочитаны, и об этом сказано.

    `props_for` для класса вне каталога отдаёт общие свойства (в каталоге это
    `Name`), поэтому ответ НЕ пуст, и решать ветку по пустоте `props` нельзя:
    тогда агент видел бы «параметры неизвестны — класс отсутствует в каталоге»
    без списка и не узнавал бы, что имена такого класса не проверяются, — тогда
    как при записи примечание есть.
    """
    _install_fake_project(monkeypatch, {
        "X": _FakeBlock("Неизвестный класс", {"a": "1"}),
    })

    text = _tool_text(await mcp.call_tool("get_block_params", {"block": "X"}))

    assert "k_0" in text, "общее свойство Name обязано быть в ответе"
    assert "отсутствует в каталоге" in text
    assert "прочитаны только общие свойства" in text
    assert "неизвестны" not in text, "диагноз «параметры неизвестны» неверен"
    assert "a = 1" not in text, (
        "специфичное свойство класса вне каталога читаться не может: "
        "его имён в каталоге нет"
    )


class _UnreadableBlock(_FakeBlock):
    """Класс есть в каталоге, но ни одно свойство не читается (отказ COM)."""

    def get_property(self, name):
        raise RuntimeError("COM: свойство не читается")


@pytest.mark.anyio
async def test_get_block_params_separates_read_failure_from_missing_class(
        monkeypatch):
    """Пустой ответ у класса ИЗ каталога — отказ чтения, а не «нет в каталоге».

    Оба случая раньше давали один текст про каталог, и агент шёл проверять
    установку simintech-api вместо того, чтобы увидеть отказ чтения.
    """
    _install_fake_project(monkeypatch, {"Gain": _UnreadableBlock("Усилитель")})

    text = await _error("get_block_params", {"block": "Gain"})

    assert "отсутствует в каталоге" not in text
    assert "Усилитель" in text


@pytest.mark.anyio
async def test_get_block_params_reports_missing_catalog(monkeypatch):
    """Каталог недоступен целиком — сказано прямо, а не списано на класс.

    `BlockCatalog.load` на пропавший файл отдаёт пустой каталог, и тогда для
    КАЖДОГО класса «класса нет в каталоге» — неверный диагноз, указывающий на
    конкретный класс вместо установки.
    """
    from simintech_api.catalog import BlockCatalog

    _install_fake_project(monkeypatch, {
        "Gain": _FakeBlock("Усилитель", {"a": "2.5"}),
    })
    monkeypatch.setattr(catalog, "load_default_catalog", lambda: BlockCatalog())

    text = _tool_text(await mcp.call_tool("get_block_params", {"block": "Gain"}))

    assert "каталог блоков недоступен целиком" in text
    assert "отсутствует в каталоге" not in text


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
async def test_set_block_param_reports_written_value_not_input(monkeypatch):
    """Ответ показывает значение из блока, а не строку из аргумента.

    `value_to_prop_string` приводит '0.000041' к '4.1e-05', поэтому прежний
    ответ, повторявший ввод, говорил не то, что записалось. Расхождение —
    примечанием, а не отказом: запись уже прошла.
    """
    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param",
        {"block": "Gain", "param": "a", "value": "0.000041"}))

    assert block._props["a"] == "4.1e-05", "записано приведённое значение"
    assert text.startswith("Gain.a = 4.1e-05"), "ответ не должен повторять ввод"
    assert "а запрошено '0.000041'" in text, "расхождение — примечанием"


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
    """Класс вне каталога не отвергается — проверять нечем, и об этом сказано.

    Класс взят заведомо вымышленный: «В файл» раньше служил примером
    непроверяемого, но теперь он в каталоге (952 класса из движка, 958 после
    досборки), и держать его здесь значило бы закреплять снятое ограничение.
    """
    block = _FakeBlock("Класс-которого-нет-в-каталоге", {"filename": "старое.txt"})
    _install_fake_project(monkeypatch, {"ToFile": block})

    text = _tool_text(await mcp.call_tool(
        "set_block_param",
        {"block": "ToFile", "param": "filename", "value": "новое.txt"}))

    assert block._props["filename"] == "новое.txt"
    assert "отсутствует в каталоге" in text


@pytest.mark.anyio
async def test_set_block_param_checks_names_of_to_file(monkeypatch):
    """«В файл» теперь в каталоге — его имена проверяются, а не пропускаются.

    Это и было целью волны 2 плана: класс, который раньше попадал в ветку
    «проверять нечем», теперь под проверкой (имена сняты с живой сборки).
    """
    block = _FakeBlock("В файл", {"filename": "старое.txt"})
    _install_fake_project(monkeypatch, {"ToFile": block})

    await mcp.call_tool(
        "set_block_param", {"block": "ToFile", "param": "step", "value": "[0.2]"})

    assert block._props["step"] == "[0.2]"


@pytest.mark.anyio
async def test_set_block_param_rejects_unknown_name_of_to_file(monkeypatch):
    """Опечатка в имени параметра «В файл» отвергается каталогом."""
    block = _FakeBlock("В файл", {"filename": "старое.txt"})
    _install_fake_project(monkeypatch, {"ToFile": block})

    with pytest.raises(Exception) as excinfo:
        await mcp.call_tool(
            "set_block_param",
            {"block": "ToFile", "param": "filenam", "value": "x.txt"})

    assert "filenam" in str(excinfo.value)
    assert "filename" in str(excinfo.value)
    assert "filenam" not in block._props


@pytest.mark.anyio
async def test_add_block_rejects_unknown_prop_without_creating_block(
        monkeypatch):
    """Неизвестный параметр в add_block — отказ, и блок на схеме не остаётся."""

    _install_fake_project(monkeypatch, {})

    text = await _error("add_block",
                        {"class_name": "Константа", "props": "y0=5"})

    assert "y0" in text
    assert session.current_project().get_main_page()._created == [], (
        "отказ до создания блока — иначе блок остался бы на схеме"
    )


@pytest.mark.anyio
async def test_add_block_allow_unknown_props_creates_block(monkeypatch):
    """`allow_unknown_props=True` — блок создаётся с «некаталожным» параметром."""

    _install_fake_project(monkeypatch, {})

    await mcp.call_tool("add_block", {"class_name": "Константа", "props": "y0=5",
                                      "allow_unknown_props": True})

    created = session.current_project().get_main_page()._created
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
    from simintech_mcp.tools.blocks import _coerce_param_value

    assert _coerce_param_value("2") == 2
    assert _coerce_param_value("2.5") == 2.5
    assert _coerce_param_value(" -3 ") == -3
    assert _coerce_param_value("текст") == "текст"


def test_coerce_param_value_parses_arrays():
    from simintech_mcp.tools.blocks import _coerce_param_value

    assert _coerce_param_value("[1, -1]") == [1, -1]
    assert _coerce_param_value("[1.5,2]") == [1.5, 2]
    assert _coerce_param_value("[]") == []


@pytest.mark.anyio
async def test_add_block_warns_when_rename_ignored(monkeypatch):
    """Если имя не применилось, инструмент сообщает об этом и даёт автоимя.

    Иначе агент получит «name=Src», а блок будет называться k_0 — и
    последующий connect по имени не найдёт блок.
    """
    monkeypatch.setattr(session, "_project", _FakeProjectWithCreate())

    text = _tool_text(await mcp.call_tool(
        "add_block", {"class_name": "Константа", "name_hint": "Src"}))

    assert "НЕ применилось" in text
    assert "k_0" in text


@pytest.mark.anyio
async def test_add_block_reports_auto_name(monkeypatch):
    """Без name_hint= инструмент возвращает фактическое автоимя."""
    monkeypatch.setattr(session, "_project", _FakeProjectWithCreate())

    text = _tool_text(await mcp.call_tool(
        "add_block", {"class_name": "Константа"}))

    assert "k_0" in text
    assert "НЕ применилось" not in text


@pytest.mark.anyio
async def test_add_block_reports_ignored_props(monkeypatch):
    """Части props без '=' не исчезают молча."""
    _install_fake_project(monkeypatch, {})

    text = _text(await mcp.call_tool(
        "add_block", {"class_name": "Константа", "props": "a=2,мусор"}))

    assert "мусор" in text
    assert "пропущены" in text


@pytest.mark.anyio
async def test_set_block_param_rejects_name(monkeypatch):
    """`Name` есть в каталоге, но COM его запись игнорирует — отказ.

    `SetBlockProp("Name", …)` блок не переименовывает: имя остаётся
    автоматическим. Без этой ветки проверка пропускала бы ровно ту запись,
    ради которой она и делалась, — успешный ответ без эффекта.
    """
    block = _FakeBlock("Усилитель", {"Name": "kx_0"})
    _install_fake_project(monkeypatch, {"Gain": block})

    text = await _error("set_block_param",
                        {"block": "Gain", "param": "Name", "value": "pid"})

    assert "не переименовываются" in text
    assert block.inited is False


@pytest.mark.anyio
async def test_set_block_param_name_with_allow_unknown_writes(monkeypatch):
    """Обход остаётся: `allow_unknown=True` пропускает и `Name`."""
    block = _FakeBlock("Усилитель", {"Name": "kx_0"})
    _install_fake_project(monkeypatch, {"Gain": block})

    await mcp.call_tool("set_block_param",
                        {"block": "Gain", "param": "Name", "value": "pid",
                         "allow_unknown": True})

    assert block._props["Name"] == "pid"


@pytest.mark.anyio
async def test_set_block_param_reports_missing_catalog(monkeypatch):
    """Каталог недоступен целиком — сказано явно, а не списано на класс.

    `BlockCatalog.load` на пропавший файл отдаёт пустой каталог, и тогда
    «класса нет в каталоге» — это отказ проверки вообще, а не свойство класса.
    """
    from simintech_api.catalog import BlockCatalog

    block = _FakeBlock("Усилитель", {"a": "1"})
    _install_fake_project(monkeypatch, {"Gain": block})
    monkeypatch.setattr(catalog, "load_default_catalog", lambda: BlockCatalog())

    text = _tool_text(await mcp.call_tool(
        "set_block_param",
        {"block": "Gain", "param": "неттакого", "value": "1"}))

    assert "каталог блоков недоступен целиком" in text
    assert block._props["неттакого"] == "1", "fail-open остаётся, но он громкий"


@pytest.mark.anyio
async def test_add_block_rejects_computed_param(monkeypatch):
    """Вычисляемый параметр отвергается и в add_block, а не только в set."""

    _install_fake_project(monkeypatch, {})

    text = await _error(
        "add_block", {"class_name": "Усилитель", "props": "formula_visible=1"})

    assert "вычисляемый" in text
    assert session.current_project().get_main_page()._created == []


class _CreatedBlock:
    """Блок, созданный `add_block`: считает записи свойств и вызовы позиции."""

    AUTO_NAME = "k_0"

    def __init__(self, class_name):
        self.class_name = class_name
        self._name = self.AUTO_NAME
        self.writes = []
        self.post_calls = []

    @property
    def id(self):
        return 1

    def set_name(self, name):
        self.post_calls.append("set_name")
        return self

    def set_in_port_count(self, count):
        self.post_calls.append("set_in_port_count")
        return self

    def set_position(self, x, y, *, width=None, height=None):
        self.post_calls.append("set_position")
        return self

    def set_property(self, name, value):
        self.writes.append((name, value))
        return self

    def get_name(self):
        return self._name


class _BrokenPosition(_CreatedBlock):
    """Блок, у которого настройка срывается уже после создания."""

    def set_position(self, x, y, *, width=None, height=None):
        raise RuntimeError("редактор недоступен")


class _CreateProject:
    """Проект с одной страницей; страница запоминает созданные блоки."""

    def __init__(self, block_cls=_CreatedBlock):
        self.created = []
        self._block_cls = block_cls
        self.id = 7  # ответы правок называют проект (issue #18)

    def get_main_page(self):
        return self

    def create_block(self, class_name, x, y):
        block = self._block_cls(class_name)
        self.created.append(block)
        return block


@pytest.mark.anyio
async def test_add_block_rejects_excessive_props(monkeypatch):
    """props сверх предела отвергается ДО создания блока, без единой записи.

    Каждая пара — отдельный COM-вызов в единственном выделенном потоке, а по
    таймауту такой вызов не прерывается: неограниченный `props` из параметра
    клиента занял бы поток надолго, и сервер восстанавливался бы только
    перезапуском `mmain.exe` вместе с `disconnect` — тот же класс опасности,
    что закрыт у `step` (`MAX_STEP_COUNT`).
    """
    project = _CreateProject()
    monkeypatch.setattr(session, "_project", project)
    props = ",".join(f"p{i}=1" for i in range(MAX_BLOCK_PROPS + 1))

    # `allow_unknown_props` снимает проверку имён по каталогу: предел обязан
    # остановить вызов сам, а не «повезло, что имён таких нет».
    text = await _error("add_block", {"class_name": "Усилитель", "props": props,
                                      "allow_unknown_props": True})

    assert str(MAX_BLOCK_PROPS) in text
    assert project.created == [], (
        "отказ до create_block — иначе блок остался бы на схеме"
    )


@pytest.mark.anyio
async def test_add_block_collapses_repeated_props(monkeypatch):
    """Повторы имён схлопываются: сто пар — одна запись, а не сто COM-вызовов.

    SimInTech применяет повторы последовательно (побеждает последнее
    значение), пользы от них нет, а COM-вызовов они добавляют ровно столько,
    сколько пар.
    """
    project = _CreateProject()
    monkeypatch.setattr(session, "_project", project)

    await mcp.call_tool("add_block",
                        {"class_name": "Константа", "props": "a=1," * 100})

    assert project.created[0].writes == [("a", 1)]


@pytest.mark.anyio
async def test_add_block_rejects_negative_in_ports(monkeypatch):
    """`in_ports=-1` отвергается до создания блока.

    Библиотека отвергает это сама (`SetPortCount` требует >= 1), но уже ПОСЛЕ
    `CreateBlock`, поэтому отказ приходил вместе с блоком-сиротой на схеме.
    """
    project = _CreateProject()
    monkeypatch.setattr(session, "_project", project)

    text = await _error("add_block", {"class_name": "Сумматор", "in_ports": -1})

    assert "in_ports" in text
    assert project.created == [], (
        "отказ до create_block — иначе блок остался бы на схеме"
    )


@pytest.mark.anyio
async def test_add_block_rejects_excessive_in_ports(monkeypatch):
    """`in_ports` сверх предела отвергается до создания блока.

    `SetPortCount` — один COM-вызов, который на таком числе портов занял бы
    выделенный поток надолго, а по таймауту COM-вызов не прерывается: тот же
    класс опасности, что закрыт у `step` (`MAX_STEP_COUNT`).
    """
    project = _CreateProject()
    monkeypatch.setattr(session, "_project", project)

    text = await _error("add_block", {"class_name": "Сумматор",
                                      "in_ports": MAX_BLOCK_IN_PORTS + 1})

    assert str(MAX_BLOCK_IN_PORTS) in text
    assert project.created == [], (
        "отказ до create_block — иначе блок остался бы на схеме"
    )


@pytest.mark.anyio
async def test_add_block_names_created_block_when_setup_fails(monkeypatch):
    """Сбой после `create_block` — отказ, называющий уже созданный блок.

    Без имени агент счёл бы вызов неудавшимся, повторил бы `add_block` и
    оставил на схеме второго сироту, а первый (частично настроенный) висел бы
    незамеченным.
    """
    project = _CreateProject(_BrokenPosition)
    monkeypatch.setattr(session, "_project", project)

    text = await _error("add_block", {"class_name": "Сумматор", "in_ports": 2})

    assert "уже создан" in text
    assert "k_0" in text
    assert len(project.created) == 1


# ─── list_wires: контракт ответа ──────────────────────────────────

class _FakeWire:
    """Линия с идентификатором — больше `list_wires` от COM не читает."""

    def __init__(self, wire_id):
        self.id = wire_id


class _BrokenWirePage:
    """Страница, у которой перечисление линий падает."""

    def __init__(self, error):
        self._error = error

    def get_wires(self):
        raise self._error


class _BrokenWireProject:
    def __init__(self, page):
        self._page = page

    def get_main_page(self):
        return self._page


def _page_with_wires(monkeypatch, wire_ids, extra_blocks=()):
    """Страница с заданными линиями (подделка собирает их из блоков)."""
    block = _PlacedBlock("k_0", 1)
    block.wires = [(_FakeWire(i), "kx_0", 0, 0) for i in wire_ids]
    blocks = {"k_0": block}
    for name, block_id in extra_blocks:
        blocks[name] = _PlacedBlock(name, block_id)
    _install_fake_project(monkeypatch, blocks)


@pytest.mark.anyio
async def test_list_wires_reports_empty_page(monkeypatch):
    """Пустая страница: названо отсутствие линий, а не счётчик с нулём."""
    _install_fake_project(monkeypatch, {"k_0": _PlacedBlock("k_0", 1)})

    text = _text(await mcp.call_tool("list_wires", {}))

    assert "Линий связи на странице нет" in text


@pytest.mark.anyio
async def test_list_wires_reports_ids_and_says_what_it_cannot(monkeypatch):
    """Ответ несёт количество, идентификаторы и оговорку про концы линий.

    Оговорка — часть контракта, а не украшение: концы линий через COM не
    читаются, и клиент обязан узнать об этом из ответа, а не догадываться по
    отсутствию пары «откуда → куда».
    """
    _page_with_wires(monkeypatch, [11, 12, 13], extra_blocks=[("kx_0", 2)])

    text = _text(await mcp.call_tool("list_wires", {}))

    assert "Линий связи: 3" in text
    assert "блоков на странице: 2" in text
    assert "11, 12, 13" in text
    assert "через COM не читаются" in text


@pytest.mark.anyio
async def test_list_wires_truncates_a_long_list_with_a_marker(monkeypatch):
    """Больше 20 линий: печатаются первые 20, усечение помечено «…».

    Без пометки клиент счёл бы список полным и не заметил бы остальные линии.
    """
    _page_with_wires(monkeypatch, range(1, 26))

    text = _text(await mcp.call_tool("list_wires", {}))

    assert "Линий связи: 25" in text
    listed = text.split("Идентификаторы:")[1]
    assert "1, 2, 3" in listed
    assert "20 …" in listed, "усечение обязано быть помечено"
    assert ", 21" not in listed, "двадцать первая линия не печатается"


@pytest.mark.anyio
async def test_list_wires_refuses_when_enumeration_fails(monkeypatch):
    """Сбой перечисления — отказ, а не «линий нет».

    Подстановка пустого списка выдала бы сбой среды за честный ноль объектов:
    клиент, доверяющий `isError`, увидел бы успех и продолжил работу по
    недостоверной модели схемы.
    """
    page = _BrokenWirePage(ComCallError("GetPageObjectCount"))
    monkeypatch.setattr(session, "_project", _BrokenWireProject(page))

    text = await _error("list_wires", {})

    assert "ComCallError" in text


# ─── set_block_size (mcp#24, п.5) ─────────────────────────────────


class _SizeBlock:
    """Блок с размером: два пути записи — оба в «Значение».

    `apply_body` моделирует переход по тексту тела контура (`setprop(…)`) —
    путь `set_block_size`; `set_size_value` — запись «Значения» для фитов
    (`fit_port_blocks`). Оба пути применяются к одному состоянию — как у
    среды, где габарит в итоге один; `graph_writes` («Формула») должен
    оставаться пустым, и это проверяется тестами обоих инструментов.
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


class _PortBlock(_SizeBlock):
    """Порт-блок: класс «Порт входа», список сигналов читается из PortNames.

    Подделка моделирует замер 01.10.2026: имена приходят строкой с `\\r\\n`
    (`'in\\r\\n'` у однозначного порта, `'a1\\r\\na2\\r\\n'` у двухзначного).
    """

    def __init__(self, names="in\r\n", name="InputPort_0", size=(64.0, 16.0)):
        super().__init__(name=name, size=size)
        self.class_name = "Порт входа"
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


def _install_fit_contour(monkeypatch, tmp_path, project, bridge):
    """Подменить клиент, проект и мост фитов — как `_install` у скриптов."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FitClient())
    monkeypatch.setattr(session, "_project", project)
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_fit_port_blocks_widens_long_labels(monkeypatch, tmp_path):
    """Длинная подпись: ширина растёт записью в «Значение», не в «Формулу»."""
    long_name = "CoolTT_C_CoolSt_WorkSt"
    block = _PortBlock(names=long_name + "\r\n")
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({block.id: block})
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
async def test_fit_port_blocks_keeps_fitting_labels(monkeypatch, tmp_path):
    """Подпись в рамке — блок не трогается, контур не зовётся; главная активна."""
    block = _PortBlock(names="in\r\n")
    project = _FakeProject({"InputPort_0": block})
    bridge = _fit_bridge({})
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

    text = _tool_text(await mcp.call_tool("fit_port_blocks", {}))

    assert ("Height", "96") in block.value_writes
    assert "высота 112 → 96 (6 вход(ов) × 16)" in text


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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

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
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("fit_port_blocks", {})

    assert "активной" in text
    assert bridge.calls == 0, "контур звали без активной страницы"
    assert port.value_writes == [] and port.graph_writes == []


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
    from simintech_mcp.tools import blocks as blocks_tools
    monkeypatch.setattr(blocks_tools, "page_export_text",
                        lambda: (text, False, None, None))


def test_constlabel_parents_reads_pairs():
    """Карта «подпись → родитель» из выгрузки (constLabel + parentblock)."""
    from simintech_mcp.tools.blocks import _constlabel_parents

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
    from simintech_mcp.tools.blocks import _constlabel_parents

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
    from simintech_mcp.tools.blocks import _constlabel_parents

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

    from simintech_mcp.tools import blocks as blocks_tools
    monkeypatch.setattr(blocks_tools, "page_export_text", fake_export)

    text = _tool_text(await mcp.call_tool("fit_value_labels", {}))

    # Цель якоря (84, 74) = (100−16, 100−8−18) у k_1; set_center — центром.
    assert sub_label.centers == [(114.0, 94.0)], \
        "подпись внутри субмодели не подтянута"
    assert "страниц обойдено: 2" in text
    assert "субмодель 'sub_1'" in text
    assert project.get_main_page().activations >= 1, \
        "главная не возвращена активной"


def _many_blocks(count: int) -> dict:
    """Страница из `count` блоков с различимыми именами и id."""
    return {f"k_{i}": _PlacedBlock(f"k_{i}", i, "Усилитель")
            for i in range(count)}


@pytest.mark.anyio
async def test_list_blocks_truncates_and_names_the_rest(monkeypatch):
    """Обрезка по умолчанию названа в ответе — с остатком и выходом.

    Боль потребителя (06.10.2026): id блока дальше 50-го было не увидеть, и
    агент перебирал числовые id вслепую. Ответ обязан называть и остаток, и
    `limit=0` — иначе обрезка снова молчаливая.
    """
    _install_fake_project(monkeypatch, _many_blocks(60))

    text = _tool_text(await mcp.call_tool("list_blocks", {}))

    assert "k_49" in text
    assert "k_50" not in text
    assert "и ещё 10" in text
    assert "всего 60" in text
    assert "limit=0" in text


@pytest.mark.anyio
async def test_list_blocks_limit_zero_shows_everything(monkeypatch):
    """`limit=0` — все блоки страницы, обрезки нет."""
    _install_fake_project(monkeypatch, _many_blocks(60))

    text = _tool_text(await mcp.call_tool("list_blocks", {"limit": 0}))

    assert "k_59" in text
    assert "и ещё" not in text


@pytest.mark.anyio
async def test_list_blocks_rejects_limit_out_of_range(monkeypatch):
    """Отрицательный и сверхпредельный limit — отказ.

    Предел — отсечка ошибок единиц (как `MAX_BLOCK_SIZE`): значение больше
    `MAX_LIST_BLOCKS` — уже не «сколько показать», а недоразумение.
    """
    _install_fake_project(monkeypatch, _many_blocks(1))

    assert "вне диапазона" in await _error("list_blocks", {"limit": -1})
    assert "вне диапазона" in await _error(
        "list_blocks", {"limit": MAX_LIST_BLOCKS + 1})


# ─── connect_branch: ветвление от существующей линии ───────────────


class _BranchBlock:
    """Блок с портами для connect_branch: id, имена и наличие портов."""

    def __init__(self, name, block_id, out_ports=1, in_ports=1):
        self._name = name
        self.id = block_id
        self._out = out_ports
        self._in = in_ports
        self.class_name = "Усилитель"

    def get_name(self):
        return self._name

    def get_out_port(self, index=0):
        from simintech_api import PortError
        if index >= self._out:
            raise PortError(f"блок {self.id}: выходной порт {index} не найден")
        return object()

    def get_in_port(self, index=0):
        from simintech_api import PortError
        if index >= self._in:
            raise PortError(f"блок {self.id}: входной порт {index} не найден")
        return object()


class _BranchBridge:
    """Мост-подделка тела ветвления: отвечает заданными строками.

    Тело идёт контуром (`createwire` и чтения) — подделка фиксирует тело и
    возвращает сценарий: успешный `created=… parent=… node=…` либо `err=…`.
    Так проверяются диагнозы инструмента на каждый `err`, а не язык.
    """

    payload: list = []
    kind = "ok"
    body = ""

    def __init__(self, client, project_id):
        self.project_id = project_id

    def run_page_script(self, body, result_path):
        from simintech_api.core.script_bridge import PageRunResult
        from simintech_api.script_probe import ContourOutcome

        type(self).body = body
        lines = [] if type(self).kind != "ok" else list(type(self).payload)
        return PageRunResult(
            outcome=ContourOutcome(kind=type(self).kind, lines=lines),
            restored_script="")


def _branch_bridge(payload=(), kind="ok"):
    """Мост с конфигурацией на тест (фабрика подкласса — урок #83)."""
    return type("_ConfiguredBranchBridge", (_BranchBridge,), {
        "payload": list(payload), "kind": kind, "body": ""})


def _branch_project():
    """Проект: источник с выходом, приёмник с входом."""
    src = _BranchBlock("k_0", 10)
    dst = _BranchBlock("kx_1", 11)
    return _FakeProject({"k_0": src, "kx_1": dst}), src, dst


@pytest.mark.anyio
async def test_connect_branch_creates_and_reports_node(monkeypatch, tmp_path):
    """Успех: ветвь создана, узел K+1 и родитель названы, реестр пополнен."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=1"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1"}))

        assert "Ветвь создана: k_0[0] → kx_1[0]" in text
        assert "wire=777" in text and "узел 1" in text
        assert "родитель 555" in text
        assert project.get_main_page().activations >= 1, (
            "контурный прогон без активной страницы")
        assert any(w[0].id == 777 for w in session.WIRES), \
            "ветвь не попала в реестр session.WIRES"
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_body_passes_point_index(monkeypatch, tmp_path):
    """Точка и порты доезжают до тела: createwire получает K как есть."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=3"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1",
                               "point_index": 2}))

        assert "createwire(prj, 0, parent, 2, 0, inp, 0)" in bridge.body
        assert "getpointcount(parent)" in bridge.body
        # Гарды портов — как у `_disconnect_wire_body`: нулевой порт иначе
        # даёт мусорную линию или ложный диагноз (находка ревью PR #110).
        assert 'if outport = 0 then writelnutf8(fid, "err=no-out-port");' \
            in bridge.body
        assert 'if inp = 0 then writelnutf8(fid, "err=no-in-port");' \
            in bridge.body
        # Узел 3 при K=2 — ожидание сходится, примечания нет.
        assert "ВНИМАНИЕ" not in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_notes_node_mismatch(monkeypatch, tmp_path):
    """K=0, узел не 1 — точка недостижима: строгое примечание."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=4"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1"}))

        assert "ВНИМАНИЕ" in text
        assert "узел 4 при K=0" in text
        assert "недостижима" in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_mismatch_on_k_gt0_hedges(monkeypatch, tmp_path):
    """K>0 и узел не K+1 — не утверждаем «недостижима»: соответствие не измерено.

    Соответствие «узел = K+1» замерено только на K=0 (находка ревью PR
    #110): на K>0 строгое примечание «точка недостижима» могло бы ругать
    корректную ветвь.
    """
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=777 parent=555 node=1"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = _tool_text(await mcp.call_tool(
            "connect_branch", {"src": "k_0", "dst": "kx_1",
                               "point_index": 2}))

        assert "ВНИМАНИЕ" in text
        assert "соответствие для K>0 не измерено" in text
        assert "недостижима" not in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_without_parent_wire(monkeypatch,
                                                          tmp_path):
    """У выхода нет линии — отказ с указанием `connect`."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=no-parent-wire"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1"})

        assert "линия не идёт" in text
        assert "`connect`" in text
        assert not any(w[0].id == 777 for w in session.WIRES)
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_point_out_of_range(monkeypatch,
                                                         tmp_path):
    """K вне точек данных — отказ ДО создания (иначе молчаливый фолбэк)."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=point-range cnt=1"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1",
                             "point_index": 1})

        assert "1 точек данных" in text
        assert "свернула бы ветвь к первой" in text
        assert "`K < 1`" in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_zero_points_advice(monkeypatch, tmp_path):
    """cnt=0: совет — только point_index=0, без невыполнимого «K < 0».

    Находка ревью PR #110: при нуле точек формула «K < cnt» давала совет,
    противоречащий собственному запрету отрицательного K.
    """
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=point-range cnt=0"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1",
                             "point_index": 1})

        assert "0 точек данных" in text
        assert "`point_index=0` (проверенный случай)" in text
        assert "K < 0" not in text
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_when_port_blind_in_contour(monkeypatch,
                                                                 tmp_path):
    """Порт виден COM, но не контуру — отказ, а не мусорная линия.

    Нулевой приёмник `createwire` превращает в реальную линию (находка
    ревью PR #110) — гард в теле обязан ловить это до создания.
    """
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["err=no-in-port"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)
    saved = list(session.WIRES)
    try:
        text = await _error("connect_branch",
                            {"src": "k_0", "dst": "kx_1"})

        assert "не нашла входной порт" in text
        assert "пересоздаться" in text
        assert not any(w[0].id == 777 for w in session.WIRES)
    finally:
        session.WIRES[:] = saved


@pytest.mark.anyio
async def test_connect_branch_refuses_on_not_compiled(monkeypatch, tmp_path):
    """Несобравшееся тело — отказ, а не «создано»."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(kind="not-compiled")
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("connect_branch", {"src": "k_0", "dst": "kx_1"})

    assert "не собралось" in text


@pytest.mark.anyio
async def test_connect_branch_rejects_bad_port_before_contour(monkeypatch,
                                                              tmp_path):
    """Несуществующий порт — отказ через COM ДО контура (диагноз, не фолбэк)."""
    project, _src, _dst = _branch_project()
    bridge = _branch_bridge(["created=1 parent=1 node=1"])
    _install_fit_contour(monkeypatch, tmp_path, project, bridge)

    text = await _error("connect_branch",
                        {"src": "k_0", "dst": "kx_1", "in_index": 5})

    assert "нет входного порта 5" in text
    assert bridge.body == "", "контур звали, хотя порт не существует"
