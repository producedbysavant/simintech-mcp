"""Инструменты пакета проектов (`.pak`): открытие, состав, выбор, расчёт.

Живые замеры 01.10.2026 (копия демо-пакета поставки 2.26.6.23) дали форму
инструментов; фейки здесь моделируют **переходы**, а не удобный ответ:
время участников выдаётся сценарием (замерло — значит замерло), состав пакета
меняется только там, где тест это задал, а id'ы участников нестабильны.
Подробности — в докстринге `simintech_mcp/tools/pack.py`.
"""

from __future__ import annotations

import pytest

from simintech_api import Pack

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import pack as pack_tools

from _support import _ClosableProject, _error, _text

#: Файлы участников — как у демо-пакета поставки (путь синтетический:
#: `C:\Users\<user>` — форма, разрешённая DLP-гейтом).
_FILES = {
    11: r"C:\Users\<user>\AppData\Local\Temp\pak-demo\Непрерывная часть.prt",
    22: r"C:\Users\<user>\AppData\Local\Temp\pak-demo\Дискретная часть.prt",
}
_PAK = r"C:\Users\<user>\AppData\Local\Temp\pak-demo\Пакет.pak"


class _FakePackClient:
    """Клиент пакета: состав, времена (сценарием) и раскладка COM-вызовов."""

    def __init__(self, members=(11, 22), times=None):
        self.members = list(members)
        #: Очередь времён для `GetProjectTime` — общая на все чтения, как в
        #: жизни: сначала «до», потом «после», по одному вызову на участника.
        #: Кончилась очередь — время стоит: рост обязан задаваться тестом.
        self.times = list(times or [])
        self.calls = []
        self.closed_packs = []
        self._next_pack = 900

    # ── COMClient-поверхность ─────────────────────────────────────
    def open_pack(self, path):
        self._next_pack += 1
        self.calls.append(("OpenPack", path))
        return self._next_pack

    def close_pack(self, pack_id):
        self.closed_packs.append(pack_id)
        self.calls.append(("ClosePack", pack_id))

    def get_opened_file_name(self, project_id):
        return _FILES.get(project_id, "")

    # ── call() — методы Pack ──────────────────────────────────────
    def call(self, method, *args):
        self.calls.append((method,) + args)
        if method == "PackGetProjCount":
            return len(self.members)
        if method == "PackGetProjectIdByIndex":
            return self.members[args[1]]
        if method == "GetProjectTime":
            return self.times.pop(0) if self.times else 0.0
        if method in ("PackStart", "PackRun", "PackStep", "PackPause",
                      "PackStop", "CloseProject"):
            return None
        raise AssertionError(f"нежданный COM-вызов: {method}")


def _install_pack(monkeypatch, client, *, pack_id=555):
    """Сделать пакет текущим, как это делает `open_pack`."""
    pack = Pack(client, pack_id)
    monkeypatch.setattr(session, "_pack", pack)
    monkeypatch.setattr(session, "_pack_path", _PAK)
    return pack


def _method_calls(client, method):
    return [c for c in client.calls if c[0] == method]


# ─── Открытие и закрытие ──────────────────────────────────────────


@pytest.mark.anyio
async def test_open_pack_names_composition(monkeypatch):
    """Открытие называет пакет, состав с индексами и файлами участников."""

    client = _FakePackClient()
    monkeypatch.setattr(session, "_ensure_client", lambda: client)
    monkeypatch.setattr(session, "_pack", None)
    monkeypatch.setattr(session, "_pack_path", None)

    text = _text(await mcp.call_tool("open_pack", {"path": _PAK}))

    assert "«Пакет.pak»" in text
    assert "проектов 2" in text
    assert "[0]" in text and "«Непрерывная часть.prt»" in text
    assert "[1]" in text and "«Дискретная часть.prt»" in text


@pytest.mark.anyio
async def test_open_pack_replaces_previous(monkeypatch):
    """Второе открытие закрывает прежний пакет и называет смену.

    Открытие того же файла дважды создаёт ВТОРОЙ пакет (замер 01.10.2026) —
    без закрытия прежнего пакеты копились бы внутри mmain.exe.
    """

    client = _FakePackClient()
    old = _install_pack(monkeypatch, client, pack_id=500)
    monkeypatch.setattr(session, "_ensure_client", lambda: client)

    text = _text(await mcp.call_tool("open_pack", {"path": _PAK}))

    assert client.closed_packs == [old.id]
    assert "ПАКЕТ СМЕНИЛСЯ" in text
    assert session._pack is not old
    assert session._pack.id != old.id


@pytest.mark.anyio
async def test_open_pack_zero_id_refuses_and_keeps_previous(monkeypatch):
    """Нулевой id — отказ, прежний пакет сессии не тронут."""

    class _Refusing(_FakePackClient):
        def open_pack(self, path):
            self.calls.append(("OpenPack", path))
            return 0

    client = _Refusing()
    old = _install_pack(monkeypatch, client, pack_id=500)
    monkeypatch.setattr(session, "_ensure_client", lambda: client)

    text = await _error("open_pack", {"path": _PAK})

    assert "не открыт" in text
    assert session._pack is old
    assert client.closed_packs == []


@pytest.mark.anyio
async def test_open_pack_resets_member_project_of_old_pack(monkeypatch):
    """Проект-участник закрытого пакета сбрасывается: он закрыт вместе с ним."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client, pack_id=500)
    monkeypatch.setattr(session, "_project", _ClosableProject(project_id=11))
    monkeypatch.setattr(session, "_project_path", None)
    monkeypatch.setattr(session, "_ensure_client", lambda: client)

    text = _text(await mcp.call_tool("open_pack", {"path": _PAK}))

    assert session._project is None
    assert "закрыт вместе с ним" in text


@pytest.mark.anyio
async def test_close_pack_closes_and_names(monkeypatch):
    """Закрытие пакета называет пакет; участник-проект сбрасывается."""

    client = _FakePackClient()
    pack = _install_pack(monkeypatch, client, pack_id=555)
    monkeypatch.setattr(session, "_project", _ClosableProject(project_id=22))
    monkeypatch.setattr(session, "_project_path", None)

    text = _text(await mcp.call_tool("close_pack", {}))

    assert client.closed_packs == [pack.id]
    assert "Пакет закрыт" in text and "«Пакет.pak»" in text
    assert session._pack is None
    assert session._project is None
    assert "закрыт вместе с ним" in text


@pytest.mark.anyio
async def test_close_pack_without_pack_is_noop(monkeypatch):
    """Закрытие без пакета — «без изменений», как у проекта."""

    monkeypatch.setattr(session, "_pack", None)

    text = _text(await mcp.call_tool("close_pack", {}))

    assert "Без изменений" in text


@pytest.mark.anyio
async def test_pack_tools_without_pack_refuse(monkeypatch):
    """Инструменты состава и расчёта без пакета отказывают с рецептом."""

    monkeypatch.setattr(session, "_pack", None)

    for tool in ("list_pack_projects", "pack_run", "pack_step", "pack_stop"):
        text = await _error(tool, {})
        assert "open_pack" in text, tool

    text = await _error("select_pack_project", {"index": 0})
    assert "open_pack" in text


# ─── Состав ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_pack_projects_shows_times_and_pack_time(monkeypatch):
    """Состав перечисляется с временами; время пакета — минимум по участникам."""

    client = _FakePackClient(times=[0.0099, 0.01])
    _install_pack(monkeypatch, client)

    text = _text(await mcp.call_tool("list_pack_projects", {}))

    assert "[0]" in text and "[1]" in text
    assert "модельное время 0.0099" in text
    assert "модельное время 0.01" in text
    assert "Время пакета (минимум по участникам): 0.0099" in text


@pytest.mark.anyio
async def test_list_pack_projects_survives_unreadable_time(monkeypatch):
    """Недоступное время печатается явно, а не прячется за успехом."""

    class _NoTime(_FakePackClient):
        def call(self, method, *args):
            if method == "GetProjectTime":
                raise RuntimeError("пакет повреждён")
            return super().call(method, *args)

    client = _NoTime()
    _install_pack(monkeypatch, client)

    text = _text(await mcp.call_tool("list_pack_projects", {}))

    assert "время недоступно" in text
    assert "Время пакета (минимум по участникам): время недоступно" in text


# ─── Выбор проекта ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_select_pack_project_switches_current(monkeypatch):
    """Выбор участника делает его текущим проектом и называет файл."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)
    monkeypatch.setattr(session, "_project", None)
    monkeypatch.setattr(session, "_project_path", None)

    text = _text(await mcp.call_tool("select_pack_project", {"index": 1}))

    assert session._project is not None
    assert session._project.id == 22
    assert "«Дискретная часть.prt»" in text
    assert "id=22" in text


@pytest.mark.anyio
async def test_select_pack_project_keeps_previous_member(monkeypatch):
    """Прежний участник пакета не закрывается: close исключил бы его из пака.

    Живой замер 01.10.2026: `CloseProject` участника сокращает состав пакета
    (2 → 1), поэтому переключение между участниками обязано его сохранять.
    """

    client = _FakePackClient()
    _install_pack(monkeypatch, client)
    previous = _ClosableProject(project_id=11)
    monkeypatch.setattr(session, "_project", previous)
    monkeypatch.setattr(session, "_project_path", None)

    text = _text(await mcp.call_tool("select_pack_project", {"index": 1}))

    assert previous.closed is False, "участник пакета закрыт — состав сломается"
    assert "оставлен в пакете" in text
    assert session._project.id == 22


@pytest.mark.anyio
async def test_select_pack_project_closes_non_member(monkeypatch):
    """Прежний проект вне пакета закрывается — как при open_project."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)
    previous = _ClosableProject(project_id=7777)
    monkeypatch.setattr(session, "_project", previous)
    monkeypatch.setattr(session, "_project_path", None)

    await mcp.call_tool("select_pack_project", {"index": 0})

    assert previous.closed is True
    assert session._project.id == 11


@pytest.mark.anyio
async def test_select_pack_project_same_is_noop(monkeypatch):
    """Повторный выбор того же участника ничего не делает."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)
    monkeypatch.setattr(session, "_project", _ClosableProject(project_id=11))
    monkeypatch.setattr(session, "_project_path", None)

    text = _text(await mcp.call_tool("select_pack_project", {"index": 0}))

    assert "уже текущий" in text
    assert _method_calls(client, "CloseProject") == []


@pytest.mark.anyio
async def test_select_pack_project_out_of_range(monkeypatch):
    """Индекс вне состава — отказ со ссылкой на перечисление."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)

    text = await _error("select_pack_project", {"index": 5})

    assert "вне состава" in text
    assert "list_pack_projects" in text


# ─── Расчёт ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pack_run_reports_times_and_honest_caveat(monkeypatch):
    """Запуск печатает время до/после и честную оговорку о непрерывности."""

    # Четыре чтения: «до» по участникам, затем «после» — время поднялось до
    # первого синхрошага, как в живом замере (0 → 0.0099/0.01).
    client = _FakePackClient(times=[0.0, 0.0, 0.0099, 0.01])
    _install_pack(monkeypatch, client)

    text = _text(await mcp.call_tool("pack_run", {}))

    assert _method_calls(client, "PackStart")
    assert _method_calls(client, "PackRun")
    assert "0 → 0.0099" in text
    assert "непрерывный прогон не наблюдался" in text
    assert "pack_step" in text


@pytest.mark.anyio
async def test_pack_step_verifies_growth(monkeypatch):
    """Шаги подтверждаются ростом времени пакета, а не кодом возврата."""

    client = _FakePackClient(times=[0.0, 0.0, 0.001, 0.002])
    _install_pack(monkeypatch, client)

    text = _text(await mcp.call_tool("pack_step", {"count": 2}))

    assert len(_method_calls(client, "PackStep")) == 2
    assert "Выполнено шагов: 2" in text
    assert "0 → 0.001" in text


@pytest.mark.anyio
async def test_pack_step_refuses_when_time_frozen(monkeypatch):
    """Время не сдвинулось — шагов не было: отказ, а не «выполнено»."""

    client = _FakePackClient(times=[0.01, 0.01, 0.01, 0.01])
    _install_pack(monkeypatch, client)

    text = await _error("pack_step", {"count": 3})

    assert "не сдвинулось" in text


@pytest.mark.anyio
async def test_pack_step_refuses_without_readable_time(monkeypatch):
    """Время нечитаемо — подтвердить шаги нечем: отказ."""

    class _NoTime(_FakePackClient):
        def call(self, method, *args):
            if method == "GetProjectTime":
                raise RuntimeError("пакет повреждён")
            return super().call(method, *args)

    client = _NoTime()
    _install_pack(monkeypatch, client)

    text = await _error("pack_step", {"count": 1})

    assert "не прочиталось" in text


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, -1, pack_tools.MAX_STEP_COUNT + 1])
async def test_pack_step_count_bounds(monkeypatch, count):
    """Пределы count — как у `step`: положительный и не больше предела."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)

    text = await _error("pack_step", {"count": count})

    assert "count" in text
    assert _method_calls(client, "PackStep") == []


@pytest.mark.anyio
async def test_pack_stop_calls_pack_stop(monkeypatch):
    """Остановка идёт в PackStop и не притворяется подтверждением."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)

    text = _text(await mcp.call_tool("pack_stop", {}))

    assert _method_calls(client, "PackStop")
    assert "остановлен" in text


# ─── Связка с проектом ────────────────────────────────────────────


@pytest.mark.anyio
async def test_close_project_refuses_pack_member(monkeypatch):
    """close_project участника отказывает: это операция над составом пакета."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)
    monkeypatch.setattr(session, "_project", _ClosableProject(project_id=11))
    monkeypatch.setattr(session, "_project_path", None)

    text = await _error("close_project", {})

    assert "участник открытого пакета" in text
    assert "close_pack" in text
    assert session._project is not None, "проект не должен быть потерян"


def test_replace_project_keeps_pack_member(monkeypatch):
    """`_replace_project` не закрывает участника открытого пакета — замер."""

    client = _FakePackClient()
    _install_pack(monkeypatch, client)
    previous = _ClosableProject(project_id=22)
    monkeypatch.setattr(session, "_project", previous)

    note = session._replace_project(_ClosableProject(project_id=7777))

    assert previous.closed is False
    assert "оставлен в пакете" in note
    assert session._project.id == 7777


# ─── Хелперы самого модуля ────────────────────────────────────────


def test_pack_time_is_min_over_members():
    """Время пакета — минимум известных времён; пусто — None."""

    assert pack_tools._pack_time([0.5, None, 0.1]) == 0.1
    assert pack_tools._pack_time([None, None]) is None
