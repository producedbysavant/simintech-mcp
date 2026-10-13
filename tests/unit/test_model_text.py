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

    def __init__(self, wires=None, blocks=None):
        #: Линии страницы: подсказка о трассировке считает их число.
        self._wires = list(wires or [])
        #: Блоки — подписям экспорта (ячейки памяти, адресация портов).
        self._blocks = list(blocks or [])

    def get_blocks(self) -> list:
        return list(self._blocks)

    def get_wires(self) -> list:
        return list(self._wires)


class _FakeProject:
    """Открытый проект: мосту нужен идентификатор, отчёту — страница."""

    def __init__(self, project_id: int = 7, wires=None, blocks=None):
        self.id = project_id
        self._wires = list(wires or [])
        self._blocks = list(blocks or [])

    def get_current_page(self):
        return _Page(self._wires, self._blocks)


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


def _install(monkeypatch, tmp_path: Path, bridge, wires=None, blocks=None) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project",
                        _FakeProject(wires=wires, blocks=blocks))
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
async def test_export_model_text_signs_cells_and_branches(monkeypatch, tmp_path):
    """Ответ несёт подписи ячеек памяти и ветвей — отдельными строками.

    Текстом выгрузки идентичность не читается (issue #153): имя ячейки не
    печатается вовсе, а родителя ветви не отличить от чужих проводов с тем же
    автоименем. Подписи идут перед текстом, а не правкой текста: текст — вход
    `import_model_text`.
    """

    class _BridgeProbe(_BridgeRunsContour):
        outcome = ContourOutcome(kind=OUTCOME_OK, lines=[
            "W|9|92+64i|92+96i|0|0",
            "W|10|92+96i|24+40i|9|2",
            "WDONE",
        ])

    class _Cell:
        def __init__(self, block_id, name, class_name, portnames):
            self.id = block_id
            self.class_name = class_name
            self._name = name
            self._portnames = portnames

        def get_name(self) -> str:
            return self._name

        def get_property(self, name: str) -> str:
            return self._portnames

        def get_port_count(self) -> int:
            return 0

        def get_in_port(self, index: int = 0):
            raise RuntimeError("у фейка нет портов")

        def get_out_port(self, index: int = 0):
            raise RuntimeError("у фейка нет портов")

    class _Wire:
        def __init__(self, wire_id):
            self.id = wire_id

    _install(monkeypatch, tmp_path, _BridgeProbe,
             wires=[_Wire(9), _Wire(10)],
             blocks=[_Cell(1, "ToMem_4", "В память", "#RA402")])

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert "Ячейки памяти (PortNames): ToMem_4 = #RA402" in result
    assert "Ветви" in result
    assert "dst=?" in result, "нерезолвнутый конец не назван честно"
    assert "(узел 1)" in result
    assert result.index("Ячейки памяти") < result.index("Текст модели"), \
        "подписи должны идти до текста"
    assert _BridgeProbe.body.count("getwirestartpointcoord") == 2, \
        "тело контура не получило строку подписи на каждую линию"


def test_parse_wire_probe_requires_marker():
    """Парсер подписи о ветвях: без маркера — None, а не «ветвей нет»."""
    from simintech_mcp.tools.model_text import _parse_wire_probe

    assert _parse_wire_probe(None) is None, "сбой прогона выдан за подпись"
    assert _parse_wire_probe(["W|9|92+64i|92+96i|0|0"]) is None
    assert _parse_wire_probe(["W|1|0+0i|24+40i|0|0", "WDONE"]) == {
        1: ("0+0i", "24+40i", 0, 0)}
    assert _parse_wire_probe(["флуд", "WDONE"]) == {}


def test_wire_probe_empty_is_not_a_break():
    """Страница без проводов — пустая подпись, а не ложный обрыв (ревью #154).

    Прежде тело подписи подавалось только при непустом списке линий, маркер
    не писался, и ответ страницы без проводов сообщал «оборвались до
    маркера». Теперь прогон не запускается (проводов нет — ветвей нет), а
    `[]` честно читается как пустая подпись; тело же маркер пишет всегда —
    на случай, если его подадут.
    """
    from simintech_mcp.tools.model_text import (
        _parse_wire_probe,
        _wire_probe_body,
    )

    assert '"WDONE"' in _wire_probe_body([]), "маркер не пишется на пустом теле"
    assert _parse_wire_probe([]) == {}, "пустой прогон выдан за обрыв"
    assert _parse_wire_probe(["WDONE"]) == {}


def test_branch_lines_name_parent_by_ends():
    """Родитель ветви называется концами, а не автоименем (issue #153)."""
    from simintech_mcp.tools.model_text import _branch_lines, _point_cell

    assert _point_cell("92+64i") == (92, 64)
    assert _point_cell("(-160-992i)") == (-160, -992)
    assert _point_cell("92.5+64i") == (92, 64), \
        "дробная координата дала чужую клетку (находка ревью #154)"
    assert _point_cell("без точки") is None

    probe = {9: ("80+64i", "192+64i", 0, 0), 10: ("192+64i", "24+40i", 9, 2),
             11: ("0+0i", "0+0i", 9, 1)}
    addressing = {(80, 64): "k_0:out:0", (192, 64): "ad_1:in:0",
                  (24, 40): "portconnector_0:in:0"}
    note = _branch_lines(probe, addressing)
    assert "dst=portconnector_0:in:0" in note
    assert "src=k_0:out:0 → dst=ad_1:in:0" in note
    assert "(узел 1)" in note
    assert note.count("— на линии") == 1, \
        "пустой сегмент ветвления (0+0i) попал в подпись"
    assert _branch_lines({9: ("80+64i", "192+64i", 0, 0)}, addressing) == "", \
        "линия без родителя попала в подпись о ветвях"
    assert "не прочитано" in _branch_lines(None, addressing), \
        "сбой прогона выдан за «ветвей нет»"
    note = _branch_lines(probe, {})
    assert "dst=?" in note and "src=? → dst=?" in note, \
        "конец без порта не назван честно"


def test_identity_notes_read_portnames():
    """Подписи: имя переменной из PortNames; не-ячейки не попадают."""
    from simintech_mcp.tools.model_text import _identity_notes

    class _Cell:
        def __init__(self, block_id, name, class_name, portnames):
            self.id = block_id
            self.class_name = class_name
            self._name = name
            self._portnames = portnames

        def get_name(self) -> str:
            return self._name

        def get_property(self, name: str) -> str:
            return self._portnames

    note = _identity_notes([
        _Cell(1, "ToMem_4", "В память", "#RA402"),
        _Cell(2, "FromMem_1", "Из памяти", "#RA402"),
        _Cell(3, "k_0", "Константа", "1"),
        _Cell(4, "ToMem_5", "В память", "#A\r\n#B"),
    ], None)
    assert "ToMem_4 = #RA402" in note
    assert "FromMem_1 = #RA402" in note
    assert "k_0" not in note
    assert "ToMem_5 = #A #B" in note, \
        "переносы строк PortNames не нормализованы"
    low = _identity_notes([_Cell(3, "k_0", "Константа", "1")], None)
    assert "Ячейки памяти" not in low, "не-ячейки дали строку подписи памяти"


@pytest.mark.anyio
async def test_export_model_text_survives_probe_failure(monkeypatch, tmp_path):
    """Сбой прогона подписи не отменяет выгрузку — только «не прочитано».

    Подпись собирается отдельным прогоном контура (находка ревью PR #154):
    в общем теле её сбой уводил исход в `aborted`, и
    `refuse_contour_failure` отменял выгрузку — хотя текст уже записан.
    """

    class _BridgeProbeFails(_BridgeRunsContour):
        def run_page_script(self, body, result_path):
            if "savemodeltofile(" not in body:
                raise ScriptBridgeError("подпись о ветвях не собралась")
            return super().run_page_script(body, result_path)

    class _Wire:
        def __init__(self, wire_id):
            self.id = wire_id

    _install(monkeypatch, tmp_path, _BridgeProbeFails, wires=[_Wire(9)])

    result = _text(await mcp.call_tool("export_model_text", {}))

    assert _BridgeRunsContour.payload in result, "выгрузка отменена сбоем подписи"
    assert "Ветви: не прочитано" in result


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


def test_text_wire_roles_scan_references_not_neighbourhood():
    """Роли проводов — по ссылкам на блоки текста, а не по соседству записей.

    Развилка выбирает предупреждение ответа на импорт (живое воспроизведение
    fdd002, 10.10.2026), и дефект бьёт по проводам к блокам ЭТОГО ЖЕ текста:
    дозагрузка блока к расставленной модели не должна пугать чужой
    геометрией (находка ревью PR #150). Ветвь (`src = "wire:0"`) разрешается
    по родителю — как в выгрузке.
    """
    from simintech_mcp.tools.model_text import _text_wire_roles

    def wire(name: str, src: str, dst: str) -> str:
        """Запись провода в форме выгрузки: адреса — отдельными строками."""
        return (f'  {name}: (\n'
                f'    type = "wire",\n'
                f'    src = "{src}",\n'
                f'    dst = "{dst}"\n'
                f'  ),')

    assert _text_wire_roles('k0: (type = "Константа")') == (False, False)
    assert _text_wire_roles(wire("w0", "old_a:out:0", "old_b:in:0")
                            ) == (True, False)
    assert _text_wire_roles(
        'k0: (type = "Ступенька",\n points = [(0, 0)]),\n'
        + wire("w0", "k0:out:0", "b0:in:0")) == (True, True)
    # Ветвь от провода текста: источник разрешается до `k0` — он в тексте;
    # та же ветвь без `k0` в тексте — чужой ссылкой не считается.
    branch = (wire("w1", "k0:out:0", "b0:in:0") + "\n"
              + wire("w2", "w1:0", "b1:in:1"))
    assert _text_wire_roles('k0: (type = "Ступенька"),\n' + branch
                            ) == (True, True)
    assert _text_wire_roles(branch) == (True, False)
    # Граница скана названа честно: адреса читаются в форме выгрузки (каждый —
    # своей строкой). Однострочная запись не разбирается — скан молчит, и
    # ответ уходит в нейтральную ветку, а не выдаёт догадку за разбор.
    assert _text_wire_roles(
        'w0: (type = "wire", src = "old_a:out:0", dst = "old_b:in:0")'
    ) == (False, False)


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
async def test_import_model_text_warns_baked_geometry_of_same_text_wires(
        monkeypatch, tmp_path):
    """Провода на блоки этого же текста — ответ называет дефект прямо.

    Маршруты проводов, поданных вместе со своими блоками, среда запекает до
    пересчёта габаритов и не пересчитывает ничем (живое воспроизведение
    fdd002, 10.10.2026); прежний ответ звал «`layout_place` проложит линии» —
    и клиент шёл чинить нормализацией то, что ею не чинится.
    """
    _install(monkeypatch, tmp_path, _BridgeAddsWires)
    text = ('k0: (type = "Константа"),\n'
            'w0: (\n'
            '  type = "wire",\n'
            '  src = "k0:out:0",\n'
            '  dst = "k0:in:0"\n'
            ')')

    result = _text(await mcp.call_tool("import_model_text", {"model_text": text}))

    assert "Линии связи: +2" in result
    assert "запекает мусорными" in result
    assert "пересоздание" in result
    assert "`connect`" in result, "проверенный путь ремонта не назван"
    assert "Блоки расставит" in result, "рекомендация расстановки потеряна"
    assert "ОДНИМ текстом" in result, \
        "исключение для моделей с памятью не названо"
    assert "проложит" not in result, \
        "ответ снова обещает прокладку запечённой геометрии"


@pytest.mark.anyio
async def test_import_model_text_patch_import_is_not_frightened(
        monkeypatch, tmp_path):
    """Провода на существующие блоки (дозагрузка) — без диагноза дефекта.

    В тексте есть и объект, и провода, но концы проводов — на блоках ВНЕ
    текста: дефект запекания бьёт по проводам к блокам этого же текста, и
    такой текст не должен получать диагноз «маршруты запечены мусорными»
    (находка ревью PR #150).
    """
    _install(monkeypatch, tmp_path, _BridgeAddsWires)
    text = ('k_new: (type = "Константа"),\n'
            'w0: (\n'
            '  type = "wire",\n'
            '  src = "old_a:out:0",\n'
            '  dst = "old_b:in:0"\n'
            ')')

    result = _text(await mcp.call_tool("import_model_text", {"model_text": text}))

    assert "Линии связи: +2" in result
    assert "ссылок на блоки этого же текста в них не видно" in result, \
        "развилка «ссылок нет» не названа"
    assert "запекает мусорными" not in result, \
        "дозагрузка обвинена дефектом геометрии"


@pytest.mark.anyio
async def test_import_model_text_wires_only_text_names_order(monkeypatch, tmp_path):
    """Текст только с проводами — предупреждение о запекании без диагноза.

    Провода к уже существующим блокам — законный отдельный импорт; ответ не
    обвиняет его дефектом смешанного текста, но говорит, что маршрут среда
    считает сама при создании, и что пустой маршрут дорисовывает
    `layout_place`, а запечённый — нет.
    """
    _install(monkeypatch, tmp_path, _BridgeAddsWires)
    text = ('w0: (\n'
            '  type = "wire",\n'
            '  src = "k0:out:0",\n'
            '  dst = "k0:in:0"\n'
            ')')

    result = _text(await mcp.call_tool("import_model_text", {"model_text": text}))

    assert "Линии связи: +2" in result
    assert "точки текста игнорируются" in result
    assert "layout_place(normalize_only=True)" in result
    assert "запекает мусорными" not in result, \
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
    assert "запекает мусорными" not in result


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
