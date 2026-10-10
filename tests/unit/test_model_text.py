"""Текст модели: выгрузка и сборка — путь внутри каталога результатов и отказы.

Мост подделывается целиком (`ScriptBridge` в контурном ядре `page_script`):
настоящий требует Windows и живого `mmain.exe`, а проверяем мы не COM, а
контракт инструмента — куда он пишет, что возвращает и как отказывает.

Подделка моделирует **переход**: артефакт пишется по пути **из тела** (иначе
тест не заметил бы, что путь потеряли), а исход задаётся тестом — так
проверяются и «не собрался», и «модель не считает».
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simintech_api.core.script_bridge import PageRunResult
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.script_probe import (
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_NOT_COMPILED,
    OUTCOME_OK,
    ContourOutcome,
)

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import page_script
from simintech_mcp.tools.model_text import MODEL_TEXT_FILE
from simintech_mcp.tools.page_script import RESULT_FILE

from _support import _error, _text


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _Page:
    """Страница без объектов: отчёту об изменениях нужен её список."""

    def __init__(self, wires=None):
        #: Линии страницы: подсказка о трассировке считает их число.
        self._wires = list(wires or [])

    def get_blocks(self) -> list:
        return []

    def get_wires(self) -> list:
        return list(self._wires)


class _FakeProject:
    """Открытый проект: мосту нужен идентификатор, отчёту — страница."""

    def __init__(self, project_id: int = 7, wires=None):
        self.id = project_id
        self._wires = list(wires or [])

    def get_current_page(self):
        return _Page(self._wires)


def _dump_path_from_body(body: str) -> str:
    """Путь выгрузки — первый строковый литерал тела."""
    start = body.index('"') + 1
    end = body.index('"', start)
    return body[start:end]


class _BridgeRunsContour:
    """Мост-подделка контура: пишет артефакт по пути из тела и отдаёт исход.

    Пишет с BOM — так делает `savemodeltofile` (замер 2026-09-29), и именно
    поэтому инструмент BOM снимает: с ним текст не вклеивается обратно.
    """

    payload = 'A: (type = "Константа", points=[(0, 0)])'
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    restored = ""
    body = ""
    result_path: Path | None = None

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        type(self).result_path = Path(result_path)
        # Артефакт пишет только тело выгрузки. У тела сборки пути нет, а первый
        # строковый литерал — имя блока («Ступенька»): запись по нему создавала
        # файл в текущем каталоге, и так он однажды попал в коммит ветки.
        # «Тело исполнилось» — это оба исхода, `ok` и `model-not-running`:
        # `savemodeltofile` идёт из `initialization`, и файл есть даже на
        # стоящем расчёте (живое наблюдение 01.10.2026) — раньше подделка
        # писала файл только при `ok` и тем повторяла допущение кода.
        ran = (OUTCOME_OK, OUTCOME_MODEL_NOT_RUNNING)
        if type(self).outcome.kind in ran and "savemodeltofile(" in body:
            Path(_dump_path_from_body(body)).write_text(
                "﻿" + type(self).payload, encoding="utf-8")
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


class _BridgeFails(_BridgeRunsContour):
    """Мост-подделка: расчёт не сдвинул время — так выглядит отказ моста."""

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        raise ScriptBridgeError("модельное время не сдвинулось")


class _BridgeSilent(_BridgeRunsContour):
    """Мост-подделка: тело «отработало», а файла нет."""

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


def _install(monkeypatch, tmp_path: Path, bridge, wires=None) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _FakeProject(wires=wires))
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_export_model_text_returns_dump_from_results_dir(monkeypatch, tmp_path):
    """Успех: текст выгрузки возвращается, а файлы пишутся в каталог результатов."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeRunsContour.payload in result
    dump_path = Path(_dump_path_from_body(_BridgeRunsContour.body))
    # Путь выгрузки и файл результата контура — внутри каталога результатов:
    # инструмент, пишущий наружу, обходил бы песочницу чтения.
    assert dump_path.parent == tmp_path
    assert _BridgeRunsContour.result_path is not None
    assert _BridgeRunsContour.result_path.parent == tmp_path
    assert dump_path.exists(), "выгрузка не записана"
    assert dump_path.name != MODEL_TEXT_FILE, \
        "выгрузка пишется под общим именем — прошлый прогон виден под тем же"


@pytest.mark.anyio
async def test_export_model_text_strips_bom(monkeypatch, tmp_path):
    """BOM снимается: с ним текст не вклеивается обратно через `eval`.

    `savemodeltofile` пишет выгрузку с BOM (замер 2026-09-29), а `eval` файла с
    BOM не принимает — возвращённый агенту текст должен быть пригоден для
    обратного пути без правки.
    """
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert "﻿" not in result
    assert _BridgeRunsContour.payload in result


@pytest.mark.anyio
async def test_export_model_text_does_not_serve_stale_dump(monkeypatch, tmp_path):
    """Старая выгрузка не выдаётся за новую — за счёт уникального имени.

    Прежде от этого защищались удалением файла прошлого прогона, но запертый
    файл удалить не даёт (WinError 32, живое наблюдение 02.10.2026), и защита
    молча отказывала. Теперь имя уникально на вызов: путь, который читает
    инструмент, создаёт только этот прогон — «тело отработало, файла нет»
    остаётся отказом, даже когда под прежним именем лежит старая выгрузка.
    """
    stale = tmp_path / MODEL_TEXT_FILE
    stale.write_text("СТАРАЯ ВЫГРУЗКА", encoding="utf-8")
    _install(monkeypatch, tmp_path, _BridgeSilent)

    message = await _error("export_model_text", {})

    assert "файла нет" in message
    assert "СТАРАЯ ВЫГРУЗКА" not in message
    assert stale.read_text(encoding="utf-8") == "СТАРАЯ ВЫГРУЗКА", \
        "файл с прежним именем тронут — запертый снять всё равно нельзя"


@pytest.mark.anyio
async def test_export_model_text_uses_fresh_names_per_call(monkeypatch, tmp_path):
    """У каждого вызова свои имена выгрузки и результата контура.

    Общее имя — тот же класс отказа, что WinError 32: обрыв оставляет файл
    запертым, и следующий вызов упирается в него. Уникальные имена заодно
    сохраняют прошлые артефакты читаемыми — их пути названы в ответах.
    """
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    await mcp.call_tool("export_model_text", {})
    first = _dump_path_from_body(_BridgeRunsContour.body)
    first_probe = _BridgeRunsContour.result_path
    await mcp.call_tool("export_model_text", {})
    second = _dump_path_from_body(_BridgeRunsContour.body)
    second_probe = _BridgeRunsContour.result_path

    assert first != second, "имя выгрузки переиспользовано между вызовами"
    assert first_probe != second_probe, "имя результата контура переиспользовано"
    assert first_probe.name != RESULT_FILE
    assert second_probe.name != RESULT_FILE
    assert Path(first).exists(), "прошлая выгрузка затёрта новым прогоном"


@pytest.mark.anyio
async def test_export_model_text_names_non_running_model(monkeypatch, tmp_path):
    """Несчитающая модель: выгрузка есть, и состояние названо (живое 01.10.2026).

    `savemodeltofile` исполняется в секции `initialization`, поэтому текст
    приходит полным и расчёт при этом не идёт. Молчать нельзя: агент решает
    по ответу, что делать дальше, а отказ здесь — только у тела, которое не
    отработало вовсе.
    """

    class _BridgeStuck(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    _install(monkeypatch, tmp_path, _BridgeStuck)

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeRunsContour.payload in result
    assert "модель не считает" in result
    assert "initialization" in result, "причина полноты текста не названа"


@pytest.mark.anyio
async def test_export_model_text_names_the_reason(monkeypatch, tmp_path):
    """Отказ моста называет причину и что проверить: модель должна считать."""
    _install(monkeypatch, tmp_path, _BridgeFails)

    message = await _error("export_model_text", {})

    assert "модельное время не сдвинулось" in message
    assert "неподключённый вход" in message


@pytest.mark.anyio
async def test_export_model_text_refuses_when_file_is_missing(monkeypatch, tmp_path):
    """Прогон прошёл, а файла нет — это отказ, а не пустой успех."""
    _install(monkeypatch, tmp_path, _BridgeSilent)

    message = await _error("export_model_text", {})

    assert "файла нет" in message

    # И файл результата контура — своё имя, не пересекающееся с выгрузкой.
    assert RESULT_FILE != MODEL_TEXT_FILE


@pytest.mark.anyio
async def test_export_model_text_refuses_when_script_did_not_compile(
        monkeypatch, tmp_path):
    """«Не собрался» — отказ с указанием, где искать текст ошибки."""
    class _Broken(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED, lines=[])

    _install(monkeypatch, tmp_path, _Broken)

    message = await _error("export_model_text", {})

    assert "не собралось" in message
    assert "окне сообщений" in message, "отказ не говорит, где искать причину"


@pytest.mark.anyio
async def test_import_model_text_refuses_empty_text(monkeypatch, tmp_path):
    """Пустой текст модели — отказ до COM-вызова."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    message = await _error("import_model_text", {"model_text": "   \n"})

    assert "пуст" in message.lower()


def test_duplicate_record_names_counts_repeats():
    """Скан дублей: считаются повторённые имена записей, без ложных срабатываний."""
    from simintech_mcp.tools.model_text import _duplicate_record_names

    text = ('k0: (type = "Константа"),\n'
            'Wire: (type = "wire"),\n'
            'sub: (type = "Субмодель", subsystem:\n'
            ' (\n'
            '   pin1: (type = "Порт входа"),\n'
            '   Wire: (type = "wire")\n'
            ' )),\n'
            'kx1: (type = "Усилитель")')

    assert _duplicate_record_names(text) == [("Wire", 2)]
    assert _duplicate_record_names("k0: (type = \"Константа\")") == []


def test_record_kinds_scans_wire_and_object_records():
    """Скан видов записей: провода (`type = "wire"`) отделяются от объектов.

    Скан выбирает предупреждение ответа на импорт (живое воспроизведение
    fdd002, 10.10.2026), поэтому он не должен ни пропускать провода, ни
    выдумывать их из постороннего текста вроде `prototype`.
    """
    from simintech_mcp.tools.model_text import _record_kinds

    assert _record_kinds('w0: (type = "wire")') == (True, False)
    assert _record_kinds('k0: (type = "Константа")') == (False, True)
    assert _record_kinds(
        'k0: (type = "Ступенька",\n points = [(0, 0)]),\n'
        'w0: (type = "wire", src = "k0:out:0", dst = "k0:in:0")'
    ) == (True, True)
    assert _record_kinds('x = prototype = "wire"') == (False, False)


@pytest.mark.anyio
async def test_import_model_text_names_duplicates_on_not_compiled(
        monkeypatch, tmp_path):
    """Дубли имён записей — названная причина not-compiled (текст ошибки скрыт).

    Повтор имени записи среда не компилирует (живой замер 08.10.2026: текст с
    двумя `Wire:` — not-compiled, ни один объект не создан), а ошибка видна
    только в окне редактора SimInTech; скан текста — единственный доступный
    диагноз. Частый источник дублей — сама выгрузка (ветви под одним
    автоименем).
    """

    class _Broken(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED, lines=[])

    _install(monkeypatch, tmp_path, _Broken)

    message = await _error("import_model_text", {"model_text": (
        'k0: (type = "Константа"),\n'
        'Wire: (type = "wire"),\n'
        'Wire: (type = "wire")')})

    assert "Wire" in message and "2×" in message
    assert "повторяются имена записей" in message
    assert "не собралось" in message, "каноническая формулировка потеряна"


@pytest.mark.anyio
async def test_import_model_text_keeps_generic_refusal_without_duplicates(
        monkeypatch, tmp_path):
    """Без дублей отказ остаётся каноническим — без надуманной причины."""

    class _Broken(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED, lines=[])

    _install(monkeypatch, tmp_path, _Broken)

    message = await _error("import_model_text", {"model_text": (
        'k0: (type = "Константа")')})

    assert "не собралось" in message
    assert "повторяются имена" not in message


@pytest.mark.anyio
async def test_import_model_text_aborted_hint_names_partial_creation(
        monkeypatch, tmp_path):
    """Обрыв на исполнении: «часть объектов могла создаться» — не «не изменён»."""
    from simintech_api.script_probe import OUTCOME_ABORTED

    class _Broken(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_ABORTED, lines=["x();"])

    _install(monkeypatch, tmp_path, _Broken)

    message = await _error("import_model_text", {"model_text": (
        'k0: (type = "Константа")')})

    assert "Часть объектов могла создаться" in message
    assert "list_blocks" in message


@pytest.mark.anyio
async def test_import_model_text_builds_body_and_reports_changes(
        monkeypatch, tmp_path):
    """Тело сборки собирается из текста, а ответ называет изменения."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "createmodel(getcurrentprojectid, model);" in _BridgeRunsContour.body
    assert 'block0: (type = "Ступенька")' in _BridgeRunsContour.body
    assert "Отчёт об изменениях" in result


@pytest.mark.anyio
async def test_import_model_text_reports_model_stuck(monkeypatch, tmp_path):
    """«Модель не считает» — не отказ сборки: текст принят, а расчёт стоит."""
    class _Stuck(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    _install(monkeypatch, tmp_path, _Stuck)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "не считает" in result


class _BridgeAddsWires(_BridgeRunsContour):
    """Мост-подделка: тело сборки добавило линии.

    Подделка моделирует **переход**: счётчик линий в подсказке — это прирост
    (до/после контура), и подделка, возвращающая одно и то же число, не могла
    бы его проверить.
    """

    def run_page_script(self, body, result_path):
        if "createmodel(" in body:
            session.ensure_project()._wires.extend([object(), object()])
        return super().run_page_script(body, result_path)


@pytest.mark.anyio
async def test_import_model_text_warns_baked_geometry_of_mixed_text(
        monkeypatch, tmp_path):
    """Смешанный текст (объекты и провода рядом) — ответ называет дефект прямо.

    Маршруты проводов, поданных вместе со своими блоками, среда запекает до
    пересчёта габаритов и не пересчитывает ничем (живое воспроизведение
    fdd002, 10.10.2026); прежний ответ звал «`layout_place` проложит линии» —
    и клиент шёл чинить нормализацией то, что ею не чинится.
    """
    _install(monkeypatch, tmp_path, _BridgeAddsWires)
    text = ('k0: (type = "Константа"),\n'
            'w0: (type = "wire", src = "k0:out:0", dst = "k0:in:0")')

    result = _text(await mcp.call_tool("import_model_text", {"model_text": text}))

    assert "Линии связи: +2" in result
    assert "запекается мусорным" in result
    assert "пересоздание" in result
    assert "проложит" not in result, \
        "ответ снова обещает прокладку запечённой геометрии"


@pytest.mark.anyio
async def test_import_model_text_wires_only_text_names_order(monkeypatch, tmp_path):
    """Текст только с проводами — предупреждение о запекании и канон сборки.

    Провода к уже существующим блокам — законный отдельный импорт; ответ не
    обвиняет его дефектом смешанного текста, но говорит, что маршрут среда
    считает сама при создании, и что пустой маршрут заполняет `layout_place`,
    а запечённый — нет.
    """
    _install(monkeypatch, tmp_path, _BridgeAddsWires)
    text = 'w0: (type = "wire", src = "k0:out:0", dst = "k0:in:0")'

    result = _text(await mcp.call_tool("import_model_text", {"model_text": text}))

    assert "Линии связи: +2" in result
    assert "точки текста игнорируются" in result
    assert "layout_place(normalize_only=True)" in result
    assert "запекается мусорным" not in result, \
        "одиночный текст с проводами обвинён дефектом смешанного импорта"


@pytest.mark.anyio
async def test_import_model_text_does_not_blame_existing_wires(
        monkeypatch, tmp_path):
    """Без прироста линии импорту не приписываются (находка ревью).

    Прежде по одному счётчику ответ утверждал «после импорта они не
    трассированы» про все линии страницы — в том числе уже проложенные.
    """
    _install(monkeypatch, tmp_path, _BridgeRunsContour,
             wires=[object(), object()])

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "новых импорт не добавил" in result
    assert "не пересчитана" not in result
    assert "запекается мусорным" not in result


@pytest.mark.anyio
async def test_import_model_text_reports_no_lines(monkeypatch, tmp_path):
    """Линий нет — ответ говорит и это, а не молчит неопределённо."""
    _install(monkeypatch, tmp_path, _BridgeRunsContour)

    result = _text(await mcp.call_tool(
        "import_model_text", {"model_text": 'block0: (type = "Ступенька")'}))

    assert "Линий связи на странице нет" in result


def test_parse_page_graph_resolves_branches():
    """Ветвь — точка съёма с той же линии: её источник — источник исходной."""
    from simintech_mcp.tools.model_text import parse_page_graph

    text = (
        '  wire_1: (\n'
        '    type = "wire",\n'
        '    points=[(0 , 0),(0 , 10)],\n'
        '    src = "n_dc:out:0",\n'
        '    dst = "ad_1:in:0"\n'
        '  ),\n'
        '  branch_1: (\n'
        '    type = "wire",\n'
        '    points=[(0 , 0),(0 , 20)],\n'
        '    src = "wire_1:0",\n'
        '    dst = "portconnector_0:in:0"\n'
        '  ),\n'
    )

    assert parse_page_graph(text) == [("n_dc", "ad_1"),
                                      ("n_dc", "portconnector_0")]


def test_parse_page_pairs_keeps_direct_pairs_with_port_indexes():
    """Прямые пары — с индексами портов; ветвь пропускается (mcp#33).

    У ветви (`src = "wire_1:0"`) на месте источника не порт, а точка съёма
    с линии: выравнивание по такому адресу поставило бы выход источника под
    случайную точку маршрута.
    """
    from simintech_mcp.tools.model_text import parse_page_pairs

    text = (
        '  wire_1: (\n'
        '    type = "wire",\n'
        '    src = "n_dc:out:0",\n'
        '    dst = "ad_1:in:2"\n'
        '  ),\n'
        '  branch_1: (\n'
        '    type = "wire",\n'
        '    src = "wire_1:0",\n'
        '    dst = "portconnector_0:in:0"\n'
        '  ),\n'
    )

    assert parse_page_pairs(text) == [("n_dc", 0, "ad_1", 2)]
