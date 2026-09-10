"""Тесты семантики сигналов: что читаемо, а что лишь имя блока.

Разбор причины (проверено на SimInTech64, 2026-09-10): обмен данными идёт
через список сигналов проекта и базу сигналов. У проекта, созданного
`Project.new()`, нет ни того, ни другого (`GetProjectDB` → `(None, None)`,
`GetProjectSignalList` → пустой список), поэтому выходы блоков **не являются**
адресуемыми элементами данных.

XML-заглушка возвращает имена БЛОКОВ, а не сигналов. Если выдавать их за
сигналы, вызывающий код считает, что сигналы есть, и падает уже на чтении.
Тесты фиксируют, что источник и читаемость различимы.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from simintech_api.core.project import Project  # noqa: E402
from simintech_api.exceptions import SignalError  # noqa: E402
from simintech_api.model import SignalInfo, TDataDescriptor  # noqa: E402


class FakeClient:
    """Клиент без сигналов: список проекта пуст, как у проекта без базы."""

    def __init__(self):
        self.calls = []

    def call(self, method, *args):
        self.calls.append(method)
        if method == "GetProjectSignalList":
            return 1          # list_id, но список пуст
        if method == "GetListCount":
            return 0
        if method == "FindProjectData":
            return TDataDescriptor(0, 0)
        if method == "FindSignalData":
            return TDataDescriptor(0, 0)
        return 0

    def find_signal(self, name, project_id):
        """Как у COMClient: поиск не находит ничего в проекте без базы."""
        return TDataDescriptor(0, 0)


def _project(client=None):
    return Project(client or FakeClient(), project_id=1)


# ─── SignalInfo ───────────────────────────────────────────────────

def test_signal_info_with_descriptor_is_readable():
    info = SignalInfo("s", "подпись", TDataDescriptor(123, 0))

    assert info.readable is True
    assert info.source == "com"


def test_signal_info_without_descriptor_is_not_readable():
    """Имя без дескриптора — прочитать нельзя, и это видно вызывающему."""
    info = SignalInfo("k_0", "")

    assert info.readable is False


# ─── list_signals ─────────────────────────────────────────────────

def test_list_signals_marks_xml_fallback_as_not_readable(monkeypatch):
    """Имена из XML помечаются источником 'xml' и как нечитаемые.

    Это имена блоков, а не сигналы: выдавать их за сигналы нельзя, иначе
    ошибка всплывёт позже и в другом месте.
    """
    import simintech_api.utils.xprt_signals as xprt
    from simintech_api.core import project as project_module

    monkeypatch.setattr(
        project_module, "extract_signal_names_from_project",
        lambda prj, **kw: ["k_0", "kx_0"], raising=False)
    monkeypatch.setattr(
        xprt, "extract_signal_names_from_project",
        lambda prj, **kw: ["k_0", "kx_0"])

    infos = _project().list_signals()

    assert [i.name for i in infos] == ["k_0", "kx_0"]
    assert all(i.source == "xml" for i in infos)
    assert all(not i.readable for i in infos)


def test_list_signals_returns_empty_when_nothing_found(monkeypatch):
    """Ни COM-списка, ни XML — пусто, а не выдуманные имена."""
    import simintech_api.utils.xprt_signals as xprt

    monkeypatch.setattr(
        xprt, "extract_signal_names_from_project", lambda prj, **kw: [])

    assert _project().list_signals() == []


# ─── find_signal ──────────────────────────────────────────────────

def test_find_signal_error_explains_missing_database():
    """Ошибка поиска сигнала объясняет причину, а не только «не найден»."""
    with pytest.raises(SignalError) as exc:
        _project().find_signal("k_0")

    message = str(exc.value)
    assert "k_0" in message
    assert "баз" in message.lower() or "сигнал" in message.lower()


def test_signal_by_block_name_is_not_treated_as_signal():
    """Имя блока не выдаётся за сигнал: поиск честно сообщает об ошибке."""
    with pytest.raises(SignalError):
        _project().signal("k_0")
