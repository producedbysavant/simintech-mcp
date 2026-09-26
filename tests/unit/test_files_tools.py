"""Инструменты чтения файлов: результат, сводка, `.xprt`."""

from __future__ import annotations

import os

import pytest

from simintech_mcp.server import mcp

from simintech_mcp import sandbox, tables
from simintech_mcp.tools import files as files_tools

from _support import (
    _XPRT_EMPTY_FIXTURE,
    _XPRT_FIXTURE,
    _XPRT_GRAPHICS_FIXTURE,
    _error,
    _text,
    _tool_text,
)


@pytest.mark.anyio
async def test_read_output_file_reads_inside_sandbox(tmp_path, monkeypatch):
    """read_output_file читает результат блока «В файл» (COM не нужен)."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "result.txt"
    path.write_text("0\t6\n0.1\t6\n0.2\t6\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": str(path)})
    text = _text(result)

    assert "строк 3" in text
    assert "0.2\t6" in text


@pytest.mark.anyio
async def test_read_output_file_relative_path_resolves_in_sandbox(
        tmp_path, monkeypatch):
    """Относительный путь ищется внутри каталога результатов."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t6\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": "out.txt"})
    text = _text(result)

    assert "строк 1" in text
    assert "0\t6" in text


@pytest.mark.anyio
async def test_read_output_file_missing(tmp_path, monkeypatch):
    """Отсутствующий файл внутри песочницы — отказ с понятной причиной."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))

    text = await _error("read_output_file", {"path": str(tmp_path / "нет.txt")})

    assert "файла нет" in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_blocks_outside(tmp_path, monkeypatch):
    """С SIMINTECH_OUTPUT_DIR читается только этот каталог и его подкаталоги."""
    sandbox = tmp_path / "results"
    sandbox.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("секрет\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    text = await _error("read_output_file", {"path": str(outside)})

    assert "разрешено только из каталога" in text
    assert "секрет" not in text


@pytest.mark.anyio
async def test_read_output_file_defaults_to_standard_dir(tmp_path, monkeypatch):
    """Без переменной песочница тоже действует — по стандартному каталогу.

    Ограничение не опция: иначе инструмент читал бы любой файл по пути от
    клиента, а клиент может быть без доступа к файловой системе.
    """
    from simintech_mcp.sandbox import default_output_dir

    monkeypatch.delenv("SIMINTECH_OUTPUT_DIR", raising=False)
    outside = tmp_path / "secret.txt"
    outside.write_text("секрет\n", encoding="utf-8")

    text = await _error("read_output_file", {"path": str(outside)})

    assert "секрет" not in text
    # Сравниваем канонизированный путь: Windows отдаёт gettempdir() в коротком
    # виде (форма 8.3), а сервер печатает длинную форму после realpath.
    assert os.path.realpath(default_output_dir()) in text, \
        "отказ должен называть стандартный каталог"


@pytest.mark.anyio
async def test_read_output_file_sandbox_root_must_be_dir(tmp_path, monkeypatch):
    """Несуществующий каталог в SIMINTECH_OUTPUT_DIR — ошибка конфигурации.

    Проверка «fail closed»: иначе инструмент молча отказывал бы с невнятной
    причиной «путь вне разрешённого каталога».
    """
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path / "нет-такого"))

    text = await _error("read_output_file", {"path": str(tmp_path / "x.txt")})

    assert "не является каталогом" in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_allows_inside(tmp_path, monkeypatch):
    """Файл внутри разрешённого каталога читается."""
    sandbox = tmp_path / "results"
    sandbox.mkdir()
    inside = sandbox / "out.txt"
    inside.write_text("0\t6\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    result = await mcp.call_tool("read_output_file", {"path": str(inside)})
    text = _text(result)

    assert "строк 1" in text
    assert "0\t6" in text


@pytest.mark.anyio
async def test_read_output_file_sandbox_blocks_parent_escape(tmp_path, monkeypatch):
    """`..` не помогает выйти из песочницы: путь раскрывается до проверки."""
    sandbox = tmp_path / "results"
    sandbox.mkdir()
    (tmp_path / "secret.txt").write_text("секрет\n", encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    escape = str(sandbox / ".." / "secret.txt")
    text = await _error("read_output_file", {"path": escape})

    assert "секрет" not in text


@pytest.mark.anyio
async def test_read_output_file_stops_on_size_limit(tmp_path, monkeypatch):
    """Объём чтения ограничен — большой файл не уходит в контекст целиком."""

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(sandbox, "MAX_OUTPUT_BYTES", 30)
    big = tmp_path / "big.txt"
    big.write_text("\n".join(["строка"] * 100) + "\n", encoding="utf-8")

    result = await mcp.call_tool("read_output_file", {"path": str(big)})
    text = _text(result)

    assert "чтение остановлено" in text
    assert text.count("строка") < 100


@pytest.mark.anyio
async def test_read_output_file_empty_is_error(tmp_path, monkeypatch):
    """Пустой файл — отказ: это признак, что расчёт не шёл."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")

    text = await _error("read_output_file", {"path": str(empty)})

    assert "пуст" in text


@pytest.mark.anyio
async def test_read_output_file_limits_returned_lines(tmp_path, monkeypatch):
    """max_lines ограничивает выдачу, но число строк в файле сообщается."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "many.txt"
    path.write_text("\n".join(str(i) for i in range(10)) + "\n", encoding="utf-8")

    text = _text(await mcp.call_tool("read_output_file",
                                     {"path": str(path), "max_lines": 3}))

    assert "строк 10" in text
    assert "показаны первые 3" in text
    assert "\n9" not in text


@pytest.mark.anyio
async def test_summarize_output_file_stats(monkeypatch, tmp_path):
    """Сводка считает точки, диапазон, min/max, среднее и наклон."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t2\n1\t5\n2\t8\n3\t11\n",
                                      encoding="utf-8")

    text = _tool_text(await mcp.call_tool("summarize_output_file",
                                          {"path": "out.txt"}))

    assert "точек 4" in text
    assert "min 2" in text
    assert "max 11" in text
    assert "среднее 6.5" in text
    assert "средний наклон: 3" in text


@pytest.mark.anyio
async def test_summarize_output_file_selects_column(monkeypatch, tmp_path):
    """Колонка выбирается: 0 — время, 1..n — значения.

    Значения подобраны так, чтобы колонки не были префиксами друг друга:
    иначе «min 1» находилось бы и в «min 100», и тест проходил бы, даже если
    `column` игнорируется целиком.
    """
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t5\t100\n1\t7\t200\n", encoding="utf-8")

    text = _tool_text(await mcp.call_tool(
        "summarize_output_file", {"path": "out.txt", "column": 1}))

    assert "значение (колонка 1)" in text
    assert "min 5" in text
    assert "max 7" in text
    assert "max 200" not in text, "считаться должна выбранная колонка"


@pytest.mark.anyio
@pytest.mark.parametrize("tool, content", [
    ("summarize_output_file", "0 1\n"),
    ("inspect_project_file", _XPRT_FIXTURE),
])
@pytest.mark.anyio
async def test_file_tools_share_one_sandbox(tool, content, monkeypatch,
                                            tmp_path):
    """Песочница у файловых инструментов одна: чужой файл не читается.

    Проверка одна на оба инструмента намеренно: правило чтения у них общее
    (`_load_result_file`), и отдельные копии теста разошлись бы так же, как
    когда-то разошлись копии кода.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "вне.xprt"
    outside.write_text(content, encoding="utf-8")
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(sandbox))

    text = await _error(tool, {"path": str(outside)})

    assert "разрешено только из каталога" in text


@pytest.mark.anyio
async def test_summarize_output_file_reports_no_numbers(monkeypatch, tmp_path):
    """Нечисловой файл — причина, а не «ноль точек»."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("заголовок\nещё строка\n",
                                      encoding="utf-8")

    text = await _error("summarize_output_file", {"path": "out.txt"})

    assert "нет ни одной числовой строки" in text


@pytest.mark.anyio
async def test_summarize_output_file_rejects_bad_column(monkeypatch, tmp_path):
    """Колонки вне строки — отказ с указанием, сколько их в файле."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text("0\t1\n", encoding="utf-8")

    text = await _error("summarize_output_file",
                        {"path": "out.txt", "column": 5})

    assert "вне диапазона" in text


@pytest.mark.anyio
async def test_inspect_project_file_without_com(monkeypatch, tmp_path):
    """Сохранённый проект разбирается без COM — работает и на Linux."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "model.xprt").write_text(_XPRT_FIXTURE, encoding="utf-8")

    text = _tool_text(await mcp.call_tool("inspect_project_file",
                                          {"path": "model.xprt"}))

    assert "классов 2, блоков 2" in text
    assert "k_0" in text
    assert "kx_0" in text
    assert "Константа" in text
    assert "formula_visible" in text
    assert "вычисляемые" in text


@pytest.mark.anyio
async def test_inspect_project_file_reports_schema_without_blocks(
        monkeypatch, tmp_path):
    """Корректный, но пустой экспорт отличается от провала разбора.

    Здесь файл валиден, поэтому это не отказ: сообщение говорит, что блоков в
    схеме нет, а секций параметров нет вовсе.
    """
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "пусто.xprt").write_text(_XPRT_EMPTY_FIXTURE, encoding="utf-8")

    text = _tool_text(await mcp.call_tool("inspect_project_file",
                                          {"path": "пусто.xprt"}))

    assert "классов 0, блоков 0" in text
    assert "нет блоков схемы" in text
    assert "нет секций <custom_props>" in text


@pytest.mark.anyio
async def test_inspect_project_file_notes_graphics_only_schema(
        monkeypatch, tmp_path):
    """Схема только из графики: объекты есть, блоков нет — и это сказано."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "графика.xprt").write_text(_XPRT_GRAPHICS_FIXTURE,
                                           encoding="utf-8")

    text = _tool_text(await mcp.call_tool("inspect_project_file",
                                          {"path": "графика.xprt"}))

    assert "только графика" in text


@pytest.mark.anyio
async def test_inspect_project_file_missing(monkeypatch, tmp_path):
    """Нет файла — отказ с подсказкой, куда его сохранять."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))

    text = await _error("inspect_project_file", {"path": "нет.xprt"})

    assert "файла нет" in text
    assert "save_project" in text


@pytest.mark.anyio
async def test_inspect_project_file_refuses_oversized(monkeypatch, tmp_path):
    """Слишком большой .xprt отвергается, а не читается в память целиком."""

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(files_tools, "MAX_PROJECT_BYTES", 64)
    (tmp_path / "big.xprt").write_text("x" * 500, encoding="utf-8")

    text = await _error("inspect_project_file", {"path": "big.xprt"})

    assert "больше" in text


@pytest.mark.anyio
async def test_summarize_refuses_file_without_complete_lines(monkeypatch,
                                                             tmp_path):
    """Одна строка без переводов: обрывок не выдаётся за точку данных.

    Два свойства сразу. Первое: строка не поднимается в память целиком —
    построчное чтение подняло бы её всю, и предел по байтам сработал бы уже
    после выделения памяти. Второе: раньше её обрывок разбирался как
    единственная точка с сотней колонок, то есть обрезанное число попадало в
    статистику как измеренное.
    """

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(tables, "MAX_SUMMARY_BYTES", 200)
    (tmp_path / "huge.txt").write_text("1 " * 100000, encoding="utf-8")

    text = await _error("summarize_output_file", {"path": "huge.txt"})

    assert "полной числовой строки" in text
    assert "не таблица" in text


@pytest.mark.anyio
async def test_summarize_drops_line_truncated_mid_number(monkeypatch, tmp_path):
    """Обрезанная на середине строка не становится «измеренным» значением.

    `2.123456`, обрезанное пределом байт, дало бы правдоподобное `2.12` и
    ушло бы в min/max/среднее/наклон. Такая строка отбрасывается, и об этом
    сказано в ответе.
    """

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    # 18 байт хватает на первую строку и половину второй.
    monkeypatch.setattr(tables, "MAX_SUMMARY_BYTES", 18)
    (tmp_path / "out.txt").write_text(
        "0 1.100000\n0.1 2.123456\n0.2 3.999999\n", encoding="utf-8")

    text = _tool_text(await mcp.call_tool("summarize_output_file",
                                          {"path": "out.txt"}))

    assert "точек 1" in text
    assert "2.12" not in text, "обрывок числа не должен попасть в сводку"
    assert "обрезана и отброшена" in text


@pytest.mark.anyio
async def test_summarize_skips_absurdly_wide_line(monkeypatch, tmp_path):
    """Строка с миллионами колонок не разбирается на объекты.

    Предела по байтам мало: 32 МБ текста из «1 1 1 …» — это миллионы
    токенов, и `split()` без ограничения превратил бы их в миллионы объектов,
    то есть память выросла бы в десятки раз против прочитанного.
    """

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(tables, "MAX_SUMMARY_COLUMNS", 8)
    (tmp_path / "wide.txt").write_text("0 " * 100 + "\n0 1\n", encoding="utf-8")

    text = _tool_text(await mcp.call_tool("summarize_output_file",
                                          {"path": "wide.txt"}))

    assert "точек 1" in text, "широкая строка пропущена, узкая прочитана"
    assert "слишком широких" in text


@pytest.mark.anyio
async def test_inspect_project_file_refuses_non_xml(monkeypatch, tmp_path):
    """Мусор вместо .xprt — отказ, а не «модель пуста».

    Без проверки корректности провал разбора давал бы «классов 0, блоков 0» —
    побайтово то же, что у пустой, но исправной схемы.
    """
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "мусор.xprt").write_text("это вовсе не XML", encoding="utf-8")

    text = await _error("inspect_project_file", {"path": "мусор.xprt"})

    assert "не является корректным" in text


@pytest.mark.anyio
async def test_inspect_project_file_refuses_truncated_xml(monkeypatch, tmp_path):
    """Обрезанный XML (недокачанный файл) — тоже отказ."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "обрыв.xprt").write_text(_XPRT_FIXTURE[:120], encoding="utf-8")

    text = await _error("inspect_project_file", {"path": "обрыв.xprt"})

    assert "не является корректным" in text


@pytest.mark.anyio
async def test_summarize_output_file_missing(monkeypatch, tmp_path):
    """Нет файла — отказ (та же ветка, что у read_output_file)."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))

    text = await _error("summarize_output_file", {"path": "нет.txt"})

    assert "файла нет" in text


@pytest.mark.anyio
async def test_summarize_counts_ragged_and_non_numeric_rows(monkeypatch,
                                                            tmp_path):
    """Учёт пропущенного виден: и мусорная строка, и строка другой ширины."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "out.txt").write_text(
        "0 1 10\nне число\n1 2 20\n2 3\n", encoding="utf-8")

    text = _tool_text(await mcp.call_tool("summarize_output_file",
                                          {"path": "out.txt"}))

    assert "точек 2" in text
    assert "нечисловых 1" in text
    assert "с другим числом колонок 1" in text
