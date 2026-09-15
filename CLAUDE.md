# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

MCP-сервер (FastMCP) для управления SimInTech из ИИ-агента. Тонкая обёртка над
библиотекой `simintech-api`, которая делает всю работу через COM API
(`IMVTU_Server`, сервер `mmain.exe`). Здесь — только MCP-слой: один модуль
`simintech_mcp/server.py`.

Точка входа: `simintech-mcp` → `simintech_mcp.server:main` (stdio).

Смежные репозитории: `simintech-code` (библиотека и знаниевый контент),
`simintech-skill` (скиллы). Всё, что касается COM, блоков, каталога параметров
и layout-алгоритмов, живёт в `simintech-code` — здесь этого кода нет.

## Ключевые факты

- **COM работает только на Windows**, требует `mmain.exe /regserver`. Unit-тесты
  идут без COM (`python3.11 -m pytest tests/unit`, работает и на Linux).
- **COM привязан к потоку** — две ловушки, обе пойманы на реальном SimInTech:
  - `CoInitializeEx` вызывается для текущего потока в `COMClient.connect()`
    (это в библиотеке). Без него вызов из рабочего потока падает с
    `CO_E_NOTINITIALIZED`.
  - COM-объект из чужого потока даёт `CO_E_OBJNOTCONNECTED`. Поэтому
    `server.py` выполняет **все** COM-вызовы через один выделенный поток
    (`_COM_EXECUTOR`, `max_workers=1`, декоратор `_com_threaded`): FastMCP
    запускает синхронные инструменты в пуле и чередует потоки, что без этого
    ломает сервер. Любой новый инструмент обязан быть под `@_com_threaded`.
- **`SetBlockProp` не отвергает неизвестное имя параметра** — отказ молчаливый.
  Имена короткие и неочевидные (`a` у «Константы», а не `y0`); `set_block_param`
  предупреждает, если имени нет в каталоге.
- **`SetBlockProp("Name", ...)` не переименовывает блок** — имя остаётся
  автоматическим (`k_0`). `add_block` сообщает об этом явно, иначе `connect`
  по заданному имени молча не найдёт блок.
- **Сигналы читаются только у проекта с базой сигналов.** `list_signals`
  различает записи по `source`: `com` — читаемые, `xml` — имена блоков
  (читать нельзя). Пустая база — не ошибка, а отсутствие данных.
- **`main()` вызывает `isolate_stdout()`** до старта транспорта: `_StdoutGuard`
  отводит `sys.stdout.write()` в stderr, оставляя `.buffer` настоящим — через
  него `mcp/server/stdio.py` пишет JSON-RPC.
- **Контракт ошибок неоднороден.** Инструменты возвращают ошибки строкой
  `"ERROR: ..."`, но `_ensure_project()` при отсутствии проекта **бросает
  `ValueError`**. MCP-фреймворк превратит его в ответ об ошибке, но форма
  ответа другая. При правке инструментов это надо иметь в виду.
- **`read_output_file` читает файл по пути от клиента.** Ограничение —
  переменная окружения `SIMINTECH_OUTPUT_DIR`: если задана, читать можно только
  внутри этого каталога (`realpath` до проверки, поэтому `..` и ссылки не
  помогают). Без неё путь не ограничивается — у вызывающего агента обычно и так
  есть доступ к файловой системе. Задавайте переменную, если сервер подключён к
  клиенту без такого доступа. Объём ответа ограничен `MAX_OUTPUT_BYTES`.
- Линт: `flake8 --max-line-length=88 --extend-ignore=E203,W503` (0 ошибок).

## Команды

```bash
pip install -e ".[test]"        # ставит simintech-api по git-тегу + fastmcp + pytest
python3.11 -m pytest tests/unit -q                # без COM, работает и на Linux
python3.11 -m pytest tests/unit/test_mcp_server.py::test_all_tools_registered -q

simintech-mcp                   # запуск сервера (stdio)
flake8 simintech_mcp/ --max-line-length=88 --extend-ignore=E203,W503
```

## Зависимость на библиотеку

Объявлена прямой git-ссылкой на тег:

```
simintech-api @ git+https://github.com/producedbysavant/simintech-code@v0.2.0
```

Для одновременной правки библиотеки и сервера заменить на path-зависимость
(`simintech-api = { path = "../simintech-code", editable = true }`).

Внимание: hatchling запрещает прямые ссылки по умолчанию — в `pyproject.toml`
обязателен `[tool.hatch.metadata] allow-direct-references = true`, иначе сборка
падает с `Dependency #1 ... cannot be a direct reference`.

## Документация

- **Официальная справка SimInTech: https://help.simintech.ru/** — брать отсюда.
  [API](https://help.simintech.ru/27_SimInTech_api/DIR_api.html),
  [командная строка](https://help.simintech.ru/27_SimInTech_api/DIR_komandnaya_stroka.html).
- `docs/roadmap-agentic-ecosystem.md` — состояние экосистемы, найденные дефекты
  и порядок работ.
- `docs/superpowers/specs/` и `docs/superpowers/plans/` — спецификация и планы
  разделения на три репозитория.
