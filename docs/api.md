# API Reference — simintech-api

Все классы доступны из корня пакета: `from simintech_api import ...`.

---

## COMClient

Низкоуровневый доступ к серверу `IMVTU_Server` (out-of-proc, `mmain.exe`).
Основа библиотеки: работает через **comtypes** (не pywin32) — структура
`TDataDescriptor` (`VT_RECORD`) маршалится корректно только через comtypes.

```python
from simintech_api import COMClient

client = COMClient(silent_mode=True).connect()   # скрытый UI
client.get_process_id()                          # PID mmain.exe
client.disconnect()
```

### Методы
| Метод | Описание |
|---|---|
| `connect() -> COMClient` | Подключиться (требует `mmain.exe /regserver`). Бросает `ComConnectionError` вне Windows. |
| `disconnect()` | Отсоединиться (приложение не закрывается). |
| `call(method, *args) -> Any` | Вызвать любой метод `IMVTU_Server`. |
| `open_project(path) -> int` | Открыть проект, вернуть ProjectId. |
| `new_project() -> int` | Создать новый проект, вернуть ProjectId. |
| `get_process_id() -> int` | PID процесса SimInTech. |
| `find_signal(name, project_id) -> TDataDescriptor` | Найти сигнал. |
| `connected`, `is_available` | Свойства состояния. |

---

## Project

```python
prj = Project.new(client)              # новый пустой проект
prj = Project.open(client, "model.prt")
prj.save_xml("out.xprt")               # / save_binary("out.prt")
prj.close()
```

### Методы
| Метод | Описание |
|---|---|
| `new(client) / open(client, path)` | Фабрики создания/открытия. |
| `save_xml(path) / save_binary(path)` | Сохранение. |
| `close()` | Закрыть проект. |
| `get_main_page() -> Page` | Главная страница. |
| `get_current_page() -> Page` | Текущая страница. |
| `find_signal(name) -> TDataDescriptor` | Поиск сигнала (SignalError, если нет). |
| `signal(name) -> Signal` | Объект Signal для чтения/записи. |
| `list_signals() -> list[SignalInfo]` | Список сигналов проекта. |
| `simulation() -> Simulation` | Управление расчётом. |
| `run() / stop()` | Удобные обёртки расчёта. |

### Свойства
`id`, `client`.

---

## Page

```python
page = prj.get_main_page()
b1 = page.create_block("Константа", 0, 0)         # x,y — координаты
b2 = page.create_block("Усилитель", 200, 0, width=60, height=40)
```

### Методы
| Метод | Описание |
|---|---|
| `activate()` | Сделать страницу текущей (SetCurrentPage). |
| `create_block(class_name, x, y, *, width, height, layer_no, parent_block) -> Block` | Создать блок. Бросает `UnsupportedBlockError` для классов из `UNSUPPORTED_COM_BLOCK_CLASSES`. |
| `get_blocks() -> list[Block]` | Все блоки текущей страницы. |
| `find_block(name) -> Block | None` | Поиск по свойству Name. |
| `create_wire(start_port, end_port, points=None, *, wire_type, layer_no) -> Wire` | Линия между портами; `points` — опорные точки. |

---

## Block

```python
b1 = page.create_block("Константа", 0, 0)
b1.set_property("y0", 5.0)              # значения приводятся к строке
b1.set_property("a", [1, 1, 1])         # массивы тоже
name = b1.get_property("Name")          # чтение как str
b1.set_position(left, top, width, height, angle)
b1.set_center(cx, cy, width, height)    # по центру
b1.get_in_port(0) / b1.get_out_port(0)  # порты (0-based, с fallback-паттерном)
b1.connect(b2)                          # out0 -> b2.in0
```

### Методы
| Метод | Описание |
|---|---|
| `set_property(name, value) -> Block` | Установить свойство (значение → строка). |
| `get_property(name) -> str` | Прочитать свойство как строку. |
| `get_name() / set_name(name)` | Свойство Name. |
| `get_points() -> str` | Сырое значение Points. |
| `set_position(x, y, *, width, height, angle)` | Left/Top/Width/Height/Angle. |
| `set_center(cx, cy, *, width, height)` | По центру. |
| `get_port_count() / get_block_port(i)` | Общий доступ к портам. |
| `get_in_port(i) / get_out_port(i)` | Входные/выходные порты. |
| `get_ports() -> list[Port]` | Все порты. |
| `connect(other, out_index=0, in_index=0) -> Wire` | Соединить. |
| `class_name` | Свойство имени класса. |

---

## Port

```python
p = b1.get_out_port(0)
info = p.get_info()          # PortInfo (12 полей)
name = p.get_name()
side = p.get_side()          # PortSide (LEFT/RIGHT/TOP/BOTTOM)
x, y = p.get_coords()        # глобальные координаты для трассировки
p.set_name("u") / p.set_side(PortSide.RIGHT) / p.set_mode(mode)
p.connect(other_port)        # источник -> приёмник
```

### `PortInfo` (dataclass)
`port_id, name, side, mode, type_id, item_id, is_inverse, is_center, is_invisible, x, y, x_global, y_global`.

---

## Wire

```python
w = page.create_wire(p1, p2)                  # точки auto
w = page.create_wire(p1, p2, [(50, 20)])      # с опорной точкой
w.set_points([(10, 20), (30, 40)])            # задать точки
w.normalize()                                 # выровнять (NormalizeWire)
w.branch_to(port, point_index=0)              # ветвление от линии
```

---

## Signal

```python
sig = prj.signal("CoolTT_C_D_SySt_TechSt")
value = sig.read()                # по DataType (float/int/bool/str)
sig.write(3.14)

arr = sig.read_array()            # массивы (ARRAY/INT_ARRAY)
sig.write_array([1.0, 2.0, 3.0])
sig.array_count() / sig.set_array_count(n)
```

Типы (`DataType`): `DOUBLE=0`, `INTEGER=1`, `BOOL=2`, `STRING=4`, `ARRAY=5`, `INT_ARRAY=12`.
Чтение/запись диспетчеризуются автоматически. Запись в bool/int — `int(value)`, в double — `float(value)`.

---

## Simulation

```python
sim = prj.simulation()
sim.start().run()              # инициализация + непрерывный расчёт
sim.step() / sim.pause() / sim.stop()
sim.run_to(10.0)               # расчёт до 10 с (bool: достигнуто ли)
sim.wait_for_time(10.0)        # блокирующее ожидание
sim.get_time() -> float        # модельное время
sim.get_state() -> int         # флаги состояния
# Пакеты:
sim.pack_start()/pack_run()/pack_step()/pack_pause()/pack_stop()
sim.run_to_pack(10.0)
sim.set_realtime_delay(flag, scale)
```

---

## Layout

### LayeredPlacer — расстановка блоков слоями
```python
from simintech_api.layout import LayeredPlacer
placer = LayeredPlacer()
positions = placer.place(
    block_ids=["A", "B", "C"],
    connections=[("A", "B"), ("B", "C")],
    sizes={"A": (60, 40), "B": (60, 40), "C": (60, 40)},
    origin=(0.0, 0.0),
)  # -> {"A": (cx, cy), ...}
```
Алгоритм: ранжирование (слои по X) → медианная эвристика (порядок в слое) →
координаты с учётом размеров. Обратная связь и чистые циклы поддерживаются
(цикл размещается, корень — блок с минимумом входов).

### ObstacleGrid — сетка препятствий
```python
from simintech_api.layout import ObstacleGrid
grid = ObstacleGrid(200, 120)          # 200x120 узлов, шаг 20 px
grid.add_rect(x, y, w, h, label="block")   # блок с отступом 1 узел
grid.add_point(x, y, label="wall")         # точечное препятствие
grid.is_blocked(gx, gy)                    # проверка узла
grid.to_grid(x, y) / grid.to_scheme(gx, gy)  # конверсия координат
```

### AStarRouter — трассировка линий
```python
from simintech_api.layout import AStarRouter
router = AStarRouter(grid_size=20, turn_penalty=2.0)
points = router.route(
    (x1, y1), (x2, y2), grid,
    start_side=1,  # PortSide правого порта
    end_side=0,    # PortSide левого порта
)  # -> список опорных точек (может быть [] — прямое соединение)
```
Свойства: ортогональное движение (4 направления), штраф за повороты,
сглаживание коллинеарных точек, «вылет» из порта по нормали к стороне.
`LayoutError` — путь невозможен.

---

## SimInTechAgent

```python
from simintech_api.agent import SimInTechAgent
agent = SimInTechAgent()
r = agent.execute('add block "Усилитель" as g1 with a=2')
if r.ok:
    print(r.message, r.data)
```
Полный список команд — в справке агента: `agent.execute("help")`.

---

## Исключения

| Класс | Родитель | Когда |
|---|---|---|
| `SimInTechError` | Exception | базовое |
| `ComConnectionError` | SimInTechError | нет COM (не Windows / не зарегистрирован) |
| `ComCallError` | SimInTechError | ошибка вызова метода (method, hr) |
| `ProjectError` / `PageError` | SimInTechError | проект/страница |
| `BlockError` | SimInTechError | блок |
| `UnsupportedBlockError` | BlockError | класс блока не создаётся через COM |
| `PortError` / `WireError` | SimInTechError | порт/линия |
| `SignalError` | SimInTechError | сигнал (не найден/неверный тип) |
| `SimulationError` | SimInTechError | расчёт |
| `LayoutError` | SimInTechError | путь/размещение невозможны |
