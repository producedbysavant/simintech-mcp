# Разделение на три репозитория — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разложить проект по трём репозиториям — `simintech-code` (библиотека и знаниевый контент), `simintech-mcp` (сервер), `simintech-skill` (скиллы) — и удалить устаревший `simintech-connector`.

**Architecture:** Библиотека переезжает в переименованный `simintech-code-library`; `simintech-mcp` остаётся тонкой обёрткой и начинает зависеть от библиотеки по git-тегу; скиллы уезжают в отдельный репозиторий. Общие файлы (лицензия, gitattributes, editorconfig, gitignore) раскладываются одинаково во все три.

**Tech Stack:** Python 3.11+, PEP 621, hatchling, pytest, GitHub API (gh CLI не установлен — используется токен из `~/.config/gh/hosts.yml`).

**Область:** спецификация `docs/superpowers/specs/2026-09-10-simintech-repos-design.md`, §3–§5, §7, §8 (шаги 6–15).

**Предусловие:** план 1 (`docs/superpowers/plans/2026-09-10-repo-split-prep.md`) выполнен. Исполнять либо после слияния PR #1 в `main`, либо от ветки `chore/repo-split-prep` — решается перед началом.

---

## Структура файлов

| Репозиторий | Что содержит | Откуда |
|---|---|---|
| `simintech-code` | `simintech_api/`, `examples/`, библиотечные тесты и документация, знаниевый контент (`blocks/`, `language/`, `patterns/`, `tutorials/`, `automation/`) | переименованный `simintech-code-library` + переезд из `simintech-mcp` |
| `simintech-mcp` | `simintech_mcp/`, `tests/unit/test_mcp_server.py`, README, CLAUDE.md | остаётся, теряет лишнее |
| `simintech-skill` | `skills-catalog/*`, `tests/unit/test_skills_catalog.py` | новый, переезд из `simintech-mcp` |

**Разделение тестов по репозиториям:**

| Файл | Куда |
|---|---|
| `test_mcp_server.py` | `simintech-mcp` |
| `test_skills_catalog.py` | `simintech-skill` |
| остальные 13 файлов `tests/unit/*` | `simintech-code` |
| `tests/integration/`, `tests/conftest.py` | `simintech-code` |

**Разделение документации:**

| Что | Куда |
|---|---|
| `docs/guide.md`, `api.md`, `algorithms.md`, `architecture.md`, `source/`, `reference/` | `simintech-code` |
| `docs/roadmap-agentic-ecosystem.md`, `docs/superpowers/` | `simintech-mcp` |
| `docs/simintech-language/` | **удалить** — дубликат корневых `blocks/language/patterns/tutorials` из `simintech-code` (§3.1 спецификации) |
| `REPORT.md` | `simintech-code` целиком; разнесение COM- и MCP-части **не делается** — см. «Отступления» |

### Отступления от спецификации

Два пункта спецификации сознательно упрощены — оба про экономию без потери результата:

1. **`REPORT.md` не разносится по частям.** Спецификация §3.4 предлагала отделить
   COM-находки (в `simintech-code`) от MCP-наблюдений (в `simintech-mcp`).
   Файл — исторический отчёт о разработке, и резать его по живому ради двух
   репозиториев дороже, чем польза: он и так читается целиком. Копируется в
   `simintech-code` как есть; при следующем обновлении часть про MCP можно
   вынести отдельно.
2. **Менеджер окружения — `pip`, а не `uv`.** Спецификация §7 называла `uv`.
   Оба окружения (Linux и Windows) уже настроены на `pip`, а выигрыш `uv` —
   скорость установки и единый lock — для трёх маленьких репозиториев не
   окупает переучивание и правку инструкций. При росте числа зависимостей
   решение стоит пересмотреть.

**Вспомогательные команды.** `gh` не установлен, поэтому GitHub-операции идут через API. Токен:

```bash
GH_TOKEN=$(python3.11 -c "import re;print(re.search(r'oauth_token:\s*(\S+)',open('/home/a-savchenko/.config/gh/hosts.yml').read()).group(1))")
```

Для `git push` используется временный credential helper (токен не попадает в `.git/config`):

```bash
git -c credential.helper='' \
    -c credential.helper='!f(){ echo username=x-access-token; echo password=$GH_TOKEN; }; f' \
    push origin <branch>
```

---

## Task 1: Переименовать `simintech-code-library` → `simintech-code`

**Files:**
- Modify: локальная копия `/mnt/c/git/simintech-code-library` (remote)

- [ ] **Step 1: Переименовать репозиторий на GitHub**

```bash
GH_TOKEN=$(python3.11 -c "import re;print(re.search(r'oauth_token:\s*(\S+)',open('/home/a-savchenko/.config/gh/hosts.yml').read()).group(1))")
curl -s -X PATCH -H "Authorization: token $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/producedbysavant/simintech-code-library \
  -d '{"name":"simintech-code"}' | python3.11 -c "import json,sys;d=json.load(sys.stdin);print(d.get('full_name') or d.get('message'))"
```
Expected: `producedbysavant/simintech-code`

- [ ] **Step 2: Обновить remote локальной копии**

```bash
cd /mnt/c/git/simintech-code-library
git remote set-url origin https://github.com/producedbysavant/simintech-code.git
git remote -v
```
Expected: оба URL указывают на `simintech-code.git`

- [ ] **Step 3: Перенести рабочую копию под новым именем**

```bash
cd /mnt/c/git
mv simintech-code-library simintech-code
cd /mnt/c/git/simintech-code && git status -sb | head -1
```
Expected: ветка `main`, дерево чистое (или только ожидаемые локальные файлы)

- [ ] **Step 4: Проверить, что push работает**

```bash
cd /mnt/c/git/simintech-code
git -c credential.helper='' \
    -c credential.helper='!f(){ echo username=x-access-token; echo password=$GH_TOKEN; }; f' \
    push origin main
```
Expected: `Everything up-to-date` (или успешный push)

---

## Task 2: Перевезти библиотеку в `simintech-code`

**Files:**
- Create: `/mnt/c/git/simintech-code/pyproject.toml`
- Copy: `simintech_api/`, `examples/`, 13 тестов, `tests/integration/`, `tests/conftest.py`, библиотечные docs, `REPORT.md`

- [ ] **Step 1: Скопировать пакет и примеры**

```bash
SRC=/mnt/c/git/simintech-mcp
DST=/mnt/c/git/simintech-code
cp -r "$SRC/simintech_api" "$DST/"
cp -r "$SRC/examples" "$DST/"
cp -r "$SRC/scripts" "$DST/"
rm -rf "$DST/simintech_api/__pycache__" "$DST/simintech_api"/*/__pycache__
cp "$SRC/REPORT.md" "$DST/"
```
Expected: `simintech_api/` в `simintech-code`, `__pycache__` не скопированы

- [ ] **Step 2: Скопировать библиотечные тесты**

```bash
SRC=/mnt/c/git/simintech-mcp
DST=/mnt/c/git/simintech-code
mkdir -p "$DST/tests/unit" "$DST/tests/integration"
cp "$SRC/tests/conftest.py" "$DST/tests/"
cp -r "$SRC/tests/integration/." "$DST/tests/integration/"
rm -rf "$DST/tests/integration/__pycache__"
for f in test_agent test_block test_catalog test_catalog_tool test_cli_runner \
         test_com_client test_converters test_layout_placer test_layout_router \
         test_performance test_sdb test_signals test_xprt_signals; do
  cp "$SRC/tests/unit/$f.py" "$DST/tests/unit/"
done
ls "$DST/tests/unit" | wc -l
```
Expected: `13`

- [ ] **Step 3: Скопировать библиотечную документацию**

```bash
SRC=/mnt/c/git/simintech-mcp
DST=/mnt/c/git/simintech-code
mkdir -p "$DST/docs"
cp "$SRC/docs/guide.md" "$SRC/docs/api.md" "$SRC/docs/algorithms.md" \
   "$SRC/docs/architecture.md" "$SRC/docs/Makefile" "$DST/docs/"
cp -r "$SRC/docs/source" "$SRC/docs/reference" "$DST/docs/"
rm -rf "$DST/docs/source/__pycache__" "$DST/docs/build"
ls "$DST/docs"
```
Expected: `Makefile algorithms.md api.md architecture.md guide.md reference source`

- [ ] **Step 4: Создать `pyproject.toml` для библиотеки**

Create `/mnt/c/git/simintech-code/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "simintech-api"
version = "0.2.0"
description = "Python-библиотека для программного управления SimInTech через внешний COM API"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "EVS360" }]
dependencies = [
    # comtypes — только Windows: COM API SimInTech там и работает
    "comtypes>=1.1.0; sys_platform == 'win32'",
    # разбор XML-выгрузок SimInTech: защита от XXE и разворачивания сущностей
    "defusedxml>=0.7",
]

[project.optional-dependencies]
test = ["pytest>=7.0"]

[project.scripts]
simintech-cli = "simintech_api.cli:main"
simintech-generate-catalog = "simintech_api.catalog_tool:main"

[tool.hatch.build.targets.wheel]
packages = ["simintech_api"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: тесты, требующие реального COM-сервера SimInTech на Windows (deselect через -m 'not integration')",
    "performance: бенчмарки производительности placer/router",
]
```

Отличия от текущего `pyproject.toml`: бэкенд `hatchling` вместо `setuptools` (не нужен явный список пакетов — он молча ломается при перемещении файлов), нет `mcp`/`fastmcp` (это MCP-слой), только один консольный скрипт `simintech-mcp` уходит в другой репозиторий.

- [ ] **Step 5: Проверить, что библиотека собирается и тесты проходят**

```bash
cd /mnt/c/git/simintech-code
python3.11 -m pip install -e ".[test]" --quiet
python3.11 -m pytest tests/unit -q
```
Expected: 13 файлов собраны, все проходят. Если `hatchling` не установлен — `python3.11 -m pip install hatchling`.

- [ ] **Step 6: Commit и тег**

```bash
cd /mnt/c/git/simintech-code
git add -A
git commit -m "feat: библиотека simintech_api, примеры и документация

Перенесено из репозитория simintech-mcp (коммиты до a2dedff) при разделении
проекта на три репозитория. pyproject переведён на hatchling: явный список
пакетов в setuptools ломается молча при перемещении файлов.

История этих файлов остаётся в simintech-mcp — он публичный, ничего не теряется."
git tag v0.2.0
```
Expected: коммит и тег созданы

- [ ] **Step 7: Отправить в GitHub**

```bash
cd /mnt/c/git/simintech-code
git -c credential.helper='' \
    -c credential.helper='!f(){ echo username=x-access-token; echo password=$GH_TOKEN; }; f' \
    push origin main --tags
```
Expected: ветка и тег `v0.2.0` на GitHub

---

## Task 3: Очистить `simintech-mcp`

**Files:**
- Delete: `simintech_api/`, `examples/`, `scripts/`, `skills-catalog/`, `REPORT.md`, библиотечные тесты и docs, `docs/simintech-language/`
- Modify: `pyproject.toml`, `README.md`, `CLAUDE.md`, `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Удалить переехавшее**

```bash
cd /mnt/c/git/simintech-mcp
git rm -r -q simintech_api examples scripts skills-catalog REPORT.md
git rm -r -q docs/guide.md docs/api.md docs/algorithms.md docs/architecture.md \
             docs/source docs/reference docs/Makefile docs/simintech-language
git rm -q tests/conftest.py tests/integration/test_lifecycle.py \
          tests/integration/debug_block_signals.py tests/integration/debug_signals.py \
          tests/unit/test_agent.py tests/unit/test_block.py tests/unit/test_catalog.py \
          tests/unit/test_catalog_tool.py tests/unit/test_cli_runner.py \
          tests/unit/test_com_client.py tests/unit/test_converters.py \
          tests/unit/test_layout_placer.py tests/unit/test_layout_router.py \
          tests/unit/test_performance.py tests/unit/test_sdb.py \
          tests/unit/test_signals.py tests/unit/test_xprt_signals.py
git status --short | head -5
```
Expected: удаления в индексе; остаются `simintech_mcp/`, `tests/unit/test_mcp_server.py`, `docs/roadmap-agentic-ecosystem.md`, `docs/superpowers/`

- [ ] **Step 2: Проверить, что не осталось ссылок на перемещённое**

```bash
cd /mnt/c/git/simintech-mcp
grep -rn "simintech_api" simintech_mcp/ tests/ 2>/dev/null
```
Expected: только импорты в `simintech_mcp/server.py` — они остаются, библиотека теперь внешняя зависимость.

- [ ] **Step 3: Переписать `pyproject.toml`**

Replace содержимое `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "simintech-mcp"
version = "0.2.0"
description = "MCP-сервер для управления SimInTech через COM API"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "EVS360" }]
dependencies = [
    # Библиотека-ядро. Для локальной разработки заменить на path-зависимость:
    # simintech-api = { path = "../simintech-code", editable = true }
    "simintech-api @ git+https://github.com/producedbysavant/simintech-code@v0.2.0",
    "mcp>=1.0.0",
    "fastmcp>=2.0",
]

[project.optional-dependencies]
test = [
    "pytest>=7.0",
    # Маркер @pytest.mark.anyio регистрирует плагин, встроенный в anyio.
    "anyio>=4.0",
]

[project.scripts]
simintech-mcp = "simintech_mcp.server:main"

[tool.hatch.build.targets.wheel]
packages = ["simintech_mcp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Проверить установку и тесты**

```bash
cd /mnt/c/git/simintech-mcp
python3.11 -m pip install -e ".[test]" --quiet
python3.11 -m pytest tests/unit -q
```
Expected: тесты MCP проходят (7 файлов в `tests/unit` осталось 2: `test_mcp_server.py`, `test_skills_catalog.py` — но скиллы уедут в Task 4).

- [ ] **Step 5: Обновить README и CLAUDE.md**

`README.md`: убрать разделы про устройство библиотеки (они теперь в другом репозитории), оставить назначение сервера, установку, список инструментов, подключение к Claude Code. Добавить ссылку на `simintech-code` и `simintech-skill`.

`CLAUDE.md`: убрать всё про `simintech_api/`, `layout/`, `catalog.py`, каталог блоков и `tests/integration` — это чужая зона ответственности. Оставить: что это MCP-обёртка, где лежит сервер, как запускать тесты, что COM работает только на Windows и привязан к потоку, как ведётся работа с зависимостью.

- [ ] **Step 6: Проверить, что сервер поднимается**

```bash
cd /mnt/c/git/simintech-mcp
timeout 60 python3.11 -c "
import json, subprocess, sys
p = subprocess.Popen([sys.executable, '-m', 'simintech_mcp.server'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})+chr(10))
p.stdin.flush()
print('initialize OK' if json.loads(p.stdout.readline())['id']==1 else 'FAIL')
p.kill()
"
```
Expected: `initialize OK`

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/git/simintech-mcp
git add -A
git commit -m "refactor: репозиторий содержит только MCP-сервер

Библиотека, примеры, скрипты и знаниевый контент переехали в simintech-code;
скиллы — в simintech-skill. Зависимость на библиотеку объявлена по git-тегу.
Сборка переведена на hatchling.

Удалён дубликат docs/simintech-language/ — канонический экземпляр в simintech-code."
```

---

## Task 4: Создать `simintech-skill`

**Files:**
- Create: репозиторий `producedbysavant/simintech-skill`
- Move: `skills-catalog/`, `tests/unit/test_skills_catalog.py`

- [ ] **Step 1: Создать репозиторий**

```bash
GH_TOKEN=$(python3.11 -c "import re;print(re.search(r'oauth_token:\s*(\S+)',open('/home/a-savchenko/.config/gh/hosts.yml').read()).group(1))")
curl -s -X POST -H "Authorization: token $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/user/repos \
  -d '{"name":"simintech-skill","description":"Скиллы (доменные знания) для работы ИИ-агента с SimInTech","private":false}' \
  | python3.11 -c "import json,sys;d=json.load(sys.stdin);print(d.get('full_name') or d.get('message'))"
```
Expected: `producedbysavant/simintech-skill`

- [ ] **Step 2: Перенести файлы**

```bash
SRC=/mnt/c/git/simintech-mcp
DST=/mnt/c/git/simintech-skill
mkdir -p "$DST/tests/unit"
cp -r "$SRC/skills-catalog" "$DST/"
cp "$SRC/tests/unit/test_skills_catalog.py" "$DST/tests/unit/"
# в исходном репозитории файлы удаляются штатно, через git rm
cd "$SRC"
git rm -r -q skills-catalog tests/unit/test_skills_catalog.py
ls "$DST"
```
Expected: `skills-catalog tests`. `git mv` между репозиториями невозможен — переносим копированием, а в источнике удаляем через `git rm`.

- [ ] **Step 3: Поправить тест под новый путь**

`test_skills_catalog.py` вычисляет каталог как `parents[2]/"skills-catalog"`. После переноса структура та же (`tests/unit/` → корень репозитория), поэтому путь остаётся верным. Проверить:

```bash
cd /mnt/c/git/simintech-skill
grep -n "skills-catalog" tests/unit/test_skills_catalog.py | head -3
```
Expected: `CATALOG = pathlib.Path(__file__).resolve().parents[2] / "skills-catalog"` — корень репозитория, корректно.

- [ ] **Step 4: Создать `pyproject.toml` (минимальный, только для тестов)**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "simintech-skill"
version = "0.2.0"
description = "Скиллы (доменные знания) для работы ИИ-агента с SimInTech"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "EVS360" }]
# Установочных зависимостей нет: скиллы — это markdown и yaml.
dependencies = []

[project.optional-dependencies]
test = ["pytest>=7.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 5: Проверить тесты**

```bash
cd /mnt/c/git/simintech-skill
python3.11 -m pytest tests/unit -q
```
Expected: тесты каталога скиллов проходят.

- [ ] **Step 6: Инициализировать git и отправить**

```bash
cd /mnt/c/git/simintech-skill
git init -q -b main
git add -A
git commit -q -m "feat: каталог скиллов SimInTech

Перенесено из simintech-mcp при разделении проекта на три репозитория.
Скиллы не имеют установочных зависимостей: это SKILL.md и manifest.yaml,
ссылающиеся на simintech-code по URL."
GH_TOKEN=$(python3.11 -c "import re;print(re.search(r'oauth_token:\s*(\S+)',open('/home/a-savchenko/.config/gh/hosts.yml').read()).group(1))")
git remote add origin https://github.com/producedbysavant/simintech-skill.git
git -c credential.helper='' \
    -c credential.helper='!f(){ echo username=x-access-token; echo password=$GH_TOKEN; }; f' \
    push -u origin main
```
Expected: ветка `main` на GitHub

---

## Task 5: Разложить общие конфиги во все три репозитория

**Files:**
- Copy: `LICENSE`, `.gitattributes`, `.editorconfig`, `.gitignore`, `CONTRIBUTING.md`, `.github/`

- [ ] **Step 1: Скопировать общий набор**

```bash
REF=/mnt/c/git/simintech-code-library
# в simintech-code общие файлы уже есть — берём их как эталон
cd /mnt/c/git/simintech-code
ls LICENSE .gitattributes .editorconfig .gitignore CONTRIBUTING.md .github 2>&1 | head -8
```
Expected: весь набор на месте (эталон).

Если `LICENSE` отсутствует — скопировать из `/mnt/c/git/simintech-mcp/LICENSE`.

- [ ] **Step 2: Разложить в `simintech-mcp`**

```bash
REF=/mnt/c/git/simintech-code
DST=/mnt/c/git/simintech-mcp
cp "$REF/LICENSE" "$REF/.gitattributes" "$REF/.editorconfig" "$REF/CONTRIBUTING.md" "$DST/"
mkdir -p "$DST/.github"
cp -r "$REF/.github/." "$DST/.github/"
# .gitignore уже расширен в плане 1 — не перезаписываем
cd "$DST" && git status --short
```
Expected: новые файлы, `.gitignore` не изменён

- [ ] **Step 3: Разложить в `simintech-skill`**

```bash
REF=/mnt/c/git/simintech-code
DST=/mnt/c/git/simintech-skill
cp "$REF/LICENSE" "$REF/.gitattributes" "$REF/.editorconfig" "$REF/.gitignore" "$REF/CONTRIBUTING.md" "$DST/"
mkdir -p "$DST/.github"
cp -r "$REF/.github/." "$DST/.github/"
cd "$DST" && git status --short
```
Expected: новые файлы

- [ ] **Step 4: Добавить `CLAUDE.md` в `simintech-skill`**

Create `/mnt/c/git/simintech-skill/CLAUDE.md`:

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Скиллы (доменные знания) для ИИ-агента, работающего с SimInTech. Скиллы
дополняют MCP-инструменты: инструменты дают возможности, скиллы — знания.

## Состав

`skills-catalog/` — по каталогу на скилл, в каждом `SKILL.md` (frontmatter
`name`/`description` + инструкции) и `manifest.yaml` (метаданные).

## Связь с другими репозиториями

- `simintech-code` — библиотека и знаниевый контент (`blocks/`, `language/`,
  `patterns/`, `tutorials/`, `automation/`). Скиллы **ссылаются** на него по
  URL и не содержат копий: иначе появятся расходящиеся версии одного текста.
- `simintech-mcp` — MCP-сервер, который скиллы описывают.

Установочных зависимостей нет: сборка не нужна, только тесты.

## Команды

```bash
python3.11 -m pytest tests/unit -q     # проверка структуры каталога скиллов
```

## Правила

- Имена параметров блоков SimInTech короткие и неочевидные (`a` у
  «Константы», а не `y0`). Источник истины — `simintech_api/data/block_catalog.json`
  в `simintech-code`, **не** `docs/simintech-language/blocks/` и не примеры.
- `SetBlockProp` не отвергает неизвестное имя свойства: ошибка в имени
  приводит к молчаливому отказу.
```

Проверка:
```bash
cd /mnt/c/git/simintech-skill && head -3 CLAUDE.md
```
Expected: `# CLAUDE.md`

- [ ] **Step 5: Commit в обоих репозиториях**

```bash
cd /mnt/c/git/simintech-mcp && git add -A && \
  git commit -q -m "chore: общий набор файлов репозитория (LICENSE, gitattributes, editorconfig, CONTRIBUTING, .github)"
cd /mnt/c/git/simintech-skill && git add -A && \
  git commit -q -m "chore: общий набор файлов репозитория и CLAUDE.md"
git -C /mnt/c/git/simintech-skill log --oneline -2
```
Expected: по коммиту в каждом

---

## Task 6: Удалить `simintech-connector`

**Files:**
- Delete: `/mnt/c/git/simintech-connector`

- [ ] **Step 1: Убедиться, что ничего нужного не осталось**

Сверить с таблицей роспуска (§6 спецификации). Перенесено в плане 1:
`sdb_adapter.py` → `simintech_api/sdb.py`, `cli_adapter.py` →
`simintech_api/cli_runner.py`, `run_macro.py`/`run_pak.py` → `examples/`,
`models.py` разобран. Остаются к удалению: `com_adapter.py` (перекрыт
`simintech_api.core`), `server.py` (перекрыт `simintech_mcp`),
`examples/basic_usage.py` (использует `com_adapter`).

```bash
cd /mnt/c/git/simintech-mcp
grep -rn "connector" simintech_api/ simintech_mcp/ 2>/dev/null | grep -v "SimInTech-connector" | head -5
```
Expected: пусто (нет импортов из коннектора)

- [ ] **Step 2: Проверить, что перенесённое на месте**

```bash
ls /mnt/c/git/simintech-code/simintech_api/sdb.py \
   /mnt/c/git/simintech-code/simintech_api/cli_runner.py \
   /mnt/c/git/simintech-code/examples/run_macro.py \
   /mnt/c/git/simintech-code/examples/run_pak.py
```
Expected: все четыре файла существуют

- [ ] **Step 3: Удалить папку**

```bash
rm -rf /mnt/c/git/simintech-connector
ls -d /mnt/c/git/simintech-connector 2>&1 | head -1
```
Expected: `No such file or directory`

---

## Task 7: Приёмка

- [ ] **Step 1: Тесты во всех трёх репозиториях (Linux)**

```bash
cd /mnt/c/git/simintech-code && python3.11 -m pytest tests/unit -q
cd /mnt/c/git/simintech-mcp && python3.11 -m pytest tests/unit -q
cd /mnt/c/git/simintech-skill && python3.11 -m pytest tests/unit -q
```
Expected: все проходят. В `simintech-mcp` — тесты MCP, в `simintech-code` — библиотека, в `simintech-skill` — каталог скиллов.

- [ ] **Step 2: Чистая установка на Windows**

```bash
WPY=/mnt/c/Users/a-savchenko/AppData/Local/Programs/Python/Python313/python.exe
cd /mnt/c/git/simintech-code && "$WPY" -m pip install -e ".[test]" --quiet
cd /mnt/c/git/simintech-mcp && "$WPY" -m pip install -e ".[test]" --quiet
"$WPY" -c "import simintech_api, simintech_mcp; print('импорт OK')"
```
Expected: `импорт OK` — зависимость `simintech-api` подтянулась из git-тега

- [ ] **Step 3: JSON-RPC через stdio на Windows**

```bash
cd /mnt/c/git/simintech-mcp
timeout 120 "$WPY" -c "
import json, subprocess, sys
p = subprocess.Popen([sys.executable, '-m', 'simintech_mcp.server'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})+chr(10))
p.stdin.close()
line = p.stdout.readline()
m = json.loads(line)
print('initialize OK' if m.get('id') == 1 else 'FAIL')
p.kill()
"
```
Expected: `initialize OK`

- [ ] **Step 4: Интеграционные тесты на Windows**

```bash
cd /mnt/c/git/simintech-code
SIMINTECH_KEEP_MMAIN=1 timeout 500 "$WPY" -m pytest tests/integration -m integration -q
```
Expected: `4 passed, 1 skipped`

- [ ] **Step 5: Убрать процессы `mmain.exe`**

```bash
export PATH="$PATH:/mnt/c/Windows/System32"
timeout 60 tasklist.exe /FI "IMAGENAME eq mmain.exe" /FO CSV /NH 2>/dev/null > /tmp/mm.txt
PIDS=$(python3.11 -c "
import csv
rows=list(csv.reader(open('/tmp/mm.txt', encoding='cp866', errors='replace')))
print(' '.join(r[1] for r in rows if len(r)>1 and r[1].isdigit()))")
for p in $PIDS; do timeout 30 taskkill.exe /F /PID "$p" >/dev/null 2>&1; done
```
Expected: процессов не осталось

---

## Проверка результата

| Критерий | Как проверить |
|---|---|
| Три репозитория существуют | `git ls-remote` для каждого |
| `simintech-code` содержит библиотеку | `simintech_api/`, `examples/`, `tests/`, `blocks/`, `language/` |
| `simintech-mcp` содержит только сервер | нет `simintech_api/`, нет `skills-catalog/` |
| `simintech-skill` содержит скиллы | `skills-catalog/` с 4 каталогами |
| Общие файлы идентичны | `LICENSE`, `.gitattributes`, `.editorconfig` совпадают байт-в-байт |
| Зависимость объявлена по тегу | `grep simintech-api simintech-mcp/pyproject.toml` |
| `simintech-connector` удалён | каталога нет |
| Дубликата справочника нет | `docs/simintech-language/` отсутствует в `simintech-mcp` |

---

## Риски

| Риск | Оценка |
|---|---|
| Переписывание истории `simintech-mcp` | Ветка `chore/repo-split-prep` уже отделена; при слиянии PR история сохраняется, удаление файлов — обычный коммит |
| Потеря истории перемещённых файлов | История остаётся в публичном `simintech-mcp`; в `simintech-code` файлы приходят одним коммитом со ссылкой на источник |
| Версия `v0.2.0` в двух репозиториях одновременно | Допустимо: это разные пакеты. При расхождении MCP пинит точный тег библиотеки |
| Ссылки в скиллах на относительные пути | Манифесты ссылаются на `simintech_api/data/block_catalog.json` — после разделения путь ложный. Проверить и заменить на версию + checksum |
| `simintech-code` содержит и библиотеку, и знаниевый контент | Осознанно: контент — часть «code»-репозитория, скиллы на него ссылаются |

---

## Что не входит

- Публикация скиллов в ClawHub, IDE-плагин, шаблоны проектов.
- Автоматизация синхронизации общих файлов (спецификация §5.3).
- Создание проектов SimInTech с подключённой базой сигналов (спецификация §10).
