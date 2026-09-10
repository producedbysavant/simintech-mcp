"""Пример 2: ПИД-регулятор.

Схема на блоках SimInTech:
  Задание (Константа) ─┬─> Сумматор (ошибка) ─┬─> Усилитель (Kp) ──> Сумматор (выход) ─> out
                       │                       ├─> Интегратор ─> Усилитель (Ki) ─┤
                       │                       └─> Производная ─> Усилитель (Kd) ─┤
  Обратная связь (измерение) ──────────────────────────────────────────────────────┘

Настроен как контур управления с обратной связью. Запуск на Windows.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simintech_api import COMClient, Project

# Коэффициенты ПИД-регулятора
KP = 2.0
KI = 0.5
KD = 0.1
SETPOINT = 100.0   # уставка


def main() -> None:
    client = COMClient(silent_mode=True).connect()
    prj = Project.new(client)
    page = prj.get_main_page()

    # Уставка (Константа) — вход задания.
    # ВНИМАНИЕ: у «Константы» параметр называется `a`, а не `y0`.
    # Запись в несуществующее имя (`y0`) не даёт ошибки и ни на что не влияет —
    # отказ молчаливый. Имена параметров сверены с реальным SimInTech.
    setpoint = page.create_block("Константа", 0, -80)
    setpoint.set_property("a", SETPOINT)

    # Измерение (в примере — константа; в реальности — датчик/обратная связь)
    measurement = page.create_block("Константа", 0, 80)
    measurement.set_property("a", 0.0)

    # Сумматор ошибки: e = задание - измерение.
    # Число входов определяется длиной массива `a`; отдельного параметра `xn`
    # у «Сумматора» не существует.
    err_sum = page.create_block("Сумматор", 200, 0)
    err_sum.set_property("a", [1.0, -1.0])

    # Пропорциональная ветвь
    kp_gain = page.create_block("Усилитель", 380, -100)
    kp_gain.set_property("a", KP)

    # Интегральная ветвь
    integr = page.create_block("Интегратор", 380, 0)
    integr.set_property("k", 1.0)
    integr.set_property("x0", 0.0)
    ki_gain = page.create_block("Усилитель", 520, 0)
    ki_gain.set_property("a", KI)

    # Дифференциальная ветвь
    deriv = page.create_block("Производная", 380, 100)
    deriv.set_property("x0", 0.0)
    kd_gain = page.create_block("Усилитель", 520, 100)
    kd_gain.set_property("a", KD)

    # Сумматор выхода: три ветви P + I + D.
    # Входов у «Сумматора» по умолчанию два — число портов задаётся явно.
    out_sum = page.create_block("Сумматор", 660, 0)
    out_sum.set_in_port_count(3)
    out_sum.set_property("a", [1.0, 1.0, 1.0])

    # --- Соединения ---
    setpoint.connect(err_sum, in_index=0)      # задание -> e+
    measurement.connect(err_sum, in_index=1)   # измерение -> e-

    err_sum.connect(kp_gain)
    err_sum.connect(integr)
    err_sum.connect(deriv)

    kp_gain.connect(out_sum, in_index=0)       # P-ветвь
    integr.connect(ki_gain)
    ki_gain.connect(out_sum, in_index=1)       # I-ветвь
    kd_gain.connect(out_sum, in_index=2)       # D-ветвь

    # --- Сохранение ---
    out = os.path.join(os.path.dirname(__file__), "pid_controller.xprt")
    prj.save_xml(out)
    print(f"ПИД-регулятор сохранён: {out}")

    # --- Расчёт ---
    sim = prj.simulation()
    sim.start()
    sim.run_to(10.0)
    print(f"Модельное время: {sim.get_time():.3f} с (Kp={KP}, Ki={KI}, Kd={KD})")

    prj.close()
    client.disconnect()
    print("Готово.")


if __name__ == "__main__":
    main()
