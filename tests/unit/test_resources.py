"""Ресурсы MCP: состояние, каталог блоков, скиллы."""

from __future__ import annotations

import sys

import pytest

from simintech_mcp import catalog, resources, sandbox, session, skills

from _support import _resource_text, _write_skill


@pytest.mark.anyio
async def test_resource_blocks_catalog_lists_classes_and_params():
    """В каталоге видно классы и имена их параметров — их и ждёт COM."""
    text = await _resource_text("simintech://blocks/catalog")

    assert "Каталог блоков" in text
    assert "Константа" in text
    assert "a" in text


@pytest.mark.anyio
async def test_resource_blocks_catalog_marks_computed_params(monkeypatch):
    """Вычисляемые параметры помечены: запись в них ничего не меняет.

    Класс выдуман намеренно. Раньше здесь стоял «Усилитель», а синтетический
    каталог для него совпадал с настоящим `block_catalog.json` — подмена была
    ненаблюдаемой, и тест прошёл бы, даже если бы ресурс читал каталог в обход
    неё. С выдуманным классом это сразу видно.
    """
    from simintech_api.catalog import BlockCatalog

    cat = BlockCatalog(
        classes={"ВыдуманныйКласс": {"a": "1", "formula_visible": "0"}},
        readonly={"ВыдуманныйКласс": ["formula_visible"]},
    )
    monkeypatch.setattr(catalog, "load_default_catalog", lambda: cat)

    text = await _resource_text("simintech://blocks/catalog")

    assert "ВыдуманныйКласс" in text
    assert "вычисляемые" in text


@pytest.mark.anyio
async def test_resource_blocks_catalog_reports_empty(monkeypatch):
    """Пустой каталог — это сообщение о причине, а не пустая строка."""
    from simintech_api.catalog import BlockCatalog

    monkeypatch.setattr(catalog, "load_default_catalog", lambda: BlockCatalog())

    text = await _resource_text("simintech://blocks/catalog")

    assert "Каталог блоков пуст" in text


@pytest.mark.anyio
async def test_resource_blocks_catalog_puts_working_classes_first(monkeypatch):
    """Рабочие классы идут первыми, а не по коду символа.

    `BlockCatalog.classes()` сортирует по коду символа, поэтому кириллица
    («Константа», «Усилитель») оказывалась после ASCII-классов (Arduino,
    GD32F): из 958 классов в первые сто строк попадал один рабочий. Ресурс
    задаёт порядок сам — иначе при усечении ответа клиентом пропадут ровно
    те классы, ради которых он существует.
    """
    from simintech_api.catalog import BlockCatalog

    cat = BlockCatalog(classes={"ZZZ-плата": {"a": "1"},
                                "Константа": {"a": "1"},
                                "Усилитель": {"k": "1"}})
    monkeypatch.setattr(catalog, "load_default_catalog", lambda: cat)

    text = await _resource_text("simintech://blocks/catalog")

    assert text.index("Константа") < text.index("ZZZ-плата")
    assert text.index("Усилитель") < text.index("ZZZ-плата")


@pytest.mark.anyio
async def test_resource_blocks_catalog_is_bounded_and_says_so(monkeypatch):
    """Ответ ограничен по объёму, и о пределе и общем числе сказано явно.

    Полный каталог — около 958 классов и 130 тыс. знаков: усечение клиентом
    отрезало бы ответ по объёму, а не по смыслу. Поэтому классов показывается
    не больше предела, число показанных и общее названы, а рабочий класс
    виден, даже если по алфавиту он в хвосте списка.
    """
    from simintech_api.catalog import BlockCatalog

    total = resources.MAX_CATALOG_CLASSES + 6
    classes = {f"Класс-{index:03d}": {"a": "1"} for index in range(total - 1)}
    classes["Константа"] = {"a": "1"}
    monkeypatch.setattr(catalog, "load_default_catalog",
                        lambda: BlockCatalog(classes=classes))

    text = await _resource_text("simintech://blocks/catalog")

    assert (f"Показаны первые {resources.MAX_CATALOG_CLASSES} классов "
            f"из {total}") in text
    assert text.count("\n  Класс-") == resources.MAX_CATALOG_CLASSES - 1
    assert "Константа" in text


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
    """Имя скилла приходит от клиента и подставляется в путь: «..» не проходит.

    Отказ по имени должен отличаться от «скилла нет» — иначе тест проходил бы
    и с полностью отключённым шаблоном имён. Рядом с каталогом скиллов лежит
    файл-приманка, и по ответу видно, что до него не дошли.
    """

    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("в корне скиллов", encoding="utf-8")
    bait = tmp_path / "приманка"
    bait.mkdir()
    (bait / "SKILL.md").write_text("СЕКРЕТ", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(skills))

    for name in ("../приманка", "../../etc/passwd", "SimInTech", ""):
        text = resources.resource_skill(name)
        assert "недопустимое имя" in text, name
        assert "СЕКРЕТ" not in text, name
        assert "в корне скиллов" not in text, name


def test_resource_skill_missing(monkeypatch, tmp_path):
    """Несуществующий скилл — отказ, а не пустой текст."""

    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))

    assert "ERROR" in resources.resource_skill("нет-такого")


@pytest.mark.anyio
async def test_resource_skill_truncates_long_skill(monkeypatch, tmp_path):
    """Тело скилла ограничено по объёму, и обрезка видна читателю."""

    _write_skill(tmp_path, "simintech-model-building", "я" * 500)
    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(skills, "MAX_SKILL_BYTES", 100)

    text = await _resource_text("simintech://skills/simintech-model-building")

    assert "обрезан" in text


def test_resource_skills_marks_unreadable_skill(monkeypatch, tmp_path):
    """Ошибка чтения скилла видна, а не выглядит как отсутствие описания."""

    _write_skill(tmp_path, "simintech-model-building", "# Заголовок\n")
    monkeypatch.setenv("SIMINTECH_SKILLS_DIR", str(tmp_path))

    def unreadable(path, max_bytes):
        raise OSError("нет доступа")

    monkeypatch.setattr(sandbox, "_read_bounded", unreadable)

    assert "описание недоступно" in resources.resource_skills()


def test_resource_status_returns_text_on_failure(monkeypatch):
    """А ресурс `simintech://status` ту же причину отдаёт текстом."""

    monkeypatch.setattr(sys, "platform", "win32")

    def unavailable():
        raise RuntimeError("COM не зарегистрирован")

    monkeypatch.setattr(session, "_ensure_client", unavailable)

    assert "недоступен" in resources.resource_status()


def test_resource_language_functions_tells_what_registry_gives():
    """Реестр языка: видно объём и то, чего в нём нет — сигнатур.

    Знаниевый контент описывает около 5% имён, а правдоподобное имя функции
    в языке может отсутствовать, поэтому агент должен уметь спросить реестр.
    Реестр даёт только имя, категорию и назначение — об этом сказано прямо,
    иначе агент принял бы молчание о сигнатурах за их наличие.
    """
    text = resources.resource_language_functions()

    assert "уникальных имён" in text
    assert "сигнатуры" in text
    assert "simintech://language/functions" in text


@pytest.mark.anyio
async def test_resource_language_function_finds_existing_name():
    """Имя из реестра: существование, категория и назначение."""
    text = await _resource_text("simintech://language/functions/abs")

    assert "abs" in text
    assert "категория:" in text
    assert "назначение:" in text


@pytest.mark.anyio
async def test_resource_language_function_missing_name_is_an_answer():
    """Несуществующее имя — ответ ресурса, а не исключение.

    Ресурс — не место для исключений: клиент по отказу чтения не поймёт,
    ошибся он именем или сломался сервер.
    """
    text = await _resource_text("simintech://language/functions/nosuchfunction")

    assert "нет в реестре" in text
    assert "ERROR" not in text
