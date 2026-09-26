"""Блоки и параметры: проверка имён до вызова COM."""

from __future__ import annotations

import pytest

from simintech_mcp.server import mcp

from simintech_mcp import catalog, session
from simintech_mcp.tools.blocks import MAX_BLOCK_IN_PORTS, MAX_BLOCK_PROPS

from _support import (
    _FakeBlock,
    _FakeProjectWithCreate,
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
    assert session._project.get_main_page()._created == [], (
        "отказ до создания блока — иначе блок остался бы на схеме"
    )


@pytest.mark.anyio
async def test_add_block_allow_unknown_props_creates_block(monkeypatch):
    """`allow_unknown_props=True` — блок создаётся с «некаталожным» параметром."""

    _install_fake_project(monkeypatch, {})

    await mcp.call_tool("add_block", {"class_name": "Константа", "props": "y0=5",
                                      "allow_unknown_props": True})

    created = session._project.get_main_page()._created
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
    assert session._project.get_main_page()._created == []


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
