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
- **Отказ приходит как `isError`, а не текстом.** `_com_threaded` (через
  `_call_guarded`) превращает и исключение, и строку `"ERROR: …"` в
  `ToolError` — только по нему MCP выставляет `isError`. Это принуждается
  кодом в одном месте: новый инструмент получает контракт автоматически,
  дописывать `isinstance`-проверки не нужно. Если функция возвращает текст
  **не** вида «ERROR: …», он считается успехом — не начинайте сообщения об
  отказе с этого префикса «для красоты».
- **Таймаут COM обязателен.** `_com_threaded` ждёт ответ не дольше
  `COM_CALL_TIMEOUT` (120 с) и возвращает отказ вместо подвисания. Оговорка:
  сам COM-вызов в потоке не прерывается, поэтому после срабатывания таймаута
  сервер пригоден только до перезапуска `mmain.exe` — инструмент об этом
  сообщает.
- **Состояние сессии имеет владельца.** `create_project`/`open_project` идут
  через `_replace_project`, который закрывает предыдущий проект (иначе они
  копились бы внутри `mmain.exe`); `disconnect()` сбрасывает и проект, и
  клиент. Новые инструменты, меняющие текущий проект, должны использовать
  `_replace_project`, а не писать в `_project` напрямую.
- **Имена параметров отражают возможности среды.** Там, где имя не
  применяется, оно так и называется: `create_project(project_hint=…)`,
  `add_block(name_hint=…)`. Имя блока адресуется параметром `block`
  (`get_block_params`, `set_block_param`, `get_signal`, `set_signal`) и всегда
  автоматическое (`k_0`, `kx_0`) — его даёт `list_blocks`.
- **`read_output_file` читает только каталог результатов — всегда.**
  Это не опция: инструмент принимает путь от клиента, а клиент может быть без
  доступа к файловой системе, поэтому по умолчанию действует песочница
  `output_root()` — ``SIMINTECH_OUTPUT_DIR``, иначе
  ``<временный каталог>/simintech-output`` (создаётся). Относительный путь
  ищется внутри неё, `realpath` выполняется до проверки, поэтому `..` и
  символические ссылки наружу не выводят; если переменная задана, но каталога
  нет — ошибка конфигурации и отказ (fail closed). Блок «В файл» обязан писать
  внутрь этого каталога. Объём ответа ограничен `MAX_OUTPUT_BYTES`. Текущий
  каталог печатает `help_text`.
  Место предсказуемо, поэтому стандартный каталог создаётся через `os.mkdir`
  (без `exist_ok`, права 0o700) и **подмена его символической ссылкой
  отвергается** — иначе `realpath` увёл бы песочницу в выбранное атакующим
  место. Каталог, заданный `SIMINTECH_OUTPUT_DIR`, — явный выбор пользователя,
  он канонизируется через `realpath` без этой проверки.
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
