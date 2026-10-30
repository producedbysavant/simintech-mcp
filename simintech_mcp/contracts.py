"""Контрактные проверки Model IR: порты класса против реестра.

Проверка не подменяет `unknown` на `passed`: неизмеренный контракт даёт
`not_evaluated` с классом и причиной. Для `fixed` и `weights` проверяются
счётчики, направления и имена (у `name_status: instance_defined` имена
пропускаются: их задаёт экземпляр); для `nport` и `portnames` — счётчики
направлений. Субмодель сверяется не с реестром, а со своей страницей:
внешние порты против внутренних порт-блоков по именам; внешняя субмодель
без страницы остаётся `not_evaluated`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from simintech_api.model_ir import (
    BlockContractRegistry,
    Container,
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
        # Отказы идут первыми: `items` собирается в порядке объектов, и при
        # десяти «не проверено» отказ мог не попасть в срез: живой прогон
        # 30.10.2026 показал статус failed без единого отказа в samples.
        failed = [item for item in self.items
                  if item.get("status") == "failed"]
        others = [item for item in self.items
                  if item.get("status") != "failed"]
        return {
            "status": self.status,
            "evaluated": self.evaluated,
            "passed": self.passed,
            "failed": self.failed,
            "not_evaluated": self.not_evaluated,
            "by_reason": dict(self.by_reason),
            "samples": list((failed + others)[:10]),
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


#: Классы порт-блоков: их порт назван значением PortNames.
_PORT_BLOCK_CLASSES = ("Порт входа", "Порт выхода")


def _portnames(obj: ModelObject) -> "list[str]":
    """Строки PortNames экземпляра: имена сигналов (порт-блок, ячейка памяти)."""
    prop = obj.visual_props.get("PortNames") or obj.props.get("PortNames")
    text = (prop.value if prop is not None else "").replace("\r", "")
    return [line.strip() for line in text.split("\n") if line.strip()]


def _compare_portnames(obj: ModelObject) -> dict[str, Any]:
    """Порт-блок: его единственный порт назван значением PortNames.

    Живой замер 30.10.2026: у «Порта входа» и «Порта выхода» одна строка
    PortNames и один порт с этим именем, направление задаёт класс. У ячеек
    памяти (`В память`, `Из памяти`) то же свойство задаёт имя переменной,
    а не порт, поэтому для них образец реестра по-прежнему не эталон.
    """
    if obj.class_name not in _PORT_BLOCK_CLASSES:
        return _item(
            "contract_ports_unknown", "not_evaluated", obj,
            "порты задаются PortNames; образец реестра не эталон",
            "portnames_instance_defined")
    names = _portnames(obj)
    if not names:
        return _item(
            "contract_ports_unknown", "not_evaluated", obj,
            "PortNames пуст: имя порта не задано", "portnames_missing")
    # Направление порта задаёт не класс, а взгляд контейнера: внутри
    # субмодели «Порт входа» отдаёт сигнал наружу (порт `out`), «Порт
    # выхода» принимает его (`in`). Поэтому сверяются имена и счётчик.
    actual = list(obj.ports)
    if len(actual) != len(names):
        return _item(
            "contract_ports_mismatch", "failed", obj,
            f"портов {len(actual)} против {len(names)} в PortNames")
    for port, wanted in zip(actual, names):
        if not _same_port_name(port.name, wanted):
            return _item(
                "contract_ports_mismatch", "failed", obj,
                f"порт {port.index}: имя {port.name!r} против {wanted!r} "
                f"из PortNames")
    return _item("contract_ports_match", "passed", obj,
                 f"порт {names[0]!r} по PortNames")


def _same_port_name(actual: str, wanted: str) -> bool:
    """Имена портов равны с точностью до записи минуса.

    Живые замеры 30.10.2026: у «Сумматора» с весом -1 порт называется то
    «−» (U+2212: импорт текста в свежем проекте), то «-» (ASCII: субмодель
    преобразователя координат из библиотеки). Оба написания значат один
    минус, и различать их отказом было бы ложным провалом.
    """
    if actual == wanted:
        return True
    return actual.replace("−", "-") == wanted.replace("−", "-")


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


def _submodel_container(model: ModelIR,
                        obj: ModelObject) -> Optional[Container]:
    """Страница субмодели: контейнер, для которого объект - родитель."""
    return next((item for item in model.containers
                 if item.parent_object_id == obj.id), None)


def _compare_submodel(obj: ModelObject, model: ModelIR) -> dict[str, Any]:
    """Субмодель: внешние порты сверяются с порт-блоками внутри.

    Образец реестра для классов-субмоделей не эталон: порты задаются
    порт-блоками на странице экземпляра, а корпус видит лишь часть
    (undirected-порты электрических субмоделей в него не попадают).
    Проверка повторяет правило класса `submodel-port-match-by-name`:
    каждый внешний порт имеет ровно один внутренний порт-блок с тем же
    именем. Живой замер 30.10.2026: у 10 субмоделей привода 41 порт,
    41 порт-блок и 41 разрешённая граница; у внешней библиотечной
    субмодели страницы в проекте нет.
    """
    container = _submodel_container(model, obj)
    if container is None:
        where = f" ({obj.filename})" if obj.filename else ""
        return _item(
            "contract_ports_unknown", "not_evaluated", obj,
            f"внешняя субмодель{where}: внутренней страницы нет, "
            f"сверять порты не с чем", "external_submodel")
    boundaries = [item for item in model.boundaries
                  if item.submodel_object_id == obj.id]
    if len(boundaries) != len(obj.ports):
        # Границы строит только xprt-парсер; в text/topology-моделях их
        # нет, и считать каждый порт-блок лишним было бы ложным отказом.
        return _item(
            "contract_ports_unknown", "not_evaluated", obj,
            f"границы субмодели не построены: портов {len(obj.ports)}, "
            f"границ {len(boundaries)}", "boundaries_unavailable")
    unresolved = [item for item in boundaries if not item.resolved]
    if unresolved:
        first = unresolved[0]
        why = ("порт-блок не найден" if first.reason == "not_found"
               else "порт-блок выбран неоднозначно")
        return _item(
            "contract_ports_mismatch", "failed", obj,
            f"порт {first.external_port_name!r}: {why}")
    blocks = [item for item in model.objects
              if item.container_id == container.id
              and item.kind is ObjectKind.PORT_BLOCK]
    used: dict[str, int] = {}
    for item in boundaries:
        used[item.internal_object_id] = used.get(
            item.internal_object_id, 0) + 1
    shared = [key for key, count in used.items() if count > 1]
    if shared:
        address = next((item.address for item in blocks
                        if item.id == shared[0]), shared[0])
        return _item(
            "contract_ports_mismatch", "failed", obj,
            f"порт-блок {address} соответствует нескольким "
            f"внешним портам: имя повторяется")
    extra = [item for item in blocks if item.id not in used]
    if extra:
        return _item(
            "contract_ports_mismatch", "failed", obj,
            f"порт-блоков {len(blocks)} против внешних портов "
            f"{len(boundaries)}: лишний {extra[0].address}")
    return _item(
        "contract_ports_match", "passed", obj,
        f"внешних портов {len(boundaries)}, порт-блоков {len(blocks)}: "
        f"имена сходятся")


def _compare(obj: ModelObject, registry: BlockContractRegistry,
             model: ModelIR) -> dict[str, Any]:
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
    if expected.model == "submodel" and obj.kind is ObjectKind.SUBMODEL:
        return _compare_submodel(obj, model)
    if expected.model in ("unknown", "submodel"):
        reason = expected.reason or ("внешние порты задаются экземпляром"
                                     if expected.model == "submodel"
                                     else "портовая схема не измерена")
        return _item(
            "contract_unknown", "not_evaluated", obj,
            reason, expected.reason or expected.model)
    if expected.model == "portnames":
        return _compare_portnames(obj)
    actual_in, actual_out = _directions(obj)
    expected_in = list(expected.inputs)
    expected_out = list(expected.outputs)
    if (len(actual_in) != len(expected_in)
            or len(actual_out) != len(expected_out)):
        return _item(
            "contract_ports_mismatch", "failed", obj,
            f"входов {len(actual_in)} против {len(expected_in)}, "
            f"выходов {len(actual_out)} против {len(expected_out)}")
    if expected.model in ("fixed", "weights"):
        # Имена проверяются, только когда контракт их обещает: у классов с
        # `name_status: instance_defined` имя задаёт сам экземпляр (в корпусе
        # `inport`, в живой модели `U`), и корпусное имя не эталон
        # (simintech-code#89).
        check_names = expected.name_status != "instance_defined"
        for actual, wanted in zip(actual_in + actual_out,
                                  expected_in + expected_out):
            if (check_names and wanted.name and actual.name
                    and not _same_port_name(actual.name, wanted.name)):
                return _item(
                    "contract_ports_mismatch", "failed", obj,
                    f"порт {actual.index}: имя {actual.name!r} против "
                    f"{wanted.name!r}")
            if actual.direction is not wanted.direction:
                return _item(
                    "contract_ports_mismatch", "failed", obj,
                    f"порт {actual.index}: направление "
                    f"{actual.direction.value} против {wanted.direction.value}")
    detail = (f"портов: {len(actual_in)} входов, {len(actual_out)} выходов")
    if expected.name_status == "instance_defined":
        detail += "; имена задаёт экземпляр"
    return _item("contract_ports_match", "passed", obj, detail)


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
        item = _compare(obj, registry, model)
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
