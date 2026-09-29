"""Инструменты языкового слоя: исход контура, отчёт об изменениях, отказы.

Мост подделывается целиком: настоящий требует Windows и живого `mmain.exe`, а
проверяется здесь контракт инструмента — что он возвращает, что пишет в каталог
результатов и как отказывает.
"""

from __future__ import annotations

from simintech_mcp.tools import page_script


# ─── Отчёт об изменениях ─────────────────────────────────────────────────────


def test_change_report_names_added_objects():
    """Отчёт: было/стало и имена добавленных объектов.

    Проверяется **чистая** функция: снимки «до» и «после» ей передаёт
    вызывающий, поэтому отчёт проверяется без моста и не подменяет собой
    проверку инструментов.
    """
    report = page_script._change_report(
        ["k_0"], ["k_0", "Субмодель_0"], "// прежний скрипт")

    assert "было 1" in report and "стало 2" in report
    assert "Субмодель_0" in report
    assert "Прежний скрипт страницы возвращён: да" in report


def test_change_report_does_not_call_replaced_object_new():
    """Переименование не выдаётся за добавление: сравнение по именам, не по числу.

    Среда сама переименовывает объекты (`kx_0`), поэтому «стало больше» и
    «добавлен объект X» — разные утверждения, и склеивать их нельзя.
    """
    report = page_script._change_report(["k_0", "kx_0"], ["k_0"], "")

    assert "стало 1" in report
    assert "добавленных объектов нет" in report
    assert "возвращён: нет" in report


def test_change_report_truncates_long_list():
    """Длинный список добавленных обрезается с честной пометкой о хвосте."""
    before = []
    after = [f"k_{index}" for index in range(page_script.MAX_REPORTED_OBJECTS + 5)]

    report = page_script._change_report(before, after, "x")

    assert "и ещё 5" in report, "хвост списка скрыт без предупреждения"
