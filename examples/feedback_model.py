"""Пример 3: модель с обратной связью + авто-размещение и трассировка.

Демонстрирует полный конвейер:
1. Спецификация модели (блоки + связи) декларативно.
2. Автоматическая расстановка (LayeredPlacer).
3. Создание блоков в позициях placer'а.
4. Трассировка линий (AStarRouter) между портами.
5. Запуск расчёта.

Обратная связь: y = u + 0.5*y (усилитель с замыканием на вход).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simintech_api import COMClient, Project
from simintech_api.layout import AStarRouter, LayeredPlacer, ObstacleGrid

# Спецификация модели: {id: (класс, свойства)}, связи: [(src, dst)]
SPEC = {
    "source": ("Ступенька", {"t": 0.0, "y0": 0.0, "yk": 1.0}),
    "sum": ("Сумматор", {"a": [1.0, 1.0], "xn": 2}),
    "gain": ("Усилитель", {"a": 1.5}),
    "fb": ("Усилитель", {"a": 0.5}),
}
LINKS = [
    ("source", "sum"),   # вход задания
    ("sum", "gain"),     # сумма -> усилитель
    ("gain", "fb"),      # выход -> обратная связь
    ("fb", "sum"),       # обратная связь -> на вход сумматора (петля)
]

# Дефолтные размеры блоков
SIZES = {k: (60.0, 40.0) for k in SPEC}


def main() -> None:
    client = COMClient(silent_mode=True).connect()
    prj = Project.new(client)
    page = prj.get_main_page()

    # 1. Расстановка (обратная связь поддерживается placer'ом)
    placer = LayeredPlacer()
    positions = placer.place(list(SPEC.keys()), LINKS, sizes=SIZES)
    print("Позиции блоков:", positions)

    # 2. Создание блоков в вычисленных позициях
    blocks = {}
    for bid, (cls, props) in SPEC.items():
        cx, cy = positions[bid]
        b = page.create_block(cls, cx, cy)
        for k, v in props.items():
            b.set_property(k, v)
        b.set_name(bid)
        blocks[bid] = b

    # 3. Сетка препятствий из позиций блоков
    grid = ObstacleGrid(200, 120)
    for bid, (cx, cy) in positions.items():
        w, h = SIZES[bid]
        grid.add_rect(cx - w / 2, cy - h / 2, w, h)

    # 4. Трассировка и создание линий
    router = AStarRouter()
    for src, dst in LINKS:
        out_p = blocks[src].get_out_port(0)
        in_p = blocks[dst].get_in_port(0)
        p1 = out_p.get_coords()
        p2 = in_p.get_coords()
        points = router.route(
            p1, p2, grid,
            start_side=out_p.get_side(),
            end_side=in_p.get_side(),
        )
        page.create_wire(out_p, in_p, points)

    # 5. Сохранение и расчёт
    out = os.path.join(os.path.dirname(__file__), "feedback_model.xprt")
    prj.save_xml(out)
    print(f"Модель с обратной связью сохранена: {out}")

    sim = prj.simulation()
    sim.start()
    sim.run_to(5.0)
    print(f"Модельное время: {sim.get_time():.3f} с")

    prj.close()
    client.disconnect()
    print("Готово. Установившееся значение выхода: u * a / (1 - a*fb) "
          "с учётом топологии сумматора.")


if __name__ == "__main__":
    main()
