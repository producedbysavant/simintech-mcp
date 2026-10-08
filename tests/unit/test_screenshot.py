"""Снимок схемы: `savescreenshot` в контуре — файл в каталоге результатов.

Мост подделывается целиком (`ScriptBridge` в контурном ядре `page_script`):
настоящий требует Windows и живого `mmain.exe`, а проверяем мы контракт
инструмента — какой тип формата уходит в тело, куда пишется файл и что ответ.

Подделка моделирует **переход**: файл снимка пишется по пути **из тела**
(иначе тест не заметил бы, что путь потеряли), а исход задаётся тестом — так
проверяются и «не собрался», и «модель не считает».
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simintech_api.core.script_bridge import PageRunResult
from simintech_api.script_probe import (
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_NOT_COMPILED,
    OUTCOME_OK,
    ContourOutcome,
)

from simintech_mcp import session
from simintech_mcp.server import mcp
from simintech_mcp.tools import page_script

from _support import _error, _text, _PlacedBlock, _WireProject


class _FakeClient:
    """COM-клиент: сессии достаточно пробного вызова `GetProcessID`."""

    def get_process_id(self) -> int:
        return 4242


class _FakeProject:
    """Открытый проект: мосту нужен идентификатор."""

    def __init__(self, project_id: int = 7):
        self.id = project_id


def _shot_path_from_body(body: str) -> str:
    """Путь снимка — первый строковый литерал тела."""
    start = body.index('"') + 1
    end = body.index('"', start)
    return body[start:end]


class _BridgeShoots:
    """Мост-подделка: пишет файл снимка по пути из тела и отдаёт исход."""

    #: Магия PNG — то, что клиент увидит в файле по умолчанию.
    payload = b"\x89PNG\r\n\x1a\nfake"
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    restored = ""
    body = ""

    def __init__(self, client, project_id: int):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        ran = (OUTCOME_OK, OUTCOME_MODEL_NOT_RUNNING)
        if type(self).outcome.kind in ran and "savescreenshot(" in body:
            Path(_shot_path_from_body(body)).write_bytes(type(self).payload)
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


class _BridgeSilent(_BridgeShoots):
    """Мост-подделка: тело «отработало», а файл не создан."""

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).body = body
        return PageRunResult(outcome=type(self).outcome,
                             restored_script=type(self).restored)


def _install(monkeypatch, tmp_path: Path, bridge) -> None:
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _FakeProject())
    monkeypatch.setattr(page_script, "ScriptBridge", bridge)


@pytest.mark.anyio
async def test_save_screenshot_writes_png_by_default(monkeypatch, tmp_path):
    """PNG — формат по умолчанию: тип 2 в теле, файл внутри каталога результатов."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = _text(await mcp.call_tool("save_screenshot", {}))

    path = _shot_path_from_body(_BridgeShoots.body)
    assert path.endswith(".png"), f"имя без формата png: {path}"
    assert f'"{path}", 2);' in _BridgeShoots.body, "в тело ушёл не тип 2 (PNG)"
    assert Path(path).is_file(), "файл снимка не создан"
    assert Path(path).read_bytes() == _BridgeShoots.payload
    assert str(tmp_path) in path, "снимок вне каталога результатов"
    assert "Снимок схемы (png)" in text
    assert path in text, "ответ обязан называть путь файла"


@pytest.mark.anyio
@pytest.mark.parametrize("fmt,code", [("bmp", 1), ("svg", 3)])
async def test_save_screenshot_maps_formats_to_types(
        monkeypatch, tmp_path, fmt, code):
    """Форматы отображаются на коды типов функции: BMP — 1, SVG — 3."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    await mcp.call_tool("save_screenshot", {"format": fmt})

    path = _shot_path_from_body(_BridgeShoots.body)
    assert path.endswith(f".{fmt}")
    assert f", {code});" in _BridgeShoots.body, \
        f"формат {fmt} ушёл не как тип {code}"


@pytest.mark.anyio
async def test_save_screenshot_rejects_unknown_format(monkeypatch, tmp_path):
    """Неизвестный формат — отказ со списком известных, а не молчание."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = await _error("save_screenshot", {"format": "tiff"})

    assert "не поддерживается" in text
    assert "png" in text and "bmp" in text and "svg" in text


@pytest.mark.anyio
async def test_save_screenshot_refuses_when_file_missing(monkeypatch, tmp_path):
    """Тело отработало, а файла нет — отказ, а не «снимок: путь» без файла.

    Так выглядит неподходящий тип: savescreenshot молча ничего не создаёт
    (живой замер 03.10.2026: тип 0).
    """
    _install(monkeypatch, tmp_path, _BridgeSilent)

    text = await _error("save_screenshot", {})

    assert "файла снимка нет" in text


@pytest.mark.anyio
async def test_save_screenshot_names_non_running_model(monkeypatch, tmp_path):
    """На несчитающей модели снимок есть — исход называет это, не подменяя успех."""
    class _Stuck(_BridgeShoots):
        outcome = ContourOutcome(kind=OUTCOME_MODEL_NOT_RUNNING, lines=[])

    _install(monkeypatch, tmp_path, _Stuck)

    text = _text(await mcp.call_tool("save_screenshot", {}))

    assert Path(_shot_path_from_body(_Stuck.body)).is_file()
    assert "initialization" in text, \
        "ответ обязан объяснить, почему снимок есть на стоящем расчёте"


@pytest.mark.anyio
async def test_save_screenshot_refuses_when_script_did_not_compile(
        monkeypatch, tmp_path):
    """`not-compiled` — отказ: снимка нет, и это не «модель не считает»."""
    class _Broken(_BridgeShoots):
        outcome = ContourOutcome(kind=OUTCOME_NOT_COMPILED,
                                 lines=["savescreenshot(...)"])

    _install(monkeypatch, tmp_path, _Broken)

    text = await _error("save_screenshot", {})

    assert "не собралось" in text
    assert "окне сообщений" in text, "отказ не говорит, где искать причину"


@pytest.mark.anyio
async def test_save_screenshot_uses_fresh_names(monkeypatch, tmp_path):
    """Имя уникально на вызов: серия «до/после» не затирает предыдущие снимки."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    first_text = _text(await mcp.call_tool("save_screenshot", {}))
    first_path = _shot_path_from_body(_BridgeShoots.body)
    second_text = _text(await mcp.call_tool("save_screenshot", {}))
    second_path = _shot_path_from_body(_BridgeShoots.body)

    assert first_path != second_path, f"имена снимков не уникальны: {first_path}"
    assert Path(first_path).is_file() and Path(second_path).is_file()
    assert first_path in first_text and second_path in second_text


def test_png_size_reads_ihdr_and_rejects_other_formats(tmp_path):
    """Размер полотна читается из заголовка PNG; не-PNG честно отвергается.

    Полотно у среды не постоянно (живой замер 05.10.2026: 1026x580 у части
    снимков и 1026x659 у другой), поэтому `save_screenshot` берёт размер из
    самого снимка. Ошибка чтения не должна выглядеть как «размер есть»:
    у BMP и SVG заголовок другой, и пересчитывать по нему кадр нельзя.
    """
    from simintech_mcp.tools.screenshot import _png_size

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                    + (1026).to_bytes(4, "big") + (659).to_bytes(4, "big"))
    assert _png_size(str(png)) == (1026, 659)

    other = tmp_path / "shot.bmp"
    other.write_bytes(b"BM" + b"\x00" * 30)
    assert _png_size(str(other)) is None
    assert _png_size(str(tmp_path / "missing.png")) is None


# ─── Высокое качество: подъём окна (hires) и плитки (zoom) ──────────────
#
# Живые замеры 08.10.2026, на которых стоит контракт этих тестов: снимок
# равен клиентской области окна (−32/−200); окно поднимается `normalizeform`
# + `setformbounds` и видно со следующего прогона (асинхронность); кадр
# (`createmodel`) и снимок в одном прогоне дают тот же PNG, что и после
# отдельной записи кадра.


def _png_payload(width: int, height: int) -> bytes:
    """Мини-PNG: подпись и `IHDR` с размерами — этого хватает `_png_size`."""
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big"))


class _BridgeWindow:
    """Мост-подделка оконных режимов: отвечает по виду тела.

    Тело с `getformbounds` получает заготовленный ответ
    (`bounds_replies` — по порядку: «до», затем «после подъёма»), тело со
    снимком пишет PNG заданного размера по пути **из тела**. Все тела
    запоминаются: по ним проверяются и подъём окна, и его возврат.
    """

    bodies: "list[str]" = []
    bounds_replies: "list[list[str]]" = []
    shot_size = (3968, 900)
    outcome = ContourOutcome(kind=OUTCOME_OK, lines=[])
    restored = ""

    def __init__(self, client, project_id):
        self.project_id = project_id

    def run_page_script(self, body: str, result_path: Path) -> PageRunResult:
        type(self).bodies.append(body)
        lines: "list[str]" = []
        if "getformbounds" in body and type(self).bounds_replies:
            lines = type(self).bounds_replies.pop(0)
        if "savescreenshot(" in body:
            Path(_shot_path_from_body(body)).write_bytes(
                _png_payload(*type(self).shot_size))
        return PageRunResult(
            outcome=ContourOutcome(kind=type(self).outcome.kind, lines=lines),
            restored_script=type(self).restored)


def _install_windowed(monkeypatch, tmp_path: Path, blocks) -> None:
    """Подмена для оконных режимов: проект с блоками (нужна рамка модели).

    Состояние моста сбрасывается полностью: anyio прогоняет каждый тест
    дважды (asyncio и trio), и `shot_size`, забытый тестом про пересчёт
    кадра, иначе протёк бы в соседние.
    """
    _BridgeWindow.bodies = []
    _BridgeWindow.bounds_replies = []
    _BridgeWindow.shot_size = (3968, 900)
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_client", _FakeClient())
    monkeypatch.setattr(session, "_project", _WireProject(blocks))
    monkeypatch.setattr(page_script, "ScriptBridge", _BridgeWindow)


def _tall_blocks(top_y: float, bottom_y: float):
    """Два блока, задающие рамку страницы высотой `bottom_y - top_y`."""
    blocks = {"A": _PlacedBlock("A", 1, class_name="Константа"),
              "B": _PlacedBlock("B", 2, class_name="Константа")}
    blocks["A"].center = (0.0, top_y)
    blocks["B"].center = (0.0, bottom_y)
    return blocks


def _shot_bodies() -> "list[str]":
    return [body for body in _BridgeWindow.bodies if "savescreenshot(" in body]


@pytest.mark.anyio
async def test_hires_raises_window_and_restores_it(monkeypatch, tmp_path):
    """`hires` поднимает окно, снимает на его полотне и возвращает границы.

    Порядок обязателен: подъём — один прогон (в нём же читаются границы
    «до»), фактические границы — следующий (подъём асинхронен), снимок —
    после них, возврат — в конце.
    """
    _install_windowed(monkeypatch, tmp_path, _tall_blocks(0.0, 100.0))
    _BridgeWindow.bounds_replies = [
        ["before=1920,137,1920,903", "raised"],
        ["bounds=0,0,4000,1100"],
    ]

    text = _text(await mcp.call_tool("save_screenshot", {"hires": True}))

    joined = "\n".join(_BridgeWindow.bodies)
    assert "normalizeform" in joined, "без него setformbounds молчит"
    assert "setformbounds(0, 0, 4000, 1100)" in joined, "окно не поднято"
    assert "setformbounds(1920, 137, 1920, 903)" in joined, \
        "окно не возвращено прежним"
    assert "Окно под снимок: 1920x903 → 4000x1100" in text
    assert "Окно возвращено: 1920x903" in text

    shots = _shot_bodies()
    assert len(shots) == 1, "hires — один файл"
    assert "createmodel" in shots[0], "кадр и снимок — одним прогоном"
    shot = _shot_path_from_body(shots[0])
    assert Path(shot).is_file()


@pytest.mark.anyio
async def test_hires_keeps_big_window_untouched(monkeypatch, tmp_path):
    """Окно не меньше целевого — не трогаем: ни подъёма, ни возврата.

    Тело подъёма фейк исполнить не может (условие живёт в среде), поэтому
    проверяется достижимый контракт: возврата границ нет, повторного чтения
    «после» нет, а ответ называет, что окно уже большое.
    """
    _install_windowed(monkeypatch, tmp_path, _tall_blocks(0.0, 100.0))
    _BridgeWindow.bounds_replies = [["before=4000,0,4000,1100"]]

    text = _text(await mcp.call_tool("save_screenshot", {"hires": True}))

    joined = "\n".join(_BridgeWindow.bodies)
    assert "setformbounds(1920, 137, 1920, 903)" not in joined, \
        "возврат границ без подъёма — правка чужого состояния"
    assert "Окно не меньше целевого" in text
    assert len(_shot_bodies()) == 1


@pytest.mark.anyio
async def test_hires_recomputes_frame_on_actual_canvas(monkeypatch, tmp_path):
    """Клиент окна разошёлся с предсказанным — кадр пересчитывается.

    Так выглядит кламп окна на другой машине: предсказали 3968×900, а
    снимок вышел 2048×700 — кадр обязан догнать факт, а не молчать.
    """
    _install_windowed(monkeypatch, tmp_path, _tall_blocks(0.0, 100.0))
    _BridgeWindow.bounds_replies = [
        ["before=1920,137,1920,903", "raised"],
        ["bounds=0,0,4000,1100"],
    ]
    _BridgeWindow.shot_size = (2048, 700)

    text = _text(await mcp.call_tool("save_screenshot", {"hires": True}))

    shots = _shot_bodies()
    assert len(shots) == 2, "кадр не пересчитан под фактическое полотно"
    assert "пересчитан" in text
    assert shots[0] != shots[1], "повтор снимка тем же кадром — не пересчёт"


@pytest.mark.anyio
async def test_zoom_shoots_tile_grid(monkeypatch, tmp_path):
    """`zoom` снимает плитками: файлов по числу плиток, кадр — своим телом.

    Схема высотой ~2040 при клиенте 900 и масштабе 1 требует трёх плиток
    (шаг с перекрытием); каждая — свой прогон с кадром и снимком.
    """
    _install_windowed(monkeypatch, tmp_path, _tall_blocks(0.0, 2000.0))
    _BridgeWindow.bounds_replies = [
        ["before=1920,137,1920,903", "raised"],
        ["bounds=0,0,4000,1100"],
    ]

    text = _text(await mcp.call_tool("save_screenshot", {"zoom": 1.0}))

    shots = _shot_bodies()
    assert len(shots) == 3, f"плиток не три: {len(shots)}"
    for body in shots:
        assert "createmodel" in body, "кадр плитки обязан ехать с её снимком"
    assert "плитками: 3 шт." in text
    assert "масштаб 1" in text
    for body in shots:
        assert Path(_shot_path_from_body(body)).is_file()
    joined = "\n".join(_BridgeWindow.bodies)
    assert "setformbounds(1920, 137, 1920, 903)" in joined, \
        "окно не возвращено после плиток"
    assert _BridgeWindow.bounds_replies == [], "не все границы прочитаны"


@pytest.mark.anyio
async def test_zoom_refuses_too_many_tiles(monkeypatch, tmp_path):
    """Плиток больше предела — отказ с советом, а не сотня прогонов.

    И окно при этом возвращается: отказ в середине не имеет права оставить
    его раздутым (`finally`).
    """
    _install_windowed(monkeypatch, tmp_path, _tall_blocks(0.0, 40000.0))
    _BridgeWindow.bounds_replies = [
        ["before=1920,137,1920,903", "raised"],
        ["bounds=0,0,4000,1100"],
    ]

    text = await _error("save_screenshot", {"zoom": 1.0})

    assert "больше предела" in text
    assert "Уменьшите zoom" in text
    joined = "\n".join(_BridgeWindow.bodies)
    assert "setformbounds(1920, 137, 1920, 903)" in joined, \
        "окно не возвращено при отказе"
    assert _shot_bodies() == [], "снимков при отказе быть не должно"


@pytest.mark.anyio
async def test_zoom_and_hires_are_mutually_exclusive(monkeypatch, tmp_path):
    """Два режима сразу — отказ до COM: они по-разному строят кадр."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = await _error("save_screenshot", {"hires": True, "zoom": 1.0})

    assert "один режим" in text


@pytest.mark.anyio
@pytest.mark.parametrize("zoom", [0.01, 5.0])
async def test_zoom_rejects_out_of_range(monkeypatch, tmp_path, zoom):
    """`zoom` вне пределов — отказ со ссылкой на альтернативу."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = await _error("save_screenshot", {"zoom": zoom})

    assert "вне пределов" in text
    assert "hires" in text


@pytest.mark.anyio
async def test_large_modes_refuse_svg(monkeypatch, tmp_path):
    """SVG — вектор: полотно его не ограничивает, hires/zoom не нужны."""
    _install(monkeypatch, tmp_path, _BridgeShoots)

    text = await _error("save_screenshot", {"format": "svg", "hires": True})

    assert "вектор" in text
    assert "hires" in text


def test_tile_centers_cover_frame_with_overlap():
    """Плитки покрывают рамку от края до края с перекрытием, а не наугад."""
    from simintech_mcp.tools.screenshot import TILE_MARGIN, tile_centers

    centers = tile_centers((0.0, 0.0, 0.0, 2368.0), 1696.0, 900.0, 1.0)
    assert len(centers) == 3
    assert centers[0][1] - 450.0 == 0.0, "первая плитка не от верхнего края"
    assert centers[-1][1] + 450.0 == 2368.0, "последняя не до нижнего края"
    first, second = centers[0][1], centers[1][1]
    assert 900.0 - (second - first) >= TILE_MARGIN, "стык без перекрытия"
    assert all(cx == 0.0 for cx, _ in centers), "лишние колонки по ширине"


def test_tile_centers_single_tile_for_small_model():
    """Схема влезает целиком — одна плитка по центру рамки."""
    from simintech_mcp.tools.screenshot import tile_centers

    assert tile_centers((10.0, 20.0, 110.0, 220.0),
                        3968.0, 900.0, 1.0) == [(60.0, 120.0)]
