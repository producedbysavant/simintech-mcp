"""Языковой слой: скрипт страницы и тела операций — через контур моста.

Инструменты поверх `ScriptBridge.run_page_script`: тело идёт в секцию
`initialization` (только там разрешено создавать объекты), исход различается на
пять состояний, прежний скрипт страницы возвращается на место. Отчёт об
изменениях собирается **здесь**: библиотека отдаёт `restored_script` и исход, а
«сколько объектов стало» — это COM-чтение снаружи контура (спецификация §3.7,
`simintech-code/docs/superpowers/specs/2026-09-29-language-contour.md`).

Отличие от `model_text.export_model_text`: там тело идёт под `if firststep then`
(так работала проба), здесь — в `initialization`, как требует создание объектов.
"""

from __future__ import annotations

from simintech_api.core.script_bridge import ScriptBridge

from .. import session

#: Сколько объектов страницы перечислять в отчёте: список — для человека, а не
#: для машинной обработки, поэтому длинный хвост обрезается.
MAX_REPORTED_OBJECTS = 20


def _bridge() -> ScriptBridge:
    """Мост для текущего проекта — общая часть всех инструментов модуля."""
    return ScriptBridge(session._ensure_client(), session._ensure_project().id)


def _object_names() -> list[str]:
    """Имена объектов текущей страницы — снимки «до» и «после».

    Именно **текущей**: мост ставит скрипт в страницу, которую называет
    `GetCurentPage`, и отчёт обязан считать по той же странице. `list_blocks`
    инструмента берёт главную — для отчёта это было бы расхождение на модели,
    где работа идёт внутри субмодели.
    """
    page = session._ensure_project().get_current_page()
    names = []
    for obj in page.get_blocks():
        try:
            names.append(obj.get_name())
        except Exception:                                            # noqa: BLE001
            names.append("(без имени)")
    return names


def _change_report(before: list[str], after: list[str],
                   restored: str) -> str:
    """Отчёт об изменениях: было/стало, добавленные имена, возврат скрипта.

    Сравнение идёт по **мультимножествам имён**, а не по числу: среда сама
    переименовывает объекты (`kx_0`), поэтому «стало больше» и «добавлен объект
    X» — разные утверждения, и склеивать их нельзя. Имя, исчезнувшее из снимка,
    добавленным не считается и не показывается: об удалении контур не сообщает
    (удаление объекта в этой сборке не предлагается вовсе — спецификация §8).
    """
    added = list(after)
    for name in before:
        if name in added:
            added.remove(name)
    shown = ", ".join(added[:MAX_REPORTED_OBJECTS])
    tail = f"; добавлено: {shown}" if shown else "; добавленных объектов нет"
    more = (f" (и ещё {len(added) - MAX_REPORTED_OBJECTS})"
            if len(added) > MAX_REPORTED_OBJECTS else "")
    restored_note = ("Прежний скрипт страницы возвращён: да" if restored
                     else "Прежний скрипт страницы возвращён: нет — он был пуст")
    return (f"Отчёт об изменениях: объектов было {len(before)}, стало "
            f"{len(after)}{tail}{more}. {restored_note}")
