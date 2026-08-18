# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

**simintech-api** — Python-библиотека программного управления SimInTech через внешний COM API (`IMVTU_Server`, сервер `mmain.exe`). Позволяет ИИ-агенту создавать проекты, размещать блоки, соединять их, запускать расчёт и обмениваться сигналами. Рядом в `/mnt/c/git/` — смежные наработки: `sitECRT` (тестовый тулкит, справочники COM API), `simintech-connector` (MCP-обёртка), `evs360_simintech` (модели бортовой логики).

## Ключевые факты

- **COM работает только на Windows**; требует `mmain.exe /regserver`. В WSL/Linux тесты COM невозможны — пишутся unit-тесты с фейковым сервером (`tests/unit/test_com_client.py`, подмена `comtypes` в `sys.modules`).
- **COM-движок — comtypes, не pywin32**: структура `TDataDescriptor` (`VT_RECORD`) маршалится только через comtypes.
- **DataType**: 0=double, 1=int, 2=bool, 4=string, 5=array(double[]), 12=intarray. Все Read/Write принимают `TDataDescriptor` по значению.
- **Два механизма создания моделей**: COM API (`CreateBlock`, имя класса русской строкой) и встроенный язык SimInTech (`createblock`/`createmodel` — в блоке «Язык программирования»). Часть классов («Из памяти», «Порт выхода», «Флаг входа в состояние») **не создаётся через COM** — см. `simintech_api/constants.py:UNSUPPORTED_COM_BLOCK_CLASSES`.
- **Сборка на Windows**: `pip install -e ".[test]"`. Запуск тестов: `python -m pytest tests/unit` (без COM) и `python -m pytest tests/integration -m integration` (Windows).
- Python в окружении: `python3.11` (pytest), `python3` = 3.14 (без тестовых пакетов). comtypes на Linux падает при импорте — это нормально, тесты подменяют его фейком.

## Архитектура

```
simintech_api/
├── constants.py      # DataType, PortSide, размеры, классы блоков
├── model.py          # TDataDescriptor (comtypes Structure), SignalInfo, PortInfo
├── exceptions.py     # иерархия (SimInTechError → ComCallError, SignalError, ...)
├── core/             # COMClient, Project, Page, Block, Port, Wire, Signal, Simulation
├── layout/           # ObstacleGrid, LayeredPlacer (Sugiyama), AStarRouter (A*)
├── utils/            # converters, logger (Named Pipe перехват лога)
├── agent.py          # SimInTechAgent — текстовые команды для ИИ-агента
└── cli.py            # simintech-cli (пакетный/интерактивный)
```

- **COMClient.call(method, *args)** — низкоуровневый доступ ко всем 176 методам `IMVTU_Server`.
- **Layout**: `LayeredPlacer.place(block_ids, connections, sizes) -> {id: (cx,cy)}` — слои по X, медианная эвристика, поддержка обратной связи. `AStarRouter.route(p1, p2, grid, start_side, end_side)` — ортогональный A*, `[]` = прямое соединение.
- **Порты**: `GetPortInfo` возвращает координаты порта (X, Y, XGlobal, YGlobal) — основа для трассировки. `get_in_port`/`get_out_port` — 0-based, есть fallback через `BlockAfterEdit`.
- **SimInTechAgent** — парсер команд (create project / add block / connect / run / get signal), единая точка входа для LLM. Список команд: `agent.execute("help")`.

## Документация

- `docs/guide.md` — руководство пользователя.
- `docs/api.md` — API Reference.
- `docs/algorithms.md` — алгоритмы layout/трассировки.
- `docs/architecture.md` — архитектура и решения.
- `examples/` — RC-цепь, ПИД, модель с обратной связью.
- Внешние источники: `sitECRT/doc/reference/com_api_inventory.md` (точная карта методов), `SIT_SimInTech_Vneshnij_API.pdf`, `source/exe/mmain.hpp`.
