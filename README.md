# simintech-api

Python-библиотека для программного управления средой динамического моделирования **SimInTech** через внешний **COM API** (`IMVTU_Server`, сервер `mmain.exe`).

Позволяет ИИ-агенту или пользователю через текстовые команды:
1. Создавать проекты SimInTech и размещать на схеме блоки из стандартных библиотек;
2. Настраивать параметры блоков;
3. Соединять блоки линиями связи;
4. Запускать расчёт, читать и записывать сигналы;
5. Автоматически расставлять блоки (layout) и трассировать линии без наложений (A*).

## Ограничения

- **Работает только на Windows** — COM API SimInTech доступен только там. Требуется зарегистрированный COM-объект: `C:\SimInTech64\bin\mmain.exe /regserver`.
- Использует **comtypes** (не pywin32): структура `TDataDescriptor` (`VT_RECORD`) корректно маршалится только через comtypes.
- Некоторые классы блоков («Из памяти», «Порт выхода», «Флаг входа в состояние») **не создаются через COM CreateBlock** — их создают через встроенный язык SimInTech (см. `constants.UNSUPPORTED_COM_BLOCK_CLASSES`).

## Установка

```bash
pip install -e ".[test]"   # Windows; поставит comtypes + pytest
```

## Быстрый старт

```python
from simintech_api import COMClient, Project

client = COMClient(silent_mode=True).connect()

# Создать проект и главную страницу
prj = Project.new(client)
page = prj.get_main_page()

# Блоки: Константа (5) -> Усилитель (x2)
b1 = page.create_block("Константа", 0, 0)
b1.set_property("y0", 5.0)

b2 = page.create_block("Усилитель", 200, 0)
b2.set_property("a", 2.0)

# Соединить
b1.connect(b2)

prj.save_xml("model.xprt")
prj.close()
client.disconnect()
```

## Автоматическая расстановка и трассировка

```python
from simintech_api.layout import LayeredPlacer, AStarRouter, ObstacleGrid

# 1) Расстановка блоков слоями (Sugiyama-подобный)
placer = LayeredPlacer()
positions = placer.place(
    block_ids=["A", "B", "C"],
    connections=[("A", "B"), ("B", "C")],
)  # -> {"A": (x,y), ...} без наложений

# 2) Трассировка линии между портами (A* на ортогональной сетке)
grid = ObstacleGrid(60, 40)
grid.add_rect(100, 50, 60, 40)          # препятствие
router = AStarRouter()
points = router.route((20, 20), (250, 20), grid)  # опорные точки для SetWirePoint
```

## Текстовые команды для ИИ-агента

`SimInTechAgent` — единая точка входа для LLM-агента. Принимает команды на
русском/английском и выполняет их:

```
create project "MyModel"
add block "Константа" as k1 at (0, 0) with y0=5
add block "Усилитель" as g1 at (200, 0) with a=2
connect k1.out to g1.in
run for 10 seconds
get signal "g1.out"
save project "MyModel.xprt"
```

```python
from simintech_api.agent import SimInTechAgent

agent = SimInTechAgent()
result = agent.execute('add block "Усилитель" as g1 with a=2')
print(result)  # [OK] Блок 'Усилитель' добавлен как 'g1' (id=...)
```

CLI-обёртка: `simintech-cli "create project \"Model\"" "add block \"Константа\""` —
выполняет команды из консоли или интерактивно.

## Структура пакета

```
simintech_api/
├── __init__.py       # экспорт публичного API
├── constants.py      # DataType, PortSide, размеры, классы блоков
├── model.py          # TDataDescriptor (comtypes Structure), SignalInfo, PortInfo
├── exceptions.py     # иерархия исключений
├── core/             # COMClient, Project, Page, Block, Port, Wire, Signal, Simulation
├── layout/           # ObstacleGrid, LayeredPlacer, AStarRouter
├── utils/            # converters (BSTR/VARIANT/Points), logger (Named Pipe)
├── cli.py            # CLI-интерфейс (см. /cli)
└── agent.py          # SimInTechAgent — текстовые команды для ИИ-агента
```

## Тестирование

```bash
# Unit-тесты (layout, конвертеры) — без COM, в любом окружении:
python -m pytest tests/unit

# Интеграционные (реальный COM, только Windows + /regserver):
python -m pytest tests/integration -m integration
```

## Документация

- `docs/architecture.md` — архитектура и ключевые решения.
- Полная карта методов COM API и DataType — в `sitECRT/doc/reference/com_api_inventory.md`.
- Официальная спецификация: `SIT_SimInTech_Vneshnij_API.pdf`, `source/exe/mmain.hpp`.
