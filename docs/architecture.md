# Архитектура simintech-api

Python-библиотека для управления SimInTech через COM API (`IMVTU_Server`, сервер `mmain.exe`, out-of-proc).

## Ключевые решения (по итогам исследования Фазы 1)

1. **COM-движок — comtypes** (не pywin32). Причина: `TDataDescriptor` — структура `VT_RECORD`; pywin32 ломает её marshalling. Проверено в `docs/reference/com_api_inventory.md`.
2. **Один интерфейс `IMVTU_Server`, 176 методов.** Все результаты — через `[out]`-параметры, идентификаторы — `__int64` (VT_I8), строки `[in]` = BSTR.
3. **`TDataDescriptor{DataId: i64, DataType: long}`** — центральная структура: `Find*` возвращает по указателю, `Read*/Write*` принимают по значению. DataType: 0=double, 1=int, 2=bool, 4=string, 5=array(double[]), 12=intarray.
4. **Два механизма создания моделей**: COM API (`CreateBlock`, имя класса строкой) и встроенный язык SimInTech (`createblock`/`createmodel` — отдельный механизм для обхода неработающих классов). Резервный путь создания через макросы/`.inc` и `SetPageScript`.
5. **Трассировка опирается на `GetPortInfo`** — возвращает `X, Y, XGlobal, YGlobal` (координаты порта на схеме).
6. **Ограничение окружения**: COM работает только на Windows. Тесты: unit (моки) в любом окружении, integration (реальный COM) — маркер `@pytest.mark.integration`, запуск на Windows.

## Структура пакета

```
simintech_api/
├── __init__.py            # экспорт публичного API
├── constants.py           # DataType, Side, Mode, код-маппинг, дефолтные размеры блоков
├── model.py               # TDataDescriptor (comtypes record), дата-классы
├── exceptions.py          # SimInTechError, ComCallError, BlockNotFound, ProjectError
├── core/
│   ├── com_client.py      # COMClient: Dispatch(mm ain.tlb), connect/disconnect, low-level call
│   ├── project.py         # Project: new/open/save/close, run/step/pause/stop, signals
│   ├── page.py            # Page: current page, create_block, create_wire, get_blocks
│   ├── block.py           # Block: props, position, ports, connect
│   ├── port.py            # Port: info (side, name, coords), set_side/name
│   ├── wire.py            # Wire: points, normalize, branch
│   ├── signal.py          # Signal: read/write по DataType, arrays
│   └── simulation.py      # Simulation: ProjectStart/Run/Step/RunTo/WaitForTime, Pack
├── layout/
│   ├── grid.py            # ObstacleGrid: сетка с препятствиями (блоки, линии)
│   ├── placer.py          # LayeredPlacer: Sugiyama (ранжирование → порядок → координаты)
│   └── router.py          # AStarRouter: A* на сетке, ортогональные пути, сглаживание
├── utils/
│   ├── converters.py      # BSTR/VARIANT <-> Python str, value <-> str для SetBlockProp
│   └── logger.py          # PipeLogger: перехват лога через SetPipeName + поток чтения
├── cli.py                 # CLI-интерфейс (Фаза 5)
└── agent.py               # SimInTechAgent: текстовые команды (Фаза 5)
```

## Потоки данных

### Подключение
```
COMClient(silent=True) → comtypes.CreateObject(MVTU_Server) → SetSilentMode(1) → SetNoCloseAppFlag(1)
```

### Создание модели (схема)
```
Project.new() → get_main_page() → Page.create_block("Усилитель", x, y)
  → InitBlock → SetBlockProp("a", "2") → SetBlockPosition(...)
Page.create_wire(port_out, port_in) → CreateWire(...PointCount=0) → NormalizeWire
```

### Сборка модели вручную (для агента)
```
SimInTechAgent.build_spec(spec) → LayeredPlacer.place(граф) → for each block: create + position
  → AStarRouter.route(port, port, obstacles) → SetWirePoint для опорных точек
```

### Расчёт и сигналы
```
Project.run() → FindSignalData(name, prj, &desc) → read/write по desc.DataType
```

## Layout (Placer)

Sugiyama-подобный layered-алгоритм:
1. Ранжирование: блоки без входов → слой 0; далее N+1 по правилу «все входы из слоёв ≤ N, хотя бы один из N».
2. Упорядочивание внутри слоя: медианная эвристика (минимизация пересечений).
3. Координаты: X по слою, Y с равными промежутками, учёт размеров (ширина/высота из GetBlockPropAsString "Points" или дефолт).
4. Постобработка: компактизация.

## Router (A*)

1. Сетка с шагом 20 px; ячейки блоков = препятствия.
2. A* с манхэттенской метрикой, штраф за повороты.
3. Сглаживание (удаление коллинеарных точек), отступы от блоков.
4. Особые случаи: порты на одной линии → прямое соединение без точек; ветвление через ParentWire.

## Тестирование

- `tests/unit/` — layout (placer, router), конвертеры, COMClient с фейковым сервером (mock). Запуск: `pytest -m "not integration"`.
- `tests/integration/` — реальный COM на Windows: lifecycle проекта, создание блоков, линий, сигналы, расчёт, сборка модели SinSource→Gain. Запуск: `pytest -m integration` (требует зарегистрированный `mmain.exe /regserver`).
