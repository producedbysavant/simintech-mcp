"""Проверка оформления модели — машинный чек-лист из issue #24 (п.4).

Проверяется по фактической геометрии, а не глазами:

* наложения габаритов блоков — нет свободного буфера (через COM, `Points`);
* подписи порт-блоков шире рамки — грубая оценка по `PortNames` и ширине;
  это **примечание, не дефект**: свисание подписи за рамку владелец
  допускает, если текст читается (замечание 07.10.2026);
* центры блоков — на разметке 8 px (1 квадратик = 8×8, стандарт 02.10.2026);
* пустые порты — `getportwireid` (функция языка, идёт контуром страницы);
* связи «прямая / с изломом» — по координатам концов
  (`getwirestartpointcoord`/`getwireendpointcoord`, тоже контур).

Двумя слоями, а не одним: габариты и подписи читаются через COM, а порты и
концы линий — нет, их отдают только функции языка. Контурный слой сдвигает
модельное время — как `step`; об этом инструмент предупреждает в докстринге.

**Чего проверка не умеет** (честная граница, а не пропуск): промежуточные
точки линий (изломы) через COM и по документированным функциям языка не
читаются — доступны только концы, поэтому «диагональных сегментов» здесь
нет, а «с изломом» — это связь, концы которой не совпадают ни по X, ни по Y.
Разметка проверяется по центрам блоков (шаг 8 px); шаг портов и величины
зазоров между блоками — вне проверки.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api.catalog import NON_BLOCK_CLASSES
from simintech_api.core.block import Block
from simintech_api.core.script_bridge import ScriptBridge
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.script_probe import (
    OUTCOME_ABORTED,
    OUTCOME_MODEL_NOT_RUNNING,
    OUTCOME_NOT_COMPILED,
    OUTCOME_OK,
    OUTCOME_SECTION_NOT_RUN,
)

from .. import runtime, sandbox, session
from ..app import mcp
from ..geometry import (
    CHAR_WIDTH_ESTIMATE, STUB, WIRE_PITCH, channel_width, collinear_overlap,
    cut_sizes, overlaps, predicted_polyline, proper_crossing, rect_of,
    segment_hits_rect, segments_of)
from .page_script import fresh_name

#: Базы имён контурных файлов проверки. Отчёт — свой файл, как в живых
#: пробах: дескриптор моста телу недоступен по имени (он назван случайной
#: частью метки), поэтому тело открывает файл само, а контур пишет маркеры в
#: свой. Полные имена уникальны на вызов (`page_script.fresh_name`, тот же
#: приём, что в #40): запертый прошлым обрывом файл (WinError 32) новому
#: вызову не мешает и не может быть выдан за отчёт этого вызова.
REPORT_FILE = "check-model-report.txt"

#: База имени файла маркеров контура (исход классифицирует библиотека, не мы).
MARKER_FILE = "check-model-contour.txt"

#: Сколько записей каждого вида перечислять в ответе; счётчики — всегда полные.
MAX_REPORTED = 10

#: Разметка схемы: 1 квадратик = 8×8 px (стандарт оформления владельца,
#: 02.10.2026: блоки 16/32 высоты, порты в `cx±16, cy` — всё кратно 8).
GRID_STEP = 8.0

#: Классы-«оформление»: не блоки — `Points` это якорь текста (один), `size` —
#: типовая карточка 60×40, а не габарит (замер 02.10.2026: `constLabel`).
#: Набор — библиотечный (`simintech_api.catalog.NON_BLOCK_CLASSES`:
#: `TextLabel`, `RotatedText`, «Комментарий», `Rectangle`…): подпись,
#: нарисованная в GUI, — это не только `constLabel` (находка ревью
#: 02.10.2026). В проверках габаритов и разметки не участвуют: иначе дают
#: ложные «наложения» и «вне сетки».
LABEL_CLASSES = NON_BLOCK_CLASSES


def _off_grid(rect: "tuple[float, float, float, float]") -> bool:
    """Вне ли центр габарита разметки 8 px.

    Допуск 0.5 px: координата приходит из сложений/делений, и 24.0000001 —
    это 24, а не «вне сетки».
    """
    for value in ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0):
        if abs(round(value / GRID_STEP) * GRID_STEP - value) > 0.5:
            return True
    return False


_POINT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([+-])\s*(\d+(?:\.\d+)?)i")

_KIND_TEXT = {
    OUTCOME_OK: "скрипт собрался и отработал",
    OUTCOME_MODEL_NOT_RUNNING: "скрипт отработал, но модель не считает",
    OUTCOME_NOT_COMPILED: ("скрипт не собрался — среда об ошибке молчит; текст "
                           "ошибки — в окне сообщений редактора SimInTech"),
    OUTCOME_ABORTED: "скрипт оборвался на исполнении",
    OUTCOME_SECTION_NOT_RUN: ("секция `initialization` не выполнилась, хотя "
                              "расчёт шёл"),
}


def bridge() -> ScriptBridge:
    """Мост для текущего проекта — общая часть с соседними инструментами."""
    return ScriptBridge(session.ensure_client(), session.ensure_project().id)


def _rect_path() -> Path:
    return Path(os.path.join(sandbox.output_root(), fresh_name(REPORT_FILE)))


def _marker_path() -> Path:
    return Path(os.path.join(sandbox.output_root(), fresh_name(MARKER_FILE)))


#: Пути контурных файлов **предыдущего** вызова этой сессии. Имена уникальны
#: на вызов, и без уборки каждый вызов оставлял бы в песочнице два новых
#: файла без предела (находка ревью #42). Убираются только свои же прошлые
#: файлы — чужие не трогаются; запертый (обрыв) снять не даст, `OSError`
#: пропускается, освободится при выходе mmain.
_PREVIOUS_PATHS: List[Path] = []


def _sweep_previous() -> None:
    """Убрать контурные файлы предыдущего вызова (best-effort)."""
    for stale in _PREVIOUS_PATHS:
        try:
            stale.unlink()
        except OSError:
            pass
    _PREVIOUS_PATHS.clear()


def read_port_names(block: Block) -> Optional[List[str]]:
    """Имена сигналов из `PortNames`; `None` — прочитать не удалось.

    Пустой список и «не прочиталось» — разные вещи: у обычного блока портов
    по имени нет вовсе, а нечитаемое свойство обязано быть названо.
    """
    try:
        value = block.get_property("PortNames")
    except Exception:                                          # noqa: BLE001
        return None
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


_SAFE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _script_safe(name: str) -> bool:
    """Годится ли имя блока для вставки в текст скрипта.

    Имя вставляется **без кавычек** — язык адресует блоки идентификаторами,
    а не строками, — поэтому всё за пределами ASCII-идентификатора
    (скобки, точки с запятой, пробелы, кавычки, не-ASCII) это потенциальная
    инъекция в исполняемый текст: проект, пришедший извне, может содержать
    имя вида `k); чужой вызов; (`. Такие блоки **пропускаются** с названной
    причиной, а не экранируются: проверенного способа экранирования имени
    блока у языка нет, а «починить и надеяться» — не защита.
    """
    # `fullmatch`, а не `match` с `$`: в Python `$` совпадает и перед хвостовым
    # переводом строки — имя `k_0\n` прошло бы охрану и легло в текст скрипта
    # без кавычек (находка ревью 02.10.2026).
    return bool(_SAFE_NAME_RE.fullmatch(name))


def _outside_sheet(rect: "tuple[float, float, float, float]") -> bool:
    """Выходит ли габарит блока за начало листа (левый или верхний край в минусе).

    Раскладка начинается с отступа (`layout_place`, `MARGIN`), поэтому
    отрицательный левый или верхний край — ровно тот случай, из-за которого
    схема выглядела срезанной: половина блока уходила за край листа (живой
    случай 04.10.2026: блоки с центром x=0 теряли левую половину).
    """
    left, top, _right, _bottom = rect
    return left < 0.0 or top < 0.0


def _check_script(report_path: Path, port_blocks: List[Tuple[str, int]],
                  wire_ids: List[int]) -> str:
    """Скрипт контура: концы каждой линии и пустые порты — построчно в отчёт.

    Без циклов языка: всё развёрнуто по данным, известным снаружи (имена
    блоков, число портов, id линий) — цикл пришлось бы писать на языке, у
    которого синтаксис петель в этом контуре не проверен живьём.

    **Своя секция — намеренно.** Тело отдаётся `run_page_script`, который
    оборачивает его в свою `initialization` (дескриптор моста телу недоступен
    по имени — он назван случайной частью метки, `build_page_script`), поэтому
    своя секция с `var chk_f` оказывается вложенной. Живой прогон 02.10.2026
    (демо-проект) подтвердил, что среда принимает эту форму: отчёт пришёл
    полным — пустые порты и классификация трёх линий. Похожая вольность уже
    работает у проб моста (`if firststep then begin var …`). Последняя строка
    отчёта — `DONE`: по ней проверка отличает полный отчёт от оборванного.
    """
    literal = str(report_path).replace("\\", "/")
    lines = [
        "initialization",
        "  var chk_f: integer;",
        f'  chk_f = createfile("{literal}", -1);',
    ]
    for wire_id in wire_ids:
        lines.append(
            f'  writelnutf8(chk_f, "W|{wire_id}|" '
            f"+ getwirestartpointcoord({wire_id}) "
            f'+ "|" + getwireendpointcoord({wire_id}));')
    for name, port_count in port_blocks:
        for index in range(port_count):
            lines.append(
                f"  if getportwireid(getblockportid({name}, {index})) = 0 "
                f'then begin writelnutf8(chk_f, "EMPTY|{name}|{index}"); '
                f"end;")
    # `DONE` — признак полного отчёта: по нему вердикты «все прямые» и
    # «пустых портов нет» не выдаются по оборванному телу (находка ревью
    # 02.10.2026).
    lines.append('  writelnutf8(chk_f, "DONE");')
    lines.append("  freeobject(chk_f);")
    lines.append("end;")
    return "\n".join(lines) + "\n"


def _parse_point(text: str) -> Optional[Tuple[float, float]]:
    """Разобрать точку в форме «x+yi» (так печатает её строка языка).

    Скобки и пробелы вокруг точки допускаются: живой формат печати ещё не
    замерялся, а «(16-56i)» и «16-56i» — одна и та же точка.
    """
    match = _POINT_RE.search(text)
    if not match:
        return None
    x = float(match.group(1))
    y = float(match.group(3))
    if match.group(2) == "-":
        y = -y
    return (x, y)


@mcp.tool()
@runtime.com_threaded
def check_model_layout() -> str:
    """Проверить оформление текущей страницы: наложения, подписи, порты, связи.

    Машинная версия чек-листа (issue #24, п.4). Проверяется по фактической
    геометрии: наложения — по габаритам (центр `Points` ± `size`); подписи —
    грубая оценка «длина имени сигнала × 8 px» против ширины блока (точную
    ширину шрифта COM не отдаёт); центры — на разметке 8 px (1 квадратик =
    8×8, стандарт 02.10.2026); пустые порты — `getportwireid`; связи
    «прямая / с изломом» — по координатам концов линий.

    **Проверка идёт контуром страницы и сдвигает модельное время** — как
    `step`: порты и концы линий через COM не читаются, их отдают функции
    языка. На остановленном проекте расчёт запускается и останавливается
    самим контуром. Проверяется **текущая** страница — та же, куда контур
    ставит скрипт (`GetCurentPage`): прежде COM-чтения шли по главной, и на
    субмодели половины одного вердикта описывали разные страницы (находка
    ревью 02.10.2026).

    **Чего проверка не умеет** (граница названа, чтобы «не найдено» не
    читалось как «всё хорошо»):

    * промежуточные точки линий (изломы) не читаются — «с изломом» значит
      лишь, что концы не совпали ни по X, ни по Y;
    * шаг портов и величины зазоров между блоками не проверяются — вне
      критерия разметки (на разметке проверяются центры блоков);
    * пустой порт — это порт без линии; для выходных портов это не всегда
      дефект, решает человек.
    * **кадр (масштаб и смещение вида) не проверяется**: свойства страницы
      читаются только выгрузкой текста, а языковой способ чтения живым замером
      не подтверждён (05.10.2026 — опыт не вернул ответа). Проверка
      ограничивается тем, что видно без кадра, — «блок выходит за начало
      листа». Сам кадр подгоняет `save_screenshot` (`fit=True`).

    Ничего не меняет в модели: контур ставит свой скрипт и возвращает прежний
    (`run_page_script` — тот же механизм и та же гарантия).
    """
    project = session.ensure_project()
    page = project.get_current_page()

    # ── Слой COM: габариты, подписи ────────────────────────────────
    geometry: List[Tuple[str, Tuple[float, float, float, float]]] = []
    no_geometry: List[str] = []
    label_warnings: List[str] = []
    off_grid: List[str] = []
    port_blocks: List[Tuple[str, int]] = []
    port_skipped: List[str] = []
    outside_sheet: List[str] = []
    for block in page.get_blocks():
        try:
            name = block.get_name()
        except Exception:                                      # noqa: BLE001
            name = str(block.id)
        try:
            if block.class_name in LABEL_CLASSES:
                continue
        except Exception:                                      # noqa: BLE001
            pass
        # Размер читается ДО габарита: габарит строится как центр(Points) ±
        # size/2 (замер 02.10.2026 — min/max полилинии габаритом не является).
        try:
            size = block.get_size()
        except Exception:                                      # noqa: BLE001
            size = None
        try:
            rect = rect_of(block.get_points(), size) if size else None
        except Exception:                                      # noqa: BLE001
            rect = None
        if rect is None:
            no_geometry.append(name)
        else:
            geometry.append((name, rect))
            if _off_grid(rect):
                off_grid.append(name)
            if _outside_sheet(rect):
                outside_sheet.append(name)
        width = size[0] if size else None
        names = read_port_names(block)
        if width and names:
            longest = max(names, key=len)
            estimate = len(longest) * CHAR_WIDTH_ESTIMATE
            if estimate > width:
                label_warnings.append(
                    f"{name}: «{longest}» (~{estimate:g} px) при ширине "
                    f"{width:g}")
        if names is None:
            port_skipped.append(name)
            continue
        try:
            count = block.get_port_count()
        except Exception:                                      # noqa: BLE001
            port_skipped.append(name)
            continue
        if count and _script_safe(name):
            port_blocks.append((name, int(count)))
        elif count:
            port_skipped.append(name)

    overlaps_found: List[Tuple[str, str]] = []
    for index, (name_a, rect_a) in enumerate(geometry):
        for name_b, rect_b in geometry[index + 1:]:
            if overlaps(rect_a, rect_b):
                overlaps_found.append((name_a, name_b))

    # ── Слой контура: пустые порты и концы линий ───────────────────
    try:
        wires = page.get_wires()
        wire_ids = [int(wire.id) for wire in wires]
    except Exception as exc:                                   # noqa: BLE001
        raise ToolError(
            f"перечислить линии страницы не удалось: {type(exc).__name__}: "
            f"{exc}. Без списка линий проверка концов невозможна.") from exc

    # Имена уникальны на вызов — прошлый файл не мешает (в #40 этот приём убрал
    # класс WinError 32); свои же файлы предыдущего вызова убираем, чтобы
    # песочница не копила по два на вызов (находка ревью #42).
    _sweep_previous()
    report_path = _rect_path()
    marker_path = _marker_path()
    _PREVIOUS_PATHS.extend((report_path, marker_path))
    script = _check_script(report_path, port_blocks, wire_ids)
    try:
        run = bridge().run_page_script(script, marker_path)
    except ScriptBridgeError as exc:
        raise ToolError(
            f"контур проверки не отработал: {exc}. Проверка портов и концов "
            f"линий не выполнена — по габаритам вердикт выше уже полный, но "
            f"он не заменяет контурную часть.") from exc

    kind = run.outcome.kind
    contour_line = f"Контур (порты и концы линий): {_KIND_TEXT.get(kind, kind)}."
    empty_ports: List[str] = []
    straight = 0
    bent: List[str] = []
    unparsed: List[str] = []
    unparsed_total = 0
    done = False
    data, _truncated, error = sandbox.load_result_file(
        str(report_path), sandbox.MAX_OUTPUT_BYTES,
        sandbox.MISSING_RESULT_FILE)
    if error:
        contour_line += (f" Отчёт контура не прочитан: {error} — пустые порты "
                         f"и связи не проверены.")
    else:
        text = data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            parts = line.split("|")
            if parts[0] == "EMPTY" and len(parts) == 3:
                empty_ports.append(f"{parts[1]}[{parts[2]}]")
            elif parts[0] == "W" and len(parts) == 4:
                start = _parse_point(parts[2])
                end = _parse_point(parts[3])
                if start is None or end is None:
                    # Счётчик — всегда полный: список ниже обрезается, но
                    # «сколько всего» должно оставаться правдой (находка
                    # ревью 02.10.2026).
                    unparsed_total += 1
                    if len(unparsed) < MAX_REPORTED:
                        unparsed.append(f"{parts[1]} ({parts[2]} → {parts[3]})")
                    continue
                if abs(start[0] - end[0]) < 0.5 or abs(start[1] - end[1]) < 0.5:
                    straight += 1
                else:
                    bent.append(parts[1])
            elif parts[0] == "DONE":
                done = True

    # ── Ответ ──────────────────────────────────────────────────────
    lines = ["Проверка оформления модели.", ""]
    if overlaps_found:
        shown = ", ".join(f"{a}—{b}" for a, b in overlaps_found[:MAX_REPORTED])
        more = (f" (и ещё {len(overlaps_found) - MAX_REPORTED})"
                if len(overlaps_found) > MAX_REPORTED else "")
        lines.append(f"ВНИМАНИЕ: наложения габаритов: {shown}{more}.")
    else:
        lines.append("Наложения габаритов: нет.")
    if label_warnings:
        shown = "; ".join(label_warnings[:MAX_REPORTED])
        more = (f" (и ещё {len(label_warnings) - MAX_REPORTED})"
                if len(label_warnings) > MAX_REPORTED else "")
        # Примечание, не «ВНИМАНИЕ»: свисание подписи за рамку — допустимый
        # стиль владельца (замечание 07.10.2026, боевая модель: 49 блоков
        # с намеренно свисающим текстом), оценка же грубая — у владельца
        # ширины и короче, и длиннее расчётной.
        lines.append(f"Примечание: подписи шире рамки (оценка "
                     f"{CHAR_WIDTH_ESTIMATE:g} px/символ; свисание за рамку "
                     f"допустимо, если читается): {shown}{more}.")
    else:
        lines.append("Подписи порт-блоков: в рамках (оценка "
                     f"{CHAR_WIDTH_ESTIMATE:g} px/символ).")
    if off_grid:
        shown = ", ".join(off_grid[:MAX_REPORTED])
        more = (f" (и ещё {len(off_grid) - MAX_REPORTED})"
                if len(off_grid) > MAX_REPORTED else "")
        lines.append(f"ВНИМАНИЕ: центры вне разметки {GRID_STEP:g} px: "
                     f"{shown}{more}.")
    else:
        lines.append(f"Разметка {GRID_STEP:g} px: центры на сетке.")
    if outside_sheet:
        shown = ", ".join(outside_sheet[:MAX_REPORTED])
        more = (f" (и ещё {len(outside_sheet) - MAX_REPORTED})"
                if len(outside_sheet) > MAX_REPORTED else "")
        lines.append(f"ВНИМАНИЕ: блоки выходят за начало листа (левый или "
                     f"верхний край в минусе): {shown}{more}.")
    else:
        lines.append("Блоки в пределах листа: левый и верхний края не в минусе.")
    lines.append(contour_line)
    if not error:
        if empty_ports:
            shown = ", ".join(empty_ports[:MAX_REPORTED])
            more = (f" (и ещё {len(empty_ports) - MAX_REPORTED})"
                    if len(empty_ports) > MAX_REPORTED else "")
            lines.append(f"ВНИМАНИЕ: пустые порты: {shown}{more}. Пустой "
                         f"вход блока расчёта останавливает расчёт всей "
                         f"модели (у порт-блоков — не блокер, замер "
                         f"08.10.2026).")
        elif done:
            lines.append("Пустые порты: нет.")
        else:
            lines.append("Пустые порты: не проверены — отчёт неполон "
                         "(тело оборвалось до конца).")
        if wire_ids:
            received = straight + len(bent) + unparsed_total
            tail: list[str] = []
            if bent:
                shown = ", ".join(bent[:MAX_REPORTED])
                more = (f" (и ещё {len(bent) - MAX_REPORTED})"
                        if len(bent) > MAX_REPORTED else "")
                tail.append(f"с изломом: {shown}{more}")
            elif received == len(wire_ids) and not unparsed_total:
                # «Все прямые» — только когда концы получены у всех линий:
                # пустой или оборванный отчёт не должен читаться как чистый
                # вердикт (находка ревью 02.10.2026).
                tail.append("все прямые")
            if received < len(wire_ids):
                tail.append(f"концы получены у {received} из {len(wire_ids)}"
                            f" — отчёт неполон")
            suffix = ("; " + "; ".join(tail)) if tail else ""
            lines.append(f"Связи: {len(wire_ids)}, прямых {straight}{suffix}.")
        else:
            lines.append("Связи: линий на странице нет.")
        if unparsed:
            more = (f" (и ещё {unparsed_total - MAX_REPORTED})"
                    if unparsed_total > MAX_REPORTED else "")
            lines.append(f"Концы не разобраны у {unparsed_total} линий: "
                         f"{', '.join(unparsed)}{more}.")
    if no_geometry:
        shown = ", ".join(no_geometry[:MAX_REPORTED])
        more = (f" (и ещё {len(no_geometry) - MAX_REPORTED})"
                if len(no_geometry) > MAX_REPORTED else "")
        lines.append(f"Геометрию прочитать не удалось у: {shown}{more}.")
    if port_skipped:
        shown = ", ".join(port_skipped[:MAX_REPORTED])
        more = (f" (и ещё {len(port_skipped) - MAX_REPORTED})"
                if len(port_skipped) > MAX_REPORTED else "")
        lines.append(f"Порты пропущены (имя или число портов не прочитались): "
                     f"{shown}{more}.")
    return "\n".join(lines)


#: Допуск «конец линии стоит на границе габарита»: вход блока стоит в `cx-16`,
#: а координату линии среда отдаёт округлённой.
_PORT_BAND = 8.0

#: Допуск колонки: центры X блоков в пределах одного квадратика разметки —
#: одна колонка. Планов раскладки у аудита нет: колонки берутся из
#: фактической геометрии, а ручная доводка и среда дают доли пикселя —
#: точное равенство центров было бы слишком строгим.
COLUMN_TOLERANCE = WIRE_PITCH

#: Допуск «связь выровнена»: Y конца у источника и Y конца у приёмника
#: совпадают (ТЗ 4.1). Оба конца — фактические, из контура; полпикселя:
#: среда считает в целых пикселях.
ALIGN_TOLERANCE = 0.5


class RoutingProblems(NamedTuple):
    """Что нашёл аудит маршрутов — чистая часть, без COM и контура."""

    #: Пары линий, отрезки которых пересекаются внутренностями.
    crossings: List[Tuple[int, int]]
    #: Пары линий, отрезки которых едут по одному треку с перекрытием.
    coincident: List[Tuple[int, int]]
    #: Линии, проходящие через внутренность чужого габарита: (линия, блок).
    block_hits: List[Tuple[int, str]]
    #: Блоки, к входам которых источники приходят в обратном порядке.
    port_order: List[str]
    #: Линии, маршрут которых не предсказывается (обратные связи).
    unchecked: List[int]
    #: Почему линия не проверена: обратная / внутриколоночная / нет колонок /
    #: конец не привязан / приёмник не правее источника.
    unchecked_reasons: Dict[int, str]
    #: Зазоры между колонками, чей канал теснее разреза (ТЗ 4.2):
    #: (зазор, cut, связей, фактический зазор, нужный канал).
    channel_overflow: List[Tuple[int, int, int, float, float]]


def _inside(rect: "tuple[float, float, float, float]",
            point: "tuple[float, float]") -> bool:
    """Точка внутри габарита (строго; на границе — нет)."""
    return rect[0] < point[0] < rect[2] and rect[1] < point[1] < rect[3]


def _port_order_violations(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]"
) -> List[str]:
    """Блоки, к входам которых источники приходят в обратном порядке.

    Для каждого габарита берутся концы линий, стоящие на его левой границе,
    — это входные порты. Порядок портов читается по Y, порядок источников —
    по Y начал тех же линий. Монотонность значит, что линии войдут в порты
    без креста; инверсия — что они пересекутся у самой стены блока.
    """
    violations: List[str] = []
    for name, rect in rects:
        incoming: List[Tuple[float, float]] = []
        for _wire_id, (start, end) in wires.items():
            if abs(end[0] - rect[0]) > _PORT_BAND:
                continue
            if not (rect[1] - _PORT_BAND <= end[1] <= rect[3] + _PORT_BAND):
                continue
            incoming.append((end[1], start[1]))
        if len(incoming) < 2:
            continue
        incoming.sort()
        sources = [source for _port, source in incoming]
        # Инверсия (нижний источник в верхний порт) — крест у стены:
        # порядок источников обязан идти по Y так же, как порядок входов.
        if sources != sorted(sources):
            violations.append(name)
    return violations


def _columns(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]"
) -> "Tuple[Dict[str, int], Dict[int, float], Dict[int, float]]":
    """Колонки слоистой укладки — из фактической геометрии блоков.

    Колонка — группа центров X в пределах `COLUMN_TOLERANCE` (один
    квадратик разметки): планов раскладки у аудита нет, а равенство
    центров было бы строже самой среды. Меньше двух колонок — колонок
    нет: аудит откатывается на аварийный путь, а не выдумывает канал.
    """
    if not rects:
        return {}, {}, {}
    centers = {(rect[0] + rect[2]) / 2.0 for _name, rect in rects}
    anchors: List[float] = []
    for center in sorted(centers):
        if anchors and center - anchors[-1] <= COLUMN_TOLERANCE:
            continue
        anchors.append(center)
    if len(anchors) < 2:
        return {}, {}, {}
    index_of = {anchor: i for i, anchor in enumerate(anchors)}
    column_of: Dict[str, int] = {}
    lefts: Dict[int, float] = {}
    rights: Dict[int, float] = {}
    for name, rect in rects:
        center = (rect[0] + rect[2]) / 2.0
        anchor = min(anchors, key=lambda value: abs(value - center))
        column = index_of[anchor]
        column_of[name] = column
        lefts[column] = min(lefts.get(column, rect[0]), rect[0])
        rights[column] = max(rights.get(column, rect[2]), rect[2])
    return column_of, lefts, rights


def _column_at(
        point: "Tuple[float, float]",
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        column_of: "Dict[str, int]"
) -> "Optional[int]":
    """Колонка блока, к которому точка ближе всего (в пределах полосы порта).

    Конец линии стоит на границе блока, поэтому «ближайший габарит» и есть
    владелец порта; дальше полосы `_PORT_BAND` связь не привязывается —
    честнее пропустить её, чем приписать чужой колонке.
    """
    best: "Optional[str]" = None
    best_gap: "Optional[float]" = None
    for name, rect in rects:
        gap = (max(rect[0] - point[0], 0.0, point[0] - rect[2])
               + max(rect[1] - point[1], 0.0, point[1] - rect[3]))
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, name
    if best is None or best_gap is None or best_gap > _PORT_BAND:
        return None
    return column_of[best]


def channel_overflow(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]"
) -> "List[Tuple[int, int, int, float, float]]":
    """Зазоры, чей канал теснее разреза (ТЗ 4.2).

    По зазору между колонками: `(номер, cut, связей, зазор, нужно)`. `cut` —
    мощность разреза (`cut_sizes`), `связей` — сколько линий фактически идёт
    через зазор, `зазор` — свободное место между колонками, `нужно` —
    `channel_width(cut)`. Тесно, если фактика меньше канона. Обратные связи в
    разрез не входят: по ТЗ 4.3 им место в отдельном нижнем канале.
    """
    column_of, lefts, rights = _columns(rects)
    if not column_of:
        return []
    gaps = max(lefts) + 1
    nets: List[Tuple[int, int, bool]] = []
    actual: Dict[int, int] = {}
    for wire_id in sorted(wires):
        start, end = wires[wire_id]
        src = _column_at(start, rects, column_of)
        dst = _column_at(end, rects, column_of)
        if src is None or dst is None:
            continue
        nets.append((src, dst, abs(start[1] - end[1]) < ALIGN_TOLERANCE))
        for gap in range(src, dst):
            actual[gap] = actual.get(gap, 0) + 1
    cut = cut_sizes(nets, gaps)
    found: "List[Tuple[int, int, int, float, float]]" = []
    for gap in range(gaps - 1):
        free = lefts.get(gap + 1, 0.0) - rights.get(gap, 0.0)
        need = channel_width(cut[gap])
        if free < need - 0.5:
            found.append((gap, cut[gap], actual.get(gap, 0), free, need))
    return found


def _wire_channels(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]",
        column_of: "Dict[str, int]", lefts: "Dict[int, float]",
        rights: "Dict[int, float]"
) -> "Tuple[Dict[int, float | None], Dict[int, str]]":
    """X канала каждой связи — её трек в зазоре между колонками (ТЗ 4.2).

    Порядок треков детерминирован: связи зазора, которым трек нужен (не
    выровненные в одну горизонталь), сортируются по Y приёмника, при равенстве
    — по Y источника, и получают X = правая граница левой колонки +
    `STUB + (k + 0.5) * WIRE_PITCH`. STUB — первым слагаемым канона
    (`channel_w = STUB + WIRE_PITCH * cut`, ТЗ 4.2): вылет из порта в
    `predicted_polyline` остаётся левее трека, и линия не делает петлю назад.
    Без этого слагаемого трек ложился **ближе** вылета и полилиния шла
    вбок-назад (числовая проверка 05.10.2026: треки 36/44 против вылета до 48).
    Связь через несколько колонок берёт трек **первого** зазора: форму такой
    связи канон не расписывает, это наше явное решение.

    **Предсказание, не замер.** Как среда укладывает треки внутри канала, мы не
    мерили и померить не можем (промежуточные точки линии среда не отдаёт),
    поэтому порядок — модель. Проверка — снимком (`save_screenshot`):
    предсказанные треки обязаны визуально совпасть с тем, как линии лежат в
    канале; если снимок покажет другой порядок (например, среда кладёт от
    краёв к центру) — порядок уточняется по нему.

    `None` — канон канала не даёт: меньше двух колонок, конец не привязан к
    блоку, связь обратная или внутриколоночная. Такие линии уходят в «не
    проверено»: выдуманная середина между концами дала бы ложные метрики.
    """
    channels: "Dict[int, float | None]" = {wid: None for wid in wires}
    reasons: "Dict[int, str]" = {wid: "нет колонок" for wid in wires}
    if not column_of:
        return channels, reasons
    placement: "Dict[int, Tuple[int, int, float, float, bool]]" = {}
    for wire_id in sorted(wires):
        start, end = wires[wire_id]
        src = _column_at(start, rects, column_of)
        dst = _column_at(end, rects, column_of)
        if src is None or dst is None:
            reasons[wire_id] = "конец не привязан"
            continue
        if dst < src:
            reasons[wire_id] = "обратная"
            continue
        if dst == src:
            reasons[wire_id] = "внутриколоночная"
            continue
        aligned = abs(start[1] - end[1]) < ALIGN_TOLERANCE
        placement[wire_id] = (src, dst, end[1], start[1], aligned)
        reasons.pop(wire_id, None)
    for gap in range(max(lefts) if lefts else 0):
        members = [wid for wid, (src, dst, _ey, _sy, aligned)
                   in placement.items()
                   if not aligned and src <= gap < dst]
        members.sort(key=lambda wid: (placement[wid][2], placement[wid][3]))
        for index, wire_id in enumerate(members):
            if gap == placement[wire_id][0]:
                channels[wire_id] = (rights[gap] + STUB
                                     + (index + 0.5) * WIRE_PITCH)
    # Выровненной связи трек не нужен: её форма — прямая, канал не
    # задействован. Но канон канал ей даёт, поэтому она проверяема, а не
    # «не проверена».
    for wire_id, (src_col, _dst, _ey, _sy, aligned) in placement.items():
        if aligned:
            channels[wire_id] = rights[src_col]
    return channels, reasons


def audit_routing_segments(
        rects: "List[Tuple[str, tuple[float, float, float, float]]]",
        wires: "Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]"
) -> RoutingProblems:
    """Посчитать проблемы маршрутов по предсказанным полилиниям.

    Чистая функция: ни COM, ни контура — потому её и проверяют тесты. Канал
    каждой связи — её трек в зазоре между колонками (`_wire_channels`, ТЗ 4.2);
    линии, которым канон канала не даёт, честно уходят в `unchecked`.

    Обратная связь (приёмник левее источника) не предсказывается — маршрут
    ведёт среда, — и такие линии честно попадают в `unchecked`, а не в чистые.
    """
    column_of, lefts, rights = _columns(rects)
    channels, reasons = _wire_channels(
        rects, wires, column_of, lefts, rights)
    segments: List[Tuple[int, Tuple[float, float], Tuple[float, float]]] = []
    unchecked: List[int] = []
    unchecked_reasons: Dict[int, str] = {}
    for wire_id in sorted(wires):
        start, end = wires[wire_id]
        channel = channels.get(wire_id)
        if channel is None:
            unchecked.append(wire_id)
            unchecked_reasons[wire_id] = reasons.get(wire_id, "нет колонок")
            continue
        polyline = predicted_polyline(start, end, channel)
        if polyline is None:
            unchecked.append(wire_id)
            unchecked_reasons[wire_id] = "приёмник не правее источника"
            continue
        for first, second in segments_of(polyline):
            segments.append((wire_id, first, second))

    crossings: List[Tuple[int, int]] = []
    coincident: List[Tuple[int, int]] = []
    for index, (wire_a, a1, a2) in enumerate(segments):
        for wire_b, b1, b2 in segments[index + 1:]:
            if wire_a == wire_b:
                continue
            if proper_crossing(a1, a2, b1, b2):
                crossings.append((wire_a, wire_b))
            elif collinear_overlap(a1, a2, b1, b2, pitch=WIRE_PITCH):
                coincident.append((wire_a, wire_b))

    block_hits: List[Tuple[int, str]] = []
    for wire_id, first, second in segments:
        start, end = wires[wire_id]
        for name, rect in rects:
            # Из своего блока линия выходит: габарит с её концом — не чужой.
            if _inside(rect, start) or _inside(rect, end):
                continue
            if segment_hits_rect(first, second, rect):
                block_hits.append((wire_id, name))

    # Пара линий может совпасть не одним отрезком, а несколькими (стык и
    # горизонталь одной прямой): в списке она обязана стоять один раз —
    # дубли вскрыл живой прогон 05.10.2026.
    return RoutingProblems(sorted(set(crossings)), sorted(set(coincident)),
                           sorted(set(block_hits)),
                           _port_order_violations(rects, wires), unchecked,
                           unchecked_reasons,
                           channel_overflow(rects, wires))


def _parse_wire_report(
        text: str
) -> "Optional[Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]]":
    """Разобрать отчёт контура: концы линий. `None` — отчёт оборван.

    `DONE` — признак полного отчёта (его пишет `_check_script` последней
    строкой): без него часть `W`-строк могла не записаться, и вердикт по
    такому отчёту не выдаётся — та же защита, что у `check_model_layout`
    (находка ревью 02.10.2026). Строки, которые не разобрались, пропускаются:
    `DONE` подтверждает, что отчёт пришёл целиком, а число прочитанных линий
    называет сам вердикт.
    """
    wires: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]] = {}
    complete = False
    for line in text.splitlines():
        parts = line.split("|")
        if parts[0] == "DONE":
            complete = True
            continue
        if parts[0] != "W" or len(parts) != 4:
            continue
        start = _parse_point(parts[2])
        end = _parse_point(parts[3])
        if start is None or end is None:
            continue
        try:
            wire_id = int(parts[1])
        except ValueError:
            continue
        wires[wire_id] = (start, end)
    return wires if complete else None


@mcp.tool()
@runtime.com_threaded
def audit_routing() -> str:
    """Проверить маршруты линий текущей страницы (читаемость, ТЗ п.1).

    Считается по предсказанной ортогонали — той же форме, которой линии
    ведёт `NormalizeWire`, — а не глазами: пересечения линий, общий трек
    с перекрытием дольше `WIRE_PITCH` (8 px — один квадратик разметки),
    попадание линии во внутренность чужого габарита, порядок входов на
    левой стене блока.

    Обратные связи (приёмник левее источника) не предсказываются — их
    маршрут ведёт среда, — и попадают в отдельный список «не проверено»,
    а не в чистый вердикт: «не проверено» не выдаётся за «хорошо».

    Вердикт: `readable` — чисто; `next` — есть что чинить, и в ответе
    названы линии и блоки. Габариты — через COM, концы линий — контуром
    страницы (та же цена, что у `check_model_layout`: контур сдвигает
    модельное время). Ничего в модели не меняет.
    """
    project = session.ensure_project()
    page = project.get_current_page()

    rects: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for block in page.get_blocks():
        try:
            name = block.get_name()
        except Exception:                                      # noqa: BLE001
            name = str(block.id)
        try:
            size = block.get_size()
        except Exception:                                      # noqa: BLE001
            size = None
        try:
            rect = rect_of(block.get_points(), size) if size else None
        except Exception:                                      # noqa: BLE001
            rect = None
        if rect is not None:
            rects.append((name, rect))

    try:
        wire_ids = [int(wire.id) for wire in page.get_wires()]
    except Exception as exc:                                   # noqa: BLE001
        raise ToolError(
            f"перечислить линии страницы не удалось: {type(exc).__name__}: "
            f"{exc}. Без списка линий аудит маршрутов невозможен.") from exc
    if not wire_ids:
        return ("Аудит маршрутов линий (читаемость, ТЗ п.1).\n"
                "Линий на странице нет — проверять нечего. "
                "Вердикт: readable.")

    _sweep_previous()
    report_path = _rect_path()
    marker_path = _marker_path()
    _PREVIOUS_PATHS.extend((report_path, marker_path))
    script = _check_script(report_path, [], wire_ids)
    try:
        bridge().run_page_script(script, marker_path)
    except ScriptBridgeError as exc:
        raise ToolError(
            f"контур аудита не отработал: {exc}. Маршруты не проверены."
        ) from exc

    data, _truncated, error = sandbox.load_result_file(
        str(report_path), sandbox.MAX_OUTPUT_BYTES,
        sandbox.MISSING_RESULT_FILE)
    if error:
        raise ToolError(
            f"отчёт контура не прочитан: {error} — концы линий не получены, "
            f"маршруты не проверены.")

    wires = _parse_wire_report(data.decode("utf-8", errors="replace"))
    if wires is None:
        raise ToolError(
            "отчёт контура оборван: признака `DONE` в нём нет — концы линий "
            "прочитаны не все, и вердикт по неполным данным не выдаётся "
            "(та же защита, что у `check_model_layout`).")
    if not wires:
        raise ToolError(
            "контур вернул отчёт без координат линий — концы не прочитаны, "
            "маршруты не проверены.")

    problems = audit_routing_segments(rects, wires)
    checked = len(wires) - len(problems.unchecked)
    lines = [
        "Аудит маршрутов линий (читаемость, ТЗ п.1).",
        f"Линий: {len(wires)}; маршрут предсказан у {checked}, "
        f"не предсказан у {len(problems.unchecked)} (обратные связи).",
    ]
    dirty = bool(problems.crossings or problems.coincident
                 or problems.block_hits or problems.port_order
                 or problems.channel_overflow)
    if not dirty:
        lines.append("Вердикт: readable — пересечений, общего трека, "
                     "попаданий в габариты и нарушений порядка входов нет.")
    else:
        lines.append("Вердикт: next — сначала layout_place, затем повторный "
                     "аудит.")
        for title, items in (
                ("линия проходит через габарит блока",
                 [f"линия {w} в «{b}»" for w, b in problems.block_hits]),
                ("пересечения линий",
                 [f"{a}—{b}" for a, b in problems.crossings]),
                ("общий трек с перекрытием",
                 [f"{a}—{b}" for a, b in problems.coincident]),
                ("порядок входов нарушен", list(problems.port_order))):
            if not items:
                continue
            shown = ", ".join(items[:MAX_REPORTED])
            more = (f" (и ещё {len(items) - MAX_REPORTED})"
                    if len(items) > MAX_REPORTED else "")
            lines.append(f"ВНИМАНИЕ: {title}: {shown}{more}.")
    if problems.channel_overflow:
        shown = "; ".join(
            f"зазор {gap}: cut={cut}, связей {count}, "
            f"зазор {free:g} px, нужно {need:g}"
            for gap, cut, count, free, need
            in problems.channel_overflow[:MAX_REPORTED])
        more = (f" (и ещё {len(problems.channel_overflow) - MAX_REPORTED})"
                if len(problems.channel_overflow) > MAX_REPORTED else "")
        lines.append(f"ВНИМАНИЕ: канал теснее разреза: {shown}{more}.")
    if problems.unchecked:
        grouped: Dict[str, List[int]] = {}
        for wire_id in problems.unchecked:
            grouped.setdefault(
                problems.unchecked_reasons.get(wire_id, "причина не названа"),
                []).append(wire_id)
        parts: List[str] = []
        for reason in sorted(grouped):
            ids = grouped[reason]
            shown = ", ".join(str(w) for w in ids[:MAX_REPORTED])
            more = (f" (и ещё {len(ids) - MAX_REPORTED})"
                    if len(ids) > MAX_REPORTED else "")
            parts.append(f"{reason} — {len(ids)}: {shown}{more}")
        lines.append(f"Не проверено: {len(problems.unchecked)} "
                     f"({' ; '.join(parts)}).")
    return "\n".join(lines) + "\n"
