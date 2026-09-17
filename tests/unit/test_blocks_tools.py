"""Блоки и параметры: проверка имён до вызова COM."""

from __future__ import annotations

import pytest

from simintech_mcp.server import mcp

from simintech_mcp import catalog, session

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
