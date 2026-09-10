# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Один `pyproject.toml` (дистрибутив `simintech-api`) собирает **два пакета**:

- `simintech_api/` — библиотека программного управления SimInTech через внешний COM API (`IMVTU_Server`, сервер `mmain.exe`): создать проект, разместить блоки, соединить, запустить расчёт, обменяться сигналами.
- `simintech_mcp/` — MCP-сервер (FastMCP), оборачивающий библиотеку в инструменты, ресурсы и промпты для ИИ-агента.

Точки входа: `simintech-cli` → `simintech_api.cli:main`, `simintech-mcp` → `simintech_mcp.server:main` (stdio).

Смежные наработки в `/mnt/c/git/`: `sitECRT` (тестовый тулкит, точная карта методов COM API), `evs360_simintech` (модели бортовой логики). Репозитории `simintech-connector` и `simintech-code-library` **заархивированы** — их содержимое перенесено сюда (второй — в `docs/simintech-language/`).

## Ключевые факты

- **COM работает только на Windows**, требует `mmain.exe /regserver`. В WSL/Linux COM недоступен, поэтому основная масса тестов — unit с фейковым сервером. `python3.11 -m pytest tests/unit` проходит на Linux (60 тестов); `python3` в окружении — 3.14 без тестовых пакетов.
- **COM-движок — comtypes, не pywin32**: структура `TDataDescriptor` (`VT_RECORD`) маршалится только через comtypes. `COMClient.connect()` перебирает ProgID'ы (`mmain.MVTU_Server`, `MVTU.Server`) и CLSID.
- **COM привязан к потоку** — две ловушки, обе пойманы только на реальном SimInTech:
  - `COMClient.connect()` вызывает `CoInitializeEx` для текущего потока. Без этого вызов из рабочего потока падает с `CO_E_NOTINITIALIZED` («Не был произведён вызов CoInitialize»).
  - COM-объект, созданный в одном потоке, **нельзя** использовать в другом — `CO_E_OBJNOTCONNECTED` («Объект не подключен к серверу»). Поэтому `simintech_mcp/server.py` выполняет все COM-вызовы через один выделенный поток (`_COM_EXECUTOR`, `max_workers=1`, декоратор `_com_threaded`): FastMCP запускает синхронные инструменты в пуле и чередует потоки, что без этого ломает сервер.
- **`SetBlockProp("Name", ...)` не переименовывает блок** — имя остаётся автоматическим (`k_0`, `kx_0`), ни в `get_name()`, ни в `.xprt`; `BlockAfterEdit` не помогает, отдельного метода переименования в COM API нет. `add_block` сообщает об этом явно, чтобы не подставлять агента: иначе `connect` по заданному имени не найдёт блок.
- **Имя класса** берётся из свойства `ClassName` (`Block._detect_class_name`), а не из `GetBlockPluginName`. Второе возвращает внутреннее имя плагина (`TMBTYBlock`), которое с именами классов в каталоге не совпадает — из-за этого `get_block_params` не находил параметры.
- **DataType**: 0=double, 1=int, 2=bool, 4=string, 5=array(double[]), 12=intarray. Все Read/Write принимают `TDataDescriptor` по значению.
- **Два механизма создания моделей**: COM API (`CreateBlock`, имя класса русской строкой) и встроенный язык SimInTech (`createblock`/`createmodel`). Часть классов («Из памяти», «Порт выхода», «Флаг входа в состояние») **не создаётся через COM** — см. `constants.UNSUPPORTED_COM_BLOCK_CLASSES`.
- **Сигналы — через XML, а не COM**. `GetProjectSignalList` возвращает только *обменные* сигналы (блоки «Вход/Выход алгоритма»); внутренние сигналы блоков в него не попадают. Поэтому `Project.list_signals()` при пустом COM-списке экспортирует проект в `.xprt` (cp1251) и парсит имена блоков (`utils/xprt_signals.py`), отфильтровывая графические объекты. **Имя сигнала блока = имя блока** — на этом построены `get_signal`/`set_signal` в MCP и агенте.
- **Владение процессами `mmain.exe`**: `COMClient.connect()` запоминает `_owned_pid`, но никого не завершает; `disconnect()`/`shutdown()` по умолчанию **не убивают** процессы SimInTech (в т.ч. запущенные пользователем). Убийство — ответственность вызывающей стороны по принципу «только PID'ы, появившиеся после начала работы» (`tests/conftest.py`, `utils/processes.py`).
- **Имена параметров блоков короткие и неочевидные**: у «Константы» — **`a`**, а не `y0`; у «Сумматора» только `a` (число входов = длина массива), параметра `xn` нет; у «Ступеньки» `t`, `y0`, `yk`. Перечислить свойства через COM нельзя (нет `GetPropCount`/`GetPropName`), поэтому имена лежат в `simintech_api/data/block_catalog.json` (**сгенерирован из реального SimInTech**, 12 классов, `meta.source == "generated"`).
- **`SetBlockProp` не отвергает неизвестное имя свойства** — отказ молчаливый: чтение даёт `''`, запись не влияет ни на что, ошибки нет. Проверено: `set_property("y0", 5.0)` на «Константе» оставляет `a == [1]`. Поэтому каталог **генерируется** (`scripts/generate_block_catalog.py`), и `set_block_param` предупреждает, если имени нет в каталоге. Имена из `examples/` и `docs/simintech-language/blocks/` источником быть не могут — там встречаются несуществующие (`y0` у «Константы», `xn` у «Сумматора»; исправлено 2026-09-10).
- **Параметры блока читаются из секции `<custom_props>` файла `.xprt`**, а не из `<visual_props>` (там оформление: `Color`, `Points`, `LabelFont`). Файл пишется в **UTF-8 с BOM** — чтение как cp1251 портит русские имена классов молча. `mode 1` — задаваемый параметр, `mode 0` — вычисляемый (например, `formula_visible` у «Усилителя»).
- `fastmcp` и `pytest-anyio` **не объявлены** в `pyproject.toml` (там только `mcp>=1.0.0` и `pytest`) — при чистой установке `simintech-mcp` упадёт на импорте, а тесты MCP не соберутся. Установлены в текущем окружении как транзитивные.
- Линт: `flake8 --max-line-length=88 --extend-ignore=E203,W503` (держится на 0 ошибок). Конфиг-файла нет — параметры передаются флагом, см. `REPORT.md`.

## Команды

```bash
pip install -e ".[test]"        # ставит comtypes + fastmcp + pytest + anyio
python3.11 -m pytest tests/unit                                   # 108 тестов, без COM, работает и на Linux
python3.11 -m pytest tests/unit/test_mcp_server.py::test_all_tools_registered -q   # одиночный тест
python3.11 -m pytest tests/unit -m performance                    # бенчмарки placer/router
python3.11 -m pytest tests/unit/test_layout_placer.py -v          # весь файл

# Только Windows + mmain.exe /regserver:
python3.11 -m pytest tests/integration -m integration             # авто-skip вне Windows/без COM
python3.11 -m pytest tests/integration -m integration --run-simintech   # форсировать прогон

simintech-mcp                   # запуск MCP-сервера (stdio)
simintech-cli "create project \"M\"" "add block \"Константа\""   # CLI / интерактивный режим

# Только Windows + mmain.exe /regserver: сгенерировать каталог свойств блоков
python3.11 scripts/generate_block_catalog.py

flake8 simintech_api/ --max-line-length=88 --extend-ignore=E203,W503
```

Переменные окружения для очистки процессов после интеграционных тестов:
`SIMINTECH_KEEP_MMAIN=1` — не убивать ничего; `SIMINTECH_KILL_ALL_MMAIN=1` — убить **все** `mmain.exe`, включая запущенные вручную (осторожно).

## Архитектура

```
simintech_api/
├── constants.py      # DataType, PortSide, размеры, классы блоков, UNSUPPORTED_COM_BLOCK_CLASSES
├── model.py          # TDataDescriptor (comtypes Structure), SignalInfo, PortInfo
├── exceptions.py     # SimInTechError → ComConnectionError/ComCallError/ProjectError/BlockError/...
├── catalog.py        # BlockCatalog — имена свойств блоков (в COM их перечислить нельзя)
├── data/             # block_catalog.json — засеянный каталог свойств
├── core/             # COMClient, Project, Page, Block, Port, Wire, Signal, Simulation
├── layout/           # ObstacleGrid, LayeredPlacer (Sugiyama), AStarRouter (A*)
├── utils/            # converters, logger (Named Pipe), processes (mmain PIDs), xprt_signals (парсер .xprt)
├── agent.py          # SimInTechAgent — текстовые команды для ИИ-агента
└── cli.py            # simintech-cli (пакетный/интерактивный)

simintech_mcp/
└── server.py         # FastMCP: 20 инструментов + 2 ресурса + 2 промпта

scripts/
└── generate_block_catalog.py   # генерация каталога свойств (только Windows)

skills-catalog/       # доменные скиллы: model-building, simulation, language-core, library-curation
```

- **`COMClient.call(method, *args)`** — низкоуровневый доступ ко всем 176 методам `IMVTU_Server`; оборачивает любую ошибку в `ComCallError` с HRESULT. Типизированные обёртки (`Project`, `Page`, `Block`, ...) ходят только через него.
- **Слои**: `core/` даёт объектную модель поверх COM; `layout/` — чистые алгоритмы без COM, поэтому полностью тестируемы на Linux; `agent.py` и `simintech_mcp/server.py` — две независимые «оболочки для LLM» над одним и тем же `core/`.
- **`LayeredPlacer.place(block_ids, connections, sizes) -> {id: (cx,cy)}`** — слои по X, медианная эвристика, поддержка обратной связи и чистых циклов. **`AStarRouter.route(p1, p2, grid, start_side, end_side)`** — ортогональный A*; `[]` означает «прямое соединение», недостижимый путь → `LayoutError`.
- **Порты**: `GetPortInfo` возвращает (X, Y, XGlobal, YGlobal) — основа трассировки. `get_in_port`/`get_out_port` 0-based, с fallback через `BlockAfterEdit`.

### Особенности MCP-слоя (`simintech_mcp/server.py`)

- Состояние сессии живёт в **модульных глобалах** `_client` / `_project`: один клиент и один проект на процесс. `_ensure_client()` подключается лениво.
- **Контракт ошибок неоднороден.** Инструменты возвращают ошибки строкой `"ERROR: ..."`, но `_ensure_project()` при отсутствии проекта **бросает `ValueError`** — то есть при вызове без `create_project` часть инструментов (`add_block`, `run`, `get_block_params`, ...) не вернёт строку, а поднимет исключение. MCP-фреймворк превратит его в ответ об ошибке, так что агент ошибку увидит, но форма ответа другая. При правке инструментов сохраняйте этот момент в виду.
- `status()` и `layout_place()` полностью работоспособны без COM и на Linux — на них и построена проверка сервера в CI-подобном режиме. `get_block_params` / `set_block_param` работают без COM только с поддельным проектом (см. `tests/unit/test_mcp_server.py`).
- **`main()` вызывает `isolate_stdout()`** до старта транспорта: `_StdoutGuard` отводит `sys.stdout.write()` в stderr, оставляя `.buffer` настоящим — через него `mcp/server/stdio.py` пишет JSON-RPC. Это страховка: сейчас в MCP-пути ничто в stdout не печатает, но подключение `PipeLogger` (его `callback` по умолчанию делает `print`) сломало бы протокол молча.

## Документация

- `docs/guide.md`, `docs/api.md`, `docs/algorithms.md`, `docs/architecture.md`; `docs/source/` — Sphinx (`docs/Makefile`).
- `docs/simintech-language/` — справочник встроенного языка SimInTech (резервный путь создания блоков).
- `REPORT.md` — состояние интеграционных тестов, найденные на реальном COM баги и принятые решения; актуален на 2026-08-18.
- `examples/` — рабочие модели (усилитель, ПИД, layout+router, RC-цепь, обратная связь) и `demo.ipynb`.
- Внешние источники: `sitECRT/doc/reference/com_api_inventory.md`, `SIT_SimInTech_Vneshnij_API.pdf`, `source/exe/mmain.hpp`.
