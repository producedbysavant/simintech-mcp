"""Пример 1: RC-цепь (интегратор с постоянной времени).

Схема: Источник ступеньки -> Усилитель (1/RC) -> Интегратор -> Выход.
Запускается на Windows (требует mmain.exe /regserver).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simintech_api import COMClient, Project

# Параметры RC-цепи
R = 1000.0    # Ом
C = 0.001     # Ф
RC = R * C    # постоянная времени, с

# --- 1. Подключение и проект ---
client = COMClient(silent_mode=True).connect()
prj = Project.new(client)
page = prj.get_main_page()

# --- 2. Блоки ---
step = page.create_block("Ступенька", 0, 0)
step.set_property("t", 0.0)   # момент скачка
step.set_property("y0", 0.0)  # начальное значение
step.set_property("yk", 5.0)  # амплитуда (напряжение источника, В)

gain = page.create_block("Усилитель", 200, 0)
gain.set_property("a", 1.0 / RC)   # коэффициент 1/RC

integr = page.create_block("Интегратор", 400, 0)
integr.set_property("k", 1.0)
integr.set_property("x0", 0.0)     # начальное напряжение на конденсаторе

# --- 3. Соединения ---
step.connect(gain)        # Ступенька -> Усилитель
gain.connect(integr)      # Усилитель -> Интегратор

# --- 4. Сохранение ---
out = os.path.join(os.path.dirname(__file__), "rc_chain.xprt")
prj.save_xml(out)
print(f"RC-цепь сохранена: {out}")

# --- 5. Расчёт ---
sim = prj.simulation()
sim.start()
sim.run_to(5 * RC)        # до 5 постоянных времени — переходный процесс завершён
v_out = integr.get_out_port(0)
print(f"Модельное время: {sim.get_time():.3f} с")

prj.close()
client.disconnect()
print("Готово. Ожидаемое установившееся напряжение: ~5 В (ступенька 5 В).")
