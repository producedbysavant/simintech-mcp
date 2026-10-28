"""Контрактные проверки Model IR: порты класса против реестра.

Проверка не подменяет `unknown` на `passed`: неизмеренный контракт даёт
`not_evaluated` с классом и причиной. Для `fixed` проверяются счётчики,
направления и имена; для `nport` и `portnames` — счётчики направлений.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from simintech_api.model_ir import (
    BlockContractRegistry,
    ModelIR,
    ModelObject,
    ModelQueryError,
    ObjectKind,
    PortDirection,
    load_default_contracts,
)


@dataclass(frozen=True, slots=True)
class ContractReport:
    evaluated: int = 0
    passed: int = 0
    failed: int = 0
    not_evaluated: int = 0
    items: tuple[dict[str, Any], ...] = ()
    by_reason: Mapping[str, int] = field(
        default_factory=lambda: dict[str, int]())

    @property
    def status(self) -> str:
        if self.failed:
            return "failed"
        if self.not_evaluated:
            return "not_evaluated"
        return "passed"

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evaluated": self.evaluated,
            "passed": self.passed,
            "failed": self.failed,
            "not_evaluated": self.not_evaluated,
            "by_reason": dict(self.by_reason),
            "samples": list(self.items[:10]),
        }


def _props(obj: ModelObject) -> dict[str, str]:
    return {name: prop.value for name, prop in obj.props.items()}


def _directions(obj: ModelObject) -> tuple[list[Any], list[Any]]:
    return (
        [port for port in obj.ports
         if port.direction is PortDirection.IN],
        [port for port in obj.ports
         if port.direction is PortDirection.OUT],
    )


def _item(code: str, status: str, obj: ModelObject,
          detail: str, reason: str = "") -> dict[str, Any]:
    item = {
        "code": code,
        "status": status,
        "subject": obj.address,
        "class": obj.class_name,
        "detail": detail,
    }
    if reason:
        item["reason"] = reason
    return item


def _compare(obj: ModelObject, registry: BlockContractRegistry) -> dict[str, Any]:
    contract = registry.get(obj.class_name)
    if contract is None:
        return _item(
            "contract_unknown", "not_evaluated", obj,
            "класса нет в реестре контрактов", "class_not_in_registry")
    try:
        expected = registry.ports_for(obj.class_name, _props(obj))
    except ModelQueryError as exc:
        return _item(
            "contract_unknown", "not_evaluated", obj,
            str(exc), "invalid_contract_property")
    if expected.model in ("unknown", "submodel"):
        reason = expected.reason or ("внешние порты задаются экземпляром"
                                     if expected.model == "submodel"
                                     else "портовая схема не измерена")
        return _item(
            "contract_unknown", "not_evaluated", obj,
            reason, expected.reason or expected.model)
    if expected.model == "portnames":
        return _item(
            "contract_ports_unknown", "not_evaluated", obj,
            "порты задаются PortNames; образец реестра не эталон",
            "portnames_instance_defined")
    actual_in, actual_out = _directions(obj)
    expected_in = list(expected.inputs)
    expected_out = list(expected.outputs)
    if (len(actual_in) != len(expected_in)
            or len(actual_out) != len(expected_out)):
        return _item(
            "contract_ports_mismatch", "failed", obj,
            f"входов {len(actual_in)} против {len(expected_in)}, "
            f"выходов {len(actual_out)} против {len(expected_out)}")
    if expected.model == "fixed":
        for actual, wanted in zip(actual_in + actual_out,
                                  expected_in + expected_out):
            if (wanted.name and actual.name
                    and actual.name != wanted.name):
                return _item(
                    "contract_ports_mismatch", "failed", obj,
                    f"порт {actual.index}: имя {actual.name!r} против "
                    f"{wanted.name!r}")
            if actual.direction is not wanted.direction:
                return _item(
                    "contract_ports_mismatch", "failed", obj,
                    f"порт {actual.index}: направление "
                    f"{actual.direction.value} против {wanted.direction.value}")
    return _item("contract_ports_match", "passed", obj,
                 f"портов: {len(actual_in)} входов, {len(actual_out)} выходов")


def check_model_contracts(
        model: ModelIR,
        registry: Optional[BlockContractRegistry] = None) -> ContractReport:
    """Проверить порты объектов модели против реестра контрактов."""
    registry = registry or load_default_contracts()
    passed = failed = not_evaluated = 0
    reasons: Dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for obj in model.objects:
        if obj.kind in (ObjectKind.ANNOTATION, ObjectKind.DECORATION):
            continue
        item = _compare(obj, registry)
        status = item["status"]
        if status == "passed":
            passed += 1
        elif status == "failed":
            failed += 1
            items.append(item)
        else:
            not_evaluated += 1
            reason = str(item.get("reason", "unknown"))
            reasons[reason] = reasons.get(reason, 0) + 1
            if len(items) < 10:
                items.append(item)
    return ContractReport(
        evaluated=passed + failed, passed=passed, failed=failed,
        not_evaluated=not_evaluated, items=tuple(items),
        by_reason=reasons)
