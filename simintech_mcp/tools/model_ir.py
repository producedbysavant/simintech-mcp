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
from typing import Any, Dict, Iterable, List, Optional, cast

from fastmcp.exceptions import ToolError
from simintech_api.core.topology import read_topology
from simintech_api.model_ir import (
    ModelIR,
    ModelQueryError,
    build_manifest,
    build_model_ir,
    influence,
    load_default_contracts,
    parse_model_text,
    parse_xprt,
    parse_xprt_file,
    semantic_coverage,
    topology_to_ir,
)
from simintech_api.topology import parse_topology

from .. import experiment_ledger, model_store, runtime, session, sandbox
from ..contracts import check_model_contracts
from ..app import mcp
from .model_text import page_export_text

MAX_MODEL_BYTES = 64 * 1024 * 1024
#: Предел элементов в ответе query_model. Синхронен `_MAX_ITEMS` библиотеки
#: v0.18.1: контракты добавляются после графа, и объединённый список тоже
#: обязан остаться в контекстном бюджете.
MAX_QUERY_ITEMS = 200
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
def _current_project_id() -> int:
    """Id текущего проекта; COM-проверка только в COM-потоке.

    `_models` раньше звал `session.ensure_project()` прямо в workflow-потоке.
    Живой прогон 29.10.2026 показал, что пробный COM-вызов оттуда терял
    сессию ещё до снимка: ошибка «mmain.exe не отвечает» приходила из
    `ensure_project`, а не из `SaveProjectXML`.
    """
    return session.ensure_project().id


@runtime.com_threaded
def _project_xprt_path() -> str:
    """Выгрузить текущий проект в `.xprt`; расчёт не запускается.

    Перед `SaveProjectXML` нужны те же два шага, что у `save_project`:
    чтение состояния и показ формы. Без них живой прогон 29.10.2026 терял
    COM на пустом и на открытом проекте: mmain не отвечал, сессия
    сбрасывалась. Файловый путь `load_model(path=...)` работал.
    """
    project = session.ensure_project()
    root = tempfile.mkdtemp(prefix="simintech-ir-")
    path = Path(root) / "project.xprt"
    try:
        state = project.simulation().get_state()
        if state != 0:
            raise ToolError(
                f"снимок IR отложен: проект не в остановленном состоянии "
                f"(состояние {state}). Остановите расчёт (`stop`) и "
                f"повторите; файл не создан.")
        project.show_form()
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


def read_model(path: str, sources: List[str],
               strict: bool) -> tuple[ModelIR, bool, Optional[int]]:
    """Собрать Model IR из файла или текущего проекта без снимка в кэше.

    Возвращает модель, признак привязки к проекту и id проекта. Нужна и
    `load_model`, который кладёт результат в `model_store`, и инструментам,
    которым модель нужна только для проверки: снимок после мутирующего
    вызова всё равно устарел бы.
    """
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
    project_id = _current_project_id()
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
    return built, True, project_id


def _semantics(model: ModelIR) -> Dict[str, Any]:
    try:
        return semantic_coverage(model)
    except ModelQueryError as exc:
        return {"error": str(exc)}


def _semantics_summary(model: ModelIR) -> Dict[str, Any]:
    data = _semantics(model)
    if "error" in data:
        return data
    keys = (
        "objects_total", "objects_with_behavior",
        "objects_with_approved_behavior", "objects_with_known_ports",
        "objects_unknown", "dependencies_total", "dependencies_known",
        "known_absences_total", "pilot_scope_counts",
    )
    return {key: data[key] for key in keys if key in data}


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
        "semantics": _semantics_summary(model),
        "ledger": experiment_ledger.summary(
            model.meta.structure_hash, model.meta.content_hash),
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

    Возвращает JSON с `model_ref`, хешем, счётчиками, покрытием,
    семантическим покрытием и первыми расхождениями. Полный словарь
    `semantic_coverage` доступен запросом `query_model`. XML, скрипты и
    полные значения свойств не возвращаются. Снимок живёт в памяти; после
    правки проекта старый `model_ref` устареет.
    """
    chosen = ([_source_from_path(path)] if path and sources is None
              else list(sources or ["xprt"]))
    unknown = [item for item in chosen if item not in _SOURCES]
    if unknown:
        raise ToolError(
            f"неизвестные источники {unknown}; допустимы {list(_SOURCES)}")
    if not chosen:
        raise ToolError("sources пуст: нужен хотя бы один источник")
    model, bound, project_id = read_model(path, chosen, strict)
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


def _ledger_item(entry: experiment_ledger.LedgerEntry,
                 current_generation: int) -> Dict[str, Any]:
    """Компактная строка списка: полная запись выдаётся только по id."""
    record = entry.record
    return {
        "id": record.id,
        "goal": entry.goal,
        "status": record.status,
        "target": record.target,
        "changed": record.source,
        "observed": record.observed,
        "recorded_at": record.recorded_at,
        "limitations": len(record.limitations),
        "stale": entry.stale(current_generation),
        "attached_at": entry.attached_at,
    }


def _ledger_payload(snapshot: model_store.Snapshot,
                    args: Dict[str, Any]) -> Dict[str, Any]:
    """Ответ query_model(experiments): список или полная запись по id."""
    structure_hash = snapshot.model.meta.structure_hash
    content_hash = snapshot.model.meta.content_hash
    entries = experiment_ledger.entries_for(structure_hash, content_hash)
    current = model_store.generation()
    wanted = str(args.get("id", "")).strip()
    other_version = False
    if wanted:
        selected = [entry for entry in entries if entry.record.id == wanted]
        if selected:
            entry = selected[0]
        else:
            found = experiment_ledger.entry_for(wanted)
            if found is None:
                raise ToolError(
                    f"experiments: запись {wanted!r} не найдена")
            entry = found
            other_version = (
                found.record.structure_hash,
                found.record.content_hash,
            ) != (structure_hash, content_hash)
        items = [entry.to_dict(current)]
        shown = 1
        total = 1
        negative = 1 if experiment_ledger.is_negative(entry.record) else 0
    else:
        try:
            limit = int(args.get("max_items", 50))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ToolError(
                "experiments: max_items должен быть числом") from exc
        limit = max(0, min(limit, MAX_QUERY_ITEMS))
        items = [_ledger_item(entry, current) for entry in entries[:limit]]
        shown = len(items)
        total = len(entries)
        negative = sum(
            1 for entry in entries
            if experiment_ledger.is_negative(entry.record))
    ledger = experiment_ledger.summary(structure_hash, content_hash)
    notes: List[str] = []
    if other_version:
        notes.append(
            "Запись относится к другой версии модели; к текущему снимку "
            "не применена.")
    if negative:
        notes.append(
            "Отрицательные наблюдения не доказывают отсутствие влияния.")
    elif not wanted and not entries:
        notes.append(
            "Записей нет; это не доказывает отсутствие влияния.")
    other = int(ledger.get("other_content_versions", 0) or 0)
    if other:
        notes.append(
            f"Есть другие версии content_hash: {other}; "
            f"их записи не применены.")
    return {
        "query": "experiments",
        "scope": "ledger",
        "model_hash": structure_hash,
        "content_hash": content_hash,
        "items": items,
        "unknown": [],
        "truncated": not wanted and total > shown,
        "evidence": {
            "records_total": total,
            "records_shown": shown,
            "records_limit": experiment_ledger.MAX_RECORDS_PER_MODEL,
            "negative": negative,
            "other_version": other_version,
            "ledger": ledger,
        },
        "notes": notes,
    }


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
    `coverage`, `influence`, `experiments`, `semantic_coverage`. Для
    `compare_models` в arguments нужен `other_ref`; для `influence` -
    `target` (порт или объект), необязательные `max_depth`, `include`,
    `max_items`; для `experiments` - необязательные `id` (полная запись) и
    `max_items` (компактный список, по умолчанию 50). `influence` по
    умолчанию включает статус `experiment` и применяет записи ledger,
    привязанные к обоим хешам снимка. Ответ несёт evidence, покрытие,
    unknown и truncated; `items` и `unknown` ограничены 200 элементами.
    `check_connections` добавляет контракты и режет объединённый список тем
    же пределом.
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
        payload = result.to_dict()
    elif query == "check_connections":
        result = snapshot.index.query(query, args)
        payload = result.to_dict()
        report = check_model_contracts(snapshot.model)
        payload["evidence"]["contracts"] = report.summary()
        if report.failed:
            payload["evidence"]["status"] = "fail"
        # Пункты контрактов идут первыми: у большой модели графических
        # предупреждений больше предела в 200, и отказы сверки вытеснялись
        # срезом: статус fail приходил без единого отказа в списке.
        merged = list(report.items) + list(payload["items"])
        payload["items"] = merged[:MAX_QUERY_ITEMS]
        payload["truncated"] = (
            bool(payload.get("truncated"))
            or len(merged) > MAX_QUERY_ITEMS)
        graph_total = int(payload["evidence"].get(
            "items_total", len(payload["items"])))
        payload["evidence"]["items_total"] = graph_total + len(report.items)
        payload["evidence"]["contract_items"] = len(report.items)
    elif query == "validate_invariants":
        result = snapshot.index.query(query, args)
        payload = result.to_dict()
        report = check_model_contracts(snapshot.model)
        payload["evidence"]["contracts"] = report.summary()
        for item in payload["items"]:
            if item.get("kind") == "contract_ports_match":
                item["status"] = report.status
                item["detail"] = (
                    f"контракты: проверено {report.evaluated}, "
                    f"ошибок {report.failed}, "
                    f"не проверено {report.not_evaluated}")
    elif query == "influence":
        target = str(args.get("target", "")).strip()
        if not target:
            raise ToolError("influence: нужен target")
        include = args.get("include") or (
            "contract", "not_established", "experiment")
        if not isinstance(include, (list, tuple)):
            raise ToolError("influence: include должен быть списком")
        include_values = tuple(
            str(value) for value in cast(Iterable[object], include))
        records = experiment_ledger.records_for(
            snapshot.model.meta.structure_hash,
            snapshot.model.meta.content_hash)
        try:
            result = influence(
                snapshot.index, load_default_contracts(), target,
                max_depth=int(args.get("max_depth", 64)),
                include=include_values,
                max_items=min(int(args.get("max_items", MAX_QUERY_ITEMS)),
                              MAX_QUERY_ITEMS),
                experiments=records)
        except ModelQueryError as exc:
            raise ToolError(f"influence: {exc}") from exc
        payload = result.to_dict()
        payload["content_hash"] = snapshot.model.meta.content_hash
        payload["evidence"]["ledger"] = experiment_ledger.summary(
            snapshot.model.meta.structure_hash,
            snapshot.model.meta.content_hash)
    elif query == "experiments":
        payload = _ledger_payload(snapshot, args)
    elif query == "semantic_coverage":
        coverage = _semantics(snapshot.model)
        if "error" in coverage:
            raise ToolError(
                f"semantic_coverage: {coverage['error']}")
        coverage_payload: Dict[str, Any] = {
            "query": "semantic_coverage",
            "scope": "full_model",
            "model_hash": snapshot.model.meta.structure_hash,
            "items": [coverage],
            "unknown": [],
            "truncated": False,
            "evidence": {},
        }
        payload = coverage_payload
    else:
        result = snapshot.index.query(query, args)
        payload = result.to_dict()
    payload["model_ref"] = model_ref
    return json.dumps(payload, ensure_ascii=False)
