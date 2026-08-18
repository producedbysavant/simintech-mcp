# Руководство пользователя

Пошаговое создание первой модели через simintech-api.

## 0. Требования

- **Windows** (COM API SimInTech работает только там).
- Установленный SimInTech 64 и зарегистрированный COM-объект:
  ```
  C:\SimInTech64\bin\mmain.exe /regserver
  ```
- Python 3.9+, comtypes:
  ```
  pip install -e ".[test]"
  ```

## 1. Подключение

```python
from simintech_api import COMClient
client = COMClient(silent_mode=True).connect()
```

`silent_mode=True` скрывает UI. Если нужно видеть окно — `silent_mode=False`.
Метод `connect()` поднимет `ComConnectionError`, если COM недоступен
(не Windows / mmain.exe не зарегистрирован).

## 2. Создание проекта и блоков

```python
from simintech_api import Project

prj = Project.new(client)          # новый проект
page = prj.get_main_page()         # главная страница

b1 = page.create_block("Ступенька", 0, 0)     # класс + координаты
b1.set_property("t", 0.0)          # момент скачка
b1.set_property("y0", 0.0)
b1.set_property("yk", 5.0)         # амплитуда

b2 = page.create_block("Усилитель", 200, 0)
b2.set_property("a", 2.0)          # коэффициент
```

Имена классов — русские, регистрозависимы («Константа», «Усилитель»,
«Сумматор», «Интегратор», «Производная», «Ступенька» и др.). Полный список
доступных классов см. в библиотеках блоков SimInTech (`bin/*.csl`).

> **Известное ограничение**: некоторые классы («Из памяти», «Порт выхода»,
> «Флаг входа в состояние») **не создаются через COM CreateBlock** — для них
> используйте встроенный язык SimInTech (макросы / блок «Язык
> программирования»). Библиотека бросает `UnsupportedBlockError`, чтобы
> предупредить об этом.

## 3. Соединение

```python
b1.connect(b2)                     # out0 -> b2.in0
# с указанием портов:
b1.get_out_port(0).connect(b2.get_in_port(1))
```

## 4. Расчёт

```python
sim = prj.simulation()
sim.start()                        # инициализация
sim.run_to(5.0)                    # расчёт до 5 с
sim.get_time()                     # текущее модельное время
sim.stop()
```

## 5. Чтение сигналов

```python
sig = prj.signal("имя_блока")      # FindSignalData: сигнал по имени блока
value = sig.read()                 # тип автоматически по DataType
sig.write(3.14)                    # запись

# Внешние (обменные) сигналы — блоки «Вход/Выход алгоритма»:
for info in prj.list_signals():
    print(info.name, info.caption)
```

`signal(name)` / `find_signal(name)` ищут сигнал по имени **блока** через
`FindSignalData` — доступно сразу после открытия проекта.

`list_signals()` возвращает только **внешние (обменные)** сигналы (блоки
«Вход/Выход алгоритма»). Внутренние сигналы блоков в него не входят — для
них используйте `signal(имя_блока)`. Для моделей без внешних интерфейсов
список будет пуст (это нормально, не ошибка). Имена сигналов также можно
взять из БД сигналов (SDB в SimInTech).

## 6. Сохранение

```python
prj.save_xml("model.xprt")         # XML — удобно для git-диффа
# или prj.save_binary("model.prt")
prj.close()
client.disconnect()
```

## 7. Автоматическая расстановка и трассировка

Для больших моделей используйте layout (см. `docs/algorithms.md`):

```python
from simintech_api.layout import LayeredPlacer, AStarRouter, ObstacleGrid

positions = LayeredPlacer().place(block_ids, connections, sizes=sizes)
grid = ObstacleGrid(200, 120)
for bid, (cx, cy) in positions.items():
    w, h = sizes[bid]
    grid.add_rect(cx - w/2, cy - h/2, w, h)
points = AStarRouter().route(p1, p2, grid, start_side=1, end_side=0)
page.create_wire(port1, port2, points)
```

## 8. ИИ-агент / CLI

```python
from simintech_api.agent import SimInTechAgent
agent = SimInTechAgent()
print(agent.execute("help"))       # список команд
agent.execute('create project "Demo"')
agent.execute('add block "Ступенька" as src with yk=5')
agent.execute('add block "Усилитель" as amp with a=2')
agent.execute("connect src.out to amp.in")
agent.execute("run for 5 seconds")
```

Из консоли:

```
simintech-cli "create project \"Demo\"" "add block \"Ступенька\"" 
simintech-cli    # интерактивный режим
```

## 9. Типичные ошибки и решения

| Ошибка | Причина | Решение |
|---|---|---|
| `ComConnectionError` | COM недоступен (не Windows / не /regserver) | Выполнить `mmain.exe /regserver`; проверить платформу |
| `SignalError: не найден` | Имя сигнала неверное | `prj.list_signals()` → проверить точное имя |
| `UnsupportedBlockError` | Класс не создаётся через COM | Использовать встроенный язык SimInTech |
| `PortError` | Нет порта с таким индексом | `get_port_count()` / `get_ports()` |
| `LayoutError` | Путь между портами невозможен | Ослабить препятствия / увеличить сетку |
| Расчёт «завис» | `run()` без stop в непрерывном режиме | Использовать `run_to()` + `stop()` |
