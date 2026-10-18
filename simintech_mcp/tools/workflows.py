"""Композиты: задачи агента, а не отдельные COM-вызовы.

Каждый композит идёт под `runtime.workflow_tool`: сам он в COM-поток не
встаёт, а вложенные инструменты занимают его по очереди. Поэтому workflow
не ждёт собственный future и не упирается в `COM_CALL_TIMEOUT`.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Dict, List, Literal, Optional

from fastmcp.exceptions import ToolError

from .. import __version__, runtime, sandbox, session, skills, surface
from ..app import mcp
from ..specs import ModelSpec, render_model_text
from .blocks import get_block_params, list_blocks
from .check_model import audit_routing, check_model_layout
from .files import read_output_file, summarize_output_file
from .layout import fit_view, layout_place
from .model_text import import_model_text
from .project import create_project, set_calc_time
from .project import status as project_status
from .screenshot import save_screenshot
from .simulation import get_time, run, step
from .wires import connect, list_wires


_ENDPOINT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*):(?P<kind>out|in):(?P<index>\d+)$")
_BLOCK_LINE_RE = re.compile(
    r"^\s+(?P<name>\S+)\s+\[(?P<cls>[^\]]+)\]\s+\(id=(?P<id>\d+)\)\s*$")

#: Сколько блоков читать с параметрами за один `inspect_model`.
MAX_PARAM_BLOCKS = 50


def _block_names(text: str) -> Dict[str, str]:
    """Карта нижний регистр имени -> фактическое имя из list_blocks."""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        match = _BLOCK_LINE_RE.match(line)
        if match:
            result[match.group("name").lower()] = match.group("name")
    return result


def _parse_endpoint(value: str) -> tuple[str, int]:
    match = _ENDPOINT_RE.match(value)
    if match is None:
        raise ToolError(
            f"конец провода {value!r} не в формате имя:out:N или имя:in:N")
    return match.group("name"), int(match.group("index"))


def _import_notes(result: str) -> List[str]:
    """Результат импорта и пояснение к вердикту пробного контура.

    Контур импорта может не продвинуть модельное время и назвать модель
    не считающей, хотя обычный run считает: это состояние пробы, а не
    приговор модели. Агент не должен считать сборку неудачной.
    """
    notes = [result]
    if "модель не считает" in result:
        notes.append(
            "Примечание: вердикт «модель не считает» относится к пробному "
            "контуру импорта; расчёт проверяется вызовом simulate.")
    return notes


def _project_or_create(project_hint: str, end_time: Optional[float],
                       new_project: bool) -> str:
    if new_project or session.current_project() is None:
        return create_project(project_hint=project_hint, end_time=end_time)
    if end_time is not None:
        return set_calc_time(end_time)
    return "используется текущий проект"


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "не установлен"


def _api_version() -> str:
    try:
        import simintech_api
        return str(getattr(simintech_api, "__version__", "unknown"))
    except Exception:                                         # noqa: BLE001
        return "unknown"


@mcp.tool(
    title="Диагностика",
    annotations={
        "read_only_hint": True,
        "idempotent_hint": True,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool
def doctor() -> str:
    """Проверить окружение SimInTech и назвать следующие шаги."""
    rows: List[tuple[str, str, str]] = []

    def add(name: str, state: str, detail: str) -> None:
        rows.append((name, state, detail))

    add("python", "OK" if sys.version_info >= (3, 11) else "FAIL",
        platform.python_version())
    add("platform", "OK" if sys.platform == "win32" else "FAIL",
        sys.platform)

    try:
        status = project_status()
        add("com", "OK" if "подключён" in status.lower() else "FAIL", status)
    except ToolError as exc:
        add("com", "FAIL", str(exc))

    root = sandbox.safe_output_root()
    add("output_dir", "OK" if os.path.isdir(root) else "FAIL", root)
    add("output_writable", "OK" if os.access(root, os.W_OK) else "FAIL", root)

    skills_root = skills.skills_root()
    add("skills_dir", "OK" if skills_root else "WARN",
        skills_root or f"не найден; задайте {skills.SKILLS_DIR_ENV}")

    project = session.current_project()
    add("project", "OK" if project is not None else "WARN",
        "проект открыт" if project is not None else "проекта нет")
    pack = session.current_pack()
    add("pack", "OK" if pack is not None else "WARN",
        "пакет открыт" if pack is not None else "пакета нет")

    add("fastmcp", "OK", _package_version("fastmcp"))
    add("simintech_api", "OK", _api_version())
    add("simintech_mcp", "OK", __version__)
    add("discovery", "OK", surface.discovery_mode())

    failed = [row[0] for row in rows if row[1] == "FAIL"]
    warned = [row[0] for row in rows if row[1] == "WARN"]
    lines = ["Диагностика simintech-mcp:", ""]
    for name, state, detail in rows:
        lines.append(f"  [{state}] {name}: {detail}")
    lines.append("")
    if failed:
        lines.append("FAIL: " + ", ".join(failed))
        lines.append("Следующий шаг: прочитайте detail и выполните "
                     "рекомендацию; для COM это disconnect и повтор.")
    elif warned:
        lines.append("WARN: " + ", ".join(warned))
        lines.append("Сервер работоспособен; предупреждения не блокируют "
                     "сборку модели.")
    else:
        lines.append("Все проверки пройдены.")
    return "\n".join(lines)


@mcp.tool(
    title="Собрать модель",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool(mutates_project=True)
def build_model(spec: Optional[ModelSpec] = None,
                model_text: str = "",
                project_hint: str = "model",
                end_time: Optional[float] = None,
                new_project: bool = True,
                layout: bool = True,
                check: bool = False,
                screenshot: bool = False) -> str:
    """Собрать модель из ModelSpec или декларативного текста.

    Для spec верхнеуровневые провода создаются connect по фактическим
    портам; провода внутри субмоделей идут текстом и требуют снимка.
    Для model_text провода импортируются текстом: это быстрый, но менее
    надёжный путь, о чём ответ предупреждает.
    """
    if (spec is None) == (not model_text.strip()):
        raise ToolError(
            "нужен ровно один источник: spec или model_text")

    notes: List[str] = []
    notes.append(_project_or_create(project_hint, end_time, new_project))

    if spec is not None:
        blocks_text = render_model_text(spec, include_wires=False)
        notes.extend(_import_notes(import_model_text(blocks_text)))
        try:
            listing = list_blocks(limit=0)
        except ToolError as exc:
            listing = ""
            notes.append(f"WARNING: список блоков не прочитан: {exc}")
        actual = _block_names(listing)
        connected = 0
        for wire in spec.wires:
            try:
                src_name, out_index = _parse_endpoint(wire.src)
                dst_name, in_index = _parse_endpoint(wire.dst)
                src = actual.get(src_name.lower())
                dst = actual.get(dst_name.lower())
                if src is None or dst is None:
                    raise ToolError(
                        f"после импорта не найден блок {src_name!r} или "
                        f"{dst_name!r}")
                connect(src, dst, out_index=out_index, in_index=in_index)
                connected += 1
            except ToolError as exc:
                notes.append(
                    f"WARNING: провод {wire.src} -> {wire.dst}: {exc}")
        notes.append(
            f"Соединено проводов: {connected} из {len(spec.wires)}")
    else:
        notes.extend(_import_notes(import_model_text(model_text)))
        notes.append(
            "WARNING: провода из текста могут получить неверную геометрию; "
            "для надёжной сборки используйте spec.")

    if layout:
        try:
            notes.append(layout_place())
        except ToolError as exc:
            notes.append(f"WARNING: раскладка не выполнена: {exc}")
    if check:
        try:
            notes.append(check_model_layout())
        except ToolError as exc:
            notes.append(f"WARNING: проверка не выполнена: {exc}")
    if screenshot:
        try:
            notes.append(save_screenshot(format="png", fit=True))
        except ToolError as exc:
            notes.append(f"WARNING: снимок не сделан: {exc}")

    return "Модель собрана.\n\n" + "\n\n".join(notes)


@mcp.tool(
    title="Обзор модели",
    annotations={
        "read_only_hint": True,
        "idempotent_hint": True,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool
def inspect_model(include_params: bool = False, limit: int = 200) -> str:
    """Показать блоки, линии и, при необходимости, параметры."""
    listing = list_blocks(limit=limit)
    wire_text = list_wires()
    wire_summary = wire_text.split("Идентификаторы:", 1)[0].strip()
    parts = [listing, wire_summary]
    if include_params:
        names = list(_block_names(listing).values())[:MAX_PARAM_BLOCKS]
        if len(names) == MAX_PARAM_BLOCKS:
            parts.append(
                f"Параметры показаны для первых {MAX_PARAM_BLOCKS} блоков; "
                f"остальные читайте get_block_params по имени.")
        for name in names:
            try:
                parts.append(get_block_params(name))
            except ToolError as exc:
                parts.append(f"WARNING: параметры {name}: {exc}")
    return "\n\n".join(parts)


@mcp.tool(
    title="Расчёт и результат",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool(mutates_project=True)
def simulate(to_time: Optional[float] = None,
             end_time: Optional[float] = None,
             step_count: Optional[int] = None,
             output_path: str = "",
             summary: bool = True) -> str:
    """Задать время, запустить расчёт или шаги, прочитать результат."""
    parts: List[str] = []
    if end_time is not None:
        parts.append(set_calc_time(end_time))
    if step_count is not None:
        parts.append(step(step_count))
    else:
        parts.append(run(to_time=to_time))
    parts.append(f"Модельное время: {get_time():g} с")
    if output_path:
        parts.append(read_output_file(output_path))
        if summary:
            parts.append(summarize_output_file(output_path))
    return "\n\n".join(parts)


@mcp.tool(
    title="Оформление модели",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool(mutates_project=True)
def format_model(
        mode: Literal["place", "normalize", "fit", "screenshot"] = "place",
        check: bool = False,
        screenshot: bool = False,
        hires: bool = False,
        zoom: Optional[float] = None) -> str:
    """Разложить, нормализовать, вписать кадр, проверить и снять снимок."""
    if hires and zoom is not None:
        raise ToolError("hires и zoom не сочетаются")
    parts: List[str] = []
    if mode == "place":
        parts.append(layout_place())
    elif mode == "normalize":
        parts.append(layout_place(normalize_only=True))
    elif mode == "fit":
        parts.append(fit_view())
    else:
        screenshot = True
    if check:
        try:
            checked = check_model_layout()
            parts.append(checked)
            if "модель не считает" in checked:
                parts.append(
                    "Примечание: «модель не считает» относится к пробному "
                    "контуру проверки; расчёт проверяется вызовом simulate.")
        except ToolError as exc:
            parts.append(f"WARNING: проверка оформления не выполнена: {exc}")
        try:
            parts.append(audit_routing())
        except ToolError as exc:
            parts.append(f"WARNING: аудит маршрутов не выполнен: {exc}")
    if screenshot:
        parts.append(save_screenshot(
            format="png", fit=True, hires=hires, zoom=zoom))
    return "\n\n".join(parts)
