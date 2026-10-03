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
from simintech_mcp.tools import help as help_tools

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
    """Фейковый реестр из четырёх функций; `find_function` ищет по нему."""
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
        _fn("createwire", "Функция создания линии связи.",
            category="Графические и системные",
            section="Порты блоков и линии связи",
            syntax="wire_id = createwire(id, line_type, parent_line_id, "
                   "point_nmb, start_port_id, end_port_id, points_count);",
            args=[HelpArg("id", "integer", "Идентификатор проекта.")]),
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

    assert "Найдено: 4 (показаны первые 2)" in text

    refusal = await _error("search_language_functions",
                           {"query": "функция", "limit": 0})
    assert "limit" in refusal


@pytest.mark.anyio
async def test_search_empty_query_is_overview(registry):
    """Пустой запрос — обзор разделов с числами и именами инструментов."""
    text = _text(await mcp.call_tool("search_language_functions", {}))

    assert "4 записей" in text
    assert "Графические и системные — 3" in text
    assert "search_language_functions" in text


@pytest.mark.anyio
async def test_search_unknown_query_is_an_answer(registry):
    """Ничего не нашлось — ответ с числом записей, а не пустота."""
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "телепорт"}))

    assert "Ничего не нашлось" in text
    assert "4 функций" in text


@pytest.mark.anyio
async def test_search_finds_five_letter_russian_words(registry):
    """«линию» находит createwire: пятибуквенные слова — по основе.

    Живой прогон 03.10.2026 на реальном реестре: запрос «линия связи» давал
    ноль находок — «линия» в «линии» не входит, а порог основы (6) не срезал
    её. Однословная форма здесь намеренно: у фразы частичный фолбэк «спасает»
    исход («связи» совпадает точно), и мутация порога осталась бы незамеченной.
    """
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "линию"}))

    assert "createwire — Функция создания линии связи." in text


@pytest.mark.anyio
async def test_search_finds_verb_form_of_noun(registry):
    """«удалить объект» находит removeprimitiv: глагол против отглагольного.

    Находка ревью 03.10.2026 (реальный реестр): «удалить» и «удаления» имеют
    общей частью «удал» (четыре буквы) — срез в две буквы до неё не дотягивал,
    и запрос с глаголом не находил очевидную функцию.
    """
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "удалить объект"}))

    # Именно полное совпадение: с частичным фолбэком «объект» тоже попадает
    # в выдачу, и мутация среза осталась бы незамеченной (ревью-цикл 03.10).
    assert "Точного совпадения нет" not in text
    assert "removeprimitiv — Функция удаления объекта со схемы" in text


def test_card_does_not_claim_absent_arguments():
    """Пустая таблица аргументов — «не заполнена», а не «их нет» (ревью).

    У `createblock` и `dopt` форма вызова аргументы называет, а таблица
    справки пуста: «у функции их нет» противоречило бы строке синтаксиса
    выше и толкало бы агента звать функцию без аргументов.
    """
    card = help_tools.format_language_function(_fn(
        "createblock", "Функция создания блока на схеме.",
        syntax="obj_id = createblock(id, class_name);"))

    assert "obj_id = createblock(id, class_name);" in card
    assert "не заполнен" in card
    assert "их нет" not in card


@pytest.mark.anyio
async def test_search_falls_back_to_partial_matches(registry):
    """Совпали не все слова — ответ показывает частичные и честно это называет.

    «сохранить снимок»: слова «снимок» в справке нет вовсе, но «сохранить»
    ведёт к семейству сохранения — частичный зацеп лучше ответа «ничего».
    """
    text = _text(await mcp.call_tool("search_language_functions",
                                     {"query": "сохранить телепорт"}))

    assert "Точного совпадения нет" in text
    assert "savescreenshot" in text


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
