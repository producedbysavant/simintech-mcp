"""Инструменты справочника встроенного языка: поиск и карточка функции.

Реестр подменяется фейком (объекты — настоящие `LanguageFunction` схемы 2):
тесты проверяют разбор запроса и форму ответов, а не содержимое поставки —
содержимое держат тесты реестра в `simintech-code`.
"""

from __future__ import annotations

import pytest
from simintech_api import language as language_api
from simintech_api.language import HelpArg, LanguageFunction

from simintech_mcp.server import mcp

from _support import _error, _text


def _fn(name: str, purpose: str, *, category: str = "Стандартные",
        section: str = "", syntax: str = "", args=(),
        graphics_only: bool = False) -> LanguageFunction:
    """Запись реестра — как её вернул бы разбор справки."""
    return LanguageFunction(
        name=name, category=category, section=section,
        doc=f"11_yazyk_programmirovaniya/6_funkcii/x/{name}.html",
        graphics_only=graphics_only, purpose=purpose, syntax=syntax,
        args=tuple(args))


@pytest.fixture
def registry(monkeypatch):
    """Фейковый реестр из трёх функций; `find_function` ищет по нему."""
    functions = [
        _fn("savescreenshot",
            "Функция сохранения в графический файл текущего изображения "
            "экрана проекта.",
            category="Графические и системные", section="Графические",
            syntax="savescreenshot(filename, type);",
            args=[HelpArg("filename", "string", "Строка с именем файла"),
                  HelpArg("type", "integer", "Формат файла")],
            graphics_only=True),
        _fn("removeprimitiv",
            "Функция удаления объекта со схемы по его идентификатору",
            category="Графические и системные", section="Графические",
            syntax="removeprimitiv(gid);",
            args=[HelpArg("gid", "integer",
                          "Идентификатор объекта на схеме")]),
        _fn("abs", "Функция получения модуля числа.",
            syntax="y = abs(x);", args=[HelpArg("x", "число", "Аргумент.")]),
    ]
    monkeypatch.setattr(language_api, "language_functions",
                        lambda path=None: list(functions))
    monkeypatch.setattr(language_api, "registry_meta",
                        lambda: {"version": 2, "help_version": "v-test",
                                 "count": len(functions)})

    def find(name):
        folded = name.strip().casefold()
        for function in functions:
            if function.name.casefold() == folded:
                return function
        return None

    monkeypatch.setattr(language_api, "find_function", find)
    return functions


@pytest.mark.anyio
async def test_search_finds_by_name(registry):
    """Поиск по началу имени находит функцию и называет карточку."""
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "save"}))

    assert "savescreenshot" in text
    assert "Найдено: 1" in text
    assert 'get_language_function("имя")' in text


@pytest.mark.anyio
async def test_search_finds_russian_words_with_case(registry):
    """Русский запрос находит и в другом падеже: «удаление» → «удаления».

    Слова в справке склоняются, и поиск по точной подстроке промахивался бы
    на падежах — по реестру же чаще всего ищут по-русски.
    """
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "удаление объекта"}))

    assert "removeprimitiv" in text
    assert "savescreenshot" not in text


@pytest.mark.anyio
async def test_search_reports_truncation_and_bounds_limit(registry):
    """Усечение видно в ответе, а нелепый предел — отказ до чтения реестра."""
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "функция", "limit": 2}))

    assert "Найдено: 3 (показаны первые 2)" in text

    refusal = await _error("search_language_functions",
                           {"query": "функция", "limit": 0})
    assert "limit" in refusal


@pytest.mark.anyio
async def test_search_empty_query_is_overview(registry):
    """Пустой запрос — обзор разделов с числами и именами инструментов."""
    text = _text(await mcp.call_tool("search_language_functions", {}))

    assert "3 записей" in text
    assert "Графические и системные — 2" in text
    assert "search_language_functions" in text


@pytest.mark.anyio
async def test_search_unknown_query_is_an_answer(registry):
    """Ничего не нашлось — ответ с числом записей, а не пустота."""
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "телепорт"}))

    assert "Ничего не нашлось" in text
    assert "3 функций" in text


@pytest.mark.anyio
async def test_get_returns_card_with_syntax_and_args(registry):
    """Карточка: раздел, назначение, синтаксис, аргументы, путь в справке."""
    text = _text(await mcp.call_tool("get_language_function",
                                     {"name": "savescreenshot"}))

    assert "savescreenshot — Графические и системные / Графические" in text
    assert "только в графическом контейнере" in text
    assert "Синтаксис:" in text
    assert "savescreenshot(filename, type);" in text
    assert "Аргументы:" in text
    assert "filename: string — Строка с именем файла" in text
    assert "Справка:" in text


@pytest.mark.anyio
async def test_get_unknown_name_suggests_and_refers_to_search(registry):
    """Промах имени — отказ с похожими именами и путём к полному поиску."""
    refusal = await _error("get_language_function",
                           {"name": "savescrenshot"})

    assert "нет в реестре" in refusal
    assert "savescreenshot" in refusal
    assert "search_language_functions" in refusal
