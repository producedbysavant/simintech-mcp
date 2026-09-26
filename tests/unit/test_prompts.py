"""Промпты-шаблоны: содержимое сверяется с каталогом блоков.

Промпт — первая инструкция, которую получает агент, и единственный канал,
который до этой проверки не был покрыт ничем. `create_pid_model` дырой уже
воспользовался: шаги собирали не ПИД — `kd` применялся к обычному
«Усилителю», дифференцирующего звена не было вовсе, — а расчёт при этом шёл,
и ошибку не диагностировало ничто. Поэтому проверяется текст промпта, а не
факт его регистрации: имена параметров проходят тот же `catalog._check_params`,
что и вызовы инструментов, число входов «Сумматора» сверяется с длиной его
`a`, а время расчёта — с `to_time`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import pytest
from simintech_api.catalog import load_default_catalog

from simintech_mcp import catalog
from simintech_mcp.server import mcp
from simintech_mcp.tools import blocks

#: `add_block(class_name="Класс", …)` из текста промпта.
_ADD_BLOCK = re.compile(r'add_block\(class_name="([^"]+)"([^)]*)\)')
#: Аргумент вида `имя=значение` или `имя="значение"`.
_KWARG = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,)\s]+))')
#: Соединение «источник → приёмник»; скобки — пояснение и `in_index`.
_CONNECT = re.compile(r"(\w+)(?:\([^)]*\))?\s*→\s*(\w+)(?:\(([^)]*)\))?")
_END_TIME = re.compile(r"end_time=([\d.]+)")
_TO_TIME = re.compile(r"to_time=([\d.]+)")

#: Промпты-шаблоны, текст которых разбирается.
PROMPTS = ("create_pid_model", "create_rc_chain")

#: Значения, с которыми рендерится ПИД: все три коэффициента различны.
_PID_ARGS = {"kp": "2", "ki": "1", "kd": "0.5"}


class _Step:
    """Один `add_block` из текста промпта."""

    def __init__(self, class_name: str, props: Dict[str, Any],
                 in_ports: int) -> None:
        self.class_name = class_name
        self.props = props
        self.in_ports = in_ports


async def _render(name: str, args: Dict[str, str]) -> str:
    """Текст промпта без обвязки `PromptResult` (сообщение, а не repr)."""
    rendered = await mcp.render_prompt(name, arguments=args)
    messages = getattr(rendered, "messages", None)
    if messages:
        content = messages[0].content
        return str(getattr(content, "text", content))
    return str(rendered)


def _parse_steps(text: str) -> List[_Step]:
    """Разобрать `add_block(...)` из текста промпта.

    Имена пар отделяет тот же `_split_props`, что и `add_block`, а значения
    приводит `_coerce_param_value` — как `set_block_param`: массивы нужны
    списком, иначе длину `a` не с чем сверить. Свой парсер проверял бы себя, а
    не то, что получит инструмент.
    """
    steps: List[_Step] = []
    for class_name, tail in _ADD_BLOCK.findall(text):
        args: Dict[str, str] = {}
        for key, quoted, bare in _KWARG.findall(tail):
            args[key] = quoted if quoted else bare
        props: Dict[str, Any] = {}
        for pair in blocks._split_props(args.get("props", "")):
            key, _, value = pair.partition("=")
            props[key.strip()] = blocks._coerce_param_value(value.strip())
        steps.append(_Step(class_name, props,
                           int(args.get("in_ports", "0") or "0")))
    return steps


def _connections(text: str) -> List[Tuple[str, str, int]]:
    """Соединения из текста: (источник, приёмник, номер входа)."""
    links: List[Tuple[str, str, int]] = []
    for src, dst, note in _CONNECT.findall(text):
        found = re.search(r"in_index=(\d+)", note or "")
        links.append((src, dst, int(found.group(1)) if found else 0))
    return links


def _weights(step: _Step) -> List[float]:
    """Веса сумматора: массив `a` в виде чисел."""
    value = step.props.get("a")
    assert isinstance(value, list), f"у «{step.class_name}» нет массива a"
    return [float(weight) for weight in value]


def _gain(step: _Step) -> float:
    """Коэффициент усилителя: скаляр `a` в виде числа."""
    value = step.props.get("a")
    assert value is not None, f"у «{step.class_name}» нет параметра a"
    return float(value)


@pytest.mark.anyio
@pytest.mark.parametrize("name", PROMPTS)
async def test_prompt_params_pass_catalog_check(name: str) -> None:
    """Имена параметров из промпта проходят ту же проверку, что вызов COM.

    `catalog._check_params` — рабочий страж `add_block`/`set_block_param`: он
    отвергает неизвестное имя и запись в вычисляемый параметр. Здесь он же
    применяется к именам из шаблона, чтобы опечатка в промпте не ушла агенту
    молчаливой записью в никуда.
    """
    catalog_blocks = load_default_catalog()
    assert len(catalog_blocks) > 0, "каталог блоков пуст — сверять нечем"

    for step in _parse_steps(await _render(name, {})):
        assert catalog_blocks.has(step.class_name), (
            f"{name}: класса '{step.class_name}' нет в каталоге блоков")
        catalog._check_params(step.class_name, list(step.props),
                              allow_unknown=False, notes=[])


@pytest.mark.anyio
@pytest.mark.parametrize("name", PROMPTS)
async def test_prompt_sum_ports_match_weights(name: str) -> None:
    """Число входов «Сумматора» совпадает с длиной `a`.

    Порты добавляет только `in_ports`: более длинный `a` сам их не создаёт
    (докстринг `add_block`). Расхождение дало бы висящий вход, а тот молча
    останавливает расчёт всей модели.
    """
    for step in _parse_steps(await _render(name, {})):
        if step.in_ports and isinstance(step.props.get("a"), list):
            assert len(_weights(step)) == step.in_ports, (
                f"{name}: у «{step.class_name}» in_ports={step.in_ports}, "
                f"а весов в a — {len(_weights(step))}")


@pytest.mark.anyio
@pytest.mark.parametrize("name", PROMPTS)
async def test_prompt_run_matches_end_time(name: str) -> None:
    """`to_time` совпадает с `end_time`: иначе расчёт не дойдёт до отметки."""
    text = await _render(name, {})
    end_time = _END_TIME.findall(text)
    to_time = _TO_TIME.findall(text)

    assert len(end_time) == 1 and len(to_time) == 1, (
        f"{name}: end_time={end_time}, to_time={to_time}")
    assert float(end_time[0]) == float(to_time[0]), (
        f"{name}: end_time={end_time[0]}, to_time={to_time[0]}")


@pytest.mark.anyio
@pytest.mark.parametrize("name", PROMPTS)
async def test_prompt_connects_exactly_created_blocks(name: str) -> None:
    """Соединяются только созданные блоки — и каждый созданный соединяется."""
    text = await _render(name, {})
    created = {step.class_name for step in _parse_steps(text)}
    links = _connections(text)
    assert links, f"{name}: в промпте нет шагов соединения"

    linked = {src for src, _, _ in links} | {dst for _, dst, _ in links}
    assert linked <= created, (
        f"{name}: соединение ссылается на несозданный блок: "
        f"{sorted(linked - created)}")
    assert created <= linked, (
        f"{name}: блок остался без соединения: {sorted(created - linked)}")


@pytest.mark.anyio
async def test_pid_prompt_has_integrator_and_derivative_branches() -> None:
    """В контуре есть интегрирующее и дифференцирующее звенья.

    Это и был дефект: `kd` применялся к обычному «Усилителю», «Производной»
    в шагах не было вовсе, а единственный «Интегратор» стоял в обратной
    связи. Модель получалась не ПИД, и по расчёту отличить её было нельзя.
    """
    steps = _parse_steps(await _render("create_pid_model", _PID_ARGS))
    classes = [step.class_name for step in steps]

    assert "Производная" in classes, "нет дифференцирующего звена"
    assert "Интегратор" in classes, "нет интегрирующего звена"

    # Коэффициент стоит именно на своём звене: усилитель идёт сразу за
    # интегратором (I-ветвь) и сразу за производной (D-ветвь).
    for element, coefficient in (("Интегратор", 1.0), ("Производная", 0.5)):
        follower = steps[classes.index(element) + 1]
        assert follower.class_name == "Усилитель", (
            f"за «{element}» идёт «{follower.class_name}», а не «Усилитель»")
        assert _gain(follower) == pytest.approx(coefficient), (
            f"коэффициент за «{element}» — {_gain(follower)}, "
            f"ожидался {coefficient}")

    # Третий усилитель — пропорциональная ветвь.
    gains = [step for step in steps if step.class_name == "Усилитель"]
    assert [_gain(step) for step in gains] == pytest.approx([2.0, 1.0, 0.5])


@pytest.mark.anyio
async def test_rc_chain_is_aperiodic_link_with_feedback() -> None:
    """RC-цепь замкнута обратной связью — иначе это не 1/(RC·s+1).

    Прежний шаблон собирал «Ступенька → Усилитель → Интегратор» без обратной
    связи: выход рос линейно (u·t/RC), а не выходил на уставку. Признаки
    замкнутого контура — разноимённые веса сумматора ошибки (u − y) и линия с
    выхода интегратора обратно на его же вход.
    """
    text = await _render("create_rc_chain", {})
    steps = _parse_steps(text)

    sums = [step for step in steps if step.class_name == "Сумматор"]
    assert sums, "нет сумматора ошибки — контур разомкнут"
    assert _weights(sums[0]) == [1.0, -1.0], (
        "сумматор ошибки должен считать разность u − y")

    links = {(src, dst) for src, dst, _ in _connections(text)}
    assert ("Интегратор", "Сумматор") in links, (
        "выход интегратора не заведён обратно на сумматор ошибки")
    assert ("Усилитель", "Интегратор") in links
