"""Гейт пинов: git-зависимости должны разрешаться в origin.

Сеть здесь не трогается: проверяется разбор pyproject и то, как скрипт
реагирует на ответ git. Сам сетевой вызов гоняет CI-шаг.
"""

from __future__ import annotations

import subprocess

import pytest

from check_pins import CheckPinsError, git_pins, main, pin_reachable


def test_git_pins_parses_pinned_dependency(tmp_path):
    """Пин вида «name @ git+url@<sha>» разбирается на имя, url и ревизию."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  "mcp>=1.0.0",\n'
        '  "simintech-api @ git+https://github.com/o/r@' + "a" * 40 + '",\n'
        "]\n",
        encoding="utf-8",
    )

    assert git_pins(pyproject) == [
        ("simintech-api", "https://github.com/o/r", "a" * 40)
    ]


def test_git_pins_rejects_short_sha(tmp_path):
    """Не-40-символьная ревизия — не immutable-пин, и это отказ, а не пропуск."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\n'
        'dependencies = ["p @ git+https://github.com/o/r@v1.0"]\n',
        encoding="utf-8",
    )

    with pytest.raises(CheckPinsError, match="не коммит"):
        git_pins(pyproject)


def test_pin_reachable_reports_dead_sha():
    """Ответ git «not our ref» — недостижимый пин, а не исключение наружу."""
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, "", "fatal: remote error: upload-pack: not our ref " + "b" * 40
        )

    assert pin_reachable("https://github.com/o/r", "b" * 40, run=run) is False


def test_fetch_runs_in_initialized_repo():
    """Перед `fetch` каталог инициализируется как репозиторий.

    Вне репозитория git отвечает «not a git repository», и функция вернула бы
    False на любом пине — гейт стал бы вечно красным, а такой отключают.
    Дефект найден живым прогоном: подделка `run` его не видела, потому что
    подменяла действие целиком.
    """
    calls = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    pin_reachable("https://github.com/o/r", "d" * 40, run=run)

    assert calls[0][:2] == ["git", "init"]
    assert calls[1][:2] == ["git", "fetch"]
    assert calls[0][2] == "--quiet" and calls[1][2] == "--depth=1"


def test_main_fails_on_dead_pin(tmp_path, capsys):
    """Мёртвый пин — код возврата 1 и названный SHA в выводе."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\n'
        'dependencies = ["p @ git+https://github.com/o/r@' + "c" * 40 + '"]\n',
        encoding="utf-8",
    )

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "not our ref")

    assert main(pyproject, run=run) == 1
    assert "c" * 40 in capsys.readouterr().out
