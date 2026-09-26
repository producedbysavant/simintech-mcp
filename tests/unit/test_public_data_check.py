"""Правила DLP-гейта: ловим приватное, не трогаем публичное.

Половина этих тестов — про то, что гейт **не** срабатывает: правило, которое
краснеет на собственной документации, отключают через неделю, и вместе с ним
исчезает настоящая проверка. Поэтому разрешённые образцы (публичные факты
вендора, шаблонные пути) закреплены тестами так же жёстко, как запрещённые.
"""

from __future__ import annotations

import subprocess

from public_data_check import Findings, main, scan_text, scan_tree


def test_private_ip_is_flagged():
    assert scan_text("адрес стенда 192.168.10.7", path="docs/x.md")


def test_public_vendor_path_is_allowed():
    assert not scan_text(r"поставка лежит в C:\SimInTech64\bin", path="README.md")


def test_real_user_profile_is_flagged():
    assert scan_text(r"C:\Users\ivanov\AppData\Local", path="x.md")


def test_placeholder_profile_is_allowed():
    assert not scan_text(r"C:\Users\<user>\AppData", path="x.md")
    assert not scan_text(r"C:\Users\Public\Documents", path="x.md")


def test_unc_path_is_flagged():
    assert scan_text(r"\\srv-files\share\project", path="x.md")


def test_vendor_help_domain_is_allowed():
    assert not scan_text("см. https://help.simintech.ru/", path="README.md")


def test_email_domain_allowlist():
    assert scan_text("пишите на ivan.petrov@corp-holding.ru", path="x.md")
    assert not scan_text("noreply@example.com", path="x.md")


def test_findings_report_lines():
    findings = Findings()
    findings.add("x.md", 3, "private-ip", "192.168.10.7")
    assert "x.md:3" in str(findings)


def test_text_path_is_not_flagged():
    """Текстового правила «путь к .prt — дамп» нет — и это решение.

    Замер на дереве `simintech-code`: 43 находки из 62, все — синтетические
    примеры в докстрингах и документации о форматах. Отличить пример от
    реального пути по тексту нечем, поэтому дампы ловит проверка **файлов**
    (см. `test_forbidden_file_in_tree`), а путь из чужого окружения —
    `user-profile` (см. `test_real_user_profile_is_flagged`).
    """
    assert not scan_text(r"модель лежит в D:\project\model.prt", path="x.md")


def test_forbidden_file_in_tree(tmp_path):
    (tmp_path / "model.prt").write_text("не модель, но расширение чужое",
                                        encoding="utf-8")
    assert scan_tree(tmp_path)


def test_vendor_fixture_directory_is_allowed(tmp_path):
    fixture = tmp_path / "tests" / "fixtures" / "vendor-public" / "sample.tbl"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("1 2 3", encoding="utf-8")
    assert not scan_tree(tmp_path)


def test_untracked_files_are_skipped(tmp_path):
    """Проверяется публикуемое: рабочие копии, не попавшие в git, — не забота гейта.

    Иначе шум давали бы `CLAUDE.md`, `.claude/` и `.remember/` — в них есть
    домашние пути, но в публикацию они не попадают.
    """
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("ок", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    (tmp_path / "local.md").write_text(
        r"каталог C:\Users\ivanov\AppData", encoding="utf-8")

    assert not scan_tree(tmp_path)


def test_named_pipe_is_not_unc():
    r"""`\\.\pipe\…` — пространство имён устройств, а не сетевой ресурс."""
    assert not scan_text(r"открывает \\.\pipe\svc", path="x.md")


def test_claude_local_filename_is_allowed():
    """`CLAUDE.local` — имя файла проекта, а не внутренний хост."""
    assert not scan_text("CLAUDE.local.md", path=".gitignore")


def test_allowlisted_zone_skips_rule():
    """В зоне, где правила определены, они не применяются."""
    unc = r"путь \\srv-files\share\project"
    assert not scan_text(unc, path="scripts/public_data_check.py")
    assert scan_text(unc, path="simintech_api/x.py")


def test_allowed_fragment_does_not_hide_other_findings():
    """Разрешённый образец гасит только себя, а не строку целиком.

    Иначе allowlist становится способом обойти гейт: достаточно упомянуть в
    строке публичный домен вендора, и приватный адрес рядом с ним не найдётся.
    """
    assert scan_text("см. https://help.simintech.ru, стенд 10.0.0.5", path="x.md")


def test_zone_prefix_requires_component_boundary():
    """`tests-evil/` не попадает под исключение `tests/`."""
    assert scan_text(r"путь \\srv-files\share\project", path="tests-evil/x.py")


def test_skip_dirs_are_relative_to_root(tmp_path):
    """Имя каталога ВЫШЕ корня не выключает проверку.

    Иначе репозиторий, лежащий внутри каталога `build` (а так бывает и в CI),
    сканировался бы вхолостую — и молча.
    """
    root = tmp_path / "build" / "repo"
    root.mkdir(parents=True)
    (root / "x.md").write_text("стенд 10.0.0.5", encoding="utf-8")

    assert scan_tree(root)


def test_empty_tree_is_not_clean(tmp_path, capsys):
    """Пустой результат ≠ чистый результат: «не проверено» — тоже отказ."""
    assert main(tmp_path) == 1
    assert "не состоялась" in capsys.readouterr().out
