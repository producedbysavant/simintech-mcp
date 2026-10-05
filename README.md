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

Инструмент берёт `@runtime.com_threaded`, если трогает COM, и
`@runtime.plain_tool`, если нет: декоратор даёт контракт отказа и запись в
журнал. Забыть его — значит молча потерять и то, и другое.

## Ограничения

- **Только Windows** — COM API SimInTech доступен лишь там. Требуется
  зарегистрированный COM-объект: `C:\SimInTech64\bin\mmain.exe /regserver`.
- **Сигналы читаются только там, где есть база сигналов.** Обмен идёт через
  список сигналов проекта и подключённую БД. У проекта без базы читать нечего:
  `list_signals` вернёт имена блоков с пометкой «не читается».
- **Концы линий через COM не читаются.** `list_wires` отдаёт количество и
  идентификаторы: в COM API нет методов для портов связи, а `Points` у линии пуст
  даже после нормализации. Обходной путь — выгрузка текста модели:
  `export_model_text` (встроенный язык через мост) описывает провода
  (`type = "wire"`, адреса концов `src = "block:out:N"` и
  `dst = "block:in:N"`), адрес ветви (`src = "wireName:K"`) и вложенные
  страницы. Форму линии выгрузка не несёт, а поле `script` в ней — временный
  скрипт моста, не скрипт проекта. Там же измерено и обратное ограничение:
  создавать объекты разрешено только в `initialization` скрипта страницы —
  поэтому инструменты языкового слоя (`import_model_text`, `run_page_script`,
  `inject_submodel_script`) и `disconnect_wire` ставят тело именно в эту
  секцию, а не под `if firststep then`, как старая проба.
- **Удаление БЛОКОВ не поддерживается.** `removeprimitiv` блок убирает, но
  следующий `ProjectStart` падает (access violation в `mbtylib.dll`; замер
  2026-09-29, воспроизведён на четырёх прогонах). Пока дефект не исправлен,
  модель правят заново, а не удалением. На **линии** дефект не
  распространяется: `disconnect_wire` снимает линию языковым путём, и среду
  это не роняет; но расчёт после снятия может **структурно стоять** — если
  вход остался без подключённой линии (в том числе когда вторая линия во
  входе осталась лишь объектом-сиротой). Вердикт в ответе это называет;
  возвращает расчёт новый `connect` (замеры 03.10.2026, приёмка).
- **Инструменты языкового слоя исполняют код, переданный клиентом.**
  `run_page_script`, `inject_submodel_script` и `import_model_text` принимают
  текст встроенного языка как есть (у `import_model_text` это декларативная
  запись модели — тоже код языка, не данные): это их назначение (иначе сбор данных и нестандартные операции
  выполнять нечем), а не недосмотр. Границы, которые сервер всё же держит: путь
  файла результата и файла сбора лежит **внутри каталога результатов**, число
  шагов расчёта ограничено таймаутом роста времени, прежний скрипт страницы
  возвращается на место, а ошибки различаются на пять исходов. Сам скрипт
  исполняется внутри `mmain.exe` с правами пользователя — ровно так же, как
  скрипт страницы, запущенный из GUI. Если политика требует меньшего, не
  вызывайте эти инструменты: сборку и выгрузку покрывают `import_model_text` и
  `export_model_text`.

## Установка

```bash
pip install -e ".[test]"
```

Зависимость `simintech-api` берётся из
[`simintech-code`](https://github.com/producedbysavant/simintech-code) по
**релизному тегу** выпуска (`@v0.13.0`). Неизменяемость тега держат три вещи
(политика владельца, 2026-10-01):

* ruleset `protect-release-tags` в репозитории библиотеки: удаление и
  перезапись тегов `v*` запрещены, bypass-акторов нет;
* **якорь** — комментарий `# sha: <коммит>` над строкой зависимости:
  `check_pins.py` сверяет с ним, во что тег разрешается сейчас, и отказывает
  «ЯКОРЬ НЕ СОВПАЛ», называя обе версии (подмена это или забытый при выпуске
  якорь — решает человек);
* **проверка содержимого**: `__version__` в разрешённом коммите обязан
  совпадать с именем тега — иначе повторяется прежняя ловушка (тег `v0.2.0`
  стоял на коде `0.1.0`, а имя выглядело новее библиотеки), которую якорь не
  ловит по построению.

Ветку `check_pins` отвергает — её двигают штатно; тег без якоря — тоже отказ,
ещё до сети; сбой связи не выдаётся за «мёртвый тег».
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
| Проекты | `create_project`, `open_project`, `reload_project`, `save_project`, `close_project`, `set_calc_time` |
| Пакет проектов | `create_pack`, `open_pack`, `close_pack`, `list_pack_projects`, `select_pack_project`, `pack_run`, `pack_step`, `pack_stop` |
| Настройки проекта | `get_project_config`, `set_project_config` |
| Блоки и связи | `add_block`, `connect`, `disconnect_wire`, `list_blocks`, `list_wires` |
| Параметры | `get_block_params`, `set_block_param`, `set_block_size` |
| Расчёт | `run`, `step`, `stop`, `get_time` |
| Сигналы | `list_signals`, `get_signal`, `set_signal`, `export_signal_db` |
| Результаты | `read_output_file`, `summarize_output_file` |
| Текст модели | `export_model_text`, `import_model_text` |
| Языковой слой | `get_page_script`, `set_page_script`, `get_block_script`, `set_block_script`, `run_page_script`, `inject_submodel_script` |
| Без COM (в т.ч. Linux) | `inspect_project_file`, `project_network_role` |
| Layout | `layout_place`, `fit_view`, `audit_routing`, `check_model_layout` |
| Снимок схемы | `save_screenshot` (кадр подгоняется сам; `fit=False` — текущий вид) |
| Справка | `help_text`, `search_language_functions`, `get_language_function` |

Всего инструментов — 54. Актуальный состав — всегда в `tools/list`; таблица
выше только для ориентира.

`create_project` создаёт проект **из шаблона** («Схема модели общего вида»):
проект из `NewProject` не считает — в нём нет расчётного слоя. Результат
удобнее всего снимать блоком «В файл» и читать `read_output_file`: он читает
только каталог результатов (`<временный каталог>/simintech-output`,
переопределяется `SIMINTECH_OUTPUT_DIR`), точный путь печатает `help_text`.

Ресурсы: `simintech://status`, `simintech://project/blocks`,
`simintech://blocks/catalog` (классы и имена параметров),
`simintech://language/functions` и `.../<имя>` (реестр функций встроенного
языка: имя, назначение, синтаксис, аргументы — поиск
`search_language_functions`, карточка `get_language_function`),
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
что закреплённый в `pyproject.toml` коммит или релизный тег `simintech-code`
существует в origin, что тег разрешается ровно в якорный коммит и что
`__version__` в нём совпадает с именем тега (историю библиотеки пересоздавали,
и мёртвый пин один раз уже уехал в `main`; ловушку «тег на чужом коммите»
якорь не ловит).

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
