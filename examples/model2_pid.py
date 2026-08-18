"""Модель 2: «ПИД-регулятор» с обратной связью.

Схема на блоках SimInTech (объект управления — интегратор):
  Задание (Ступенька) ─> Сумматор (ошибка e = r - y) ─> ПИД (Kp/Ki/Kd) ─> Интегратор (объект) ─> Выход y
                                                     ^                                                    │
                                                     └──────────────────  обратная связь (y) ────────────────┘

Расчёт на 20 секунд, вывод переходной характеристики в CSV.
Запуск (Windows): python examples/model2_pid.py
"""

import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simintech_api import COMClient, Project

KP, KI, KD = 1.0, 0.5, 0.1
SETPOINT = 1.0
T_FINAL = 20.0
DT = 0.1


def main() -> None:
    print("=== Модель 2: ПИД-регулятор ===")
    client = COMClient(silent_mode=True).connect()
    prj = Project.new(client)
    page = prj.get_main_page()

    # --- Блоки ---
    ref = page.create_block("Ступенька", 0, -120)
    ref.set_property("Name", "Step")
    ref.set_property("t", 0.0)      # момент скачка
    ref.set_property("y0", 0.0)     # до скачка
    ref.set_property("yk", SETPOINT)  # уставка

    # Сумматор ошибки: e = r - y (2 входа: +задание, -обратная связь)
    err = page.create_block("Сумматор", 180, -120)
    err.set_property("Name", "Sum")
    err.set_property("a", [1.0, -1.0])

    # ПИД-ветви
    kp = page.create_block("Усилитель", 360, -200)
    kp.set_property("Name", "Kp")
    kp.set_property("a", KP)

    ki = page.create_block("Усилитель", 360, -120)
    ki.set_property("Name", "Ki")
    ki.set_property("a", KI)

    kd = page.create_block("Усилитель", 360, -40)
    kd.set_property("Name", "Kd")
    kd.set_property("a", KD)

    # Сумматор ПИД: u = P + I + D
    pid_sum = page.create_block("Сумматор", 520, -120)
    pid_sum.set_property("Name", "PID")
    pid_sum.set_property("a", [1.0, 1.0, 1.0])

    # Объект управления — интегратор
    plant = page.create_block("Интегратор", 700, -120)
    plant.set_property("Name", "Plant")
    plant.set_property("k", 1.0)
    plant.set_property("x0", 0.0)

    # --- Соединения ---
    ref.connect(err, in_index=0)        # задание -> e+
    # Соединяем ветви ПИД
    err.connect(kp)
    err.connect(ki)
    err.connect(kd)
    kp.connect(pid_sum, in_index=0)
    ki.connect(pid_sum, in_index=1)
    kd.connect(pid_sum, in_index=2)
    pid_sum.connect(plant)
    # Обратная связь: выход объекта -> e- (вход 1 сумматора с коэффициентом -1)
    plant.connect(err, in_index=1)

    prj.save_xml(os.path.join(os.path.dirname(__file__), "model2_pid.xprt"))
    print("Схема ПИД сохранена: model2_pid.xprt")

    # --- Расчёт ---
    sim = prj.simulation()
    sim.start()

    # Шаговый расчёт с чтением выхода
    signals = {s.name: s for s in prj.list_signals()}
    out_name = _find_signal(signals, "Plant")
    print(f"Сигнал выхода объекта: {out_name}")

    csv_path = os.path.join(os.path.dirname(__file__), "model2_pid.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["t", "y"])
        t = 0.0
        while t <= T_FINAL:
            sim.step()
            try:
                y = signals[out_name].read()
            except Exception:
                y = float("nan")
            writer.writerow([f"{t:.2f}", f"{y:.6f}"])
            t += DT

    sim.stop()
    prj.close()
    client.shutdown()

    print(f"Переходная характеристика: {csv_path}")
    print("ПИД-регулятор готов. Ожидается выход на уставку без статической "
          "ошибки (интегральная составляющая).")


def _find_signal(signals: dict, block_name: str) -> str:
    """Найти имя сигнала по имени блока (точное или по подстроке)."""
    if block_name in signals:
        return block_name
    for name in signals:
        if block_name.lower() in name.lower():
            return name
    return next(iter(signals), "")


if __name__ == "__main__":
    main()
