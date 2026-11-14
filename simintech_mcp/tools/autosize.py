"""Скрипт саморазмера блока: генерация текста и установка в `OnInitScript`.

Живые замеры 14.11.2026 (2.26.x, COM-контур):

* слот `OnInitScript` исполняется при запуске проекта на расчёт только в
  форме `initialization ... end;`, и свойства блока доступны через `self.`
  (`self.Height`, `self.PortNames`); голое тело и секция `beforecompile` в
  слоте не работают;
* `setprop(obj, "OnInitScript", …)` в контуре страницы запись применяет, а
  `SetBlockProp`/`set_block_param` по этому свойству - молчаливый no-op
  (как у `PortNames` и `script`), поэтому установка идёт контуром;
* `getportinfo` в контуре страницы **не компилируется**, а в скрипте блока
  работает (проверено на «Мультиплексоре»: 5 входов → высота 80);
* чужой `OnInitScript` не перетирается: пишем только в пустой слот.

Правило высоты - общее с `rules.grid_height` (16 px на контакт, при разном
числе входов и выходов - `16*НОК`), чтобы скрипт и подгонка не спорили:
иначе блок переставлял бы размер при каждом старте расчёта.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, List, NamedTuple, Tuple

from fastmcp.exceptions import ToolError

from .block_script import _normalize_script, _runtime_literal
from .block_size import size_pair_lines
from .page_script import (
    activate_or_refuse,
    refuse_contour_failure,
    run_contour,
)

#: Метка принадлежности скрипта: по ней видно, что слот занят нами, а не
#: человеком (и что текст можно когда-нибудь обновить осознанно).
MARKER = "// simintech-mcp: авторазмер 16 px на контакт, сетка 8"


#: Скрипт порт-блока: высота по строкам `PortNames` (правило 01.10.2026).
#: Строки считаются по CRLF: `extractstrings` делит только по `,;` и табу,
#: а перевод строки в литерале рантайма недоступен (нет `CLRF`).
_PORT_SCRIPT = "\n".join([
    MARKER,
    "var",
    "  __p: string,",
    "  __n: integer,",
    "  __i: integer;",
    "initialization",
    "  __p = self.PortNames;",
    "  __n = 0;",
    "  __i = 1;",
    "  while __i <= length(__p) - 1 do begin",
    "    if strcopy(__p, __i, 2) = chr(13) + chr(10) then __n = __n + 1;",
    "    __i = __i + 1;",
    "  end;",
    "  if length(__p) > 0 then",
    "    if strcopy(__p, length(__p), 1) <> chr(10) then __n = __n + 1;",
    "  if __n > 0 then self.Height = 16 * __n;",
    "end;",
])

#: Скрипт блока с контактами: высота по сетке 8 px, `16*НОК(входы, выходы)`.
#: Ноль заменяется единицей перед НОК - сторона без портов не участвует.
_ROWS_SCRIPT = "\n".join([
    MARKER,
    "var",
    "  __i: integer,",
    "  __p: integer,",
    "  __t: integer,",
    "  __nm: string,",
    "  __lt: integer,",
    "  __nin: integer,",
    "  __nout: integer,",
    "  __a: integer,",
    "  __b: integer,",
    "  __g: integer;",
    "initialization",
    "  __nin = 0;",
    "  __nout = 0;",
    "  __i = 0;",
    "  while __i <= getblockportcount(self) - 1 do begin",
    "    __p = getblockportid(self, __i);",
    "    __t = getportinfo(__p, __nm, __lt);",
    "    if __t = 0 then __nin = __nin + 1;",
    "    if __t = 1 then __nout = __nout + 1;",
    "    __i = __i + 1;",
    "  end;",
    "  if (__nin > 0) or (__nout > 0) then begin",
    "    __a = __nin;",
    "    if __a < 1 then __a = 1;",
    "    __b = __nout;",
    "    if __b < 1 then __b = 1;",
    "    while __b > 0 do begin",
    "      __g = __a mod __b;",
    "      __a = __b;",
    "      __b = __g;",
    "    end;",
    "    self.Height = 16 * (__nin * __nout div __a);",
    "  end;",
    "end;",
])


def script_text(class_name: str) -> str | None:
    """Текст скрипта для класса правила; `None` - класс вне правила."""
    from ..rules import PORT_HEIGHT_CLASSES, ROW_HEIGHT_CLASSES

    if class_name in PORT_HEIGHT_CLASSES:
        return _PORT_SCRIPT
    if class_name in ROW_HEIGHT_CLASSES:
        return _ROWS_SCRIPT
    return None


class InstallResult(NamedTuple):
    """Итог установки: поставленные имена, занятые чужим скриптом, размеры."""

    installed: List[str]
    skipped: List[str]
    sized: Tuple[str, ...] = ()


def script_state(block: Any) -> str:
    """Состояние слота `OnInitScript`: `empty`, `ours`, `foreign`.

    Нечитаемый слот - `foreign`: сомнение решается в пользу «не трогать».
    Перетереть чужой скрипт хуже, чем оставить блок без саморазмера; блок
    всё равно получит высоту по правилу, а причина называется в ответе.
    """
    try:
        value = str(block.get_property("OnInitScript") or "")
    except Exception:                                          # noqa: BLE001
        return "foreign"
    if not value.strip():
        return "empty"
    if MARKER in value:
        return "ours"
    return "foreign"


def rule_height(block: Any, class_name: str) -> int | None:
    """Высота блока по правилу прямо сейчас; `None` - не читается.

    Считается тем же `rules.required_height`, что у подгонки и проверки:
    у порт-блоков - строки `PortNames`, у субмоделей и nport-классов -
    входы и выходы (сетка 8 px). Нечитаемые данные - `None`, и вызывающий
    называет блок, а не молчит: «не знаю» здесь неотличимо от «правило не
    применяется».
    """
    from ..rules import PORT_HEIGHT_CLASSES, required_height
    from .blocks import read_port_counts, read_port_names

    if class_name in PORT_HEIGHT_CLASSES:
        names = read_port_names(block)
        if not names:
            return None
        return required_height(class_name, len(names))
    try:
        in_ports, out_ports = read_port_counts(block)
    except Exception:                                          # noqa: BLE001
        return None
    return required_height(class_name, (in_ports, out_ports))


def _block_name(block: Any) -> str:
    """Имя блока для отчёта; нечитаемое имя - ссылка по id."""
    try:
        name = block.get_name()
    except Exception:                                          # noqa: BLE001
        return f"id={block.id}"
    return name or f"id={block.id}"


def install_body(targets: Iterable[Tuple[Any, str]], token: str) -> str:
    """Тело контура: записать скрипт в пустые слоты, назвать занятые.

    Слот читается **внутри** контура: чтение через COM отдельным вызовом
    стоило бы по COM-вызову на блок, а решение «пуст или занят» принимается
    по тому же значению, что видит запись.
    """
    lines: List[str] = []
    for block, class_name in targets:
        text = script_text(class_name)
        if text is None:
            continue
        name = _block_name(block)
        lines += [
            f"obj = {block.id};",
            'if getpropasstring(obj, "OnInitScript") = "" then begin',
            f"  setprop(obj, \"OnInitScript\", "
            f"{_runtime_literal(_normalize_script(text))});",
            f'  writelnutf8(fid, '
            f'{_runtime_literal(f"{token}_SET {name}")});',
            "end else begin",
            f'  writelnutf8(fid, '
            f'{_runtime_literal(f"{token}_BUSY {name}")});',
            "end;",
        ]
    return "\n".join(lines) + ("\n" if lines else "")


def parse_install(lines: List[str], token: str) -> InstallResult:
    """Разобрать строки контура: `TOKEN_SET имя` и `TOKEN_BUSY имя`."""
    installed: List[str] = []
    skipped: List[str] = []
    for line in lines:
        parts = line.strip().split(" ", 1)
        if len(parts) != 2:
            continue
        if parts[0] == f"{token}_SET":
            installed.append(parts[1])
        elif parts[0] == f"{token}_BUSY":
            skipped.append(parts[1])
    return InstallResult(installed=installed, skipped=skipped)


def install_scripts(page: Any, targets: List[Tuple[Any, str]], *,
                    where: str, project_note: str = "Проект не изменён.",
                    size_now: bool = True) -> InstallResult:
    """Поставить скрипты и (по умолчанию) выставить высоту - одним прогоном.

    Контур стоит ~14 с, поэтому установка скрипта и немедленная правка высоты
    идут вместе: скрипт лечит размер при каждом старте расчёта, а `size_now`
    ставит его сразу, чтобы раскладка до первого старта не работала по
    устаревшему габариту. Чужой `OnInitScript` при этом не трогается, но
    высоту блок получает всё равно - правило размера от владельца слота не
    зависит.

    Проверка - перечитывание слота: молча не применённая запись не должна
    вернуться успехом (тот же канон, что у `set_block_script`).
    """
    selected: List[Tuple[Any, str]] = []
    skipped: List[str] = []
    for block, class_name in targets:
        if script_text(class_name) is None:
            continue
        state = script_state(block)
        if state == "empty":
            selected.append((block, class_name))
        elif state == "foreign":
            skipped.append(_block_name(block))
    targets = selected
    if not targets:
        return InstallResult([], skipped)
    token = "AUTO" + uuid.uuid4().hex[:12]
    body = install_body(targets, token)
    sized: List[str] = []
    if size_now:
        for block, class_name in targets:
            try:
                height = float(block.get_size()[1])
            except Exception:                                  # noqa: BLE001
                continue
            want = rule_height(block, class_name)
            if want is None or float(want) == height:
                continue
            body += ("\n".join(
                size_pair_lines(block.id, "Height", float(want))) + "\n")
            sized.append(f"{_block_name(block)}: {height:g} → {want:g}")
    activate_or_refuse(page, action=where, project_note=project_note)
    outcome, _restored = run_contour(
        body, failed=f"{where}: установить скрипт саморазмера не удалось")
    refuse_contour_failure(
        outcome, failed=where, unsure="установка скрипта не подтверждена",
        project_note=project_note)
    result = parse_install(outcome.lines, token)
    expected = {_block_name(block): _normalize_script(script_text(class_name))
                for block, class_name in targets}
    for block, class_name in targets:
        name = _block_name(block)
        if name not in result.installed:
            continue
        try:
            actual = block.get_property("OnInitScript") or ""
        except Exception:                                      # noqa: BLE001
            continue
        if _normalize_script(actual) != expected[name]:
            raise ToolError(
                f"{where}: скрипт саморазмера записан, но перечитанный текст "
                f"блока '{name}' не совпал с запрошенным - среда запись не "
                f"приняла. Повторите вызов.")
    return InstallResult(result.installed, skipped + result.skipped,
                         tuple(sized))
