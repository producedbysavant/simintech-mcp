# Дизайн: разделение проекта SimInTech на три репозитория

**Дата:** 2026-09-10
**Статус:** утверждено
**Область:** структура репозиториев, зависимости, общие файлы, инструменты сборки,
роспуск `simintech-connector`, чтение сигналов через базу данных

## 1. Задача

Разделить проект на три репозитория под общим именем `simintech`:

| Репозиторий | Назначение |
|---|---|
| `simintech-code` | Python-библиотека управления SimInTech + знаниевый контент |
| `simintech-mcp` | MCP-сервер (FastMCP), обёртка над библиотекой |
| `simintech-skill` | Скиллы (доменные знания для ИИ-агента) |

Во всех трёх — одинаковые правила git и одинаковый набор файлов конфигурации.

Отдельно: локальная папка `simintech-connector` (устаревшая) распускается по
системе и удаляется.

## 2. Исходное состояние (проверено)

- Единый репозиторий `simintech-mcp` содержит **два Python-пакета** в одном
  `pyproject.toml`: `simintech_api/` (библиотека) и `simintech_mcp/` (сервер),
  плюс `skills-catalog/`, `docs/`, `examples/`, `tests/`.
- `simintech-code-library` — контентный репозиторий (`blocks/`, `language/`,
  `patterns/`, `tutorials/`, `automation/`), **не** Python-пакет. Уже содержит
  эталонный набор общих конфигов.
- `simintech-connector` — **не git-репозиторий** (нет `.git`), на GitHub
  отсутствует (404). Папка с кодом на 2169 строк.
- MCP-сервер использует из библиотеки всего **4 символа**: `COMClient`,
  `Project`, `load_default_catalog`, `LayeredPlacer` — контракт узкий.
- Скиллы не имеют установочной связности (только markdown и yaml).

## 3. Границы репозиториев

### 3.1. `simintech-code`

Переименование `simintech-code-library` → `simintech-code` с добавлением
библиотеки.

Приезжает из `simintech-mcp`:

- `simintech_api/` целиком — включая `core/`, `layout/`, `utils/`, `catalog.py`,
  `data/block_catalog.json`, `cli.py`, `agent.py`, `constants.py`, `model.py`,
  `exceptions.py`;
- `examples/` (все примеры библиотеки; MCP-примеры остаются в README сервера);
- `tests/unit/` — все файлы, кроме `test_mcp_server.py` и
  `test_skills_catalog.py`;
- `tests/integration/`, `tests/conftest.py`;
- `docs/guide.md`, `docs/api.md`, `docs/algorithms.md`, `docs/architecture.md`,
  `docs/source/` (Sphinx), `docs/reference/com_api_inventory.md`.

Уже есть в репозитории: `blocks/`, `language/`, `patterns/`, `tutorials/`,
`automation/`, общие конфиги.

**Дедупликация.** `docs/simintech-language/` в `simintech-mcp` — это копия
корневых `blocks/`, `language/`, `patterns/`, `tutorials/` из
`simintech-code-library`, перенесённая туда ранее. Канонический экземпляр —
корневой, в `simintech-code`. Копия из `simintech-mcp` удаляется, а не
переезжает: иначе после разделения останутся две расходящиеся копии одного
текста. Ссылки на справочник в `simintech-code` указывают на собственные
каталоги.

Приезжает из `simintech-connector` (см. §6):

- `simintech_api/sdb.py` ← `sdb_adapter.py`;
- `simintech_api/cli_runner.py` ← `cli_adapter.py`.

Entry points: `simintech-cli` (`simintech_api.cli:main`) и генератор каталога
(повышается из `scripts/generate_block_catalog.py` до entry point пакета, чтобы
скиллы и MCP не зависели от структуры каталогов).

### 3.2. `simintech-mcp`

Остаётся с текущим именем. После разделения содержит:

- `simintech_mcp/server.py`;
- `tests/unit/test_mcp_server.py`;
- собственные `README.md`, `CLAUDE.md`, `pyproject.toml`, общие конфиги.

Entry point: `simintech-mcp`. Зависимость — на `simintech-code`.

### 3.3. `simintech-skill`

Новый репозиторий:

- `skills-catalog/` → 4 скилла (`SKILL.md` + `manifest.yaml`):
  `simintech-model-building`, `simintech-simulation`,
  `simintech-language-core`, `simintech-library-curation`;
- `tests/unit/test_skills_catalog.py`.

Знаниевый контент (`blocks/`, `language/`, `patterns/`, `tutorials/`,
`automation/`, `docs/simintech-language/`) **в этот репозиторий не переезжает**:
он живёт только в `simintech-code`, скиллы ссылаются на него по URL.

### 3.4. Что где остаётся спорным

- `REPORT.md` — смешанный (находки по COM и по MCP). COM-часть → в
  `simintech-code`, MCP-часть → в `simintech-mcp`. Разнести при миграции.
- `docs/roadmap-agentic-ecosystem.md` — сквозной документ, «своего» репозитория
  нет. Оставить в `simintech-mcp` как в интеграционной точке; при появлении
  зонтичного репозитория перенести туда.
- `docs/build/` — генерируемый, уже в `.gitignore`, в новые репозитории не
  переносить.

## 4. Зависимости

```
simintech-skill  - - - >  simintech-code     документальная (версия + checksum каталога)
simintech-mcp    ------->  simintech-code     установочная (install-time)
```

- **Локальная разработка:** editable path-зависимость
  (`simintech-api = { path = "../simintech-code", editable = true }`).
- **CI и релизы:** git-зависимость с тегом
  (`simintech-api @ git+https://github.com/producedbysavant/simintech-code@v0.1.0`).
  Тег обязателен — привязка к ветке даёт неповторяемые сборки.
- **PyPI не используем:** библиотека Windows-only (`comtypes`), публикация
  усложнит установку на Linux при малой аудитории.
- **Git submodule не используем:** Python-пакет как сабмодуль создаёт проблемы
  при установке и в CI, не даёт версионной привязки.
- **Скиллы:** в `manifest.yaml` фиксируются `requires.library_version` и
  checksum каталога вместо относительных путей — после разделения относительные
  пути станут ложными.

## 5. Общие файлы и правила

### 5.1. Идентичны во всех трёх репозиториях

| Файл | Примечание |
|---|---|
| `LICENSE` | MIT — **сейчас файла нет**, создать |
| `.gitattributes` | критично: нормализация LF против churn между Windows и WSL |
| `.editorconfig` | единый стиль (LF, utf-8, Python — отступ 4) |
| `.gitignore` | единый, с `.remember/`, `.claude/`, кэшами инструментов |
| `CONTRIBUTING.md` | правила ветвления, коммитов, PR |
| `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/*` | |

Эталон — файлы из `simintech-code-library` (там они уже проработаны).

### 5.2. Специфичны для репозитория

`README.md`, `CLAUDE.md`, `pyproject.toml`, конфигурация CI.

`CLAUDE.md` **не копируется целиком**: инструкции агенту специфичны для
репозитория. Общим может быть только блок конвенций.

### 5.3. Механизм синхронизации

Выбран вариант **без автоматизации**: разложить общий набор один раз и править
по мере необходимости. Шаблонный репозиторий с `copier` и reusable workflows
даёт больше инфраструктуры, чем экономии при трёх репозиториях и низкой частоте
изменений. Механизм вводится, когда расхождение станет реальной проблемой.

## 6. Роспуск `simintech-connector`

`ARCHIVED.md` содержит требование «не удалять без согласования» — согласование
получено. Распределение наработок:

| Файл | Судьба | Обоснование |
|---|---|---|
| `sdb_adapter.py` (315) | → `simintech-code`, `simintech_api/sdb.py` | доступ к БД сигналов (Category → Group → Signal) в библиотеке отсутствует. **Основа для чтения сигналов** — см. §10 |
| `cli_adapter.py` (210) | → `simintech-code`, `simintech_api/cli_runner.py` | запуск `mmain.exe` из WSL, декодирование cp866; открывает CLI-путь и `run_macro` из дорожной карты |
| `run_macro.py` (46) | → `examples/` в `simintech-code` | демонстрация запуска макроса с потоковым логом |
| `run_pak.py` (33) | → `examples/` в `simintech-code` | запуск `.pak` |
| `models.py` (52) | сверить с `simintech_api.model`; при перекрытии удалить | `ProjectState`, `DataType`, `SimulationResult` частично дублируются |
| `com_adapter.py` (256) | удалить | перекрыт `simintech_api.core` (comtypes вместо pywin32) |
| `server.py` (879) | удалить | перекрыт `simintech_mcp` |
| `examples/basic_usage.py` (86) | удалить после проверки | использует `com_adapter` |
| `.ruff_cache/`, `.remember/`, `.claude.json` | удалить | локальные артефакты |

После переноса папка `C:\git\simintech-connector` удаляется целиком.
Репозиторий на GitHub удалять не нужно — его не существует.

**Статус переноса (2026-09-10, план подготовки выполнен):**

| Файл | Состояние |
|---|---|
| `sdb_adapter.py` | ✅ перенесён в `simintech_api/sdb.py`, 4 теста |
| `cli_adapter.py` | ✅ перенесён в `simintech_api/cli_runner.py`, 9 тестов; `SimulationResult` → `CLIResult` |
| `run_macro.py`, `run_pak.py` | ✅ переписаны в `examples/` без жёстких путей |
| `models.py` | ✅ разобран: `DataType` дублирует `constants`, `SignalInfo` конфликтует именем с `model.SignalInfo`, `ProjectState`/`ProjectInfo` нигде не используются |
| `com_adapter.py`, `server.py`, `examples/basic_usage.py` | ⬜ удаляются вместе с папкой в плане 2 |
| `.ruff_cache/`, `.remember/`, `.claude.json` | ⬜ удаляются вместе с папкой в плане 2 |

## 7. Инструменты сборки

- **Метаданные:** PEP 621 (`[project]`) как единственный источник.
- **Бэкенд:** `hatchling` вместо `setuptools`. Причина: `hatchling` включает
  package-data автоматически и не требует явного списка пакетов — сейчас в
  `pyproject.toml` перечислены 5 пакетов, и при перемещении файлов этот список
  молча ломается.
- **Менеджер окружения:** `uv` (быстрее в WSL, ставит нужный Python, умеет
  editable path-зависимости и платформенные маркеры). Poetry — допустимая
  альтернатива, если привычнее; как публикатор не нужен, поскольку PyPI не
  планируется.
- **Консольные скрипты:** `simintech-cli` → `simintech-code`,
  `simintech-mcp` → `simintech-mcp`.

Дефекты, которые чинятся независимо от разделения:

| Дефект | Расположение | Правка |
|---|---|---|
| `LICENSE` отсутствует | корень | создать (MIT) |
| `comtypes` без маркера платформы | `pyproject.toml:14` | `comtypes>=1.1.0; sys_platform == 'win32'` |
| `requires-python` недостижим | `pyproject.toml:10` | `>=3.11` |
| Устаревшая строка про `fastmcp` | `CLAUDE.md:32` | `fastmcp` уже объявлен — утверждение неверно |

## 8. План миграции

**Подготовка (в текущем состоянии, до разделения):**

1. Добавить `LICENSE`, `.gitattributes`, `.editorconfig`; расширить `.gitignore`.
2. Починить маркер `comtypes`, поднять `requires-python`, поправить `CLAUDE.md`.
3. Перевести генератор каталога в entry point пакета.
4. Убедиться, что тесты зелёные; поставить тег `v0.1.0` как точку отката.

**Перенос кода:**

5. Перенести `sdb_adapter.py` и `cli_adapter.py` из `connector` в библиотеку,
   покрыть тестами (без COM — на разборе XML и на формировании команды).
5a. ~~Проверить чтение через базу сигналов~~ — **выполнено** 2026-09-10.
   Допущение подтвердилось, попутно найден и устранён дефект подмены
   дескриптора (коммит `0f9d818`). Результат — в §10.
6. Переименовать GitHub `simintech-code-library` → `simintech-code`; перевезти
   туда `simintech_api/`, `examples/`, тесты, документацию библиотеки.
7. Очистить `simintech-mcp` от библиотеки и скиллов.
8. Создать `simintech-skill`, перенести `skills-catalog/` и
   `test_skills_catalog.py`, поправить ссылки в манифестах на версию и checksum.
9. Разнести `REPORT.md` по частям.
10. Удалить из `simintech-mcp` дубликат `docs/simintech-language/` (§3.1) —
    канонический экземпляр остаётся в `simintech-code`.

**Перевязка:**

11. Новые `pyproject.toml` (hatchling, PEP 621), зависимость MCP на
    `simintech-code` по git-тегу, editable-path для локальной разработки.
12. Разложить общие конфиги во все три репозитория.
13. Поправить ссылки в документации и манифестах, которые после разделения
    станут ложными; покрыть тестом.

**Завершение:**

14. Удалить локальную папку `simintech-connector`.
15. Приёмка: `pytest tests/unit` на Linux во всех трёх; чистая установка на
    Windows; JSON-RPC-хендшейк `simintech-mcp` через stdio.

## 9. Риски

| Риск | Оценка |
|---|---|
| Переписывание истории затрагивает публичный `simintech-mcp` | Репозиторий создан 2026-09-10, история своя и свежая — допустимо. Форков и внешних ссылок нет |
| Ссылки в документации и манифестах после разделения ложные | Ломаются молча — покрыть тестом после переноса |
| Рассинхрон версий библиотеки и MCP | Лечится пином по тегу; при появлении drift добавить Renovate |
| Тесты, завязанные на пути (`parents[2]/"skills-catalog"`) | Сломаются молча — проверить явно |
| Устаревание снимка каталога у скиллов | Фиксировать версию генератора и checksum, перегенерировать при релизе библиотеки |
| Интеграционные тесты требуют Windows и SimInTech | В публичном CI не автоматизируются — остаются ручными |

## 10. Чтение сигналов — решено, отдельной работы не требует

Раздел переписан по результатам проверки 5a (2026-09-10). Исходное допущение
подтвердилось, но причина неработающего чтения оказалась **не той**, что
предполагалась.

**Что выяснилось:**

1. Допущение о базе верно: у проекта с подключённой базой `list_signals()`
   возвращает настоящие сигналы (`source="com"`, `readable=True`), а
   `ReadAsFloat` читает значения. Проверено на `sar/SAR/2.1.regulator.prt`
   (база `signals.db`).
2. Но чтение не работало и **у проекта с базой** — из-за нашего дефекта:
   `_to_descriptor` оборачивал родной дескриптор comtypes в наш одноимённый
   `TDataDescriptor`, и comtypes отвергал его («expected TDataDescriptor
   instance instead of TDataDescriptor»). Дефект **устранён** (коммит
   `0f9d818`), чтение и запись проверены сквозь MCP.

**Следствие для этой работы:** отдельной задачи «прикрутить чтение через базу»
нет. `sdb_adapter.py` (§6) остаётся полезным для работы с файлом базы, но
выгрузку результатов больше не блокирует.

**Что остаётся открытым (вне этой работы):** проект, созданный с нуля через
`Project.new()`, базы не имеет, поэтому сигналов у него нет. Это задача уровня
«создавать проект с базой», а не чтения; в план не входит.

## 11. Что не входит в эту работу

- Чтение сигналов — решено, см. §10.
- Создание проектов с подключённой базой сигналов (см. §10, последний абзац).
- Выгрузка результатов макросами (`run_macro.py` → `SiT-*.txt`) — путь
  отвергнут; сами примеры из `connector` переносятся (§6), но развивать этот
  путь не планируется.
- Публикация скиллов в ClawHub, IDE-плагин, шаблоны проектов.
- Автоматизация синхронизации общих файлов (сознательно отложена, §5.3).
