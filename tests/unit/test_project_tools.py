"""Жизненный цикл проекта: создание, расчёт, сохранение."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.server import mcp

from simintech_mcp import session
from simintech_mcp.tools import project as project_tools

from _support import (
    _OwnedClientStub,
    _SavableProject,
    _TemplateProject,
    _WireProject,
    _error,
    _install_fake_template,
    _install_savable,
    _text,
)


@pytest.mark.anyio
async def test_status_on_linux():
    """status() на Linux сообщает о несовместимости платформы."""
    if sys.platform == "win32":
        pytest.skip("Тест для не-Windows окружения")
    result = await mcp.call_tool("status", {})
    text = result[0].text if isinstance(result, (list, tuple)) else str(result)
    assert "Windows" in text


@pytest.mark.anyio
async def test_create_project_uses_template_and_sets_end_time(monkeypatch):
    """create_project идёт через шаблон и применяет end_time."""

    project, opened = _install_fake_template(monkeypatch)

    text = _text(await mcp.call_tool("create_project", {"end_time": 2.5}))

    assert opened == [project], "проект должен создаваться из шаблона"
    assert project.end_time == 2.5
    assert "2.5 с" in text
    assert session.current_project() is project


@pytest.mark.anyio
async def test_create_project_rejects_non_positive_end_time(monkeypatch):
    """Неверное время отвергается ДО открытия шаблона.

    Иначе созданный проект остался бы висеть в mmain.exe: он не попал бы ни в
    `_project`, ни в закрытие.
    """
    _project_obj, opened = _install_fake_template(monkeypatch)

    text = await _error("create_project", {"end_time": 0})

    assert "положительным" in text
    assert opened == [], "шаблон открывать было нельзя"


@pytest.mark.anyio
async def test_open_project_names_file_and_reports_switch(monkeypatch, tmp_path):
    """open_project называет файл и смену проекта (issue #18).

    Раньше ответ был «Проект открыт (id=17)»: агент не видел, какой файл
    стал текущим и на какой сменён, — на этом и разошлись образец и правка.
    """

    # Путь существует по-настоящему: open_project проверяет его до COM
    # (см. test_open_project_refuses_missing_file_before_com).
    target = tmp_path / "sub_TractionState.prt"
    target.write_text("", encoding="utf-8")
    opened = _WireProject({}, project_id=17)
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(lambda client, path: opened))
    monkeypatch.setattr(session, "ensure_client", lambda: object())
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(_WireProject({}, project_id=12),
                        source_path=r"C:\a\CoolInt.prt")
    try:
        text = _text(await mcp.call_tool(
            "open_project", {"path": str(target)}))

        assert "Проект открыт: «sub_TractionState.prt» (id=17)" in text
        assert "СМЕНИЛСЯ" in text
        assert "было «CoolInt.prt» (id=12)" in text
        assert session.current_project() is opened
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_open_project_refuses_missing_file_before_com(
        monkeypatch, tmp_path):
    """Несуществующий путь — отказ до COM (живой замер 03.10.2026).

    `OpenProject` на таком пути возвращает ненулевой id: среда показывает
    модальное «Cannot open file…», но наружу это не выходит —
    `GetOpenedFileName` открытого «проекта» возвращает тот же путь. Без
    предпроверки инструмент отчитался бы «Проект открыт», и агент работал
    бы с пустым проектом-фантомом.
    """

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("COM трогать нельзя: файла нет")

    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(_must_not_be_called))
    monkeypatch.setattr(session, "ensure_client", _must_not_be_called)

    text = await _error(
        "open_project", {"path": str(tmp_path / "nope.prt")})

    assert "не найден" in text


@pytest.mark.anyio
async def test_open_project_requires_absolute_path(monkeypatch):
    """Относительный путь — отказ до COM (находка ревью).

    Предпроверка раскрыла бы его от рабочего каталога процесса сервера, а
    среда — от своего; на этом они расходятся, и проверка либо пропустила бы
    чужой файл, либо отказала бы там, где среда открыла бы.
    """

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("COM трогать нельзя: путь не абсолютный")

    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(_must_not_be_called))
    monkeypatch.setattr(session, "ensure_client", _must_not_be_called)

    text = await _error("open_project", {"path": "model.prt"})

    assert "абсолют" in text


@pytest.mark.anyio
async def test_open_project_resolves_path_before_com(monkeypatch, tmp_path):
    """В COM уходит полностью определённый путь (хвост находки ревью).

    Windows-путь без диска (`\\foo\\m.prt`) абсолютен, но каждый процесс
    раскрывает его от своего текущего диска — та же расходимость, что у
    относительных путей. Путь нормализуется до проверки и подачи в COM:
    что проверили, то и откроется.
    """
    (tmp_path / "sub").mkdir()
    target = tmp_path / "model.prt"
    target.write_text("", encoding="utf-8")
    tricky = tmp_path / "sub" / ".." / "model.prt"
    opened = _WireProject({}, project_id=5)
    seen = {}

    def fake_open(client, path):
        seen["path"] = path
        return opened

    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(fake_open))
    monkeypatch.setattr(session, "ensure_client", lambda: object())
    prev_project, prev_path = session.current_project(), session._project_path
    try:
        _text(await mcp.call_tool("open_project", {"path": str(tricky)}))

        assert seen["path"] == str(target)
        assert session.current_project() is opened
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_mutating_tool_marks_unsaved(monkeypatch):
    """Мутирующий инструмент помечает несохранённые правки (issue #18).

    Счёт читает `reload_project` («правки отброшены» против «перечитан тот
    же файл»); ставит его обвязка — одна точка на все мутирующие инструменты
    (`runtime._append_mutation_note`): заведи счёт инструменты сами, новый
    мутирующий выпал бы из него молча.
    """
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(_TemplateProject())
    try:
        assert not session.unsaved_changes()

        _text(await mcp.call_tool("set_calc_time", {"seconds": 1.0}))

        assert session.unsaved_changes()
    finally:
        session.set_project(prev_project, source_path=prev_path)


# ─── reload_project (issue #18, п.3) ──────────────────────────────


def _install_reload(monkeypatch, tmp_path):
    """Общая обвязка reload-тестов: файл на диске, прежний проект, фейк open."""

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("Project.open трогать нельзя")

    target = tmp_path / "CoolInt.prt"
    target.write_text("x", encoding="utf-8")
    opened = _WireProject({}, project_id=17)
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(lambda client, path: opened))
    monkeypatch.setattr(session, "ensure_client", lambda: object())
    previous = _WireProject({}, project_id=12)
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(previous, source_path=str(target))
    return target, previous, opened, prev_project, prev_path, \
        _must_not_be_called


@pytest.mark.anyio
async def test_reload_project_without_project_names_the_way_out(monkeypatch):
    """Проекта нет — отказ называет причину и ведёт к `open_project`.

    Живой случай 04.10.2026: после перезапуска SimInTech сессия отвечала
    «подключён», а reload звал к `create_project` — хотя файл проекта есть, и
    правильный выход другой: открыть его заново. Причина «проект не переживает
    смену инстанса» обязана быть названа, иначе отказ читается как «проекта не
    бывало».
    """
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(None)
    try:
        text = await _error("reload_project", {})

        assert "откатывать нечего" in text
        assert "open_project" in text
        assert "create_project" not in text
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_reload_project_reopens_from_file(monkeypatch, tmp_path):
    """reload_project закрывает текущий экземпляр и открывает файл заново.

    Прежний экземпляр закрывается **без сохранения** — правки, не записанные
    `save_project`, отбрасываются (issue #18: откат того, что раньше делали
    вручную переоткрытием).
    """
    target, previous, opened, prev_project, prev_path, _ = \
        _install_reload(monkeypatch, tmp_path)
    try:
        text = _text(await mcp.call_tool("reload_project", {}))

        assert "ПЕРЕОТКРЫТ ИЗ ФАЙЛА" in text
        assert "было «CoolInt.prt» (id=12)" in text
        assert "стало «CoolInt.prt» (id=17)" in text
        assert "Несохранённых правок не было" in text
        assert previous.closed, "прежний экземпляр закрыт"
        assert session.current_project() is opened
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_reload_project_says_when_edits_were_dropped(monkeypatch,
                                                           tmp_path):
    """Есть несохранённые правки — ответ называет их отброшенными.

    Счёт ведёт сессия: правку помечают мутирующие инструменты, снимает
    `save_project`; без счёта сообщение было бы ложью в одну из сторон.
    """
    target, previous, opened, prev_project, prev_path, _ = \
        _install_reload(monkeypatch, tmp_path)
    session.mark_mutated()
    try:
        text = _text(await mcp.call_tool("reload_project", {}))

        assert "Несохранённые правки прежнего экземпляра отброшены." in text
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_reload_project_does_not_claim_drop_on_same_id(monkeypatch,
                                                             tmp_path):
    """Совпал COM id — «правки отброшены» не утверждаем (гвард не закрыл прежний).

    Находка ревью 04.10.2026: при совпадении id `replace_project` пропускает
    закрытие прежнего экземпляра, и хвост «отброшены» был бы ложью — состояние
    прежнего объекта не измерено. Обычный путь (id разошлись) проверен живым
    замером: правки действительно откатываются.
    """
    target = tmp_path / "CoolInt.prt"
    target.write_text("x", encoding="utf-8")
    previous = _WireProject({}, project_id=7)
    opened = _WireProject({}, project_id=7)        # тот же id — случай гварда
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(lambda client, path: opened))
    monkeypatch.setattr(session, "ensure_client", lambda: object())
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(previous, source_path=str(target))
    session.mark_mutated()
    try:
        text = _text(await mcp.call_tool("reload_project", {}))

        assert "тот же id" in text
        assert "откат правок не подтверждён" in text
        assert "правки прежнего экземпляра отброшены" not in text, \
            "ложное «отброшены» при несостоявшемся закрытии"
    finally:
        session.set_project(prev_project, source_path=prev_path)


class _StuckCloseProject(_WireProject):
    """Прежний экземпляр, закрытие которого срывается (среда не отпустила)."""

    def close(self) -> None:
        raise RuntimeError("среда занята")


@pytest.mark.anyio
async def test_reload_project_does_not_claim_drop_when_close_failed(
        monkeypatch, tmp_path):
    """Закрытие прежнего сорвалось — «отброшены» не утверждаем.

    Находка ревью 04.10.2026: при срыве `CloseProject` прежний экземпляр
    остаётся жить с несохранёнными правками, и «отброшены» противоречило бы
    предупреждению `replace_project` в том же ответе (same-id случай —
    соседний тест; здесь id разные, срывается само закрытие).
    """
    target, previous, opened, prev_project, prev_path, _ = \
        _install_reload(monkeypatch, tmp_path)
    session.set_project(_StuckCloseProject({}, project_id=12),
                        source_path=str(target))
    session.mark_mutated()
    try:
        text = _text(await mcp.call_tool("reload_project", {}))

        assert "закрыть не удалось" in text, "предупреждение обязано быть"
        assert "прежнего экземпляра не отброшены" in text
        assert "прежнего экземпляра отброшены" not in text, \
            "ложное «отброшены» при сорвавшемся закрытии"
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_reload_project_refuses_template_project(monkeypatch, tmp_path):
    """Проект из шаблона — отказ: файла нет, переоткрывать нечего."""
    target, previous, opened, prev_project, prev_path, must_not = \
        _install_reload(monkeypatch, tmp_path)
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(must_not))
    session.set_project(previous)  # source_path=None — проект из шаблона
    try:
        text = await _error("reload_project", {})

        assert "из шаблона" in text
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_reload_project_refuses_pack_member(monkeypatch, tmp_path):
    """Участник пакета — отказ: закрытие исключило бы его из состава."""
    target, previous, opened, prev_project, prev_path, must_not = \
        _install_reload(monkeypatch, tmp_path)
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(must_not))
    monkeypatch.setattr(session, "pack_membership", lambda: True)
    try:
        text = await _error("reload_project", {})

        assert "участник пакета" in text
        assert "open_pack" in text
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_reload_project_refuses_missing_file(monkeypatch, tmp_path):
    """Файл проекта исчез после открытия — откатывать не к чему."""
    target, previous, opened, prev_project, prev_path, must_not = \
        _install_reload(monkeypatch, tmp_path)
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(must_not))
    session.set_project(previous, source_path=str(tmp_path / "уехал.prt"))
    try:
        text = await _error("reload_project", {})

        assert "не найден" in text
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_open_project_directory_gets_hint(tmp_path):
    """Каталог вместо файла — отказ с подсказкой.

    Живой случай 03.10.2026: у демо поставки проект лежит в **одноимённом
    каталоге**, и путь без хвоста «\\Имя.prt» выглядит как файл — среда
    отвечает модальной ошибкой, а по COM промах неотличим (см. тест выше).
    """

    text = await _error("open_project", {"path": str(tmp_path)})

    assert "каталог" in text


@pytest.mark.anyio
async def test_create_project_reports_switch_from_previous(monkeypatch):
    """Создание поверх открытого проекта называет смену (issue #18)."""

    _project_obj, _opened = _install_fake_template(monkeypatch)
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(_WireProject({}, project_id=12),
                        source_path=r"C:\a\CoolInt.prt")
    try:
        text = _text(await mcp.call_tool("create_project", {}))

        assert "СМЕНИЛСЯ" in text
        assert "было «CoolInt.prt» (id=12)" in text
        assert "стало «проект из шаблона» (id=5)" in text
    finally:
        session.set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_set_calc_time_delegates_to_project(monkeypatch):
    """set_calc_time передаёт значение в проект."""

    project = _TemplateProject()
    monkeypatch.setattr(session, "_project", project)

    text = _text(await mcp.call_tool("set_calc_time", {"seconds": 7.0}))

    assert project.end_time == 7.0
    assert "7.0 с" in text


def _temp_of(target) -> str:
    """Временный путь записи, каким его строит инструмент (`<имя>.tmp.<ext>`)."""
    stem, ext = os.path.splitext(str(target))
    return f"{stem}.tmp{ext}"


@pytest.mark.anyio
async def test_save_project_defaults_to_xml(monkeypatch, tmp_path):
    """По умолчанию сохраняется XML, и перед записью показывается форма.

    Без показа формы в файл уходит признак «окно скрыто», и GUI открывает
    проект, не показывая окно модели. Запись идёт во временный файл рядом и
    заменяет целевой атомарно (`os.replace`): обрыв в момент записи боевой
    файл не трогает (репорт 08.10.2026 — 0-байтный проект после сбоя).
    """
    target = tmp_path / "m.xprt"
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool("save_project", {"path": str(target)}))

    assert project.calls == [("show_form", None), ("xml", _temp_of(target))], \
        "среда обязана писать временный файл, а не целевой"
    assert "XML" in text
    assert "Форма проекта показана" in text
    assert target.read_bytes() == b"<stub/>", "целевой файл не получил запись"
    assert not os.path.exists(_temp_of(target)), "временный хвост остался"


@pytest.mark.anyio
async def test_save_project_binary_flag_writes_prt(monkeypatch, tmp_path):
    """binary=True пишет нативный .prt — его открывает GUI SimInTech."""
    target = tmp_path / "m.prt"
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": str(target), "binary": True}))

    assert project.calls == [("show_form", None), ("binary", _temp_of(target))]
    assert ".prt" in text
    assert target.read_bytes() == b"<stub/>"


@pytest.mark.anyio
async def test_save_project_can_skip_showing_form(monkeypatch, tmp_path):
    """show_form=False — безоконное сохранение: форму не показываем."""
    target = tmp_path / "m.prt"
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": str(target), "binary": True,
                         "show_form": False}))

    assert project.calls == [("binary", _temp_of(target))]
    assert "Форму не показывали" in text


@pytest.mark.anyio
async def test_save_project_failure_is_error(monkeypatch):
    """Неудачная запись — отказ, а не ответ «проект сохранён»."""
    _install_savable(monkeypatch, raises=True)

    text = await _error("save_project", {"path": r"C:\m.prt", "binary": True})

    assert "диск переполнен" in text


@pytest.mark.anyio
async def test_save_project_failure_keeps_target_intact(monkeypatch, tmp_path):
    """Сбой записи не трогает боевой файл — ради этого запись идёт через tmp.

    Репорт 08.10.2026: сбой в момент записи оставлял целевой .prt нулевым
    (среда открывала файл и не наполняла). Теперь среда пишет временный
    файл, и целевой при сбое остаётся прежним.
    """
    target = tmp_path / "m.xprt"
    target.write_bytes(b"<old/>")
    _install_savable(monkeypatch, raises=True)

    text = await _error("save_project", {"path": str(target)})

    assert "диск переполнен" in text
    assert target.read_bytes() == b"<old/>", "боевой файл тронут при сбое"
    assert not os.path.exists(_temp_of(target)), "временный хвост остался"


@pytest.mark.anyio
async def test_save_project_refuses_zero_byte_write(monkeypatch, tmp_path):
    """Файл нулевой длины — отказ, а не «сохранено» (оборванная запись).

    Прежняя проверка считала успехом любое расхождение размера — в том числе
    обрезание до нуля; репорт 08.10.2026 показал, что так выглядит оборванная
    запись, и отвечать на неё «сохранено» нельзя.
    """
    class _ZeroByte(_SavableProject):
        def save_xml(self, path: str) -> None:
            self.calls.append(("xml", path))
            Path(path).write_bytes(b"")

    target = tmp_path / "m.xprt"
    monkeypatch.setattr(session, "_project", _ZeroByte())

    text = await _error("save_project", {"path": str(target)})

    assert "нулевой длины" in text
    assert not target.exists(), "нулевой файл заменой проходить не должен"
    assert not os.path.exists(_temp_of(target))


@pytest.mark.anyio
async def test_save_project_keeps_temp_when_replace_fails(monkeypatch,
                                                          tmp_path):
    """Замена не прошла (целевой занят?) — записанное не теряется.

    Отказ называет временный файл, где лежит результат: целевой не тронут,
    а записанное можно спасти руками.
    """
    target = tmp_path / "m.xprt"
    target.write_bytes(b"<old/>")
    _install_savable(monkeypatch)

    def _boom(src, dst):
        raise OSError(13, "файл занят другой программой")

    monkeypatch.setattr(project_tools.os, "replace", _boom)

    text = await _error("save_project", {"path": str(target)})

    assert "заменить" in text
    tmp = _temp_of(target)
    assert os.path.exists(tmp), "записанный временный файл потерян"
    assert Path(tmp).read_bytes() == b"<stub/>"
    assert target.read_bytes() == b"<old/>", "целевой файл тронут"


@pytest.mark.anyio
async def test_save_project_refuses_silent_missing_file(monkeypatch, tmp_path):
    """«Успех без файла» — отказ с диагнозом залипшей сессии (code#21).

    Живой симптом: `SaveProjectXML` сообщает об успехе, файла нет; следом
    `CloseProject` падает с Access violation. Отчитаться «сохранено» здесь —
    соврать клиенту, поэтому запись проверяется по диску. Запись идёт во
    временный файл, поэтому отказ ещё и свидетельствует: целевой не тронут.
    """
    target = tmp_path / "m.xprt"
    _install_savable(monkeypatch, writes=False)

    text = await _error("save_project", {"path": str(target)})

    assert "не записан" in text
    assert "не появился" in text
    assert "#21" in text
    assert "disconnect" in text
    assert "не тронут" in text, "отказ обязан сказать, что целевой цел"
    assert not target.exists()


@pytest.mark.anyio
async def test_save_project_refuses_untouched_existing_file(
        monkeypatch, tmp_path):
    """Прежний боевой файл за результат записи не принимается.

    При залипании не создаётся и временный файл — отказ; целевой при этом
    остаётся прежним.
    """
    target = tmp_path / "m.xprt"
    target.write_bytes(b"<old/>")
    _install_savable(monkeypatch, writes=False)

    text = await _error("save_project", {"path": str(target)})

    assert "не появился" in text
    assert "#21" in text
    assert target.read_bytes() == b"<old/>"


@pytest.mark.anyio
@pytest.mark.parametrize("name,binary,distinct", [
    ("m.prt", False, "сохраняем XML"),
    ("m.xprt", True, "сохраняем нативный"),
    # Win32 сам отбрасывает хвостовые точки/пробелы последнего компонента:
    # «m.prt » на диске — это «m.prt», и проверка по сырой строке такое
    # имя пропускала.
    ("m.prt ", False, "сохраняем XML"),
    ("m.xprt.", True, "сохраняем нативный"),
])
async def test_save_project_refuses_format_extension_mismatch(
        monkeypatch, tmp_path, name, binary, distinct):
    """Имя обещает не тот формат, которым пишем, — отказ до всякой работы.

    Среда выбирает формат по расширению: XML, записанный в файл `.prt`, GUI
    показал «Ошибка загрузки страницы проекта: data error» (живой случай
    02.10.2026 — тот же текст под именем `.xprt` открылся). Проверка обязана
    стоять до COM-вызовов: ни формы, ни записи.

    `distinct` — фрагмент, которым направления отказа отличаются: общие
    «.xprt»/«.prt» есть в обоих текстах, и перепутанные ветки на них не
    видны.
    """
    project = _install_savable(monkeypatch)

    text = await _error("save_project",
                        {"path": str(tmp_path / name), "binary": binary})

    assert distinct in text, "отказ обязан называть формат, которым пишем"
    assert ".xprt" in text and ".prt" in text
    assert project.calls == [], "проверка формата прошла после COM-работы"


class _CoarseTimestampProject(_SavableProject):
    """Запись действительна, но ФС с грубым временем: метка правки не встала.

    FAT хранит время с точностью 2 с, сетевые shares — 1 с: перезапись в ту
    же секунду не меняет ни метку, ни размер (содержимое записывается той же
    длины). Подделка моделирует ровно этот переход: файл **перезаписан
    другим содержимым**, метка восстановлена на прежнюю.
    """

    def save_xml(self, path: str) -> None:
        # Порядок как у базовой подделки (`_record`): запись в журнал →
        # ручка raises → ручка writes → файл. Переопределение не смеет
        # оставлять ручки молча нерабочими (находка ревью).
        self.calls.append(("xml", path))
        if self._raises:
            raise RuntimeError("диск переполнен")
        if not self._writes:
            return
        old = os.stat(path)
        Path(path).write_bytes(b"<new!>")
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))


class _IdenticalRewriteProject(_SavableProject):
    """Перезапись идентичным содержимым с той же меткой (грубая ФС)."""

    def save_xml(self, path: str) -> None:
        self.calls.append(("xml", path))
        if self._raises:
            raise RuntimeError("диск переполнен")
        if not self._writes:
            return
        old = os.stat(path)
        raw = Path(path).read_bytes()
        Path(path).write_bytes(raw)
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))


@pytest.mark.anyio
async def test_save_project_accepts_coarse_timestamp_fs(monkeypatch, tmp_path):
    """ФС с грубым временем: перезапись в ту же секунду — не «не записано».

    У FAT (2 с) и сетевых shares (1 с) успешная запись может не изменить
    метку времени; размер тоже совпадает (содержимое той же длины), и
    прежняя проверка по метке ложно отказывала бы в успехе (замечание
    ревью mcp#26). Содержимое при этом другое — вердикт «записано» по нему
    верен. Сценарий с хвостом временного файла (от прошлого сбоя) — тот, где
    сверка доходит до содержимого.
    """
    target = tmp_path / "m.xprt"
    Path(_temp_of(target)).write_bytes(b"<old/>")
    monkeypatch.setattr(session, "_project", _CoarseTimestampProject())

    text = _text(await mcp.call_tool("save_project", {"path": str(target)}))

    assert "сохранён" in text
    assert target.read_bytes() == b"<new!>"


@pytest.mark.anyio
async def test_save_project_refuses_identical_rewrite_on_coarse_fs(
        monkeypatch, tmp_path):
    """Идентичная перезапись с той же меткой — отказ: от залипания неотличима.

    Остаток проверки назван в докстринге честно: если ни метка, ни размер,
    ни содержимое не разошлись, отличить состоявшуюся запись от залипшей
    сессии нечем — и «сохранено» здесь было бы догадкой.
    """
    target = tmp_path / "m.xprt"
    Path(_temp_of(target)).write_bytes(b"<same>")
    monkeypatch.setattr(session, "_project", _IdenticalRewriteProject())

    text = await _error("save_project", {"path": str(target)})

    assert "не обнов" in text
    assert "#21" in text
    assert not target.exists(), "отказ не имеет права заменять целевой"


def test_status_refuses_when_com_unavailable(monkeypatch):
    """`status` отказывает, если подключиться не удалось.

    Текстом это выглядело бы успехом для клиента, доверяющего `isError`.
    """

    monkeypatch.setattr(sys, "platform", "win32")

    def unavailable():
        raise RuntimeError("COM не зарегистрирован")

    monkeypatch.setattr(session, "ensure_client", unavailable)

    with pytest.raises(ToolError, match="недоступен"):
        project_tools.status()


def test_status_names_ownership(monkeypatch):
    """`status` называет владение сессией: подключение только к своему.

    Гейт владения пропускает лишь OWNED-процессы (`session._require_owned`),
    и `status` это подтверждает явно — клиенту видно, что сервер работает со
    своим экземпляром, а не с чужим.
    """

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(session, "ensure_client", lambda: _OwnedClientStub())

    text = project_tools.status()

    assert "PID=4242" in text
    assert "ownership=owned" in text
