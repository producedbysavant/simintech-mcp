"""SystemSpec в MCP: компиляция, сборка, readback IR и вердикт.

Инструмент принимает декларативную спецификацию, проверяет её до COM,
собирает модель существующим путём `build_model` (блоки текстом,
верхнеуровневые провода через connect) и сверяет фактический `.xprt` с
ожидаемой моделью. Частичный успех не превращается в `passed`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, cast

from fastmcp.exceptions import ToolError
from pydantic import ValidationError
from simintech_api.model_ir import (
    CompiledSpec,
    ModelIR,
    SystemSpecError,
    compile_system_spec,
    verify_model,
)

from .. import runtime
from ..app import mcp
from ..contracts import ContractReport, check_model_contracts
from ..specs import BlockSpec, ModelSpec, SystemSpec, WireSpec
from .model_ir import read_model
from .workflows import build_model

_CONNECTED_RE = re.compile(r"Соединено проводов:\s*(\d+)\s*из\s*(\d+)")
_RESERVED_PROPS = frozenset({"type", "points", "portnames", "subsystem"})
_STATUS_ORDER = {"failed": 0, "not_evaluated": 1, "unknown": 2, "passed": 3}


def _list_of(value: object) -> List[Any]:
    """Поле JSON-словаря как список; иное значение даёт пустой список."""
    if not isinstance(value, list):
        return []
    return list(cast(Sequence[Any], value))


def _walk_blocks(level: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    for raw_block in _list_of(level.get("blocks")):
        if not isinstance(raw_block, dict):
            continue
        block = cast(Dict[str, Any], raw_block)
        yield block
        subsystem = block.get("subsystem")
        if isinstance(subsystem, dict):
            yield from _walk_blocks(cast(Dict[str, Any], subsystem))


def _normalized(spec: SystemSpec) -> dict[str, Any]:
    """Словарь для компилятора; `PortNames` из props переносится в поле.

    `PortNames` - свойство записи порт-блока, а не свойство класса в
    реестре. Компилятор проверяет props по реестру, поэтому имя, заданное
    в props, переносится в `portnames` до проверки, а не отвергается.
    """
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    for block in _walk_blocks(raw):
        raw_props = block.get("props")
        if not isinstance(raw_props, dict):
            continue
        props = cast(Dict[str, Any], raw_props)
        for key in ("PortNames", "portnames"):
            if key not in props:
                continue
            if block.get("portnames") is not None:
                raise ToolError(
                    f"блок {block.get('name')!r}: portnames задан и в props, "
                    f"и полем portnames")
            block["portnames"] = props.pop(key)
    return raw


def _validation_text(exc: ValidationError) -> str:
    lines: List[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "$"
        lines.append(f"  {location}: {error.get('msg')}")
    return "\n".join(lines)


def _block_spec(block: Mapping[str, Any], path: str) -> BlockSpec:
    name = str(block.get("name", ""))
    raw_props = block.get("props")
    props = (cast(Dict[str, Any], raw_props)
             if isinstance(raw_props, dict) else {})
    reserved = sorted(set(props) & _RESERVED_PROPS)
    if reserved:
        raise ToolError(
            f"{path} ({name or '?'}): свойства {reserved} задаются полями "
            f"записи, а не в props")
    subsystem = block.get("subsystem")
    return BlockSpec(
        name=name,
        type=str(block.get("class", "")),
        props=dict(props),
        portnames=cast(Any, block.get("portnames")),
        subsystem=(_level_spec(cast(Dict[str, Any], subsystem),
                               path + ".subsystem")
                   if isinstance(subsystem, dict) else None),
    )


def _level_spec(level: Mapping[str, Any], path: str) -> ModelSpec:
    block_items = _list_of(level.get("blocks"))
    wire_items = _list_of(level.get("wires"))
    blocks = [cast(Dict[str, Any], item) for item in block_items
              if isinstance(item, dict)]
    wires = [cast(Dict[str, Any], item) for item in wire_items
             if isinstance(item, dict)]
    return ModelSpec(
        blocks=[
            _block_spec(block, f"{path}.blocks[{pos}]")
            for pos, block in enumerate(blocks)
        ],
        wires=[
            WireSpec(src=str(wire.get("src", "")),
                     dst=str(wire.get("dst", "")))
            for wire in wires
        ],
    )


def _model_spec(raw: Mapping[str, Any]) -> ModelSpec:
    try:
        return _level_spec(raw, "$")
    except ValidationError as exc:
        raise ToolError(
            "SystemSpec не переводится в текст сборки:\n"
            + _validation_text(exc)) from exc


def _compile(raw: Mapping[str, Any]) -> CompiledSpec:
    try:
        return compile_system_spec(raw)
    except SystemSpecError as exc:
        lines = [
            f"  {item.get('path', '$')} [{item.get('code', 'error')}]: "
            f"{item.get('message', '')}"
            for item in exc.problems
        ]
        raise ToolError(
            "SystemSpec не скомпилирована:\n" + "\n".join(lines)) from exc


def _build_counts(text: str) -> tuple[Optional[int], Optional[int]]:
    match = _CONNECTED_RE.search(text)
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _warnings(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines()
            if "WARNING" in line.upper()]


def _enrich_contracts(
        items: List[dict[str, Any]], actual: ModelIR,
        invariants: tuple[dict[str, Any], ...]) -> Optional[ContractReport]:
    """Подкрепить `contract_ports_match` результатом реестра контрактов.

    `verify_model` кладёт инварианты с общим `kind="invariant"`, поэтому
    нужный инвариант находится по id из спецификации, а не по kind.
    """
    ids = {
        str(item.get("id", item.get("kind")))
        for item in invariants
        if item.get("kind") == "contract_ports_match"
    }
    if not ids:
        return None
    report = check_model_contracts(actual)
    detail = (f"контракты: проверено {report.evaluated}, "
              f"ошибок {report.failed}, "
              f"не проверено {report.not_evaluated}")
    for item in items:
        if (item.get("kind") == "invariant"
                and str(item.get("id")) in ids):
            item["status"] = report.status
            item["detail"] = detail
    return report


def _profile_items(profile: Mapping[str, Any]) -> List[dict[str, Any]]:
    items: List[dict[str, Any]] = []
    if profile.get("simulate"):
        items.append({
            "id": "profile:simulate", "kind": "profile",
            "status": "not_evaluated",
            "detail": "расчётное испытание по профилю не выполнялось",
        })
    requested = profile.get("readback", ["xprt"])
    values: List[str] = []
    if isinstance(requested, str):
        values = [requested]
    elif isinstance(requested, (list, tuple)):
        values = [str(item) for item in cast(List[Any], requested)]
    extra = [item for item in values if item != "xprt"]
    if extra:
        items.append({
            "id": "profile:readback", "kind": "profile",
            "status": "not_evaluated",
            "detail": f"readback {extra} не выполняется; сверка идёт по xprt",
        })
    return items


def _counts(items: List[dict[str, Any]]) -> Dict[str, int]:
    result = {"passed": 0, "failed": 0, "not_evaluated": 0, "unknown": 0}
    for item in items:
        status = str(item.get("status"))
        if status in result:
            result[status] += 1
    return result


def _verdict(counts: Mapping[str, int]) -> str:
    if counts.get("failed"):
        return "failed"
    if counts.get("not_evaluated") or counts.get("unknown"):
        return "partial"
    return "passed"


def _limited(items: List[dict[str, Any]],
             limit: int) -> tuple[List[dict[str, Any]], int]:
    ordered = sorted(
        enumerate(items),
        key=lambda pair: (_STATUS_ORDER.get(str(pair[1].get("status")), 9),
                          pair[0]))
    return [item for _pos, item in ordered[:limit]], len(items)


def _render(compiled: CompiledSpec, actual: ModelIR, build_log: str,
            items: List[dict[str, Any]], counts: Mapping[str, int],
            contracts: Optional[ContractReport], limit: int,
            expected_wires: int) -> str:
    verdict = _verdict(counts)
    connected, expected = _build_counts(build_log)
    warnings = _warnings(build_log)
    if connected is None:
        build_line = (f"Сборка: проводов в спецификации {expected_wires}, "
                      f"предупреждений {len(warnings)}")
    else:
        build_line = (f"Сборка: провода {connected} из {expected}, "
                      f"предупреждений {len(warnings)}")
    coverage = actual.coverage
    lines = [
        f"Сборка и проверка модели: {verdict}",
        f"Спецификация: {compiled.name}",
        f"Вердикт: {verdict}",
        build_line,
        f"Модель: hash={actual.meta.structure_hash}, "
        f"контейнеров {len(actual.containers)}, "
        f"объектов {len(actual.objects)}, проводов {len(actual.wires)}",
        f"Покрытие: {coverage.source_status.value}, "
        f"неразрешённых концов {coverage.unresolved_ends}, "
        f"несвязанных концов {coverage.unconnected_ends}",
        f"Проверки: passed {counts.get('passed', 0)}, "
        f"failed {counts.get('failed', 0)}, "
        f"not_evaluated {counts.get('not_evaluated', 0)}, "
        f"unknown {counts.get('unknown', 0)}",
    ]
    if contracts is not None:
        lines.append(
            f"Контракты: проверено {contracts.evaluated}, "
            f"ошибок {contracts.failed}, "
            f"не проверено {contracts.not_evaluated}")
    shown, total = _limited(items, limit)
    if shown:
        lines.append(
            f"Пункты проверки (всего {total}, показано {len(shown)}):")
        for item in shown:
            ident = str(item.get("id") or item.get("kind") or "?")
            kind = str(item.get("kind", ""))
            lines.append(
                f"  [{item.get('status')}] {ident} ({kind}): "
                f"{item.get('detail', '')}")
    else:
        lines.append("Расхождений нет.")
    if warnings:
        lines.append("Предупреждения сборки:")
        lines.extend(f"  {warning}" for warning in warnings)
    hints: List[str] = []
    if counts.get("not_evaluated") or counts.get("unknown"):
        hints.append(
            "not_evaluated и unknown означают, что проверка не выполнялась; "
            "partial не равен passed.")
    if compiled.assertions or compiled.profile.get("simulate"):
        hints.append("assertions и расчётные испытания не исполнялись.")
    if warnings:
        hints.append(
            "часть проводов не создана; расхождения показывают фактические "
            "связи.")
    hints.append(
        "снимок IR после сборки не выдаётся: сборка помечает проект "
        "изменённым; для запросов вызовите load_model.")
    lines.append("Подсказки:")
    lines.extend(f"  - {hint}" for hint in hints)
    return "\n".join(lines)


@mcp.tool(
    title="Собрать и проверить модель",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    },
)
@runtime.workflow_tool(mutates_project=True)
def build_and_verify(spec: SystemSpec,
                     project_hint: str = "model",
                     end_time: Optional[float] = None,
                     new_project: bool = True,
                     layout: bool = True,
                     fit_sizes: bool = True,
                     max_items: int = 50) -> str:
    """Собрать модель по SystemSpec и проверить фактический IR.

    Спецификация компилируется до обращения к COM: неизвестные классы и
    свойства, повтор имён, самопетли и неверные концы проводов собираются
    все за один вызов. Затем модель строится как ModelSpec: блоки и
    субмодели текстом, верхнеуровневые провода через connect.

    Readback идёт снимком `.xprt` без запуска расчёта. Вердикт: `failed`
    при любом расхождении, `partial` при неизвестных или непроверенных
    пунктах, `passed` только когда все проверки прошли. `assertions` и
    расчётные испытания без simulate остаются `not_evaluated`.

    `new_project=True` (по умолчанию) строит чистую модель; при
    несохранённых правках вызов отказывает, пока их не сохранить. Снимок
    `model_ref` не возвращается: сборка помечает проект изменённым, и
    ссылка устарела бы сразу; для запросов вызовите `load_model`.
    """
    raw = _normalized(spec)
    compiled = _compile(raw)
    model_spec = _model_spec(raw)
    try:
        build_log = build_model(
            spec=model_spec, project_hint=project_hint, end_time=end_time,
            new_project=new_project, layout=layout, fit_sizes=fit_sizes,
            check=False, screenshot=False)
    except ToolError as exc:
        raise ToolError(f"сборка не выполнена: {exc}") from exc
    try:
        actual, _bound, _project_id = read_model("", ["xprt"], False)
    except Exception as exc:                                  # noqa: BLE001
        raise ToolError(
            f"модель собрана, но снимок .xprt не прочитан: {exc}") from exc
    report = verify_model(actual, compiled)
    items = [dict(item) for item in report.items]
    contracts = _enrich_contracts(items, actual, compiled.invariants)
    items.extend(_profile_items(compiled.profile))
    counts = _counts(items)
    return _render(compiled, actual, build_log, items, counts, contracts,
                   max(1, min(int(max_items), 200)), len(model_spec.wires))
