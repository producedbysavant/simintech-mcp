# simintech-mcp

MCP-сервер (FastMCP) для управления средой динамического моделирования
**SimInTech** из ИИ-агента: создать проект, разместить блоки, соединить,
запустить расчёт, прочитать и записать сигналы.

Сервер — тонкая обёртка над библиотекой
[`simintech-api`](https://github.com/producedbysavant/simintech-code),
которая делает всю работу через внешний COM API (`IMVTU_Server`, сервер
`mmain.exe`). Здесь остаётся только MCP-слой.

## Структура

| Модуль | Что в нём |
|---|---|
| `app.py` | единственный экземпляр `FastMCP` |
| `runtime.py` | COM-поток, контракт отказа (`isError`), журнал вызовов |
| `session.py` | состояние сессии: клиент, проект, созданные линии |
| `sandbox.py` | песочница результатов и ограниченное чтение |
| `tables.py` | разбор числовых таблиц — чистый, без файловой системы |
| `catalog.py` | каталог блоков и проверка имён параметров до вызова COM |
| `skills.py` | скиллы из репозитория `simintech-skill` |
| `tools/` | инструменты по предметным областям |
| `resources.py`, `prompts.py` | ресурсы и промпты |
| `stdio.py` | защита stdout, в который пишет транспорт |
| `server.py` | сборка и точка входа |

Инструмент берёт `@_com_threaded`, если трогает COM, и `@_plain_tool`, если нет:
декоратор даёт контракт отказа и запись в журнал. Забыть его — значит молча
потерять и то, и другое.

## Ограничения

- **Только Windows** — COM API SimInTech доступен лишь там. Требуется
  зарегистрированный COM-объект: `C:\SimInTech64\bin\mmain.exe /regserver`.
- **Сигналы читаются только там, где есть база сигналов.** Обмен идёт через
  список сигналов проекта и подключённую БД. У проекта без базы читать нечего:
  `list_signals` вернёт имена блоков с пометкой «не читается».

## Установка

```bash
pip install -e ".[test]"
```

Зависимость `simintech-api` берётся из
[`simintech-code`](https://github.com/producedbysavant/simintech-code) по
коммиту выпуска `0.3.0` — **не по тегу `v0.3.0`**, который на него указывает:
ссылку на тег можно передвинуть, и та же строка зависимости начнёт разрешаться
в другой код молча. **Тег `v0.2.0` брать нельзя тем более**: он стоит на коде
с `__version__ = "0.1.0"`, где нет API, используемых сервером на уровне
импорта, — при этом имя тега выглядит новее самой библиотеки.
Для одновременной правки библиотеки и сервера замените её на path-зависимость:

```toml
simintech-api = { path = "../simintech-code", editable = true }
```

## Запуск

```bash
simintech-mcp                  # stdio
python -m simintech_mcp.server
```

Подключение к Claude Code:

```bash
claude mcp add simintech -- simintech-mcp
# или вручную: { "mcpServers": { "simintech": { "command": "simintech-mcp" } } }
```

## Инструменты

| Группа | Инструменты |
|---|---|
| Подключение | `status`, `disconnect` |
| Проекты | `create_project`, `open_project`, `save_project`, `close_project`, `set_calc_time` |
| Настройки проекта | `get_project_config`, `set_project_config` |
| Блоки и связи | `add_block`, `connect`, `list_blocks`, `list_wires` |
| Параметры | `get_block_params`, `set_block_param` |
| Расчёт | `run`, `step`, `stop`, `get_time` |
| Сигналы | `list_signals`, `get_signal`, `set_signal`, `export_signal_db` |
| Результаты | `read_output_file`, `summarize_output_file` |
| Без COM (в т.ч. Linux) | `inspect_project_file`, `project_network_role` |
| Layout | `layout_place` |
| Справка | `help_text` |

Всего инструментов — 29. Актуальный состав — всегда в `tools/list`; таблица
выше только для ориентира.

`create_project` создаёт проект **из шаблона** («Схема модели общего вида»):
проект из `NewProject` не считает — в нём нет расчётного слоя. Результат
удобнее всего снимать блоком «В файл» и читать `read_output_file`: он читает
только каталог результатов (`<временный каталог>/simintech-output`,
переопределяется `SIMINTECH_OUTPUT_DIR`), точный путь печатает `help_text`.

Ресурсы: `simintech://status`, `simintech://project/blocks`,
`simintech://blocks/catalog` (классы и имена параметров),
`simintech://skills` и `simintech://skills/<имя>` (скиллы из `simintech-skill`).
Промпты: `create_pid_model`, `create_rc_chain`.

## Важное про параметры блоков

Имена параметров короткие и неочевидные: у «Константы» — `a` (не `y0`),
у «Сумматора» — `a` (число входов задаётся длиной массива, параметра `xn` нет).

`SetBlockProp` **не отвергает неизвестное имя**: запись в несуществующий
параметр проходит без ошибки и ни на что не влияет. Поэтому сервер сверяет
имена с каталогом, сгенерированным из реального SimInTech, **до** записи:
неизвестное имя или вычисляемый параметр — отказ со списком известных имён.
Полный список — в ресурсе `simintech://blocks/catalog` и в `get_block_params`.
Если параметр существует, но в каталог не попал, — `allow_unknown=True`
(`allow_unknown_props=True` у `add_block`).

## Работа без SimInTech

Расчёт идёт только на Windows, но сохранённый проект (`.xprt`) разбирается
где угодно: `inspect_project_file` показывает классы блоков, их параметры и
имена блоков. Файл — как и результаты расчёта — должен лежать в каталоге
результатов (`SIMINTECH_OUTPUT_DIR`).

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `SIMINTECH_OUTPUT_DIR` | каталог, из которого разрешено читать результаты и проекты |
| `SIMINTECH_SKILLS_DIR` | каталог `skills-catalog` репозитория `simintech-skill` |
| `SIMINTECH_MCP_LOG` | журнал вызовов: `stderr` или путь к файлу (по умолчанию выключен) |

## Тестирование

```bash
python3.11 -m pytest tests/unit -q     # без COM, работает и на Linux
flake8 simintech_mcp tests scripts --max-line-length=88 --extend-ignore=E203,W503
mypy
```

`scripts/check_pins.py` — гейт целостности зависимости: до установки проверяет,
что закреплённый в `pyproject.toml` коммит `simintech-code` существует в origin
(историю библиотеки пересоздавали, и мёртвый пин один раз уже уехал в `main`).

Тесты разложены по тем же границам, что и код: `tests/unit/test_<область>.py`,
общие фейки — в `tests/unit/_support.py`. То же проверяет CI
(`.github/workflows/ci.yml`) на push в `main` и на каждый pull request.

Интеграционные тесты живут в `simintech-code` — они проверяют библиотеку и COM.

## Связанные репозитории

- [`simintech-code`](https://github.com/producedbysavant/simintech-code) —
  библиотека `simintech-api`, примеры, справочник встроенного языка SimInTech.
- [`simintech-skill`](https://github.com/producedbysavant/simintech-skill) —
  доменные знания (скиллы) для агента.

## Документация

- [Официальная справка SimInTech](https://help.simintech.ru/) — первоисточник.
  Ключевые разделы: [API](https://help.simintech.ru/27_SimInTech_api/DIR_api.html),
  [командная строка](https://help.simintech.ru/27_SimInTech_api/DIR_komandnaya_stroka.html).
- `docs/roadmap-agentic-ecosystem.md` — состояние экосистемы и планы.
