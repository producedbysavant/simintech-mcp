"""Проверка оформления модели — машинный чек-лист из issue #24 (п.4).

Проверяется по фактической геометрии, а не глазами:

* наложения габаритов блоков — нет свободного буфера (через COM, `Points`);
* высота блоков с входными контактами: 16 px на вход или строку сигнала;
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
Разметка проверяется по центрам блоков (шаг 8 px). Высота сверяется с
числом входов для классов правила (порт-блоки, субмодели и nport-классы),
но фактический шаг портов по координатам не измеряется; величины зазоров
между блоками - вне проверки.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

from fastmcp.exceptions import ToolError
from simintech_api.catalog import NON_BLOCK_CLASSES
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
from ..rules import (ALGORITHM_TABLE_CLASSES, NPORT_BLOCK_WIDTH, NPORT_PARAM,
                     NPORT_SIZE_CLASSES, PORT_HEIGHT_CLASSES, PORT_ROW_HEIGHT,
                     ROW_HEIGHT_CLASSES, required_height, table_height)
from ..geometry import CHAR_WIDTH_ESTIMATE, overlaps, rect_of
from .blocks import read_port_counts, read_port_names
from .check_routing import (
    ALIGN_TOLERANCE as ALIGN_TOLERANCE,
    COLUMN_TOLERANCE as COLUMN_TOLERANCE,
    PORT_BAND as _PORT_BAND,
    RoutingProblems as RoutingProblems,
    audit_routing_segments as audit_routing_segments,
    channel_overflow as channel_overflow,
    column_at as _column_at,
    columns as _columns,
    inside as _inside,
    node_caption as _node_caption,
    node_overflow as node_overflow,
    parse_node_branches as _parse_node_branches,
    parse_parents as _parse_parents,
    parse_point as _parse_point,
    parse_wire_report as _parse_wire_report,
    port_order_violations as _port_order_violations,
    wire_channels as _wire_channels,
)
from .page_script import fresh_name

#: Реэкспорт помощников аудита маршрутов (они живут в `check_routing`):
#: прежние точки импорта и атрибуты модуля остаются на месте. Имена в
#: `__all__` pyright не считает неиспользованным импортом.
__all__ = [
    "ALIGN_TOLERANCE",
    "COLUMN_TOLERANCE",
    "REPORT_FILE",
    "MARKER_FILE",
    "MAX_REPORTED",
    "GRID_STEP",
    "LABEL_CLASSES",
    "RoutingProblems",
    "audit_routing",
    "audit_routing_segments",
    "check_model_layout",
    "channel_overflow",
    "node_overflow",
    "_PORT_BAND",
    "_column_at",
    "_columns",
    "_inside",
    "_node_caption",
    "_parse_node_branches",
    "_parse_parents",
    "_parse_point",
    "_parse_wire_report",
    "_port_order_violations",
    "_wire_channels",
]

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
        "  var chk_w: integer;",
        f'  chk_f = createfile("{literal}", -1);',
    ]
    for wire_id in wire_ids:
        lines.append(
            f'  writelnutf8(chk_f, "W|{wire_id}|" '
            f"+ getwirestartpointcoord({wire_id}) "
            f'+ "|" + getwireendpointcoord({wire_id}));')
    # Родитель линии — для вердикта о подключении: у обычной линии (без
    # ветвления) родителя нет, и портов она касается двумя; ветвь живёт на
    # родителе и касается одного (живые замеры 08.10.2026).
    for wire_id in wire_ids:
        lines.append(
            f'  writelnutf8(chk_f, "P|{wire_id}|" '
            f"+ inttostr(getparentwireid({wire_id})));")
    # Узел ветви — для проверки «в узел не больше 3 линий» (правило
    # владельца; живые замеры 08.10.2026: три `connect_branch` от одного
    # выхода сели в один узел родителя — четыре линии в точке). Родитель
    # берётся из P-строки: второй вызов `getparentwireid` на ту же линию не
    # нужен (находка ревью 08.10.2026 — #136 и #138 дублировали его).
    for wire_id in wire_ids:
        lines.append(
            f'  writelnutf8(chk_f, "N|{wire_id}|" '
            f"+ inttostr(getparentwirenodeindex({wire_id})));")
    for name, port_count in port_blocks:
        for index in range(port_count):
            lines.append(
                f"  chk_w = getportwireid(getblockportid({name}, {index}));")
            lines.append(
                f'  if chk_w = 0 then writelnutf8(chk_f, "EMPTY|{name}|'
                f'{index}");')
            lines.append(
                f'  if chk_w <> 0 then writelnutf8(chk_f, "LINK|{name}|'
                f'{index}|" + inttostr(chk_w));')
    # `DONE` — признак полного отчёта: по нему вердикты «все прямые» и
    # «пустых портов нет» не выдаются по оборванному телу (находка ревью
    # 02.10.2026).
    lines.append('  writelnutf8(chk_f, "DONE");')
    lines.append("  freeobject(chk_f);")
    lines.append("end;")
    return "\n".join(lines) + "\n"


@mcp.tool(title="Проверка оформления", annotations={"readOnlyHint": False})
@runtime.com_threaded
def check_model_layout() -> str:
    """Проверить оформление текущей страницы: наложения, подписи, порты, связи.

    Машинная версия чек-листа (issue #24, п.4). Проверяется по фактической
    геометрии: наложения — по габаритам (центр `Points` ± `size`); подписи —
    грубая оценка «длина имени сигнала × 8 px» против ширины блока (точную
    ширину шрифта COM не отдаёт); центры — на разметке 8 px (1 квадратик =
    8×8, стандарт 02.10.2026); пустые порты — `getportwireid`; связи
    «прямая / с изломом» — по координатам концов линий.

    **Подключение линий.** Линия без ветвления (родитель = 0,
    `getparentwireid`) обязана касаться двух портов — выхода источника и
    входа приёмника; касается меньше — предупреждение: во вход, вероятно,
    уже приходит другая линия (среда допускает две линии в один вход, а
    `getportwireid` показывает лишь одну — живой замер 08.10.2026: линия
    второго источника видна только со стороны своего выхода). Ветви
    (родитель ≠ 0) законно касаются одного порта и не сигналят; направление
    концов не различается — `getportinfo` в контуре этой сборки не
    компилируется (замер 08.10.2026), и текст вердикта этого не утверждает.
    Неполные данные дают «не проверено», а не вердикт: оборванный отчёт
    (нет `DONE`), непрочитанные родители линий, пропущенные блоки — линия
    у пропущенного блока честно видна одному порту, и предупреждение по
    ней было бы ложным (находки ревью 08.10.2026).

    **Высота, ширина и `nport`.** Для порт-блоков высота сверяется с 16 ×
    число строк `PortNames`; для субмоделей и nport-классов - с 16 ×
    фактическое число входов. У nport-классов ширина меньше штатной 32 и
    расхождение параметра `nport` с числом пинов - отдельные
    предупреждения: запись свойства в обход `set_block_param` пины не
    пересобирает. Нечитаемое число входов - «не проверена», а не чистый
    вердикт. Правило и оговорки: `simintech://guides/block-sizing`.

    **Индексы в списке пустых портов** - сквозные, 0-based, как у
    `getblockportid`/`GetBlockPort`; выходные и ненаправленные порты входят
    в тот же перебор. Это не `in:N` текста модели (входной индекс с нуля),
    но тот же сквозной 0-based индекс, что и `end_port_index` выгрузки
    `.xprt`. У блока, где выход идёт первым, вход `in:N` попадает на сквозной
    индекс N+1; сверять `in:N` с отчётом без пересчёта нельзя.

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
    * фактический шаг портов по координатам не измеряется; проверяется
      высота по числу входных контактов для классов правила (порт-блоки -
      16 px на строку `PortNames`, субмодели и nport-классы - 16 px на
      вход), а зазоры между блоками - вне критерия разметки (на разметке
      проверяются центры блоков);
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
    height_warnings: List[str] = []
    height_notes: List[str] = []
    height_skipped: List[str] = []
    height_checked = 0
    height_rule_blocks = 0
    width_narrow: List[str] = []
    nport_mismatch: List[str] = []
    nport_skipped: List[str] = []
    off_grid: List[str] = []
    port_blocks: List[Tuple[str, int]] = []
    port_skipped: List[str] = []
    outside_sheet: List[str] = []
    for block in page.get_blocks():
        try:
            name = block.get_name()
        except Exception:                                      # noqa: BLE001
            name = str(block.id)
        class_name = ""
        try:
            class_name = str(block.class_name).strip()
            if class_name in LABEL_CLASSES:
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
        height = size[1] if size else None
        names = read_port_names(block)
        if width and names:
            longest = max(names, key=len)
            estimate = len(longest) * CHAR_WIDTH_ESTIMATE
            if estimate > width:
                label_warnings.append(
                    f"{name}: «{longest}» (~{estimate:g} px) при ширине "
                    f"{width:g}")
        if class_name in (*PORT_HEIGHT_CLASSES, *ROW_HEIGHT_CLASSES):
            height_rule_blocks += 1
        # Ширина nport-блока - свойство габарита, а не следствие читаемости
        # пинов: проверяется и тогда, когда число входов не прочиталось.
        if class_name in NPORT_SIZE_CLASSES and width is not None:
            if float(width) < float(NPORT_BLOCK_WIDTH):
                width_narrow.append(
                    f"{name}: ширина {width:g} < {NPORT_BLOCK_WIDTH}")
        # Высота по правилу: порт-блоки - 16 px на строку PortNames,
        # субмодели и nport-классы - 16 px на входной контакт. Нечитаемые
        # данные дают «не проверена», а не положительный вердикт.
        if height is None and class_name in (*PORT_HEIGHT_CLASSES,
                                             *ROW_HEIGHT_CLASSES,
                                             *ALGORITHM_TABLE_CLASSES):
            height_skipped.append(name)
        elif class_name in PORT_HEIGHT_CLASSES and names is None:
            height_skipped.append(name)
        if height is not None and class_name in PORT_HEIGHT_CLASSES and names:
            required = required_height(class_name, len(names))
            if required is not None:
                height_checked += 1
                if float(height) != float(required):
                    height_warnings.append(
                        f"{name}: высота {height:g}, строк {len(names)}, нужно "
                        f"{required} ({PORT_ROW_HEIGHT} px на строку)")
        if height is not None and class_name in ALGORITHM_TABLE_CLASSES:
            # Табличный блок алгоритма: высота по позициям таблицы, включая
            # пустые заполнители, без строки-заголовка (канон владельца
            # 21.11.2026). Нечитаемая таблица - «не проверена», а не успех.
            #
            # Авторазмер среды считает заголовок и у чужих проектов это
            # норма: такая высота при AutoChangeHeight=1 называется
            # примечанием, а не расхождением (замечание fdd002 21.11.2026).
            try:
                raw = block.get_property("SignalNames") or ""
                header = str(block.get_property("fExcludeFirst") or "1")
                auto = str(block.get_property("AutoChangeHeight") or "0")
            except Exception:                                  # noqa: BLE001
                height_skipped.append(name)
            else:
                with_header = header.strip().lower() not in ("0", "false")
                expected = table_height(raw, with_header)
                if expected is not None:
                    height_checked += 1
                    auto_height = (expected // PORT_ROW_HEIGHT
                                   + (1 if with_header else 0)) * PORT_ROW_HEIGHT
                    if float(height) != float(expected):
                        if (auto.strip().lower() not in ("0", "false")
                                and float(height) == float(auto_height)):
                            height_notes.append(
                                f"{name}: высота {height:g} - авторазмер "
                                f"среды с заголовком; канон по позициям "
                                f"{expected}")
                        else:
                            height_warnings.append(
                                f"{name}: высота {height:g}, позиций "
                                f"{expected // PORT_ROW_HEIGHT}, нужно "
                                f"{expected} ({PORT_ROW_HEIGHT} px на "
                                f"позицию таблицы)")
        if height is not None and class_name in ROW_HEIGHT_CLASSES:
            try:
                in_ports, out_ports = read_port_counts(block)
            except Exception:                                  # noqa: BLE001
                height_skipped.append(name)
                if class_name in NPORT_SIZE_CLASSES:
                    # Сверка nport с пинами без числа входов невозможна.
                    nport_skipped.append(name)
            else:
                required = required_height(class_name, (in_ports, out_ports))
                if required is not None:
                    height_checked += 1
                    if float(height) != float(required):
                        height_warnings.append(
                            f"{name}: высота {height:g}, входов {in_ports}, "
                            f"выходов {out_ports}, нужно {required} "
                            f"({PORT_ROW_HEIGHT} px на большую сторону)")
                if class_name in NPORT_SIZE_CLASSES:
                    try:
                        nport = block.get_property(NPORT_PARAM)
                    except Exception:                          # noqa: BLE001
                        nport_skipped.append(name)
                    else:
                        try:
                            nport_num = int(float(str(nport).strip()))
                        except Exception:                      # noqa: BLE001
                            nport_skipped.append(name)
                        else:
                            if nport_num != in_ports:
                                nport_mismatch.append(
                                    f"{name}: nport={nport_num}, входов "
                                    f"{in_ports}")
        if names is None:
            port_skipped.append(name)
            continue
        if not names and class_name in PORT_HEIGHT_CLASSES:
            # У порт-блока имя сигнала есть всегда: пустой список здесь
            # означает отказ чтения, а не «имён нет».
            port_skipped.append(name)
            if name not in height_skipped:
                height_skipped.append(name)
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
    #: Линия → родитель (`getparentwireid`): у обычной линии 0, у ветви — id
    #: родительской. Нужен вердикту «линия касается меньше двух портов».
    parents: Dict[int, int] = {}
    #: Линия → сколько портов её видят (`getportwireid`) и где первый из них.
    link_counts: Dict[int, int] = {}
    link_where: Dict[int, str] = {}
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
            elif parts[0] == "P" and len(parts) == 3:
                try:
                    parents[int(parts[1])] = int(parts[2])
                except ValueError:
                    continue
            elif parts[0] == "LINK" and len(parts) == 4:
                try:
                    wid = int(parts[3])
                except ValueError:
                    continue
                link_counts[wid] = link_counts.get(wid, 0) + 1
                link_where.setdefault(wid, f"{parts[1]}[{parts[2]}]")
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
    if height_warnings:
        shown = "; ".join(height_warnings[:MAX_REPORTED])
        more = (f" (и ещё {len(height_warnings) - MAX_REPORTED})"
                if len(height_warnings) > MAX_REPORTED else "")
        lines.append(
            f"ВНИМАНИЕ: высота не по правилу {PORT_ROW_HEIGHT} px на "
            f"вход/строку: {shown}{more}. Подгоните `fit_port_blocks`; у "
            f"nport-классов число входов меняет "
            f"`set_block_param(nport=N)`; правило: "
            f"simintech://guides/block-sizing.")
    if height_notes:
        shown = "; ".join(height_notes[:MAX_REPORTED])
        more = (f" (и ещё {len(height_notes) - MAX_REPORTED})"
                if len(height_notes) > MAX_REPORTED else "")
        lines.append(
            f"Примечание: {shown}{more}. Так ставит авторазмер среды; "
            f"канон владельца считает позиции таблицы без заголовка.")
    elif not height_skipped:
        if height_checked:
            lines.append(f"Высота блоков с входными контактами: по правилу "
                         f"{PORT_ROW_HEIGHT} px на вход/строку.")
        elif not height_rule_blocks:
            lines.append("Высота блоков с входными контактами: блоков "
                         "правила на странице нет.")
    if height_skipped:
        shown = ", ".join(height_skipped[:MAX_REPORTED])
        more = (f" (и ещё {len(height_skipped) - MAX_REPORTED})"
                if len(height_skipped) > MAX_REPORTED else "")
        lines.append(f"Высота не проверена (число входов не читается): "
                     f"{shown}{more}.")
    if width_narrow:
        shown = "; ".join(width_narrow[:MAX_REPORTED])
        more = (f" (и ещё {len(width_narrow) - MAX_REPORTED})"
                if len(width_narrow) > MAX_REPORTED else "")
        lines.append(f"ВНИМАНИЕ: ширина nport-блока меньше штатной "
                     f"{NPORT_BLOCK_WIDTH}: {shown}{more}.")
    if nport_mismatch:
        shown = "; ".join(nport_mismatch[:MAX_REPORTED])
        more = (f" (и ещё {len(nport_mismatch) - MAX_REPORTED})"
                if len(nport_mismatch) > MAX_REPORTED else "")
        lines.append(
            f"ВНИМАНИЕ: nport не совпадает с числом входов: {shown}{more}. "
            f"Это расхождение параметра и пинов: смените nport через "
            f"`set_block_param`, он пересоберёт пины; запись свойства в обход "
            f"инструмента пины не меняет.")
    if nport_skipped:
        shown = ", ".join(nport_skipped[:MAX_REPORTED])
        more = (f" (и ещё {len(nport_skipped) - MAX_REPORTED})"
                if len(nport_skipped) > MAX_REPORTED else "")
        lines.append(f"nport не проверен (не читается или не число): "
                     f"{shown}{more}.")
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
            lines.append(
                f"ВНИМАНИЕ: пустые порты (индекс сквозной, 0-based, как "
                f"getblockportid/GetBlockPort и end_port_index .xprt; это "
                f"не in:N из текста, выходные порты тоже в списке): "
                f"{shown}{more}. Пустой "
                f"вход блока расчёта останавливает расчёт всей модели "
                f"(у порт-блоков — не блокер, замер 08.10.2026).")
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
        if wire_ids:
            # Линия без родителя (не ветвь) обязана касаться двух портов:
            # выхода источника и входа приёмника. Меньше двух — во вход,
            # вероятно, уже приходит другая линия: среда это допускает, а
            # `getportwireid` показывает лишь одну (живой замер 08.10.2026 —
            # «две линии в один вход», линия второго источника видна только
            # со стороны своего выхода). Направление концов здесь не
            # различается: `getportinfo` в контуре этой сборки не компилируется
            # (замер 08.10.2026), и текст вердикта этого и не утверждает.
            #
            # Неполные данные — «не проверено», а не вердикт (находки ревью
            # 08.10.2026): при оборванном отчёте («связка и молчание» не
            # отличались от «чисто»), при непрочитанных родителях и при
            # пропущенных блоках (линия у пропущенного блока честно видна
            # одному порту — «касается меньше двух» было бы ложной тревогой).
            if not done:
                lines.append("Подключение линий: не проверено — отчёт "
                             "контура неполон (нет признака `DONE`).")
            elif not parents:
                lines.append("Подключение линий: не проверено — строки о "
                             "родителях линий не прочитаны.")
            elif port_skipped:
                lines.append("Подключение линий: не проверено — часть "
                             "блоков пропущена (см. ниже): линия, чей конец "
                             "в пропущенном блоке, видна меньше чем двум "
                             "портам.")
            else:
                lonely = [
                    (wid, f"id={wid} (видна у "
                          f"{link_where.get(wid, 'ни одного порта здешнего')}, "
                          f"портов: {link_counts.get(wid, 0)})")
                    for wid, parent in sorted(parents.items())
                    if parent == 0 and link_counts.get(wid, 0) < 2]
                if lonely:
                    shown = ", ".join(
                        desc for _wid, desc in lonely[:MAX_REPORTED])
                    more = (f" (и ещё {len(lonely) - MAX_REPORTED})"
                            if len(lonely) > MAX_REPORTED else "")
                    lines.append(
                        f"ВНИМАНИЕ: линии без ветвления, касающиеся меньше "
                        f"двух портов: {shown}{more}. У обычной линии два "
                        f"конца; вероятная причина — во вход уже приходит "
                        f"другая линия (среда допускает две линии в один "
                        f"вход) либо связь повреждена. Проверьте выгрузкой "
                        f"(`export_model_text`, поле `dst`).")
                else:
                    lines.append("Подключение линий: у каждой линии без "
                                 "ветвления видны оба конца.")
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


@mcp.tool(title="Аудит маршрутов", annotations={"readOnlyHint": False})
@runtime.com_threaded
def audit_routing() -> str:
    """Проверить маршруты линий текущей страницы (читаемость, ТЗ п.1).

    Считается по предсказанной ортогонали — той же форме, которой линии
    ведёт `NormalizeWire`, — а не глазами: пересечения линий, общий трек
    с перекрытием дольше `WIRE_PITCH` (8 px — один квадратик разметки),
    попадание линии во внутренность чужого габарита, порядок входов на
    левой стене блока.

    **Узлы ветвления.** У ветви `getparentwireid` даёт родительскую линию,
    `getparentwirenodeindex` — номер её точки данных, куда ветвь села (у
    среды с единицы; живой замер 08.10.2026: три `connect_branch` от одного
    выхода сели в один узел — четыре линии в точке). В узел сходятся родитель
    и его ветви: больше трёх линий — предупреждение. Правило владельца
    (07.10.2026): «в 1 точку не может приходить больше 3 линий»; при этом
    три линии могут сойтись «плюсом» из четырёх лучей — сквозная линия у
    узла одна (уточнение владельца 08.10.2026). Пустые сегменты — пары,
    которые среда рождает вместе с ветвью (конец `0+0i`), — в счёт не идут:
    они невидимы. «Врезка ветви в середину сегмента» и «наклонный подвод к
    пину» по данным не различаются и не проверяются: маршрут родителя среда
    не отдаёт, а наклонные лучи «звезды» — свойство отрисовки перегруженного
    узла, не данных (замер 07.10.2026).

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

    report_text = data.decode("utf-8", errors="replace")
    wires = _parse_wire_report(report_text)
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
    node_crowd = node_overflow(
        _parse_node_branches(report_text, wires, _parse_parents(report_text)))
    checked = len(wires) - len(problems.unchecked)
    lines = [
        "Аудит маршрутов линий (читаемость, ТЗ п.1).",
        f"Линий: {len(wires)}; маршрут предсказан у {checked}, "
        f"не предсказан у {len(problems.unchecked)} (обратные связи).",
    ]
    dirty = bool(problems.crossings or problems.coincident
                 or problems.block_hits or problems.port_order
                 or problems.channel_overflow or node_crowd)
    if not dirty:
        lines.append("Вердикт: readable — пересечений, общего трека, "
                     "попаданий в габариты, нарушений порядка входов и "
                     "перегруженных узлов нет.")
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
                ("порядок входов нарушен", list(problems.port_order)),
                ("в узел приходит больше 3 линий",
                 [_node_caption(parent, node, branches)
                  for parent, node, branches in node_crowd])):
            if not items:
                continue
            shown = ", ".join(items[:MAX_REPORTED])
            more = (f" (и ещё {len(items) - MAX_REPORTED})"
                    if len(items) > MAX_REPORTED else "")
            lines.append(f"ВНИМАНИЕ: {title}: {shown}{more}.")
        if node_crowd:
            lines.append(
                "Правило: в узел не больше 3 линий — сквозная линия у узла "
                "одна, поэтому 3 линии могут сойтись «плюсом» из 4 лучей "
                "(уточнение владельца 08.10.2026). Повторные ветви стройте "
                "цепочкой: родителем следующей пусть будет предыдущая ветвь "
                "(рецепт — в докстринге `connect_branch`).")
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
