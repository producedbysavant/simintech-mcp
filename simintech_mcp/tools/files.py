"""Инструменты чтения файлов: результат расчёта, его сводка и разбор `.xprt`.

`inspect_project_file` работает без COM, то есть и на Linux. Разбор `.xprt`
сперва проверяет корректность файла (`defusedxml`): без этого повреждённый или
вовсе не-XML файл давал бы «классов 0, блоков 0» — побайтово то же, что у
пустой, но исправной схемы, то есть провал разбора выглядел бы как пустая
модель.
"""

from __future__ import annotations

import io
from typing import List

import defusedxml.ElementTree as DefusedET
from fastmcp.exceptions import ToolError
from simintech_api.catalog import (
    decode_xprt,
    parse_xprt_block_props,
    parse_xprt_readonly,
)
from simintech_api.utils.xprt_signals import XprtSignalReader

from .. import runtime, sandbox, tables
from ..app import mcp


@mcp.tool()
@runtime._plain_tool
def read_output_file(path: str, max_lines: int = 200) -> str:
    """Прочитать текстовый файл с результатами расчёта.

    Основной способ получить результаты: `get_signal` работает только у проекта
    с подключённой базой сигналов, а блок «В файл» пишет результат в текстовый
    файл независимо от базы. Каждая строка — один момент времени:
    «<время> <значение 1> … <значение n>».

    Порядок работы: `add_block("В файл", props="filename=<путь>,count=1,step=[0.1]")`
    → соединить с выходом модели → `run(to_time=…)` → `read_output_file(<путь>)`.

    **Читается только каталог результатов.** По умолчанию это
    ``<временный каталог>/simintech-output`` (переопределяется переменной
    `SIMINTECH_OUTPUT_DIR`), и блок «В файл» должен писать **внутрь** него —
    иначе инструмент откажет. Это стандартное ограничение, а не опция: иначе
    инструмент превращается в «прочитать произвольный файл по пути от клиента».
    Относительный путь ищется внутри каталога результатов, символические ссылки
    раскрываются до проверки — поэтому `..` и ссылки выйти наружу не помогают.
    Текущий каталог печатает `help_text`.

    Args:
        path: путь к файлу внутри каталога результатов (абсолютный или
            относительный — тогда он ищется в этом каталоге).
        max_lines: сколько первых строк вернуть; значение по умолчанию — из
            сигнатуры.
    """
    # Именно байты, а не символы: кириллица в UTF-8 весит вдвое больше, и по
    # символам предел объёма занижался бы. Чтение ограничено заранее — иначе
    # предел срабатывал бы уже после того, как файл занял память.
    data, truncated, error = sandbox._load_result_file(path, sandbox.MAX_OUTPUT_BYTES,
                                                       sandbox._MISSING_RESULT_FILE)
    if error:
        return error
    text = data.decode("utf-8", errors="replace")
    lines: List[str] = []
    total = 0
    for raw in io.StringIO(text):
        total += 1
        if len(lines) < max_lines:
            lines.append(raw.rstrip("\r\n"))
    if total == 0:
        # Пустой результат — не «данных нет», а признак, что расчёт не шёл:
        # блок «В файл» создаёт файл, но без вычислений не пишет ни строки.
        return (f"ERROR: файл {path} пуст — блок «В файл» ничего не записал. "
                f"Обычно это значит, что расчёт не шёл (проверьте `get_time()` "
                f"после `run` и соединения блоков).")
    if truncated:
        return (f"{path}: прочитано строк {total} — файл больше "
                f"{sandbox.MAX_OUTPUT_BYTES} байт, чтение остановлено\n"
                + "\n".join(lines))
    head = f"{path}: строк {total}"
    if total > max_lines:
        head += f", показаны первые {max_lines}"
    return head + "\n" + "\n".join(lines)


@mcp.tool()
@runtime._plain_tool
def summarize_output_file(path: str, column: int = -1) -> str:
    """Свести результат расчёта к числам: диапазон, min/max, среднее, наклон.

    Дополняет `read_output_file`, который отдаёт строки как есть: проверять
    модель по двумстам строкам текста неудобно, а по сводке видно, попала ли
    кривая в ожидание. Работает **без COM** — сохранённый файл результата
    разбирается и на машине без SimInTech.

    Колонки файла блока «В файл»: `0` — время, `1..n` — значения.
    По умолчанию берётся последняя колонка (выход модели).

    Args:
        path: путь внутри каталога результатов (как у `read_output_file`).
        column: номер колонки значения; отрицательный — с конца строки
            (`-1` — последняя). `0` — время.
    """
    data, truncated, error = sandbox._load_result_file(path, tables.MAX_SUMMARY_BYTES,
                                                       sandbox._MISSING_RESULT_FILE)
    if error:
        return error
    table = tables._read_numeric_table(data, truncated)
    rows = table.rows
    if not rows:
        if table.partial_dropped:
            # Единственная «строка» файла не уместилась в предел сводки. Это
            # не пустой результат расчёта, и говорить «нет данных» нельзя:
            # числа в файле есть, но это не таблица (нет переводов строк).
            return (f"ERROR: в файле {path} нет ни одной полной числовой "
                    f"строки: строка не умещается в предел сводки "
                    f"({tables.MAX_SUMMARY_BYTES} байт). Похоже, это не таблица — "
                    f"в файле нет переводов строк.")
        return (f"ERROR: в файле {path} нет ни одной числовой строки"
                + (f" (нечисловых строк: {table.skipped})"
                   if table.skipped else "")
                + ". Пустой результат — признак, что расчёт не шёл.")

    width = len(rows[0])
    if not -width <= column < width:
        raise ToolError(
            f"В файле {width} колонок (0 — время, далее значения), "
            f"column={column} вне диапазона."
        )
    usable = [row for row in rows if len(row) == width]
    ragged = len(rows) - len(usable)
    times = [row[0] for row in usable]
    series = [row[column] for row in usable]
    count = len(series)
    vmin, vmax = min(series), max(series)
    mean = sum(series) / count
    span = times[-1] - times[0]

    label = "время" if column == 0 else f"значение (колонка {column})"
    lines = [
        f"{path}: точек {count}, колонок {width}",
        f"  {label}: первое {series[0]:g}, последнее {series[-1]:g}",
        f"  min {vmin:g} при t={times[series.index(vmin)]:g}, "
        f"max {vmax:g} при t={times[series.index(vmax)]:g}, среднее {mean:g}",
        f"  время: {times[0]:g} … {times[-1]:g}",
    ]
    if span:
        lines.append(f"  средний наклон: {(series[-1] - series[0]) / span:g} "
                     f"за секунду (по концам ряда)")
    if table.skipped or ragged or table.too_wide or table.partial_dropped:
        notes = []
        if table.skipped:
            notes.append(f"нечисловых {table.skipped}")
        if ragged:
            notes.append(f"с другим числом колонок {ragged}")
        if table.too_wide:
            notes.append(f"слишком широких (больше {tables.MAX_SUMMARY_COLUMNS} "
                         f"колонок) {table.too_wide}")
        if table.partial_dropped:
            notes.append("последняя строка обрезана и отброшена")
        lines.append("  пропущено строк: " + ", ".join(notes))
    if table.truncated:
        lines.append(f"  ВНИМАНИЕ: файл больше предела сводки "
                     f"({tables.MAX_SUMMARY_ROWS} строк или "
                     f"{tables.MAX_SUMMARY_BYTES} байт) — посчитаны первые {count}.")
    return "\n".join(lines)


# ─── Разбор проекта без COM (в том числе на Linux) ────────────────

#: Предел объёма разбираемого .xprt: файл проекта читается целиком.
MAX_PROJECT_BYTES = 64 * 1024 * 1024


def _xprt_block_names(text: str) -> List[str]:
    """Имена блоков из XML проекта.

    Имена — вспомогательная часть разбора: если список не собрался, об этом
    честнее сказать пустым результатом, чем отказать в разборе целиком.
    """
    try:
        return list(XprtSignalReader(text).parse())
    except Exception:                                          # noqa: BLE001
        return []


@mcp.tool()
@runtime._plain_tool
def inspect_project_file(path: str) -> str:
    """Разобрать сохранённый проект (.xprt) **без COM** — годится и для Linux.

    SimInTech работает только на Windows, но XML-экспорт проекта (его пишет
    `save_project`) читается где угодно: видно, какие блоки в модели и с
    какими параметрами. Это способ проверить чужую модель, не поднимая среду.

    Что даёт разбор: классы блоков с именами их параметров (включая
    вычисляемые — запись в них ничего не меняет) и имена блоков, по которым
    адресуются `connect`/`get_signal`.

    Чего не даёт: связей и координат — по XML они не восстанавливаются
    надёжно, — и расчёта: без Windows он не идёт. Значения параметров
    показаны не будут: в файле они у каждого экземпляра свои.

    Args:
        path: путь к `.xprt` внутри каталога результатов (как у
            `read_output_file`): файл должен лежать в нём.
    """
    raw, truncated, error = sandbox._load_result_file(path, MAX_PROJECT_BYTES,
                                                      sandbox._MISSING_PROJECT_FILE)
    if error:
        return error
    if truncated:
        raise ToolError(
            f"Файл {path} больше {MAX_PROJECT_BYTES} байт — разбор проекта "
            f"такого объёма не выполняется"
        )

    text = decode_xprt(raw)
    # Корректность проверяется ДО разбора. Без этой проверки повреждённый,
    # обрезанный или вовсе не-XML файл давал бы «классов 0, блоков 0» —
    # ровно тот же ответ, что у пустой, но исправной схемы: провал разбора
    # выглядел бы как «модель пуста». Парсер — defusedxml, как и в
    # библиотеке: обычный `xml.etree` на чужом файле открывает XXE и
    # «бомбы». Проверено на реальном .xprt из SimInTech (693 КБ): структура
    # корректна, 32 объекта.
    try:
        DefusedET.fromstring(text)
    except Exception as exc:                                   # noqa: BLE001
        raise ToolError(
            f"Файл {path} не является корректным экспортом SimInTech "
            f"(.xprt): {type(exc).__name__}: {exc}"
        ) from exc

    classes = parse_xprt_block_props(text)
    readonly = parse_xprt_readonly(text)
    names = _xprt_block_names(text)

    parts = [f"{path}: классов {len(classes)}, блоков {len(names)}"]
    if names:
        shown = names[:50]
        parts.append("Блоки:\n" + "\n".join(f"  {name}" for name in shown)
                     + (f"\n  ... и ещё {len(names) - 50}"
                        if len(names) > 50 else ""))
    else:
        # Отличить «нечего показывать» от «разбор имён не справился» важно:
        # при найденных параметрах классов пустой список имён означает
        # ограничение разбора, а не отсутствие объектов в схеме.
        parts.append(
            "Имена блоков не найдены — " + (
                "разбор имён ограничен, хотя параметры блоков в файле есть."
                if classes else "в файле нет блоков схемы.")
        )
    if classes:
        lines = []
        for cls in sorted(classes):
            computed = readonly.get(cls) or []
            tail = (f" [вычисляемые, задавать нельзя: {', '.join(computed)}]"
                    if computed else "")
            lines.append(f"  {cls}: {', '.join(sorted(classes[cls]))}{tail}")
        parts.append("Параметры по классам:\n" + "\n".join(lines))
    else:
        # Объекты считаются отдельно: и парсер параметров, и извлекатель имён
        # отбрасывают графику (`Line`, `PolyLine`, ...), поэтому схема из одной
        # графики выглядит как пустая — а это разные вещи.
        objects = text.count("<object>")
        parts.append(
            "Параметры блоков не найдены: в файле нет секций <custom_props>"
            + (f" (объектов в файле: {objects} — возможно, это только "
               f"графика)." if objects else ".")
        )
    return "\n".join(parts)
