# simintech-mcp

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
b1.set_property("a", 5.0)     # у «Константы» параметр `a`, не `y0`

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

## MCP-сервер

`simintech-mcp` поставляется с **FastMCP-сервером** (20 инструментов,
2 ресурса, 2 промпта) для управления SimInTech из ИИ-агента (Claude Code и др.).
Работает на Windows (требует `mmain.exe /regserver`).

```bash
pip install -e ".[test]"     # ставит comtypes + mcp + fastmcp
simintech-mcp                # запуск сервера (stdio)
```

Подключение к Claude Code:

```bash
claude mcp add simintech -- simintech-mcp
# или вручную в claude.json:
# { "mcpServers": { "simintech": { "command": "simintech-mcp" } } }
```

Инструменты:

| Группа | Инструменты |
|---|---|
| Подключение | `status`, `disconnect` |
| Проекты | `create_project`, `open_project`, `save_project`, `close_project` |
| Блоки/связи | `add_block`, `connect`, `list_blocks` |
| Параметры | `get_block_params`, `set_block_param` |
| Расчёт | `run`, `step`, `stop`, `get_time` |
| Сигналы | `list_signals`, `get_signal`, `set_signal` |
| Layout | `layout_place` (авто-расстановка блоков) |
| Справка | `help_text` |

Ресурсы: `simintech://status`, `simintech://project/blocks`.
Промпты: `create_pid_model`, `create_rc_chain`.

**Имена параметров блоков короткие и неочевидные.** Перечислить свойства через
COM нельзя, поэтому они берутся из каталога
(`simintech_api/data/block_catalog.json`). Каталог генерируется из реального
SimInTech: `python scripts/generate_block_catalog.py` (Windows) — см.
`skills-catalog/simintech-library-curation/`.

| Класс | Параметры |
|---|---|
| Константа | `a`, `src_type`, `txt`, `formula_visible` |
| Усилитель | `a` |
| Сумматор | `a` — массив весов; число входов = длина массива |
| Интегратор | `k`, `x0` |
| Ступенька | `t`, `y0`, `yk` |
| Синусоида | `a`, `w`, `f` |

Осторожно: запись в несуществующее имя свойства **не даёт ошибки** — параметр
не меняется, а модель считается с прежним значением. Так, `y0` у «Константы»
и `xn` у «Сумматора» молча ничего не делают.

Пример использования из ИИ-агента:
```
create_project "RC"
add_block "Ступенька" name="Step" props="yk=5"
add_block "Усилитель" name="Gain" props="a=2"
connect "Step" to "Gain"
run to_time=10
get_signal "Gain"
```

## Текстовые команды для ИИ-агента

`SimInTechAgent` — единая точка входа для LLM-агента. Принимает команды на
русском/английском и выполняет их:

```
create project "MyModel"
add block "Константа" as k1 at (0, 0) with a=5
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

- `docs/guide.md` — руководство пользователя (пошаговое создание первой модели).
- `docs/api.md` — API Reference (все классы и методы).
- `docs/algorithms.md` — алгоритмы размещения (Sugiyama) и трассировки (A*).
- `docs/architecture.md` — архитектура и ключевые решения.
- `docs/simintech-language/` — справочник встроенного языка SimInTech
  (резервный путь создания блоков, перенесён из `simintech-code-library`).
- `examples/` — рабочие примеры: модель 1 (усилитель), модель 2 (ПИД),
  модель 3 (layout+router), RC-цепь, обратная связь.
- `examples/demo.ipynb` — Jupyter Notebook с пояснениями на русском.
- `REPORT.md` — отчёт о разработке и тестировании.
- Полная карта методов COM API и DataType — в `sitECRT/doc/reference/com_api_inventory.md`.
- Официальная спецификация: `SIT_SimInTech_Vneshnij_API.pdf`, `source/exe/mmain.hpp`.

## Смежные репозитории

- `sitECRT` — тестовый тулкит и справочники COM API.
- `simintech-connector` — **заархивирован** (его COM-функционал перенесён
  в `simintech-mcp`; см. `ARCHIVED.md`).
- `simintech-code-library` — **заархивирован** (содержимое перенесено в
  `docs/simintech-language/`).
