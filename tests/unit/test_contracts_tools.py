"""M2: контрактные проверки в query_model и ресурс контрактов."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_mcp import model_store  # noqa: E402
from simintech_mcp.contracts import check_model_contracts  # noqa: E402
from simintech_mcp.resources import (  # noqa: E402
    resource_block_contract,
    resource_block_contracts,
)
from simintech_mcp.server import mcp  # noqa: E402
from simintech_api.model_ir import parse_xprt  # noqa: E402

from _support import _text  # noqa: E402

MODEL = """<Header><project><page>
  <object>
    <name>`M`</name>
    <class_name>`Перемножитель`</class_name>
    <obj_type>`100`</obj_type>
    <custom_props>
      <data><name>`nport`</name><value>`2`</value></data>
    </custom_props>
    <ports>
      <port><name>`Port_0`</name><mode>`1`</mode></port>
      <port><name>`PORT1`</name><mode>`0`</mode></port>
      <port><name>`PORT2`</name><mode>`0`</mode></port>
    </ports>
  </object>
</page></project></Header>"""

UNKNOWN = """<Header><project><page>
  <object>
    <name>`U`</name>
    <class_name>`Arduino - I2C - Ведомый I2C`</class_name>
    <obj_type>`100`</obj_type>
  </object>
</page></project></Header>"""


MEMORY = """<Header><project><page>
  <object>
    <name>`To`</name><class_name>`В память`</class_name><obj_type>`104`</obj_type>
    <ports><port><name>`u`</name><mode>`0`</mode></port></ports>
    <visual_props><data><name>`PortNames`</name><value>`VAR`</value></data></visual_props>
  </object>
  <object>
    <name>`From`</name><class_name>`Из памяти`</class_name><obj_type>`105`</obj_type>
    <ports><port><name>`y`</name><mode>`1`</mode></port></ports>
    <visual_props><data><name>`PortNames`</name><value>`VAR`</value></data></visual_props>
  </object>
</page></project></Header>"""


def setup_function():
    model_store.clear()


def test_contract_report_passes_and_fails():
    passed = check_model_contracts(parse_xprt(MODEL))
    assert passed.status == "passed"
    assert passed.passed == 1

    broken = check_model_contracts(parse_xprt(
        MODEL.replace("<value>`2`</value>", "<value>`3`</value>")))
    assert broken.status == "failed"
    assert broken.items[0]["code"] == "contract_ports_mismatch"

    unknown = check_model_contracts(parse_xprt(UNKNOWN))
    assert unknown.status == "not_evaluated"
    assert unknown.items[0]["reason"] == "not_observed_in_corpus"


def test_portnames_ports_are_instance_defined():
    """Образец реестра у portnames не эталон: проверка не выдумывает счёт."""
    report = check_model_contracts(parse_xprt(MEMORY))
    assert report.status == "not_evaluated"
    assert report.failed == 0
    assert report.by_reason == {"portnames_instance_defined": 2}


@pytest.mark.anyio
async def test_query_model_attaches_contracts(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "m.xprt").write_text(MODEL, encoding="utf-8")
    loaded = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "m.xprt"})))
    ref = loaded["model_ref"]

    check = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": ref, "query": "check_connections"})))
    assert check["evidence"]["contracts"]["status"] == "passed"
    assert check["evidence"]["contracts"]["evaluated"] == 1

    invariants = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": ref, "query": "validate_invariants",
         "arguments": {"invariants": [
             {"id": "ports", "kind": "contract_ports_match"}]}})))
    item = next(item for item in invariants["items"]
                if item["kind"] == "contract_ports_match")
    assert item["status"] == "passed"


def test_contract_resources_are_compact():
    summary = resource_block_contracts()
    assert "классов: 958" in summary
    assert "Перемножитель" in summary
    assert "Полный дамп не выводится" in summary

    contract = resource_block_contract("Перемножитель")
    assert "# Перемножитель" in contract
    assert "ports: nport" in contract
    assert "constraint nport-input-count" in contract
    assert "property nport" in contract
    assert len(contract) < 4000


SUM = """<Header><project><page>
  <object>
    <name>`S`</name>
    <class_name>`Сумматор`</class_name>
    <obj_type>`100`</obj_type>
    <custom_props>
      <data><name>`a`</name><value>`[1 , -1]`</value></data>
    </custom_props>
    <ports>
      <port><name>` `</name><mode>`1`</mode></port>
      <port><name>`+`</name><mode>`0`</mode></port>
      <port><name>`−`</name><mode>`0`</mode></port>
    </ports>
  </object>
</page></project></Header>"""

DELAY = """<Header><project><page>
  <object>
    <name>`D`</name>
    <class_name>`Задержка по включению`</class_name>
    <obj_type>`100`</obj_type>
    <custom_props>
      <data><name>`addport`</name><value>`1`</value></data>
    </custom_props>
    <ports>
      <port><name>`outport`</name><mode>`1`</mode></port>
      <port><name>`inport`</name><mode>`0`</mode></port>
      <port><name>`T1`</name><mode>`0`</mode></port>
    </ports>
  </object>
</page></project></Header>"""

INVERTER = """<Header><project><page>
  <object>
    <name>`N`</name>
    <class_name>`Оператор НЕ`</class_name>
    <obj_type>`100`</obj_type>
    <ports>
      <port><name>`outport`</name><mode>`1`</mode></port>
      <port><name>`U`</name><mode>`0`</mode></port>
    </ports>
  </object>
</page></project></Header>"""


def test_contracts_sum_weights_drive_names():
    """Имена входов «Сумматора» считаются по весам, а не по образцу реестра."""
    report = check_model_contracts(parse_xprt(SUM))
    assert report.status == "passed"

    broken = check_model_contracts(parse_xprt(
        SUM.replace("[1 , -1]", "[1 , 1]")))
    assert broken.status == "failed"
    assert "имя '−' против '+'" in broken.items[0]["detail"]


def test_contracts_optional_port_follows_addport():
    """T1 у задержки ожидается только при включённом addport."""
    assert check_model_contracts(parse_xprt(DELAY)).status == "passed"

    # addport нет, а второй вход есть: счётчик расходится с контрактом
    without = DELAY.replace("<value>`1`</value>", "<value>`0`</value>")
    report = check_model_contracts(parse_xprt(without))
    assert report.status == "failed"
    assert "входов 2 против 1" in report.items[0]["detail"]


def test_contracts_skip_instance_defined_names():
    """Имя порта, заданное экземпляром, не повод для отказа.

    Реестр знает `inport`, живая модель 29.10.2026 дала `U`
    (simintech-code#89): классы с `name_status: instance_defined` сверяются
    по счётчикам и направлениям, а имена пропускаются.
    """
    report = check_model_contracts(parse_xprt(INVERTER))
    assert report.status == "passed"
    assert report.passed == 1

    from simintech_api.model_ir import load_default_contracts
    from simintech_mcp.contracts import _compare

    model = parse_xprt(INVERTER)
    item = _compare(model.objects[0], load_default_contracts(), model)
    assert "имена задаёт экземпляр" in item["detail"]


def test_contracts_accept_ascii_minus_in_port_name():
    """Минус в имени порта: U+2212 и ASCII равнозначны.

    Живые замеры 30.10.2026: свежий «Сумматор» с `a = [1 , -1]` даёт порт
    «−» (U+2212), а субмодель преобразователя координат из библиотеки при
    том же весе даёт «-» (ASCII). Отказ на этом был бы ложным.
    """
    report = check_model_contracts(parse_xprt(SUM.replace("`−`", "`-`")))
    assert report.status == "passed"

    # разный знак по-прежнему расхождение
    broken = check_model_contracts(parse_xprt(SUM.replace("`−`", "`+`")))
    assert broken.status == "failed"


def test_contract_summary_puts_failures_first():
    """Отказы видны в `samples`, даже если «не проверено» много.

    Пункты собираются в порядке объектов: на живой модели привода статус
    failed пришёл с samples из одних `not_evaluated`, и отказ пришлось
    искать по сохранённому .xprt (живой прогон 30.10.2026).
    """
    from simintech_mcp.contracts import ContractReport

    report = ContractReport(
        evaluated=2, passed=1, failed=1, not_evaluated=12,
        items=tuple(
            [{"status": "not_evaluated", "code": "x"} for _ in range(12)]
            + [{"status": "failed", "code": "contract_ports_mismatch"}]))
    samples = report.summary()["samples"]
    assert samples[0]["status"] == "failed"
    assert len(samples) == 10


BAD_CONST = """<Header><project><page>
  <object>
    <name>`I`</name>
    <class_name>`Интегратор`</class_name>
    <obj_type>`100`</obj_type>
    <ports>
      <port><name>`outport`</name><mode>`1`</mode></port>
      <port><name>`inport`</name><mode>`0`</mode></port>
      <port><name>`extra`</name><mode>`0`</mode></port>
    </ports>
  </object>
</page></project></Header>"""


@pytest.mark.anyio
async def test_query_model_keeps_contract_failure_under_item_cap(
        monkeypatch, tmp_path):
    """Отказ сверки виден в списке, даже когда графических пунктов больше предела.

    На живой модели привода статус fail приходил без единого отказа: список
    режется до 200, а контрактные пункты стояли после графических.
    """
    from simintech_mcp.tools import model_ir as model_ir_tools

    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(model_ir_tools, "MAX_QUERY_ITEMS", 1)
    (tmp_path / "bad.xprt").write_text(BAD_CONST, encoding="utf-8")
    loaded = json.loads(_text(await mcp.call_tool(
        "load_model", {"path": "bad.xprt"})))

    check = json.loads(_text(await mcp.call_tool(
        "query_model",
        {"model_ref": loaded["model_ref"], "query": "check_connections"})))

    codes = [item.get("code") for item in check["items"]]
    assert "contract_ports_mismatch" in codes
    assert check["evidence"]["contracts"]["failed"] == 1


SUBMODEL_IN_BLOCK = """      <object>
        <name>`PI`</name>
        <class_name>`Порт входа`</class_name>
        <obj_type>`103`</obj_type>
        <ports><port><name>`PWM`</name><mode>`0`</mode></port></ports>
        <visual_props>
          <data><name>`PortNames`</name><value>`PWM`</value></data>
        </visual_props>
      </object>
"""

SUBMODEL_OUT_BLOCK = """      <object>
        <name>`PO`</name>
        <class_name>`Порт выхода`</class_name>
        <obj_type>`103`</obj_type>
        <ports><port><name>`A`</name><mode>`1`</mode></port></ports>
        <visual_props>
          <data><name>`PortNames`</name><value>`A`</value></data>
        </visual_props>
      </object>
"""

SUBMODEL = """<Header><project><page>
  <object>
    <name>`SM`</name>
    <class_name>`ЭП - ИНВ - Инвертор`</class_name>
    <obj_type>`102`</obj_type>
    <ports>
      <port><name>`PWM`</name><mode>`0`</mode></port>
      <port><name>`A`</name><mode>`2`</mode></port>
    </ports>
    <page>
""" + SUBMODEL_IN_BLOCK + SUBMODEL_OUT_BLOCK + """    </page>
  </object>
</page></project></Header>"""


def test_submodel_ports_match_inner_port_blocks():
    """Субмодель: внешние порты сверяются с порт-блоками её страницы.

    Живой замер 30.10.2026 на приводе 480 кВт: у 10 субмоделей с
    внутренней страницей внешних портов, порт-блоков и границ ровно
    поровну (41 граница, все разрешены), у внешней библиотечной
    субмодели страницы нет. Образец реестра для таких классов неполон
    (undirected-порты электрических субмоделей в корпус не попадают),
    поэтому эталон - порт-блоки экземпляра.
    """
    model = parse_xprt(SUBMODEL)
    report = check_model_contracts(model)
    assert report.status == "passed"
    # субмодель и два её порт-блока сверяются независимо
    assert report.passed == 3

    from simintech_api.model_ir import load_default_contracts
    from simintech_mcp.contracts import _compare

    item = _compare(model.object_by_address("main::SM"),
                    load_default_contracts(), model)
    assert item["code"] == "contract_ports_match"
    assert "порт-блоков 2" in item["detail"]


def test_submodel_port_without_inner_block_fails():
    """Внешний порт без порт-блока внутри: граница не разрешена."""
    report = check_model_contracts(parse_xprt(
        SUBMODEL.replace(SUBMODEL_IN_BLOCK, "")))
    assert report.status == "failed"
    assert "порт 'PWM'" in report.items[0]["detail"]
    assert "не найден" in report.items[0]["detail"]


def test_submodel_extra_inner_block_fails():
    """Порт-блок без внешнего порта: лишний, его вход некуда подать."""
    extra = SUBMODEL_OUT_BLOCK.replace(
        "`PO`</name>", "`PX`</name>").replace("`A`", "`X`")
    report = check_model_contracts(parse_xprt(
        SUBMODEL.replace(SUBMODEL_OUT_BLOCK, extra + SUBMODEL_OUT_BLOCK)))
    assert report.status == "failed"
    assert "лишний main/SM::PX" in report.items[0]["detail"]


def test_submodel_inner_block_class_follows_direction():
    """Направлению `out` отвечает «Порт выхода», а не «Порт входа».

    У электрических субмоделей внешний порт бывает `undirected` (живой
    инвертор: A, B, C, +), и тогда годится любой класс. Строгое
    направление класса не допускает.
    """
    wrong = (SUBMODEL.replace(
        "<port><name>`A`</name><mode>`2`</mode></port>",
        "<port><name>`A`</name><mode>`1`</mode></port>")
        .replace("<class_name>`Порт выхода`</class_name>",
                 "<class_name>`Порт входа`</class_name>"))
    report = check_model_contracts(parse_xprt(wrong))
    assert report.status == "failed"
    assert "порт 'A'" in report.items[0]["detail"]


def test_submodel_without_ports_passes():
    """Субмодель без интерфейса: сверять нечего, но страница есть."""
    empty = SUBMODEL.replace(SUBMODEL_IN_BLOCK, "").replace(
        SUBMODEL_OUT_BLOCK, "").replace(
        "<ports>\n      <port><name>`PWM`</name><mode>`0`</mode></port>\n"
        "      <port><name>`A`</name><mode>`2`</mode></port>\n    </ports>",
        "")
    model = parse_xprt(empty)
    report = check_model_contracts(model)
    assert report.status == "passed"
    assert model.object_by_address("main::SM") is not None


def test_submodel_without_boundaries_stays_not_evaluated():
    """Границы строит только xprt-источник: без них сверка была бы ложной.

    `parse_model_text` создаёт контейнеры субмоделей, но не строит
    `boundaries` (они появляются при разборе `.xprt`). Считать каждый
    порт-блок лишним на такой модели - ложный отказ.
    """
    from dataclasses import replace as dc_replace

    model = parse_xprt(SUBMODEL)
    stripped = dc_replace(model, boundaries=())
    report = check_model_contracts(stripped)
    assert report.status == "not_evaluated"
    assert report.by_reason["boundaries_unavailable"] == 1
    assert report.failed == 0


def test_submodel_shared_inner_block_fails():
    """Два внешних порта с одним именем делят один порт-блок: это ошибка."""
    shared = (SUBMODEL.replace(SUBMODEL_OUT_BLOCK, "")
              .replace("<port><name>`A`</name><mode>`2`</mode></port>",
                       "<port><name>`PWM`</name><mode>`0`</mode></port>"))
    report = check_model_contracts(parse_xprt(shared))
    assert report.status == "failed"
    assert "нескольким" in report.items[0]["detail"]


def test_external_submodel_stays_not_evaluated():
    """Внешняя библиотечная субмодель: страницы в проекте нет, сверять не с чем."""
    external = """<Header><project><page>
  <object>
    <name>`U`</name>
    <class_name>`ЭЦД2 - Источник напряжения`</class_name>
    <obj_type>`102`</obj_type>
    <filename>`ecd/ist/x.prt`</filename>
    <ports><port><name>`us`</name><mode>`0`</mode></port></ports>
  </object>
</page></project></Header>"""
    report = check_model_contracts(parse_xprt(external))
    assert report.status == "not_evaluated"
    assert report.by_reason == {"external_submodel": 1}
    assert "ecd/ist/x.prt" in report.items[0]["detail"]


PORT_IN = """<Header><project><page>
  <object>
    <name>`P`</name>
    <class_name>`Порт входа`</class_name>
    <obj_type>`103`</obj_type>
    <ports><port><name>`in1`</name><mode>`0`</mode></port></ports>
    <visual_props>
      <data><name>`PortNames`</name><value>`in1`</value></data>
    </visual_props>
  </object>
</page></project></Header>"""


def test_port_block_checked_against_portnames():
    """Порт-блок сверяется по PortNames, а не отделывается «не проверено».

    Живой замер 30.10.2026: у «Порта входа» одна строка PortNames и один
    порт с этим именем. Раньше такие блоки всегда были `not_evaluated`, и
    на модели привода их набиралось 41.
    """
    report = check_model_contracts(parse_xprt(PORT_IN))
    assert report.status == "passed"
    assert report.passed == 1

    broken = check_model_contracts(parse_xprt(
        PORT_IN.replace("`in1`</value>", "`in2`</value>")))
    assert broken.status == "failed"
    assert "из PortNames" in broken.items[0]["detail"]

    missing = check_model_contracts(parse_xprt(
        PORT_IN.replace("`in1`</value>", "` `</value>")))
    assert missing.status == "not_evaluated"
    assert missing.by_reason == {"portnames_missing": 1}
