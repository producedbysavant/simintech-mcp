"""Состояние сессии: смена проекта, закрытие, отключение, живучесть COM."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from simintech_api import ComConnectionError, SessionOwnership

from simintech_mcp.server import mcp

from simintech_mcp import session
from simintech_mcp.tools import project as project_tools

from _support import (
    _ClosableProject,
    _ConnectingBlock,
    _FakeWire,
    _OwnedClientStub,
    _WireProject,
    _error,
    _install_wire_project,
    _text,
)


def test_replace_project_clears_wire_registry(monkeypatch):
    """Смена проекта обнуляет реестр: чужие WireId указывают в никуда."""

    src = _ConnectingBlock("k_0", 1)
    dst = _ConnectingBlock("kx_0", 2)
    _install_wire_project(monkeypatch, {"k_0": src, "kx_0": dst})
    session.WIRES.append(_FakeWire(99))  # запись целиком не важна

    session.replace_project(_WireProject({}))

    assert session.WIRES == []


@pytest.mark.anyio
async def test_close_project_clears_wire_registry(monkeypatch):
    """close_project обнуляет реестр линий вместе с проектом."""
    project = _WireProject({})
    _install_wire_project(monkeypatch, {})
    monkeypatch.setattr(session, "_project", project)
    session.WIRES.append(_FakeWire(1))

    text = _text(await mcp.call_tool("close_project", {}))

    assert session.WIRES == []
    assert "закрыт" in text


def test_replace_project_closes_previous(monkeypatch):
    """Смена проекта закрывает предыдущий.

    Иначе create_project/open_project копили бы открытые проекты внутри
    mmain.exe, а инструменты молча работали бы с последним.
    """

    # id разные: совпадение — особый случай, он проверяется отдельным тестом.
    previous, fresh = _ClosableProject(project_id=7), _ClosableProject(project_id=8)
    monkeypatch.setattr(session, "_project", previous)

    session.replace_project(fresh)

    assert previous.closed, "предыдущий проект не закрыт"
    assert session.current_project() is fresh


def test_replace_project_keeps_same_id_project_open(monkeypatch):
    """Совпал COM id прежнего и нового — прежний НЕ закрываем.

    Среда переиспользует идентификаторы: при совпадении «прежний» и «новый»
    для среды — один объект, и `CloseProject` бьёт по текущему проекту.
    Живой замер 04.10.2026 (минимум вендорского дефекта): такое закрытие
    отвечает «успешно», а сессия после него сломана — уже не пишет
    («SaveProjectXML: успех, файла нет»), следующий `save_project` падает
    Access Violation в `FormShow`.
    """
    previous = _ClosableProject(project_id=7)
    fresh = _ClosableProject(project_id=7)
    monkeypatch.setattr(session, "_project", previous)

    note = session.replace_project(fresh)

    assert not previous.closed, \
        "закрытие при совпавшем id — удар по текущему проекту"
    assert "тот же id" in note, "причина пропуска обязана быть названа"
    assert session.current_project() is fresh


def test_replace_project_tolerates_already_closed(monkeypatch):
    """Уже закрытый средой проект не должен ломать смену."""

    monkeypatch.setattr(session, "_project", _ClosableProject(raises=True))
    fresh = _ClosableProject()

    session.replace_project(fresh)

    assert session.current_project() is fresh


def test_replace_project_without_previous(monkeypatch):
    """Первый проект в сессии — закрывать нечего."""

    monkeypatch.setattr(session, "_project", None)
    fresh = _ClosableProject()

    session.replace_project(fresh)

    assert session.current_project() is fresh
    assert not fresh.closed


# ─── Имя проекта в ответах (issue #18) ────────────────────────────


def _with_project(project, source_path=None):
    """Поставить проект текущим; вернуть функцию восстановления прежнего."""
    prev_project, prev_path = session.current_project(), session._project_path
    session.set_project(project, source_path=source_path)
    return lambda: session.set_project(prev_project, source_path=prev_path)


def test_project_label_names_opened_file_and_id():
    """Открытый из файла проект называется именем файла и id.

    Это имя ответы правок приводят как адрес («Изменения внесены в: …»):
    без каталога — по имени файла проект и находят, — но с id, который
    различает одноимённые файлы из разных каталогов.
    """
    restore = _with_project(_WireProject({}, project_id=12),
                            source_path=r"C:\work\CoolInt.prt")
    try:
        assert session.project_label() == "«CoolInt.prt» (id=12)"
    finally:
        restore()


def test_project_label_for_created_project():
    """У созданного из шаблона имени нет — метка говорит это прямо."""
    restore = _with_project(_WireProject({}, project_id=5))
    try:
        assert session.project_label() == "«проект из шаблона» (id=5)"
    finally:
        restore()


def test_project_label_without_project_is_empty():
    """Без проекта метки нет: подставлять «None» в адрес правки нельзя."""
    restore = _with_project(None)
    try:
        assert session.project_label() == ""
    finally:
        restore()


def test_replace_project_note_names_the_switch(monkeypatch):
    """Смена проекта названа явно: было A → стало B (issue #18).

    Смена — событие сессии: без её названия агент узнаёт о ней только по
    косвенному признаку (счётчику объектов в следующем ответе).
    """
    restore = _with_project(_ClosableProject(project_id=12),
                            source_path=r"C:\a\CoolInt.prt")
    try:
        note = session.replace_project(
            _ClosableProject(project_id=17),
            source_path=r"C:\b\sub_TractionState.prt")

        assert "СМЕНИЛСЯ" in note
        assert "было «CoolInt.prt» (id=12)" in note
        assert "стало «sub_TractionState.prt» (id=17)" in note
    finally:
        restore()


def test_replace_project_without_previous_has_no_switch_note(monkeypatch):
    """Первый проект «сменой» не объявляется: было — ничего."""
    restore = _with_project(None)
    try:
        note = session.replace_project(_ClosableProject(project_id=5))
        assert note == ""
    finally:
        restore()


def test_mutation_note_names_current_project():
    """Хвост ответа правки: адресат и постоянная строка о несохранённом.

    Строка о несохранённости стоит в каждом ответе правки, и это не
    оговорка: сразу после правки она истинна по определению — записать её
    может `save_project`, откатить `reload_project` (issue #18).
    """
    restore = _with_project(_WireProject({}, project_id=3),
                            source_path=r"C:\work\CoolInt.prt")
    try:
        assert session.mutation_note() == (
            "\nИзменения внесены в: «CoolInt.prt» (id=3)\n"
            "Не сохранено: `save_project` запишет, `reload_project` откатит.")
    finally:
        restore()


def test_unsaved_flag_tracks_mutations_and_resets():
    """Счёт несохранённых правок: ставит правка, снимают сохранение и смена.

    Счёт читает `reload_project` («правки отброшены» против «перечитан тот
    же файл»), а ставит его обвязка правок — здесь проверяется сама
    механика: `mark_mutated` и оба сброса.
    """
    restore = _with_project(_WireProject({}, project_id=3))
    try:
        assert not session.unsaved_changes()
        session.mark_mutated()
        assert session.unsaved_changes()
        session.clear_unsaved()
        assert not session.unsaved_changes()
        session.mark_mutated()
        session.set_project(_WireProject({}, project_id=4))
        assert not session.unsaved_changes(), "смена проекта снимает счёт"
    finally:
        restore()


def test_mutation_note_without_project_is_empty():
    """Без проекта хвост пуст: «внесены в никуда» — ложь."""
    restore = _with_project(None)
    try:
        assert session.mutation_note() == ""
    finally:
        restore()


@pytest.mark.anyio
async def test_disconnect_resets_project_and_client(monkeypatch):
    """disconnect() сбрасывает и проект: иначе остаётся мёртвый ProjectId.

    Процесс сессии при этом снимается **управляемо** (`shutdown`), а не
    только отпускается COM-ссылка: иначе он остался бы жить сиротой —
    отпускание последней ссылки завершает сервер лишь иногда (замеры
    02.10.2026). Ответ называет снятый PID.
    """

    project = _ClosableProject()
    client = _OwnedClientStub(pid=777)
    monkeypatch.setattr(session, "_project", project)
    monkeypatch.setattr(session, "_project_path", None)
    monkeypatch.setattr(session, "_client", client)
    # «Завершён» называется по проверке исчезновения процесса: подделка
    # сообщает «ушёл», чтобы утверждение опиралось на факт, а не на веру.
    monkeypatch.setattr(project_tools, "wait_for_pid_exit",
                        lambda pid, timeout=3.0: True)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert project.closed
    assert client.shutdown_called
    assert session.current_project() is None
    assert session.current_client() is None
    assert "Сессия завершена" in text
    assert "id=7" in text, "завершение сессии обязано назвать закрытый проект"
    assert "PID 777" in text, "ответ обязан назвать снятый процесс"


@pytest.mark.anyio
async def test_disconnect_warns_when_process_stays_alive(monkeypatch):
    """«Завершён» — по проверке: живой процесс называется предупреждением.

    `shutdown` библиотеки об исчерпании попыток не сообщает (его завершение —
    best-effort), поэтому безусловная формулировка выдавала бы недоказанное
    за факт (ревью #41). Проверка делает ветку «всё ещё жив» живой.
    """

    client = _OwnedClientStub(pid=777)
    monkeypatch.setattr(session, "_project", None)
    monkeypatch.setattr(session, "_client", client)
    monkeypatch.setattr(project_tools, "wait_for_pid_exit",
                        lambda pid, timeout=3.0: False)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert "всё ещё жив" in text
    assert "PID 777) завершён" not in text


@pytest.mark.anyio
async def test_disconnect_without_session_is_noop(monkeypatch):
    """Нечего сбрасывать — сообщение «без изменений», а не вид действия."""

    monkeypatch.setattr(session, "_project", None)
    monkeypatch.setattr(session, "_client", None)

    text = _text(await mcp.call_tool("disconnect", {}))

    assert "Без изменений" in text


@pytest.mark.anyio
async def test_close_project_without_project_is_noop(monkeypatch):
    """Закрытие без проекта — тоже «без изменений»."""

    monkeypatch.setattr(session, "_project", None)

    text = _text(await mcp.call_tool("close_project", {}))

    assert "Без изменений" in text


@pytest.mark.anyio
async def test_close_project_names_what_was_closed(monkeypatch):
    """Закрытие называет проект и что текущего проекта больше нет (issue #18)."""

    _install_wire_project(monkeypatch, {})
    monkeypatch.setattr(session, "_project", _WireProject({}, project_id=42))
    monkeypatch.setattr(session, "_project_path", None)

    text = _text(await mcp.call_tool("close_project", {}))

    assert "«проект из шаблона» (id=42)" in text
    assert "текущего проекта больше нет" in text


def test_replace_project_reports_failed_close(monkeypatch):
    """Неудачное закрытие предыдущего проекта не выдаётся за успех."""

    monkeypatch.setattr(session, "_project",
                        _ClosableProject(raises=True, project_id=7))
    fresh = _ClosableProject(project_id=8)

    note = session.replace_project(fresh)

    assert "ВНИМАНИЕ" in note
    assert session.current_project() is fresh


@pytest.mark.anyio
async def test_disconnect_reports_failed_close(monkeypatch):
    """disconnect сбрасывает состояние, но сообщает о неудачном закрытии."""

    class _Client:
        session_pid = None

        def shutdown(self, kill_pids=None):
            pass

    monkeypatch.setattr(session, "_project",
                        _ClosableProject(raises=True))
    monkeypatch.setattr(session, "_client", _Client())

    text = _text(await mcp.call_tool("disconnect", {}))

    assert session.current_project() is None
    assert "ВНИМАНИЕ" in text


def test_forget_wire_removes_only_named_wire():
    """Реестр теряет именно снятую линию: прочие записи целы.

    Линия теперь может умереть раньше проекта (`disconnect_wire`), и запись о
    ней обязана уйти вместе с ней — иначе `layout_place` выравнивал бы блоки
    по мёртвой связи, считая её живой.
    """
    saved = list(session.WIRES)
    first, second = _FakeWire(1), _FakeWire(2)
    session.WIRES[:] = [(first, "k_0", 0, "kx_0", 0),
                        (second, "k_0", 0, "k_1", 1)]
    try:
        session.forget_wire(1)

        assert [record[0] for record in session.WIRES] == [second]
    finally:
        session.WIRES[:] = saved


def test_set_project_clears_wires():
    """Смена проекта сбрасывает линии: их COM-идентификаторы мертвы.

    Инвариант держится одним местом (`set_project`), поэтому проверяется
    здесь, а не через `create_project`/`disconnect`.
    """

    saved_project = session.current_project()
    session.WIRES.append(("wire", "k_0", 0, "kx_0", 0))
    try:
        session.set_project(None)

        assert session.WIRES == []
        assert session.current_project() is None
    finally:
        session.WIRES.clear()
        session.set_project(saved_project)


# ─── Живучесть COM ────────────────────────────────────────────────


class _DeadClient:
    """Клиент, у которого `connected` остался True, а COM не отвечает.

    Ровно это состояние наступает после перезапуска `mmain.exe`: флаг отражает
    только состоявшийся `connect()`, а прокси уже мёртв.
    """

    connected = True

    def call(self, method, *args):
        raise ComConnectionError("COM-сервер не отвечает")

    def get_process_id(self):
        return self.call("GetProcessID")


class _FreshClient(_OwnedClientStub):
    """Живой клиент своей сессии; считает пробы живучести."""

    def __init__(self, ownership=None):
        super().__init__(ownership=ownership)
        self.probed = 0

    def get_process_id(self):
        self.probed += 1
        return self.session_pid


def _install_client_factory(monkeypatch, ownership=None):
    """Подменить конструктор клиента, записав созданные экземпляры.

    `ownership` — владение, которое «увидит» созданный клиент: по умолчанию
    свой (OWNED); EXTERNAL/UNKNOWN — для кейсов отказа гейта владения.
    """
    made = []

    def factory(silent_mode=True):
        client = _FreshClient(ownership=ownership)
        made.append(client)
        return client

    monkeypatch.setattr(session, "COMClient", factory)
    return made


def test_ensure_client_reconnects_dead_com(monkeypatch):
    """Мёртвый клиент не отдаётся повторно: `ensure_client` переподключается.

    Без этого рецепт «перезапустите mmain.exe и повторите» неисполним:
    `connected` остаётся True, `ensure_client` возвращал бы тот же мёртвый
    прокси, и каждый следующий COM-инструмент падал бы, пока не вызван
    `disconnect`. Проект сбрасывается вместе с клиентом: он держит собственный
    `_client`, и его COM-идентификаторы после смены сервера недействительны.
    """

    dead = _DeadClient()
    monkeypatch.setattr(session, "_client", dead)
    monkeypatch.setattr(session, "_project", _ClosableProject())
    session.WIRES.append(_FakeWire(1))
    made = _install_client_factory(monkeypatch)

    client = session.ensure_client()

    assert client is not dead, "старый мёртвый клиент не должен возвращаться"
    assert session.current_client() is client
    assert made == [client], "новый клиент создаётся ровно один"
    assert session.WIRES == [], "линии мертвого проекта сбрасываются с ним"
    with pytest.raises(ToolError, match="Нет открытого проекта"):
        session.ensure_project()


def test_ensure_client_reuses_live_client(monkeypatch):
    """Живой клиент проверяется пробным вызовом, но не пересоздаётся.

    Без проверки клиент считается живым по флагу — а он не отражает живучесть
    COM; пересоздание же живого клиента рвало бы работающую сессию.
    """

    alive = _FreshClient()
    monkeypatch.setattr(session, "_client", alive)
    made = _install_client_factory(monkeypatch)

    assert session.ensure_client() is alive
    assert alive.probed == 1, "живой клиент должен быть проверен вызовом"
    assert made == [], "живой клиент пересоздавать нельзя"


class _DeadProject:
    """Проект, чьи COM-вызовы падают: сервер mmain.exe уже не отвечает."""

    def get_main_page(self):
        raise ComConnectionError("Сервер RPC недоступен")


@pytest.mark.anyio
async def test_ensure_project_reports_dead_com(monkeypatch):
    """Мёртвый COM виден и инструменту, который проект не создаёт.

    Смерть `mmain.exe` переподключали только `create_project`, `open_project` и
    `status`: остальные шли через `ensure_project()` мимо пробника и отдавали
    сырой `ComCallError` из недр библиотеки. Теперь смерть видна и здесь: отказ
    называет причину и рецепт, а состояние сессии сброшено — иначе повтор
    подхватил бы тот же мёртвый прокси, и отказывали бы все COM-инструменты.
    """

    monkeypatch.setattr(session, "_client", _DeadClient())
    monkeypatch.setattr(session, "_project", _DeadProject())
    session.WIRES.append(_FakeWire(7))

    text = await _error("list_blocks", {})

    assert "потеряно" in text, "отказ обязан называть причину, а не молчать"
    assert "create_project" in text, "рецепт должен быть исполним"
    assert session.current_project() is None
    assert session.current_client() is None
    assert session.WIRES == [], "линии мёртвого проекта сбрасываются с ним"
    with pytest.raises(ToolError, match="Нет открытого проекта"):
        session.ensure_project()


def test_ensure_client_refuses_external_session(monkeypatch):
    """Чужой (EXTERNAL) экземпляр — отказ, кандидат отпущен, процесс не тронут.

    `CreateObject` умеет подключаться к уже запущенному SimInTech — в том
    числе открытому человеком (замеры 02.10.2026, simintech-code v0.11.0).
    Инструменты сервера создают и закрывают проекты, переключают страницы —
    в чужой сессии это разрушило бы работу пользователя. Поэтому владение
    проверяется до всякой работы, и EXTERNAL — отказ.
    """

    monkeypatch.setattr(session, "_client", None)
    monkeypatch.setattr(session, "_project", None)
    made = _install_client_factory(monkeypatch,
                                   ownership=SessionOwnership.EXTERNAL)

    with pytest.raises(ToolError) as excinfo:
        session.ensure_client()

    text = str(excinfo.value)
    assert "ownership=external" in text, "отказ обязан назвать владение"
    assert "не наша" in text
    assert "Закройте SimInTech" in text, "нужен исполнимый рецепт"
    assert made and made[0].disconnected, "кандидата обязаны отпустить"
    assert session.current_client() is None, "чужой клиент не должен осесть в сессии"


def test_ensure_client_refuses_unknown_session(monkeypatch):
    """Неподтверждённое владение (UNKNOWN) — тот же отказ: не разрешение."""

    monkeypatch.setattr(session, "_client", None)
    monkeypatch.setattr(session, "_project", None)
    made = _install_client_factory(monkeypatch,
                                   ownership=SessionOwnership.UNKNOWN)

    with pytest.raises(ToolError) as excinfo:
        session.ensure_client()

    text = str(excinfo.value)
    assert "ownership=unknown" in text
    assert "подтвердить не удалось" in text
    assert "повторите" in text, "рецепт при сбое снимка — повтор, не закрытие"
    assert "Закройте SimInTech" not in text, (
        "рецепт EXTERNAL на UNKNOWN — тупик (ревью #41)")
    assert made and made[0].disconnected
    assert session.current_client() is None
