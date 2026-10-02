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
    assert session._project is project


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
async def test_open_project_names_file_and_reports_switch(monkeypatch):
    """open_project называет файл и смену проекта (issue #18).

    Раньше ответ был «Проект открыт (id=17)»: агент не видел, какой файл
    стал текущим и на какой сменён, — на этом и разошлись образец и правка.
    """

    opened = _WireProject({}, project_id=17)
    monkeypatch.setattr(project_tools.Project, "open",
                        staticmethod(lambda client, path: opened))
    monkeypatch.setattr(session, "_ensure_client", lambda: object())
    prev_project, prev_path = session._project, session._project_path
    session._set_project(_WireProject({}, project_id=12),
                         source_path=r"C:\a\CoolInt.prt")
    try:
        text = _text(await mcp.call_tool(
            "open_project", {"path": r"C:\b\sub_TractionState.prt"}))

        assert "Проект открыт: «sub_TractionState.prt» (id=17)" in text
        assert "СМЕНИЛСЯ" in text
        assert "было «CoolInt.prt» (id=12)" in text
        assert session._project is opened
    finally:
        session._set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_create_project_reports_switch_from_previous(monkeypatch):
    """Создание поверх открытого проекта называет смену (issue #18)."""

    _project_obj, _opened = _install_fake_template(monkeypatch)
    prev_project, prev_path = session._project, session._project_path
    session._set_project(_WireProject({}, project_id=12),
                         source_path=r"C:\a\CoolInt.prt")
    try:
        text = _text(await mcp.call_tool("create_project", {}))

        assert "СМЕНИЛСЯ" in text
        assert "было «CoolInt.prt» (id=12)" in text
        assert "стало «проект из шаблона» (id=5)" in text
    finally:
        session._set_project(prev_project, source_path=prev_path)


@pytest.mark.anyio
async def test_set_calc_time_delegates_to_project(monkeypatch):
    """set_calc_time передаёт значение в проект."""

    project = _TemplateProject()
    monkeypatch.setattr(session, "_project", project)

    text = _text(await mcp.call_tool("set_calc_time", {"seconds": 7.0}))

    assert project.end_time == 7.0
    assert "7.0 с" in text


@pytest.mark.anyio
async def test_save_project_defaults_to_xml(monkeypatch, tmp_path):
    """По умолчанию сохраняется XML, и перед записью показывается форма.

    Без показа формы в файл уходит признак «окно скрыто», и GUI открывает
    проект, не показывая окно модели.
    """
    target = str(tmp_path / "m.xprt")
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool("save_project", {"path": target}))

    assert project.calls == [("show_form", None), ("xml", target)]
    assert "XML" in text
    assert "Форма проекта показана" in text


@pytest.mark.anyio
async def test_save_project_binary_flag_writes_prt(monkeypatch, tmp_path):
    """binary=True пишет нативный .prt — его открывает GUI SimInTech."""
    target = str(tmp_path / "m.prt")
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": target, "binary": True}))

    assert project.calls == [("show_form", None), ("binary", target)]
    assert ".prt" in text


@pytest.mark.anyio
async def test_save_project_can_skip_showing_form(monkeypatch, tmp_path):
    """show_form=False — безоконное сохранение: форму не показываем."""
    target = str(tmp_path / "m.prt")
    project = _install_savable(monkeypatch)

    text = _text(await mcp.call_tool(
        "save_project", {"path": target, "binary": True,
                         "show_form": False}))

    assert project.calls == [("binary", target)]
    assert "Форму не показывали" in text


@pytest.mark.anyio
async def test_save_project_failure_is_error(monkeypatch):
    """Неудачная запись — отказ, а не ответ «проект сохранён»."""
    _install_savable(monkeypatch, raises=True)

    text = await _error("save_project", {"path": r"C:\m.prt", "binary": True})

    assert "диск переполнен" in text


@pytest.mark.anyio
async def test_save_project_refuses_silent_missing_file(monkeypatch, tmp_path):
    """«Успех без файла» — отказ с диагнозом залипшей сессии (code#21).

    Живой симптом: `SaveProjectXML` сообщает об успехе, файла нет; следом
    `CloseProject` падает с Access violation. Отчитаться «сохранено» здесь —
    соврать клиенту, поэтому запись проверяется по диску.
    """
    target = str(tmp_path / "m.xprt")
    _install_savable(monkeypatch, writes=False)

    text = await _error("save_project", {"path": target})

    assert "не записан" in text
    assert "не появился" in text
    assert "#21" in text
    assert "disconnect" in text


@pytest.mark.anyio
async def test_save_project_refuses_untouched_existing_file(
        monkeypatch, tmp_path):
    """Прежний файл за результат записи не принимается.

    При залипании сохранение поверх существующего файла оставляет старый —
    и проверка «файл есть» была бы ложным успехом; поэтому сверяется и время
    правки.
    """
    target = tmp_path / "m.xprt"
    target.write_bytes(b"<old/>")
    _install_savable(monkeypatch, writes=False)

    text = await _error("save_project", {"path": str(target)})

    assert "не обнов" in text  # «не обновился»
    assert "#21" in text


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
    верен.
    """
    target = tmp_path / "m.xprt"
    target.write_bytes(b"<old/>")
    monkeypatch.setattr(session, "_project", _CoarseTimestampProject())

    text = _text(await mcp.call_tool("save_project", {"path": str(target)}))

    assert "сохранён" in text


@pytest.mark.anyio
async def test_save_project_refuses_identical_rewrite_on_coarse_fs(
        monkeypatch, tmp_path):
    """Идентичная перезапись с той же меткой — отказ: от залипания неотличима.

    Остаток проверки назван в докстринге честно: если ни метка, ни размер,
    ни содержимое не разошлись, отличить состоявшуюся запись от залипшей
    сессии нечем — и «сохранено» здесь было бы догадкой.
    """
    target = tmp_path / "m.xprt"
    target.write_bytes(b"<same>")
    monkeypatch.setattr(session, "_project", _IdenticalRewriteProject())

    text = await _error("save_project", {"path": str(target)})

    assert "не обнов" in text
    assert "#21" in text


def test_status_refuses_when_com_unavailable(monkeypatch):
    """`status` отказывает, если подключиться не удалось.

    Текстом это выглядело бы успехом для клиента, доверяющего `isError`.
    """

    monkeypatch.setattr(sys, "platform", "win32")

    def unavailable():
        raise RuntimeError("COM не зарегистрирован")

    monkeypatch.setattr(session, "_ensure_client", unavailable)

    with pytest.raises(ToolError, match="недоступен"):
        project_tools.status()
