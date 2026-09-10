"""Тесты каталога скиллов: структура, frontmatter, согласованность имён."""

import os
import pathlib
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

CATALOG = pathlib.Path(__file__).resolve().parents[2] / "skills-catalog"


def _skill_dirs():
    if not CATALOG.is_dir():
        return []
    return sorted(p for p in CATALOG.iterdir() if p.is_dir())


def _read_frontmatter(path: pathlib.Path) -> dict:
    """Разобрать YAML-frontmatter между первыми `---` файла."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    result = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def test_catalog_dir_exists():
    assert CATALOG.is_dir(), f"нет каталога скиллов: {CATALOG}"


def test_catalog_readme_exists():
    assert (CATALOG / "README.md").is_file()


@pytest.mark.parametrize("skill", _skill_dirs(), ids=lambda p: p.name)
def test_skill_has_required_files(skill):
    """В каждом скилле есть SKILL.md и manifest.yaml."""
    assert (skill / "SKILL.md").is_file(), f"{skill.name}: нет SKILL.md"
    assert (skill / "manifest.yaml").is_file(), f"{skill.name}: нет manifest.yaml"


@pytest.mark.parametrize("skill", _skill_dirs(), ids=lambda p: p.name)
def test_skill_frontmatter_matches_dirname(skill):
    """`name` во frontmatter совпадает с именем каталога."""
    front = _read_frontmatter(skill / "SKILL.md")

    assert front.get("name") == skill.name
    assert front.get("description"), f"{skill.name}: пустое описание"


@pytest.mark.parametrize("skill", _skill_dirs(), ids=lambda p: p.name)
def test_manifest_name_matches_dirname(skill):
    """`name` в manifest.yaml совпадает с именем каталога."""
    yaml = pytest.importorskip("yaml")

    data = yaml.safe_load((skill / "manifest.yaml").read_text(encoding="utf-8"))

    assert data.get("name") == skill.name
    assert data.get("source_of_truth"), f"{skill.name}: не указан источник"


@pytest.mark.parametrize("skill", _skill_dirs(), ids=lambda p: p.name)
def test_skill_declares_verification_status(skill):
    """У каждого скилла явно указано, проверен ли он."""
    yaml = pytest.importorskip("yaml")

    data = yaml.safe_load((skill / "manifest.yaml").read_text(encoding="utf-8"))

    assert "verified" in data, f"{skill.name}: не указан статус проверки"
