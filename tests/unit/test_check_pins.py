"""Гейт пинов: git-зависимости должны разрешаться в origin.

Сеть здесь не трогается: проверяется разбор pyproject и то, как скрипт
реагирует на ответ git. Сам сетевой вызов гоняет CI-шаг.
"""

from __future__ import annotations

import subprocess

import pytest

from check_pins import (
    CheckPinsError,
    expected_shas,
    git_pins,
    main,
    pin_reachable,
    resolve_tag,
)


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


def test_git_pins_parses_release_tag(tmp_path):
    """Релизный тег `vX.Y.Z` — допустимая ревизия: `v*` прикрыт ruleset'ом."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  "simintech-api @ git+https://github.com/o/r@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )

    assert git_pins(pyproject) == [
        ("simintech-api", "https://github.com/o/r", "v0.10.1")
    ]


def test_git_pins_rejects_loose_tag(tmp_path):
    """Тег без патча (`v1.0`) — не релизный: формат строго `vX.Y.Z`."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\n'
        'dependencies = ["p @ git+https://github.com/o/r@v1.0"]\n',
        encoding="utf-8",
    )

    with pytest.raises(CheckPinsError, match="ни полный SHA"):
        git_pins(pyproject)


def test_git_pins_rejects_branch_ref(tmp_path):
    """Ветка в пине — отказ: её двигают штатной записью."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\n'
        'dependencies = ["p @ git+https://github.com/o/r@main"]\n',
        encoding="utf-8",
    )

    with pytest.raises(CheckPinsError, match="ни полный SHA"):
        git_pins(pyproject)


def test_resolve_tag_prefers_commit_of_annotated_tag():
    """Для аннотированного тега берётся коммит (`^{}`), а не tag-объект.

    Подделка моделирует переход: строку `^{}` git отдаёт, только если её
    спросили, — иначе фейк повторял бы удобный ответ, а не поведение git
    (урок «фейк не видит живое»).
    """
    def run(cmd, **kwargs):
        out = "a" * 40 + "\trefs/tags/v0.10.1\n"
        if any("^{}" in part for part in cmd):
            out += "b" * 40 + "\trefs/tags/v0.10.1^{}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    assert resolve_tag("https://github.com/o/r", "v0.10.1", run=run) == "b" * 40


def test_resolve_tag_reports_transport_failure():
    """Сбой ls-remote — не «тега нет»: исключение с названной причиной."""
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")

    with pytest.raises(CheckPinsError, match="не удалось спросить origin"):
        resolve_tag("https://github.com/o/r", "v0.10.1", run=run)


def test_resolve_tag_asks_for_peeled_ref():
    """В команде спрашиваются обе ссылки — иначе аннотированный тег не развернуть.

    Живой прогон 01.10.2026: `ls-remote` с одним паттерном отдаёт только
    tag-объект, без строки `^{}`; резолвер возвращал id обёртки, и гейт
    объявил «переставленным» только что поставленный тег. Исходная подделка
    повторяла ожидание кода, а не поведение git, — поэтому здесь проверяется
    сама команда.
    """
    calls = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    resolve_tag("https://github.com/o/r", "v0.10.1", run=run)

    assert calls, "ls-remote не вызывался"
    assert "refs/tags/v0.10.1^{}" in calls[0], calls[0]


def test_resolve_tag_returns_none_when_missing():
    """Пустой ответ ls-remote — выпуска нет, и это отказ, а не успех."""
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert resolve_tag("https://github.com/o/r", "v9.9.9", run=run) is None


def test_main_prints_resolution_for_tag_pin(tmp_path, capsys):
    """Успешный тег-пин печатает коммит и версию: адрес закреплённого кода."""
    url = "https://github.com/o/r"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  # sha: ' + "c" * 40 + "\n"
        f'  "p @ git+{url}@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )
    run = _tag_git_run({url: "c" * 40 + "\trefs/tags/v0.10.1\n"})

    assert main(pyproject, run=run) == 0
    assert ("v0.10.1 -> " + "c" * 12 + " (версия 0.10.1)"
            in capsys.readouterr().out)


def test_main_fails_on_missing_tag(tmp_path, capsys):
    """Отсутствующий тег — код 1 и названный тег в выводе."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  # sha: ' + "d" * 40 + "\n"
        '  "p @ git+https://github.com/o/r@v9.9.9",\n'
        "]\n",
        encoding="utf-8",
    )

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert main(pyproject, run=run) == 1
    assert "v9.9.9" in capsys.readouterr().out


def test_expected_shas_reads_anchor_above_dependency(tmp_path):
    """Якорь `# sha:` над строкой зависимости привязывается к её тегу.

    Хвост после коммита допускается — так выглядит стиль комментариев этого
    репозитория («— выпуск …»), и якорь не должен от него ломаться.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        "  # v0.10.1 — выпуск\n"
        "  # sha: " + "e" * 40 + " — коммит выпуска\n"
        '  "simintech-api @ git+https://github.com/o/r@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )

    assert expected_shas(pyproject) == {("simintech-api", "v0.10.1"): "e" * 40}


def test_expected_shas_keeps_pins_apart_by_name(tmp_path):
    """Одинаковые теги разных зависимостей не перекрывают друг друга."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        "  # sha: " + "a" * 40 + "\n"
        '  "p1 @ git+https://github.com/o/r1@v1.0.0",\n'
        "  # sha: " + "b" * 40 + "\n"
        '  "p2 @ git+https://github.com/o/r2@v1.0.0",\n'
        "]\n",
        encoding="utf-8",
    )

    assert expected_shas(pyproject) == {
        ("p1", "v1.0.0"): "a" * 40,
        ("p2", "v1.0.0"): "b" * 40,
    }


def _tag_git_run(by_url, *, version="0.10.1", fetch_rc=0):
    """Фейк git для тег-пути: ls-remote — из `by_url`, содержимое — `version`.

    `version=None` — `show` не отвечает (файл версии не прочитан);
    `fetch_rc` — код `git fetch`.
    """
    def run(cmd, **kwargs):
        parts = list(cmd)
        if "ls-remote" in parts:
            url = parts[parts.index("ls-remote") + 1]
            return subprocess.CompletedProcess(cmd, 0, by_url.get(url, ""), "")
        if "init" in parts:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "fetch" in parts:
            return subprocess.CompletedProcess(cmd, fetch_rc, "", "")
        if "show" in parts:
            if version is None:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(
                cmd, 0, f'__version__ = "{version}"\n', "")
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected command")

    return run


def test_main_rejects_tag_without_anchor(tmp_path, capsys):
    """Тег без якоря — отказ ДО сети: без якоря перестановка неотличима."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\n'
        'dependencies = ["p @ git+https://github.com/o/r@v0.10.1"]\n',
        encoding="utf-8",
    )

    called = []

    def run(cmd, **kwargs):
        called.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert main(pyproject, run=run) == 1
    assert "БЕЗ ЯКОРЯ" in capsys.readouterr().out
    assert called == [], "без якоря сеть не трогаем — отказ локальный"


def test_main_reports_anchor_mismatch(tmp_path, capsys):
    """Якорь не совпал с разрешением тега — отказ с обеими версиями."""
    url = "https://github.com/o/r"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        "  # sha: " + "f" * 40 + "\n"
        f'  "p @ git+{url}@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )
    run = _tag_git_run({url: "0" * 40 + "\trefs/tags/v0.10.1\n"})

    assert main(pyproject, run=run) == 1
    assert "ЯКОРЬ НЕ СОВПАЛ" in capsys.readouterr().out


def test_main_detects_tag_on_wrong_commit(tmp_path, capsys):
    """Тег разрешился в якорь, но версия в коммите — не имя тега.

    Это ловушка v0.2.0: якорь пишется с того же разрешения и подтвердит
    любой коммит — ловит только сверка содержимого.
    """
    url = "https://github.com/o/r"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  # sha: ' + "c" * 40 + "\n"
        f'  "p @ git+{url}@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )
    run = _tag_git_run({url: "c" * 40 + "\trefs/tags/v0.10.1\n"},
                       version="0.9.0")

    assert main(pyproject, run=run) == 1
    out = capsys.readouterr().out
    assert "НЕ НА ТОТ КОММИТ" in out
    assert "«0.9.0»" in out


def test_main_reports_unreadable_content(tmp_path, capsys):
    """Коммит тега не прочитан — «не проверен», а не «мёртвый» и не «OK»."""
    url = "https://github.com/o/r"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  # sha: ' + "c" * 40 + "\n"
        f'  "p @ git+{url}@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )
    run = _tag_git_run({url: "c" * 40 + "\trefs/tags/v0.10.1\n"},
                       version=None)

    assert main(pyproject, run=run) == 1
    out = capsys.readouterr().out
    assert "НЕ ПРОВЕРЕН ПО СОДЕРЖИМОМУ" in out
    assert "МЁРТВЫЙ" not in out


def test_main_reports_transport_failure(tmp_path, capsys):
    """Сбой ls-remote — «ПИН НЕ ПРОВЕРЕН» с причиной, а не «мёртвый тег»."""
    url = "https://github.com/o/r"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n'
        '  # sha: ' + "c" * 40 + "\n"
        f'  "p @ git+{url}@v0.10.1",\n'
        "]\n",
        encoding="utf-8",
    )

    def run(cmd, **kwargs):
        if "ls-remote" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert main(pyproject, run=run) == 1
    out = capsys.readouterr().out
    assert "ПИН НЕ ПРОВЕРЕН" in out
    assert "не удалось спросить origin" in out
    assert "МЁРТВЫЙ" not in out


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
