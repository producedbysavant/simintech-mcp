"""Model IR в MCP: загрузка, паспорт и точные запросы к полной модели.

Ответ `load_model` не содержит XML, скриптов и полных значений свойств.
Полная модель живёт в снимке сервера; агент получает `model_ref`, счётчики,
покрытие и первые расхождения.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp.exceptions import ToolError
from simintech_api.core.topology import read_topology
from simintech_api.model_ir import (
    ModelIR,
    build_manifest,
    build_model_ir,
    parse_model_text,
    parse_xprt,
    parse_xprt_file,
    topology_to_ir,
)
from simintech_api.topology import parse_topology

from .. import model_store, runtime, session, sandbox
from ..app import mcp
from .model_text import page_export_text

MAX_MODEL_BYTES = 64 * 1024 * 1024
_SOURCES = ("xprt", "model_text", "topology")


def _source_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in (".xprt", ".xml"):
        return "xprt"
    if suffix == ".txt":
        return "model_text"
    if suffix in (".topology", ".tsv"):
        return "topology"
    raise ToolError(
        f"файл {Path(path).name}: ожидается .xprt/.xml, .txt или "
        f".topology/.tsv")


@runtime.com_threaded
def _project_xprt_path() -> str:
    """Выгрузить текущий проект в `.xprt`; расчёт не запускается."""
    project = session.ensure_project()
    root = tempfile.mkdtemp(prefix="simintech-ir-")
    path = Path(root) / "project.xprt"
    try:
        project.save_xml(str(path))
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return str(path)


@runtime.com_threaded
def _text_source() -> str:
    """Текст текущего контейнера; контур сдвигает модельное время."""
    text, truncated, _outcome, _path = page_export_text()
    if truncated:
        raise ToolError(
            f"текст модели обрезан пределом {sandbox.MAX_OUTPUT_BYTES} байт; "
            f"сверка по нему неполна")
    return text


@runtime.com_threaded
def _topology_source() -> Any:
    """Протокол топологии текущего контейнера; контур сдвигает время."""
    project = session.ensure_project()
    root = tempfile.mkdtemp(prefix="simintech-topology-")
    path = Path(root) / "topology.txt"
    try:
        return read_topology(project.client, project.id, path)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _models(path: str, sources: List[str],
            strict: bool) -> tuple[ModelIR, bool, Optional[int]]:
    if path:
        inferred = _source_from_path(path)
        if sources and sources != [inferred]:
            raise ToolError(
                f"путь {Path(path).name} задаёт источник {inferred}, "
                f"а sources={sources}")
        data, truncated, error = sandbox.load_result_file(
            path, MAX_MODEL_BYTES, sandbox.MISSING_PROJECT_FILE)
        if error:
            raise ToolError(error)
        if truncated:
            raise ToolError(
                f"файл {Path(path).name} больше {MAX_MODEL_BYTES} байт")
        name = Path(path).name
        if inferred == "xprt":
            model = parse_xprt(data, source_name=name)
            built = build_model_ir(xprt=model, strict=strict)
        elif inferred == "model_text":
            raw_text = data.decode("utf-8-sig", errors="replace")
            built = build_model_ir(
                text=parse_model_text(raw_text, source_name=name),
                strict=strict)
        else:
            raw_text = data.decode("utf-8-sig", errors="replace")
            built = build_model_ir(
                topology=topology_to_ir(parse_topology(raw_text),
                                        source_name=name),
                strict=strict)
        return built, False, None
    project = session.ensure_project()
    xprt: Optional[ModelIR] = None
    text: Optional[ModelIR] = None
    topology: Optional[ModelIR] = None
    if "xprt" in sources:
        path_value = _project_xprt_path()
        try:
            xprt = parse_xprt_file(path_value)
        finally:
            shutil.rmtree(Path(path_value).parent, ignore_errors=True)
    if "model_text" in sources:
        text = parse_model_text(_text_source(), source_name="model-text")
    if "topology" in sources:
        topology = topology_to_ir(_topology_source(), source_name="topology")
    built = build_model_ir(xprt=xprt, text=text, topology=topology,
                           strict=strict)
    return built, True, project.id


def _summary(model: ModelIR, ref: str, sources: List[str]) -> str:
    coverage = model.coverage
    by_code: Dict[str, int] = {}
    for item in model.discrepancies:
        by_code[item.code] = by_code.get(item.code, 0) + 1
    sample = [
        {"code": item.code, "severity": item.severity.value,
         "subject": item.subject}
        for item in model.discrepancies[:5]
    ]
    hints: List[str] = []
    if "model_text" in sources or "topology" in sources:
        hints.append("источники model_text/topology идут контуром и сдвигают "
                     "модельное время")
    if coverage.source_status.value != "ok":
        hints.append("покрытие частичное: см. coverage и discrepancies")
    payload = {
        "model_ref": ref,
        "model_hash": model.meta.structure_hash,
        "content_hash": model.meta.content_hash,
        "ir_schema": model.meta.ir_schema,
        "sources": [item.value for item in model.meta.source_set],
        "source_name": model.meta.source_name,
        "counts": {
            "containers": len(model.containers),
            "objects": len(model.objects),
            "wires": len(model.wires),
            "branches": coverage.branches,
            "ports_real": coverage.ports_real,
            "ports_synthetic": coverage.ports_synthetic,
            "boundaries": len(model.boundaries),
            "memory_links": len(model.memory_links),
        },
        "coverage": {
            "source_status": coverage.source_status.value,
            "unresolved_ends": coverage.unresolved_ends,
            "unconnected_ends": coverage.unconnected_ends,
            "unresolved_branches": coverage.unresolved_branches,
            "unknown_object_types": coverage.unknown_object_types,
            "external_submodels": coverage.external_submodels,
            "skipped": dict(coverage.skipped),
        },
        "discrepancies": {
            "total": len(model.discrepancies),
            "by_code": by_code,
            "sample": sample,
        },
        "hints": hints,
    }
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool(
    title="Загрузить модель в IR",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool
def load_model(path: str = "",
               sources: Optional[List[str]] = None,
               strict: bool = False) -> str:
    """Загрузить проект в Model IR и вернуть компактный паспорт-сводку.

    `path=""` читает текущий проект: `.xprt` снимается SaveProjectXML без
    запуска расчёта; `model_text` и `topology` идут контуром и сдвигают
    модельное время. `path` - файл в каталоге результатов: `.xprt`/`.xml`,
    `.txt` (savemodeltofile) или `.topology`/`.tsv`.

    Возвращает JSON с `model_ref`, хешем, счётчиками, покрытием и первыми
    расхождениями. XML, скрипты и полные значения свойств не возвращаются.
    Снимок живёт в памяти; после правки проекта старый `model_ref` устареет.
    """
    chosen = ([_source_from_path(path)] if path and sources is None
              else list(sources or ["xprt"]))
    unknown = [item for item in chosen if item not in _SOURCES]
    if unknown:
        raise ToolError(
            f"неизвестные источники {unknown}; допустимы {list(_SOURCES)}")
    if not chosen:
        raise ToolError("sources пуст: нужен хотя бы один источник")
    model, bound, project_id = _models(path, chosen, strict)
    ref = model_store.store(model, bound=bound, project_id=project_id)
    return _summary(model, ref, chosen)


@mcp.tool(
    title="Паспорт модели IR",
    annotations={
        "read_only_hint": True,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
    },
)
@runtime.plain_tool
def get_model_manifest(model_ref: str, budget_tokens: int = 3000,
                       focus: str = "") -> str:
    """Компактный Markdown-паспорт снимка в бюджете 2000..5000 токенов.

    Содержит заголовок, покрытие, структуру, классы, сигналы, горячие узлы,
    расхождения и `Omitted`. Полные объекты и XML не выводятся. `focus`
    оставляет выбранный раздел плюс заголовок и покрытие.
    """
    snapshot = model_store.get(model_ref)
    model_store.require_fresh(snapshot)
    return build_manifest(snapshot.model, budget_tokens, focus)


@mcp.tool(
    title="Запрос к модели IR",
    annotations={
        "read_only_hint": True,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
    },
)
@runtime.plain_tool
def query_model(model_ref: str, query: str,
                arguments: Optional[Dict[str, Any]] = None) -> str:
    """Точный запрос к полному графу снимка.

    Запросы: `inspect_node`, `find_objects`, `trace_signal`, `ancestors`,
    `descendants`, `find_path`, `find_cycles`, `check_connections`,
    `compare_models`, `validate_invariants`, `list_discrepancies`,
    `coverage`. Для `compare_models` в arguments нужен `other_ref`.
    Ответ несёт evidence, покрытие, unknown и truncated.
    """
    snapshot = model_store.get(model_ref)
    model_store.require_fresh(snapshot)
    args = dict(arguments or {})
    if query == "compare_models":
        other_ref = str(args.pop("other_ref", ""))
        if not other_ref:
            raise ToolError("compare_models: нужен other_ref")
        other = model_store.get(other_ref)
        model_store.require_fresh(other)
        result = snapshot.index.compare_models(
            other.model, bool(args.get("include_geometry", False)))
    else:
        result = snapshot.index.query(query, args)
    payload = result.to_dict()
    payload["model_ref"] = model_ref
    return json.dumps(payload, ensure_ascii=False)
