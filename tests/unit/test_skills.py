"""Скиллы: поиск каталога и сообщения об ошибках."""

from __future__ import annotations

from simintech_mcp import skills


def test_skills_root_finds_sibling_checkout(monkeypatch, tmp_path):
    """Без переменной каталог скиллов ищется рядом с репозиторием.

    Проверяется подменой `__file__`: иначе тест зависел бы от того, лежит ли
    рядом настоящий checkout `simintech-skill` (из worktree его не видно, из
    основной копии — видно), и вёл бы себя по-разному в двух местах.
    """

    monkeypatch.delenv(skills.SKILLS_DIR_ENV, raising=False)
    package = tmp_path / "simintech-mcp" / "simintech_mcp"
    package.mkdir(parents=True)
    catalog = tmp_path / "simintech-skill" / "skills-catalog"
    catalog.mkdir(parents=True)
    monkeypatch.setattr(skills, "__file__", str(package / "server.py"))

    assert skills.skills_root() == str(catalog)


def test_skills_missing_message_without_env(monkeypatch):
    """Без переменной подсказка называет саму переменную, а не её значение."""

    monkeypatch.delenv(skills.SKILLS_DIR_ENV, raising=False)

    text = skills._skills_missing_message()

    assert skills.SKILLS_DIR_ENV in text
    assert "Задайте каталог" in text
