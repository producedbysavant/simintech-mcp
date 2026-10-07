"""Скрипт блока «Язык программирования»: чтение и запись.

Чтение — снимком выгрузки (`SaveProjectXML`), запись — языковой парой
`setprop` + `reinitlangblock` с пересборкой пинов. Выделено из `blocks.py`
(issue #121).
"""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
from typing import Any, List, NamedTuple, Optional
import uuid

from fastmcp.exceptions import ToolError
from simintech_api.catalog import decode_xprt, parse_xprt_block_script
from simintech_api.exceptions import ScriptBridgeError

from .. import runtime, session
from ..app import mcp
from .blocks import missing_block, resolve_block, resolved_name
from .page_script import describe_outcome, refuse_contour_failure, run_contour


#: Имя свойства скрипта блока в языке и выгрузке.
_BLOCK_SCRIPT_PROP = "script"


def _normalize_script(text: str) -> str:
    """Переводы строк — к CRLF: в такой форме их несёт среда.

    Литерал встроенного языка не может содержать перевод строки, поэтому
    текст собирается построчно (`_runtime_literal`), а обратное чтение
    сравнивается с запрошенным после этой же нормализации: клиент вправе
    прислать текст с LF.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def _runtime_literal(text: str) -> str:
    """Литерал встроенного языка для **рантайма**: chr(34) и chr(13)+chr(10).

    `CLRF` здесь не годится: измерено 01.10.2026 — это константа
    декларативного текста, в рантайме её нет (скрипт с `clrf` не компилируется).

    Вход нормализуется к LF **внутри**: перевод строки в куске литерала
    недопустим, а собранный скрипт страницы прогоняется библиотечным
    `build_page_script` через `splitlines()`. Границ у него больше, чем
    CRLF: вертикальная табуляция, `\\f`, `\\x1c`–`\\x1e`, NEL (`\\x85`) и
    разделители строк Unicode (U+2028/U+2029) режут так же — такой символ,
    уйдя в литерал сырым, разрывал его и компиляция падала с диагнозом не
    по адресу (находка ревью PR #122; CRLF-ветка — прежняя находка ревью).
    Перевод строки выражается только `chr(13) + chr(10)`.
    """
    if not text:
        return '""'
    text = re.sub(r"[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]", "\n", text)
    parts: List[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        for piece_index, piece in enumerate(line.split('"')):
            if piece_index:
                parts.append("chr(34)")
            if piece:
                parts.append('"' + piece + '"')
        if index < len(lines) - 1:
            parts.append("chr(13) + chr(10)")
    return " + ".join(parts) if parts else '""'


def _block_script_snapshot(project: Any) -> str:
    """Снимок проекта в `.xprt` как текст — без запуска расчёта.

    `SaveProjectXML` — тот же путь, которым мост читает скриптовые записи:
    выгрузка не запускает расчёт и не сдвигает модельное время (в отличие от
    чтения скрипта контуром — `getpropasstring` перезапускает модель).
    Каталог временный: файл уходит вместе с ним.
    """
    with tempfile.TemporaryDirectory(
            prefix="simintech-block-script-",
            ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "page.xprt"
        project.save_xml(str(path))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ToolError(
                f"выгрузка проекта не создана: SaveProjectXML сообщил об "
                f"успехе, но файла {path} нет ({exc}). Снимок — путь чтения "
                f"скрипта блока без запуска расчёта, и продолжать без него "
                f"нельзя.") from exc
    return decode_xprt(raw)


@mcp.tool()
@runtime.com_threaded
def get_block_script(block: str) -> str:
    """Прочитать скрипт блока — например, «Языка программирования».

    Скрипт читается **снимком выгрузки** (`SaveProjectXML`), как скрипт
    страницы у `get_page_script`: расчёт не запускается и модельное время не
    сдвигается. Контурный путь (`getpropasstring`) для чтения не годится — он
    перезапускает модель и уничтожает результаты вызывающего.

    Скрипт есть у блока класса «Язык программирования» (запись `Script` в
    свойствах блока). У блока без такой записи ответ — «скрипта нет», а не
    отказ: это состояние блока, а не ошибка вызова. Ищется блок **главной
    страницы** (как у `connect`): у субмодели своей записи `Script` нет, и
    скрипт вложенного в неё блока за её собственный не выдаётся.

    Args:
        block: имя блока (автоимя из `list_blocks`) или его числовой id.
    """
    project = session.ensure_project()
    target = resolve_block(project.get_main_page(), block)
    if target is None:
        return missing_block(block)
    # Дальше адресат — по ИМЕНИ: и выгрузка (`parse_xprt_block_script`), и
    # тело записи ищут объект по имени, а не по id — с сырым числовым
    # токеном оба промахивались (ложное «скрипта нет» / «блок не найден»;
    # находка ревью PR #122). Как у `connect`: имя — из найденного объекта.
    name = resolved_name(target, block)
    try:
        class_name = target.class_name
    except Exception:                                             # noqa: BLE001
        class_name = ""
    marked = f"'{name}'" + (f" [{class_name}]" if class_name else "")
    try:
        script = parse_xprt_block_script(_block_script_snapshot(project), name)
    except ScriptBridgeError as exc:
        raise ToolError(
            f"прочитать скрипт блока {marked} не удалось: {exc}. Снимок "
            f"выгрузки не разобран или значение записи не той формы — "
            f"повторный вызов после `save_project`/переоткрытия может помочь; "
            f"проект этим вызовом не тронут.") from exc
    if script is None:
        return (f"У блока {marked} скрипта нет: в выгрузке проекта у него нет "
                f"записи `Script`. Скрипт есть у блоков «Язык "
                f"программирования»; состав блоков — `list_blocks`.")
    if not script.strip():
        return (f"Скрипт блока {marked} пуст: запись `Script` есть, значение "
                f"пустое.")
    return f"Скрипт блока {marked}:\n{script}"


class _ScriptReply(NamedTuple):
    """Разобранный ответ тела записи скрипта.

    `kind` — «written» | «no-block» | «unknown»; `old`/`new` — прежний и
    перечитанный скрипт, `ports_before`/`ports_after` — число портов блока.
    """

    kind: str
    old: str = ""
    new: str = ""
    ports_before: int = 0
    ports_after: int = 0


#: Строка ответа тела с числом портов: `ports=2->4` — целиком, не поиском
#: подстроки: такие же символы могут стоять в тексте скрипта (находка ревью).
_PORTS_RE = re.compile(r"ports=(\d+)->(\d+)")


def _parse_script_reply(lines: List[str], token: str) -> _ScriptReply:
    """Разобрать строки тела: скрипты между маркерами, число портов.

    Маркеры уникальны на вызов (токен), и область разбора ограничена ими же:
    сентинел `err=no-block` и строка `ports=…` ищутся **вне** текста скрипта
    (эхо-текст идёт между маркерами). Иначе строка-сентинел внутри самого
    скрипта подменяла бы ответ — ровно то, от чего маркеры и защищают
    (находка ревью: скрипт с комментарием `// ports=9->9` давал чужие числа).
    """
    def between(which: str) -> Optional[str]:
        begin = f"{token}_{which}_BEGIN"
        end = f"{token}_{which}_END"
        try:
            first = lines.index(begin)
            last = lines.index(end)
        except ValueError:
            return None
        if last < first:
            return None
        return "\n".join(lines[first + 1:last])

    old = between("OLD")
    new = between("NEW")
    if old is None or new is None:
        # Ветка «блок не найден» маркеров не печатает вовсе — её сентинел
        # ищется только здесь, когда разбор скриптов уже не состоялся.
        if "err=no-block" in [line.strip() for line in lines]:
            return _ScriptReply("no-block")
        return _ScriptReply("unknown")
    ports_before = ports_after = 0
    for line in lines[lines.index(f"{token}_NEW_END") + 1:]:
        match = _PORTS_RE.fullmatch(line.strip())
        if match:
            ports_before = int(match.group(1))
            ports_after = int(match.group(2))
            break
    return _ScriptReply("written", old=old, new=new,
                        ports_before=ports_before, ports_after=ports_after)


def _set_block_script_body(block_name: str, script: str, token: str) -> str:
    """Тело записи скрипта блока — форма, проверенная живым прогоном.

    Рецепт (замеры 01.10.2026 и 03.10.2026): `setprop(obj, "script", …)`,
    затем `reinitlangblock(obj)` — без второго шага пины не пересобираются
    (свежий блок остаётся с дефолтным портом, провода к другим пинам —
    половинками), а `set_block_param("script")` молча не применяется.
    Прежний скрипт читается до записи, новый — после: это подтверждение
    записи. Число портов до/после показывает, что пересборка пинов прошла.
    """
    literal_name = _runtime_literal(block_name)
    literal_script = _runtime_literal(script)
    return (
        f"obj = findobjectbyname({literal_name});\n"
        'if obj = 0 then writelnutf8(fid, "err=no-block");\n'
        "if obj <> 0 then begin\n"
        f'  old_script = getpropasstring(obj, "{_BLOCK_SCRIPT_PROP}");\n'
        "  ports_before = getblockportcount(obj);\n"
        f'  setprop(obj, "{_BLOCK_SCRIPT_PROP}", {literal_script});\n'
        "  reinitlangblock(obj);\n"
        f'  new_script = getpropasstring(obj, "{_BLOCK_SCRIPT_PROP}");\n'
        "  ports_after = getblockportcount(obj);\n"
        f'  writelnutf8(fid, "{token}_OLD_BEGIN");\n'
        "  writelnutf8(fid, old_script);\n"
        f'  writelnutf8(fid, "{token}_OLD_END");\n'
        f'  writelnutf8(fid, "{token}_NEW_BEGIN");\n'
        "  writelnutf8(fid, new_script);\n"
        f'  writelnutf8(fid, "{token}_NEW_END");\n'
        '  writelnutf8(fid, "ports=" + inttostr(ports_before) + "->" + '
        "inttostr(ports_after));\n"
        "end;\n"
    )


@mcp.tool()
@runtime.com_threaded(mutates_project=True)
def set_block_script(block: str, script: str) -> str:
    """Записать скрипт в блок «Язык программирования» и пересобрать его пины.

    **Прежний скрипт — в ответе** (как у `set_page_script`): запись о прежнем
    содержимом не сообщает, и клиент должен иметь возможность его вернуть.

    **Как это устроено.** Запись — пара «`setprop(obj, "script", …)` +
    `reinitlangblock(obj)`» через контур (замеры 01.10.2026 и 03.10.2026):
    без второго шага пины не пересобираются — свежий блок остаётся с
    дефолтным портом, а провода к остальным пинам создаются половинками;
    `set_block_param("script")` при этом молча не применяется. После записи
    текст перечитывается — ответ подтверждает запись фактом, а не строкой
    тела.

    **Об ошибках компиляции среда молчит** — текст ошибки виден только в окне
    сообщений редактора SimInTech. Признака «скрипт не собрался» у инструмента
    нет: пины пересобираются и у скрипта с синтаксической ошибкой (замер:
    4 → 2), поэтому «валидность» по числу портов не проверяется. Ответ
    называет число портов до и после — по нему видно, что пересборка прошла.

    **Пустой скрипт отвергается**: он оставляет блок без портов (замер:
    2 → 0) и рвёт соединения. Если цель — осознанная очистка, передайте текст
    с комментарием — так она видна и в модели.

    **Пересборка пинов может оставить провода половинками** (у блока пины
    пересоздаются, а провода из прошлой конфигурации — нет): проверьте
    соединения и досоедините `connect`-ом; вердикт в ответе называет, считает
    ли модель. Блоки ищутся на **главной странице** проекта (как у `connect`
    и `get_block_script`).

    Args:
        block: имя блока (автоимя из `list_blocks`) или его числовой id.
        script: новый текст скрипта (канон: секции `input`/`output`/`var` и
            тело; переводы строк — любые, нормализуются к CRLF).
    """
    if not script.strip():
        raise ToolError(
            "скрипт пуст: пустой текст оставляет блок без портов (замер: "
            "2 → 0) и рвёт соединения. Если цель — очистка, передайте текст "
            "с одним комментарием — так она видна и в модели.")
    if "CTX_BEGIN" in script or "CTX_END" in script:
        raise ToolError(
            "текст скрипта содержит служебные маркеры контура "
            "(`CTX_BEGIN`/`CTX_END`): текст возвращается ответом через файл "
            "результата, границы которого эти маркеры и держат, — запись "
            "такого текста рвёт разбор ответа. Соберите маркер в тексте "
            "конкатенацией (например, `\"CTX\" + \"_END\"`), если он нужен "
            "как содержание, и повторите.")
    project = session.ensure_project()
    target = resolve_block(project.get_main_page(), block)
    if target is None:
        return missing_block(block)
    # Тело ищет объект по имени (`findobjectbyname`) — числовой id туда не
    # годится; имя берётся из найденного блока (находка ревью PR #122).
    name = resolved_name(target, block)
    normalized = _normalize_script(script)
    token = "BLK" + uuid.uuid4().hex[:12]
    outcome, _restored = run_contour(
        _set_block_script_body(name, normalized, token),
        failed="записать скрипт блока не удалось")
    refuse_contour_failure(
        outcome, failed="скрипт не записан",
        unsure="запись скрипта не подтверждена",
        aborted_hint=("Скрипт блока мог измениться — проверьте его "
                      "`get_block_script`."))
    reply = _parse_script_reply(outcome.lines, token)
    if reply.kind == "no-block":
        raise ToolError(
            f"скрипт не записан: блок '{name}' не найден при исполнении "
            f"тела — он мог исчезнуть со страницы. Проект не изменён.")
    if reply.kind != "written":
        raise ToolError(
            "запись скрипта не подтверждена: тело отработало, но не оставило "
            "распознаваемого ответа. Проверьте скрипт блока `get_block_script`.")
    if _normalize_script(reply.new) != normalized:
        raise ToolError(
            f"запись не подтверждена: перечитанный скрипт не совпал с "
            f"запрошенным. В блоке теперь:\n{reply.new}\nВерните нужный текст "
            f"повторным вызовом — записи «наполовину» молча не проходят.")
    tail = (f"\n---- прежний скрипт ----\n{reply.old}" if reply.old
            else "\nПрежний скрипт был пуст.")
    return (f"Скрипт блока '{name}' записан. Портов: {reply.ports_before} → "
            f"{reply.ports_after}.\n"
            f"{describe_outcome(outcome, what='Вердикт')}{tail}")
