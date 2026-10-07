"""Инструмент справки: что сервер умеет и куда пишет результаты.

Печатает текущий каталог результатов (`sandbox.safe_output_root`) — иначе
клиент не знает, где искать файл блока «В файл», и не может передать путь
`read_output_file`.

Здесь же — справочник встроенного языка: поиск по реестру функций справки
поставки (`search_language_functions`) и карточка функции с синтаксисом и
аргументами (`get_language_function`). Реестр читается из `simintech_api`
(`data/language_functions.json`, собран из справки поставки): по нему видно,
что вообще можно в SimInTech, — правдоподобное имя функции в языке может
отсутствовать, а раздел «Графические и системные» (объекты, порты, линии)
заменяет отсутствующие COM-методы.

COM не трогает, поэтому идёт под `plain_tool`: под `com_threaded` справка
вставала бы в очередь за единственным COM-потоком и при занятом `mmain.exe`
сама упиралась бы в `COM_CALL_TIMEOUT`, хотя ей этого не нужно.
"""

from __future__ import annotations

import difflib
from typing import Dict, List

from fastmcp.exceptions import ToolError
from simintech_api import language as language_api

from .. import runtime, sandbox
from ..app import mcp


@mcp.tool()
@runtime.plain_tool
def help_text() -> str:
    """Справка: порядок работы и где взять список инструментов.

    Перечня команд здесь намеренно нет: он дублировал бы `tools/list` и
    расходился бы с ним при каждом добавлении инструмента. Источник истины по
    составу — `tools/list`.
    """
    return (
        "Состав инструментов — в `tools/list` (единственный источник истины).\n"
        "\n"
        "Порядок работы:\n"
        "  1. create_project(end_time=N) — проект из шаблона пустой модели\n"
        "  2. add_block(class_name, props=\"a=2\") — блоки; имена не задаются,\n"
        "     фактическое имя возвращает сам вызов\n"
        "  3. connect(src, dst) — связи; соединяйте ВСЕ входы: блок с висящим\n"
        "     входом молча останавливает расчёт всей модели\n"
        "  4. layout_place() — без аргументов: все блоки и все связи сессии —\n"
        "     расставит и трассирует линии (иначе провода идут по диагонали)\n"
        "  5. run(to_time=N) — расчёт; проверьте get_time() в ответе\n"
        f"  6. read_output_file(путь) или summarize_output_file(путь) —\n"
        f"     результат блока «В файл» (сводка: min/max/среднее/наклон)\n"
        "  7. save_screenshot() — снимок схемы (PNG) в каталог результатов:\n"
        "     смотрите на схему глазами — наложения и нечитаемую раскладку\n"
        "     текстовая выгрузка не показывает\n"
        "\n"
        "Ответы правок называют текущий проект («Изменения внесены в: …»)\n"
        "и говорят, что правка не сохранена; open/create/close называют смену\n"
        "проекта («было … → стало …»): сверяйте, что правка пошла в нужный\n"
        "проект, в том же ответе, а не по счётчику объектов в следующем.\n"
        "Откатить несохранённые правки — reload_project (переоткрывает\n"
        "текущий проект из файла без сохранения).\n"
        "\n"
        f"Результаты читаются только из каталога:\n"
        f"  {sandbox.safe_output_root()}\n"
        f"Блок «В файл» должен писать внутрь него (свойство filename); каталог\n"
        f"переопределяется переменной SIMINTECH_OUTPUT_DIR.\n"
        "\n"
        "Блоки библиотеки «Конечные автоматы» через COM создаются, но только\n"
        "по полному имени записи: `Конечные автоматы - Состояние автомата` —\n"
        "блок; короткий заголовок с палитры (`Состояние автомата`) CreateBlock\n"
        "не принимает (возвращает 0). Полный автомат через MCP не собрать:\n"
        "состояния живут на внутренней странице карты (`GetSubmodelPage`),\n"
        "инструмента для неё нет; переходы — линия с классом\n"
        "`КА - Связь блоков состояний` (SetGraphBlockProp; см. simintech-code,\n"
        "docs/reference/com_api_inventory.md §5). Класс «Порт выхода»\n"
        "(единственный в UNSUPPORTED_COM_BLOCK_CLASSES) add_block отвергает:\n"
        "это защита библиотеки, а не свойство COM. Замерено на живом COM\n"
        "2026-09-18: CreateBlock создаёт запись и возвращает ненулевой id,\n"
        "блок появляется на странице и его свойства читаются; но годность\n"
        "класса в модели не проверена, поэтому защита оставлена. «Из памяти»\n"
        "снят с запрета 05.10.2026: годность в расчёте подтверждена живым\n"
        "замером (пара ToMem_0/FromMem_0 с PortNames считает) — add_block его\n"
        "создаёт наравне с прочими классами.\n"
        "\n"
        "Разобрать сохранённый проект (.xprt) можно и без SimInTech:\n"
        "inspect_project_file(путь) — классы, параметры и имена блоков\n"
        "(файл должен лежать в каталоге результатов).\n"
        "\n"
        "Аргументы инструментов и их ограничения описаны в их docstring.\n"
        "Ресурсы (read-only): simintech://status, simintech://project/blocks,\n"
        "  simintech://blocks/catalog — классы и имена параметров блоков;\n"
        "  simintech://language/functions и .../<имя> — реестр функций\n"
        "  встроенного языка (имя, назначение, синтаксис, аргументы);\n"
        "  искать — search_language_functions, карточка — get_language_function;\n"
        "  simintech://skills и simintech://skills/<имя> — инструкции из\n"
        "  репозитория simintech-skill (каталог задаёт SIMINTECH_SKILLS_DIR)\n"
        "Промпты (шаблоны): create_pid_model, create_rc_chain\n"
        "Среда и ограничения COM: репозиторий simintech-code — CLAUDE.md и\n"
        "docs/reference/com_api_inventory.md\n"
    )


# ─── Справочник встроенного языка ─────────────────────────────────

#: Сколько результатов поиска печатать за вызов. Каждая строка — результат;
#: «показать всё» превратило бы поиск в выгрузку 900 записей в контекст,
#: а усечение ответа клиентом отрезало бы ровно то, что искали (так же
#: устроен `resources.MAX_CATALOG_CLASSES`).
MAX_LANGUAGE_RESULTS = 50
DEFAULT_LANGUAGE_RESULTS = 20


def format_language_registry(functions: List[language_api.LanguageFunction],
                             meta: Dict[str, object]) -> str:
    """Обзор реестра языка: объём, разделы и как искать.

    Общий для инструмента (пустой запрос) и ресурса
    `simintech://language/functions`: два разных текста об одном реестре
    разошлись бы при первой же правке.
    """
    unique = len({function.name for function in functions})
    with_syntax = sum(1 for function in functions if function.syntax)
    with_args = sum(1 for function in functions if function.args)
    counts: Dict[str, int] = {}
    for function in functions:
        counts[function.category] = counts.get(function.category, 0) + 1
    help_version = str(meta.get("help_version") or "")
    head = (f"Реестр функций встроенного языка SimInTech: {len(functions)} "
            f"записей, {unique} уникальных имён")
    if help_version:
        head += f" (справка поставки {help_version})"
    lines = [f"{head}.",
             f"Синтаксис зафиксирован у {with_syntax} записей, аргументы — "
             f"у {with_args}; разделы «Графические и системные» и «Порты "
             f"блоков и линии связи» заменяют отсутствующие COM-методы."]
    for category, count in sorted(counts.items()):
        lines.append(f"  {category} — {count}")
    lines.append("Поиск по имени и смыслу — search_language_functions; "
                 "карточка функции — get_language_function; ресурс по "
                 "имени — simintech://language/functions/<имя>.")
    return "\n".join(lines)


def format_language_function(
        function: language_api.LanguageFunction) -> str:
    """Карточка функции: имя, раздел, назначение, синтаксис, аргументы.

    Общая для инструмента и ресурса: ответы обязаны совпадать, иначе агент
    получал бы разную форму вызова из инструмента и из ресурса. Синтаксис и
    аргументы печатаются, только если запись их несёт: у 59 записей раздела
    «Аргументы» нет — у `closeapp`-подобных аргументов не бывает, — и пустой
    заголовок выглядел бы поломкой. Реестр схемы 1 честно говорит, что формы
    вызова в нём нет.
    """
    lines = [f"{function.name} — {function.full_category}"]
    if function.graphics_only:
        lines.append("Доступна только в графическом контейнере.")
    lines.append(function.purpose or "Назначение в справке не указано.")
    if function.syntax:
        lines.append("")
        lines.append("Синтаксис:")
        lines.extend(f"  {line}" for line in function.syntax.splitlines())
    if function.args:
        lines.append("")
        lines.append("Аргументы:")
        for arg in function.args:
            head = arg.name + (f": {arg.type}" if arg.type else "")
            tail = f" — {arg.description}" if arg.description else ""
            lines.append(f"  {head}{tail}")
    else:
        # Пустая таблица — не «аргументов нет»: у `createblock` и `dopt`
        # форма вызова выше аргументы называет, а таблица справки пуста —
        # «их нет» противоречило бы строке синтаксиса и вводило агента в
        # заблуждение (находка ревью 03.10.2026).
        lines.append("")
        if function.syntax:
            lines.append("Раздел «Аргументы» в справке не заполнен — состав "
                         "и порядок смотрите по форме вызова выше.")
        else:
            lines.append("Раздел «Аргументы» в справке не заполнен.")
    if not function.syntax:
        lines.append("Формы вызова в реестре этой поставки нет — смотрите "
                     "справку по пути ниже.")
    lines.append("")
    lines.append(f"Справка: {function.doc} (путь от каталога webhelp "
                 f"поставки; полный текст с примером — там).")
    return "\n".join(lines)


def _language_haystack(function: language_api.LanguageFunction) -> str:
    """Все поля записи одной строкой — по ним идёт поиск.

    Синтаксис и имена аргументов входят намеренно: одну и ту же функцию
    ищут и по делу («удаление объекта»), и по форме вызова (`createwire`).
    """
    parts = [function.name, function.category, function.section,
             function.purpose, function.syntax,
             " ".join(arg.name for arg in function.args)]
    return " ".join(parts).casefold()


def _token_hits(token: str, haystack: str) -> bool:
    """Найдено ли слово запроса: точное вхождение или основа слова.

    Русские слова в справке склоняются и спрягаются («удаление» против
    «удаления», «линия» против «линии», «удалить» против «удаления»), и
    поиск по точной подстроке промахивался бы на формах — а по реестру чаще
    всего ищут именно по-русски. Основа — слово без последних `min(3, len-4)`
    букв: **одно правило** на все длины, от четырёх букв («abs», «wire» —
    только точное вхождение, срез от них давал бы мусор). Грубо, зато
    предсказуемо; точное вхождение проверяется первым и не теряется.

    Правило подбиралось замерами, и лестница порогов по длинам не выжила
    (ревью 03.10.2026): с ней «удалить объект» не находил `removeprimitiv`
    («удалить» → «удали», а в справке «удаления» — общая часть «удал»),
    «линия связи» — `createwire`, «сохранить» — `savescreenshot`.
    """
    if token in haystack:
        return True
    cut = min(3, len(token) - 4)
    return cut > 0 and token[:-cut] in haystack


def _render_matches(matches: List[language_api.LanguageFunction],
                    limit: int, head: str) -> str:
    """Список найденных функций: строка «имя — назначение [раздел]» на запись.

    Общий для полного и частичного (по отдельным словам) ответов поиска:
    два текста об одном списке разошлись бы при первой же правке.
    """
    shown = matches[:limit]
    text = f"{head}: {len(matches)}"
    if len(matches) > len(shown):
        text += f" (показаны первые {len(shown)})"
    lines = [text + "."]
    for function in shown:
        summary = function.purpose or "назначение в справке не указано"
        lines.append(f"  {function.name} — {summary} "
                     f"[{function.full_category}]")
    lines.append("Карточка (синтаксис, аргументы) — "
                 "get_language_function(\"имя\").")
    # Непустая выдача не значит исчерпывающая: слова обязаны совпасть все, и
    # лишний глагол в запросе («получить линию связи») молча сужает её
    # (находка ревью 03.10.2026). Строка учит расширять, а не верить счёту.
    lines.append("Поиск требует все слова сразу: если искомого нет — "
                 "сократите запрос (одно-два слова).")
    return "\n".join(lines)


@mcp.tool()
@runtime.plain_tool
def search_language_functions(
        query: str = "",
        limit: int = DEFAULT_LANGUAGE_RESULTS) -> str:
    """Найти функцию встроенного языка SimInTech — по имени или по смыслу.

    Реестр — около 900 функций из справки поставки: имя, категория,
    назначение, синтаксис и аргументы. Поиск идёт по всем полям сразу, без
    учёта регистра; запрос разбивается на слова, и совпасть должны все. Это
    ответ на вопрос «что вообще можно в SimInTech»: правдоподобное имя может
    отсутствовать, а нужную функцию ищут по делу — «удаление объекта»,
    «сохранить снимок экрана», «линия связи».

    Имя, совпавшее целиком, печатается первым; терпимость к кириллическим
    двойникам латинских букв (в справке есть `arсsin` с кириллической «с»)
    даёт `get_language_function`.

    Пустой запрос — обзор: разделы справки и число функций в каждом.
    Если ни одна запись не содержит **все** слова сразу, ответ покажет
    частичные совпадения по отдельным словам и честно назовёт их частичными:
    у агента чаще частичный зацеп («сохранить снимок» — сами слова не из
    справки, а «сохранить» ведёт к семейству) лучше ответа «ничего».

    Args:
        query: слова поиска; пусто — обзор разделов.
        limit: сколько результатов показать (1…50). Ответ называет общее
            число найденных.
    """
    if limit < 1 or limit > MAX_LANGUAGE_RESULTS:
        raise ToolError(
            f"limit должен быть от 1 до {MAX_LANGUAGE_RESULTS}: каждый "
            f"результат — строка ответа, и предел держит ответ читаемым.")
    try:
        functions = language_api.language_functions()
    except OSError as exc:
        raise ToolError(
            f"реестр функций встроенного языка недоступен: {exc}") from exc
    query_text = query.strip()
    if not query_text:
        return format_language_registry(functions,
                                        language_api.registry_meta())
    tokens = query_text.casefold().split()
    # Haystack — один раз на функцию и на оба прохода (ревью 03.10.2026:
    # прежний код пересобирал шесть полей на каждое слово и повторял проход).
    entries = [(function, _language_haystack(function))
               for function in functions]
    found = [function for function, hay in entries
             if all(_token_hits(token, hay) for token in tokens)]
    if not found:
        # «Все слова сразу» не сошлось — показываем, что есть по отдельным
        # словам: у агента чаще частичный зацеп («сохранить снимок» — слова
        # вендорским текстом не названо, а «сохранить» находит семейство)
        # лучше ответа «ничего». Это честно названо частичным совпадением.
        partial = [function for function, hay in entries
                   if any(_token_hits(token, hay) for token in tokens)]
        if partial:
            return _render_matches(
                partial, limit,
                f"Точного совпадения нет: «{query_text}» — все слова сразу не "
                f"встречаются ни в одной записи. По отдельным словам")
        return (f"Ничего не нашлось: «{query_text}». Реестр знает "
                f"{len(functions)} функций справки — попробуйте одно-два "
                f"ключевых слова (createwire, удаление, порт, сохранить) "
                f"или обзор разделов пустым запросом.")
    exact = language_api.find_function(query_text)
    if exact is not None:
        # Совпавшее имя — первым: обычно ищут саму функцию, а не упоминание.
        found.sort(key=lambda f: (f.name, f.doc) != (exact.name, exact.doc))
    return _render_matches(found, limit, "Найдено")


@mcp.tool()
@runtime.plain_tool
def get_language_function(name: str) -> str:
    """Карточка функции встроенного языка: синтаксис, аргументы, назначение.

    По имени из реестра справки поставки. Поиск терпим к регистру и
    кириллическим двойникам латинских букв: `arcsin`, набранный латиницей,
    находит `arсsin` (в справке «с» кириллическая) — годное к вставке в
    скрипт имя печатает сама карточка.

    Промах — не молчание: ответ предлагает похожие имена и полный поиск
    (`search_language_functions`).

    Args:
        name: имя функции (`savescreenshot`, `removeprimitiv`).
    """
    try:
        function = language_api.find_function(name)
        functions = language_api.language_functions()
    except OSError as exc:
        raise ToolError(
            f"реестр функций встроенного языка недоступен: {exc}") from exc
    if function is None:
        by_fold = {item.name.casefold(): item.name for item in functions}
        folded = name.strip().casefold()
        close = difflib.get_close_matches(folded, list(by_fold),
                                          n=5, cutoff=0.6)
        suggestions = [by_fold[key] for key in close]
        if not suggestions and folded:
            suggestions = [item for key, item in by_fold.items()
                           if folded in key][:5]
        hint = (f"Похожие имена: {', '.join(suggestions)}. "
                if suggestions else "")
        raise ToolError(
            f"Функции «{name}» нет в реестре встроенного языка "
            f"({len(functions)} записей справки поставки): правдоподобное "
            f"имя в языке может отсутствовать. {hint}Полный поиск — "
            f"search_language_functions(query=…).")
    return format_language_function(function)
