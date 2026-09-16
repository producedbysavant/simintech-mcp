"""Промпты: готовые сценарии сборки модели для клиента."""

from __future__ import annotations

from .app import mcp


# ─── Промпты (шаблоны) ────────────────────────────────────────────

@mcp.prompt()
def create_pid_model(kp: float = 1.0, ki: float = 0.5,
                     kd: float = 0.1, setpoint: float = 1.0) -> str:
    """Шаблон создания ПИД-регулятора в SimInTech.

    Возвращает последовательность команд для сборки ПИД-контура
    с обратной связью через инструменты MCP-сервера.

    Args:
        kp, ki, kd: коэффициенты ПИД-регулятора.
        setpoint: уставка (значение ступеньки).
    """
    return (
        f"Создай ПИД-регулятор в SimInTech:\n"
        f"1. create_project(project_hint=\"pid\", end_time=20)\n"
        f"2. add_block(class_name=\"Ступенька\", props=\"yk={setpoint}\")\n"
        f"3. add_block(class_name=\"Сумматор\", props=\"a=[1.0,-1.0]\")\n"
        f"4. add_block(class_name=\"Усилитель\", props=\"a={kp}\")\n"
        f"5. add_block(class_name=\"Усилитель\", props=\"a={ki}\")\n"
        f"6. add_block(class_name=\"Усилитель\", props=\"a={kd}\")\n"
        f"7. add_block(class_name=\"Сумматор\", in_ports=3, "
        f"props=\"a=[1.0,1.0,1.0]\")\n"
        f"8. add_block(class_name=\"Интегратор\", props=\"k=1.0,x0=0.0\")\n"
        f"9. connect вход→сумматор, сумматор→усилители, усиливающие→PID\n"
        f"10. connect PID → Интегратор, Интегратор → сумматор (обратная связь)\n"
        f"11. layout_place по фактическим именам блоков\n"
        f"12. run(to_time=20) и проверь get_time() в ответе\n"
        f"\n"
        f"Блоки НЕ переименовываются: `name_hint` не применяется, а фактические\n"
        f"имена (`k_0`, `kx_0`, …) возвращает add_block — по ним и соединяй.\n"
        f"Соедини ВСЕ входы: блок с висящим входом молча останавливает расчёт.\n"
    )


@mcp.prompt()
def create_rc_chain(rc: float = 1.0, amplitude: float = 5.0) -> str:
    """Шаблон создания RC-цепи (ступенька → усилитель → интегратор)."""
    return (
        f"Создай RC-цепь в SimInTech:\n"
        f"1. create_project(project_hint=\"rc\", end_time={5.0 * rc})\n"
        f"2. add_block(class_name=\"Ступенька\", props=\"yk={amplitude}\")\n"
        f"3. add_block(class_name=\"Усилитель\", props=\"a={_gain_for_rc(rc)}\")\n"
        f"4. add_block(class_name=\"Интегратор\", props=\"k=1.0,x0=0.0\")\n"
        f"5. соедини блоки по фактическим именам из ответов add_block\n"
        f"   (Ступенька → Усилитель → Интегратор; все входы заняты)\n"
        f"6. run(to_time={5.0 * rc}) и проверь get_time() в ответе\n"
        f"\n"
        f"Переименование через COM недоступно — используй автоимена.\n"
    )


# ─── Внутреннее ───────────────────────────────────────────────────

def _gain_for_rc(rc: float) -> float:
    """Коэффициент усилителя для RC-цепи: 1/RC (защита от деления на 0)."""
    return 1.0 / rc if rc else 1.0
