"""Проверка оформления модели — машинный чек-лист из issue #24 (п.4).

Проверяется по фактической геометрии, а не глазами:

* наложения габаритов блоков — нет свободного буфера (через COM, `Points`);
* подписи порт-блоков шире рамки — грубая оценка по `PortNames` и ширине;
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
from typing import List, Optional, Tuple

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
from ..geometry import overlaps, rect_of
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


#: Грубая оценка ширины символа подписи, px. Точную ширину шрифта COM не
#: отдаёт, поэтому оценка намеренно названа оценкой: она ловит случаи с
#: запасом (имя в 20+ символов на 32-пиксельной рамке), а не косметику.
CHAR_WIDTH_ESTIMATE = 8.0

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


def _read_port_names(block: Block) -> Optional[List[str]]:
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
        width = size[0] if size else None
        names = _read_port_names(block)
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
        lines.append(f"ВНИМАНИЕ: подписи шире рамки (оценка "
                     f"{CHAR_WIDTH_ESTIMATE:g} px/символ): {shown}{more}.")
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
    lines.append(contour_line)
    if not error:
        if empty_ports:
            shown = ", ".join(empty_ports[:MAX_REPORTED])
            more = (f" (и ещё {len(empty_ports) - MAX_REPORTED})"
                    if len(empty_ports) > MAX_REPORTED else "")
            lines.append(f"ВНИМАНИЕ: пустые порты: {shown}{more}. Пустой "
                         f"вход останавливает расчёт всей модели.")
        elif done:
            lines.append("Пустые порты: нет.")
        else:
            lines.append("Пустые порты: не проверены — отчёт неполон "
                         "(тело оборвалось до конца).")
        if wire_ids:
            received = straight + len(bent) + unparsed_total
            tail = []
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
