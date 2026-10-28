"""SystemSpec в MCP: компиляция, сборка, readback IR и вердикт.

Инструмент принимает декларативную спецификацию, проверяет её до COM,
собирает модель существующим путём `build_model` (блоки текстом,
верхнеуровневые провода через connect) и сверяет фактический `.xprt` с
ожидаемой моделью. Частичный успех не превращается в `passed`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, cast

from fastmcp.exceptions import ToolError
from pydantic import ValidationError
from simintech_api.model_ir import (
    BlockContractRegistry,
    CompiledSpec,
    ExpectedLevel,
    ExpectedObject,
    ModelIR,
    SystemSpecError,
    compile_system_spec,
    load_default_contracts,
    verify_model,
)

from .. import runtime, session
from ..app import mcp
from ..contracts import ContractReport, check_model_contracts
from ..specs import BlockSpec, ModelSpec, SystemSpec, WireSpec
from .model_ir import read_model
from .workflows import build_model

_CONNECTED_RE = re.compile(r"Соединено проводов:\s*(\d+)\s*из\s*(\d+)")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_PROPS = frozenset({"type", "points", "subsystem"})
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


def _normalized(spec: SystemSpec,
                registry: BlockContractRegistry) -> dict[str, Any]:
    """Словарь для компилятора; `PortNames` переносится только у portnames.

    У классов с портовой моделью `portnames` свойства `PortNames` нет в
    реестре: порты задаются значением записи, поэтому оно переносится в
    поле `portnames` до проверки свойств. У остальных классов `PortNames`
    остаётся в props: неизвестное свойство должен отвергнуть компилятор, а
    не обойти проверку.
    """
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    for block in _walk_blocks(raw):
        contract = registry.get(str(block.get("class", "")))
        if contract is None or contract.ports.model != "portnames":
            continue
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
    props = dict(cast(Dict[str, Any], raw_props)
                 if isinstance(raw_props, dict) else {})
    portnames = block.get("portnames")
    for key in ("PortNames", "portnames"):
        if key not in props:
            continue
        if portnames is not None:
            raise ToolError(
                f"{path} ({name or '?'}): portnames задан и в props, "
                f"и полем portnames")
        portnames = props.pop(key)
    reserved = sorted(set(props) & _RESERVED_PROPS)
    if reserved:
        raise ToolError(
            f"{path} ({name or '?'}): свойства {reserved} задаются полями "
            f"записи, а не в props")
    subsystem = block.get("subsystem")
    return BlockSpec(
        name=name,
        type=str(block.get("class", "")),
        props=props,
        portnames=portnames,
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


def _walk_with_paths(level: Mapping[str, Any],
                     path: str) -> Iterator[tuple[dict[str, Any], str]]:
    for pos, raw_block in enumerate(_list_of(level.get("blocks"))):
        if not isinstance(raw_block, Mapping):
            continue
        block = cast(Dict[str, Any], raw_block)
        block_path = f"{path}.blocks[{pos}]"
        yield block, block_path
        subsystem = block.get("subsystem")
        if isinstance(subsystem, Mapping):
            yield from _walk_with_paths(
                cast(Dict[str, Any], subsystem), block_path + ".subsystem")


def _spec_problems(raw: Mapping[str, Any]) -> List[dict[str, str]]:
    """Ошибки, которых не видит `compile_system_spec`, но которые ломают сборку.

    Имена блоков должны подходить для текста createmodel; `max` инварианта
    обязан быть целым (иначе `validate_invariants` падает уже после
    сборки); id инвариантов должны быть уникальны, иначе обогащение
    контрактами затирает чужой статус по совпавшему id.
    """
    problems: List[dict[str, str]] = []
    for block, path in _walk_with_paths(raw, "$"):
        name = str(block.get("name", ""))
        if name and _NAME_RE.fullmatch(name) is None:
            problems.append({
                "path": path + ".name", "code": "bad_name",
                "message": (f"имя {name!r}: нужна латиница, цифры и "
                            f"подчёркивание, первый символ не цифра"),
            })
    seen: Dict[str, int] = {}
    for pos, raw_invariant in enumerate(_list_of(raw.get("invariants"))):
        if not isinstance(raw_invariant, Mapping):
            continue
        invariant = cast(Mapping[str, Any], raw_invariant)
        ident = str(invariant.get("id", invariant.get("kind", "")))
        if ident in seen:
            problems.append({
                "path": f"$.invariants[{pos}].id",
                "code": "duplicate_invariant_id",
                "message": f"id {ident!r} уже был в invariants[{seen[ident]}]",
            })
        else:
            seen[ident] = pos
        kind = invariant.get("kind")
        if kind not in ("max_fanout", "container_depth"):
            continue
        default = 1 if kind == "max_fanout" else 8
        value = invariant.get("max", default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append({
                "path": f"$.invariants[{pos}].max",
                "code": "bad_max",
                "message": f"max={value!r}: ожидается целое >= 1",
            })
    return problems


def _spec_error(problems: List[dict[str, str]]) -> ToolError:
    lines = [
        f"  {item.get('path', '$')} [{item.get('code', 'error')}]: "
        f"{item.get('message', '')}"
        for item in problems
    ]
    return ToolError("SystemSpec не скомпилирована:\n" + "\n".join(lines))


def _compile(raw: Mapping[str, Any],
             registry: BlockContractRegistry) -> CompiledSpec:
    problems = _spec_problems(raw)
    try:
        compiled = compile_system_spec(raw, registry)
    except SystemSpecError as exc:
        raise _spec_error(list(exc.problems) + problems) from exc
    if problems:
        raise _spec_error(problems)
    return compiled


def _hinted_names(level: Mapping[str, Any]) -> set[str]:
    """Имена блоков уровня, у которых подсказки портов заданы явно."""
    result: set[str] = set()
    for raw_block in _list_of(level.get("blocks")):
        if not isinstance(raw_block, Mapping):
            continue
        block = cast(Mapping[str, Any], raw_block)
        hints = block.get("ports")
        if not isinstance(hints, Mapping):
            continue
        ports = cast(Mapping[str, Any], hints)
        if ports.get("inputs") or ports.get("outputs"):
            result.add(str(block.get("name", "")).lower())
    return result


def _relax_level(level: ExpectedLevel, raw: Mapping[str, Any],
                 registry: BlockContractRegistry) -> ExpectedLevel:
    """Снять точные порты у portnames-классов без подсказок.

    У таких классов порты задаются значением `PortNames` экземпляра, а
    образец реестра - лишь наблюдение. Сверка по образцу даёт ложный
    `failed` на корректной модели и ложный `passed` на блоке без порта;
    вместо этого проверка честно остаётся `not_evaluated`.
    """
    hinted = _hinted_names(raw)
    raw_by_name: Dict[str, Mapping[str, Any]] = {}
    for raw_block in _list_of(raw.get("blocks")):
        if isinstance(raw_block, Mapping):
            block = cast(Mapping[str, Any], raw_block)
            raw_by_name[str(block.get("name", "")).lower()] = block
    objects: List[ExpectedObject] = []
    for obj in level.objects:
        parent = raw_by_name.get(obj.name.lower())
        subsystem = parent.get("subsystem") if parent is not None else None
        children = tuple(
            _relax_level(
                child,
                cast(Mapping[str, Any], subsystem)
                if isinstance(subsystem, Mapping) else {},
                registry)
            for child in obj.children)
        contract = registry.get(obj.class_name)
        relax = (contract is not None
                 and contract.ports.model == "portnames"
                 and obj.name.lower() not in hinted)
        objects.append(replace(
            obj,
            ports_known=False if relax else obj.ports_known,
            children=children))
    return replace(level, objects=tuple(objects))


def _relax_portnames(compiled: CompiledSpec, raw: Mapping[str, Any],
                     registry: BlockContractRegistry) -> CompiledSpec:
    level = _relax_level(compiled.expected.level, raw, registry)
    return replace(compiled, expected=replace(compiled.expected, level=level))


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
        invariants: tuple[dict[str, Any], ...],
        registry: BlockContractRegistry) -> Optional[ContractReport]:
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
    report = check_model_contracts(actual, registry)
    detail = (f"контракты: проверено {report.evaluated}, "
              f"ошибок {report.failed}, "
              f"не проверено {report.not_evaluated}")
    if report.by_reason:
        reasons = ", ".join(
            f"{key}: {value}" for key, value in sorted(report.by_reason.items()))
        detail += f"; причины: {reasons}"
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
    if not values or "xprt" not in values:
        items.append({
            "id": "profile:readback", "kind": "profile",
            "status": "not_evaluated",
            "detail": f"readback {values} не включает xprt; "
                      f"сверка всё равно идёт по xprt",
        })
    else:
        extra = [item for item in values if item != "xprt"]
        if extra:
            items.append({
                "id": "profile:readback", "kind": "profile",
                "status": "not_evaluated",
                "detail": f"readback {extra} не выполняется; "
                          f"сверка идёт по xprt",
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
    if any(str(item.get("kind")) == "port_unknown" for item in items):
        hints.append(
            "port_unknown: у portnames-классов укажите ports (inputs/outputs); "
            "иначе портовая схема класса не проверяется.")
    if counts.get("not_evaluated") or counts.get("unknown"):
        hints.append(
            "not_evaluated и unknown означают, что проверка не выполнялась; "
            "partial не равен passed.")
    if compiled.assertions or compiled.profile.get("simulate"):
        hints.append("assertions и расчётные испытания не исполнялись.")
    if (connected is not None and expected is not None
            and connected < expected):
        hints.append(
            "часть проводов не создана; расхождения показывают фактические "
            "связи.")
    elif warnings:
        hints.append(
            "сборка завершилась с предупреждениями; проверка показывает "
            "фактические связи.")
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
    registry = load_default_contracts()
    raw = _normalized(spec, registry)
    compiled = _compile(raw, registry)
    compiled = _relax_portnames(compiled, raw, registry)
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
    try:
        report = verify_model(actual, compiled)
        items = [dict(item) for item in report.items]
        contracts = _enrich_contracts(
            items, actual, compiled.invariants, registry)
        items.extend(_profile_items(compiled.profile))
        counts = _counts(items)
        return _render(compiled, actual, build_log, items, counts, contracts,
                       max(1, min(int(max_items), 200)),
                       len(model_spec.wires))
    except ToolError:
        raise
    except Exception as exc:                                  # noqa: BLE001
        note = session.mutation_note()
        raise ToolError(
            f"модель собрана, но проверка не выполнена: {exc}{note}"
        ) from exc
