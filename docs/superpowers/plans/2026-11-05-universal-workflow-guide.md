# Universal SimInTech MCP Workflow Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one universal workflow guide to the MCP server and a matching skill, so an agent starts from a source map and evidence discipline instead of treating one tool as the whole model.

**Architecture:** The MCP resource `simintech://guides/workflow` carries the canonical five-step formula and the source-selection matrix; server instructions point to it. The skill mirrors the formula for agents that load skills before tools. Both artifacts are documentation; no runtime behavior changes.

**Tech Stack:** Python 3.11, FastMCP 4, pytest, PyYAML, Markdown.

**Spec:** the formula agreed with the owner on 2026-11-05: "Карта -> Копия -> Вопрос -> Изменение -> Доказательство". This plan embeds the full text; no separate spec file.

## Global Constraints

- No U+2014 (em dash) in any file, commit, issue, or PR. Use a hyphen, colon, or a restructured phrase.
- No Claude attribution in commits, files, issues, or PRs.
- No runtime behavior changes: this plan adds a resource, tests, documentation, and a skill.
- MCP work happens in `/mnt/c/git/simintech-mcp` on branch `feat/universal-workflow-guide`.
- Skill work happens in `/mnt/c/git/simintech-skill` on branch `feat/universal-workflow-skill`.
- Commit locally after each task. Do not push, open issues, or open PRs until the owner says so.
- MCP tests run with Windows-Python. Skill tests run with `python3.11` and `SIMINTECH_CODE_DIR=/mnt/c/git/simintech-code`.

## Review Focus

- An agent must see the workflow before choosing a tool. The server instructions and the guide resource both name it, and a test checks both.
- The guide and the skill must not drift. Both carry the same five steps, the same source matrix, and the same seven laws; separate tests check the needles in both files.
- The README catalog must list the new skill, and the root and catalog READMEs must stay byte-identical.

---

## File Structure

MCP repository:

- Modify: `simintech_mcp/tool_docs.py` - add `resource_workflow_guide`.
- Modify: `simintech_mcp/app.py` - add one instruction line pointing to the guide.
- Modify: `tests/unit/test_resources.py` - test guide content and server instruction.
- Modify: `docs/tool-surface.md` - short section with the guide URI.
- Create: `docs/superpowers/plans/2026-11-05-universal-workflow-guide.md` - this plan.

Skill repository:

- Create: `skills-catalog/simintech-universal-workflow/SKILL.md`.
- Create: `skills-catalog/simintech-universal-workflow/manifest.yaml`.
- Modify: `README.md` and `skills-catalog/README.md` - add the skill row; keep the files identical.
- Modify: `tests/unit/test_skills_catalog.py` - test the skill content.

---

### Task 1: MCP workflow guide resource

**Files:**
- Modify: `simintech_mcp/tool_docs.py`
- Modify: `simintech_mcp/app.py`
- Modify: `tests/unit/test_resources.py`
- Modify: `docs/tool-surface.md`

**Interfaces:**
- Consumes: `mcp` from `simintech_mcp.app`, the existing resource registration pattern in `tool_docs.py`.
- Produces: MCP resource `simintech://guides/workflow` and a server instruction naming it.

- [ ] **Step 1: Write the failing resource test**

Add to `tests/unit/test_resources.py`:

```python
@pytest.mark.anyio
async def test_resource_workflow_guide_covers_sources():
    """Универсальный порядок называет источники, границы и хеши."""
    text = await _resource_text("simintech://guides/workflow")

    for needle in (
        "doctor",
        "load_model",
        "get_model_manifest",
        "export_model_text",
        "topology",
        "build_and_verify",
        "influence",
        "semantic_coverage",
        "structure_hash",
        "content_hash",
        "SaveProjectXML",
        "unknown",
    ):
        assert needle in text


def test_server_instructions_name_workflow_guide():
    """Инструкции сервера ссылаются на порядок до первого инструмента."""
    from simintech_mcp.server import mcp

    assert "simintech://guides/workflow" in (mcp.instructions or "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /mnt/c/git/simintech-mcp
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
  /mnt/c/Users/a-savchenko/AppData/Local/Programs/Python/Python311/python.exe \
  -m pytest tests/unit/test_resources.py \
  -k "workflow_guide or instructions_name" -q
```

Expected: both tests fail. The resource test reports that `simintech://guides/workflow` is unknown; the instruction test reports that the URI is absent.

- [ ] **Step 3: Add the guide resource**

In `simintech_mcp/tool_docs.py`, add this function after `resource_model_text_guide` and before `resource_model_ir_guide`:

```python
@mcp.resource("simintech://guides/workflow")
def resource_workflow_guide() -> str:
    """Универсальный порядок работы с SimInTech MCP."""
    return (
        "Порядок работы с SimInTech MCP\n"
        "\n"
        "Формула: карта -> копия -> вопрос -> изменение -> доказательство.\n"
        "\n"
        "1. Карта. Сначала соберите представления, а не начинайте с правки.\n"
        "  doctor: ownership, версия, проект, каталог результатов.\n"
        "  load_model: IR, model_ref, structure_hash, content_hash, покрытие,\n"
        "    семантика и unknown.\n"
        "  get_model_manifest: паспорт в бюджете токенов.\n"
        "  export_model_text: концы проводов, скрипты и субмодели.\n"
        "  load_model(sources=['topology']): протокол топологии.\n"
        "  inspect_model/list_blocks/get_block_params: живой COM.\n"
        "  build_and_verify(spec=SystemSpec): компиляция спецификации и вердикт.\n"
        "  semantic_coverage: причины неизвестности.\n"
        "\n"
        "2. Копия. Работайте с копией. Запишите model_ref, structure_hash,\n"
        "content_hash, путь и версию среды. После любой правки снимок IR\n"
        "устарел: нужен новый load_model. Оригинал не трогайте.\n"
        "\n"
        "3. Вопрос -> источник:\n"
        "  связи и пути: IR и топология;\n"
        "  порты и параметры: живой COM и каталог;\n"
        "  поведение скрипта: export_model_text;\n"
        "  причинное влияние: influence и контракты;\n"
        "  динамика: simulate и выходные файлы;\n"
        "  неизвестность: semantic_coverage.\n"
        "Структурная достижимость не является причинным влиянием.\n"
        "\n"
        "4. Одно изменение. Сначала baseline, затем один вход, параметр или\n"
        "состояние. Ожидаемый эффект и признак остановки задайте до запуска.\n"
        "После SaveProjectXML страничные вызовы только после reload_project.\n"
        "При риске деградации страницы начните новый процесс mmain.\n"
        "\n"
        "5. Доказательство. Свидетельство действительно только с обоими\n"
        "хешами, условиями прогона, изменением и наблюдением. Сравните\n"
        "influence с эталоном и экспериментом. unknown оставьте unknown.\n"
        "Реальные модель и траектории не публикуйте.\n"
        "\n"
        "Законы: структурный путь не причинность; любой слой неполон; после\n"
        "изменения снимок IR устарел; после SaveProjectXML сначала reload;\n"
        "один эксперимент - одно изменение - один baseline; без хешей и\n"
        "условий нет свидетельства; unknown это ответ."
    )
```

- [ ] **Step 4: Run the resource test**

Run the same command. Expected: `test_resource_workflow_guide_covers_sources` passes; the instruction test still fails.

- [ ] **Step 5: Point server instructions to the guide**

In `simintech_mcp/app.py`, add one line to the `instructions` string, immediately after `"Начните с doctor. "`:

```python
        "Порядок работы с моделью: simintech://guides/workflow. "
```

The resulting beginning is:

```python
    instructions=(
        "Управление SimInTech через COM API (Windows, mmain.exe /regserver). "
        "Начните с doctor. "
        "Порядок работы с моделью: simintech://guides/workflow. "
        "Сборка: build_model(spec=...) с ModelSpec; "
```

- [ ] **Step 6: Run both tests and flake8**

Run:

```bash
cd /mnt/c/git/simintech-mcp
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
  /mnt/c/Users/a-savchenko/AppData/Local/Programs/Python/Python311/python.exe \
  -m pytest tests/unit/test_resources.py \
  -k "workflow_guide or instructions_name" -q
python3.11 -m flake8 simintech_mcp tests \
  --max-line-length=88 --extend-ignore=E203,W503
```

Expected: `2 passed`; flake8 exits 0.

- [ ] **Step 7: Add the guide to tool-surface documentation**

In `docs/tool-surface.md`, add this section immediately after the visible-core list and before `## Поиск`:

```markdown
## Порядок работы

Универсальный порядок с источниками и законами:
`simintech://guides/workflow`. Он обязателен до первой правки: карта
источников, копия, выбор источника по вопросу, одно изменение, доказательство.
```

- [ ] **Step 8: Verify no em dash in the changed files**

Run:

```bash
cd /mnt/c/git/simintech-mcp
grep -n $'\u2014' simintech_mcp/tool_docs.py simintech_mcp/app.py \
  tests/unit/test_resources.py docs/tool-surface.md | head
```

Expected: no output. If output appears, replace each em dash with a hyphen or a colon and rerun.

- [ ] **Step 9: Commit**

```bash
cd /mnt/c/git/simintech-mcp
git add simintech_mcp/tool_docs.py simintech_mcp/app.py \
  tests/unit/test_resources.py docs/tool-surface.md \
  docs/superpowers/plans/2026-11-05-universal-workflow-guide.md
git commit -m "docs(mcp): универсальный порядок работы с моделью" \
  -m "Ресурс simintech://guides/workflow сводит карту источников, выбор источника, одно изменение и доказательство. Инструкции сервера ссылаются на него до первого инструмента."
```

---

### Task 2: Matching skill in simintech-skill

**Files:**
- Create: `skills-catalog/simintech-universal-workflow/SKILL.md`
- Create: `skills-catalog/simintech-universal-workflow/manifest.yaml`
- Modify: `README.md`
- Modify: `skills-catalog/README.md`
- Modify: `tests/unit/test_skills_catalog.py`

**Interfaces:**
- Consumes: the MCP resource URI `simintech://guides/workflow` from Task 1.
- Produces: skill `simintech-universal-workflow` with `SKILL.md` and `manifest.yaml`; both READMEs list it.

- [ ] **Step 1: Write the failing skill test**

Add to `tests/unit/test_skills_catalog.py`, next to the other skill-content tests:

```python
def test_universal_workflow_skill_covers_formula():
    """Скилл повторяет формулу MCP, а не пересказывает один инструмент."""
    text = (CATALOG / "simintech-universal-workflow" / "SKILL.md").read_text(
        encoding="utf-8")

    for needle in (
        "doctor",
        "load_model",
        "get_model_manifest",
        "export_model_text",
        "topology",
        "build_and_verify",
        "influence",
        "semantic_coverage",
        "structure_hash",
        "content_hash",
        "SaveProjectXML",
        "unknown",
    ):
        assert needle in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /mnt/c/git/simintech-skill
SIMINTECH_CODE_DIR=/mnt/c/git/simintech-code \
  python3.11 -m pytest tests/unit/test_skills_catalog.py \
  -k universal_workflow -q
```

Expected: FAIL with `FileNotFoundError` for `skills-catalog/simintech-universal-workflow/SKILL.md`.

- [ ] **Step 3: Create the skill**

Create `skills-catalog/simintech-universal-workflow/SKILL.md` with exactly this content:

```markdown
---
name: simintech-universal-workflow
description: "Use when starting any SimInTech MCP task: build a source map first (doctor, Model IR, model text, topology, live COM, contracts), work on a copy, choose the source by question, change one thing, and keep evidence with hashes. Prevents treating one tool as the whole model."
---

# Универсальный порядок работы с SimInTech MCP

## Когда применять

- в начале любой задачи с SimInTech MCP;
- когда агент уже открыл один инструмент и считает его полной картиной;
- перед живым экспериментом, правкой модели или выводами о влиянии;
- когда нужно объяснить, почему ответ `unknown`, а не выдать догадку.

Это процесс, а не новая архитектура. Он не заменяет спецификации Model IR,
функциональной семантики и сборки.

## Формула

```
карта -> копия -> вопрос -> изменение -> доказательство
```

## 1. Карта

Сначала соберите представления, а не начинайте с правки:

- `doctor`: ownership, версия, проект, каталог результатов;
- `load_model`: IR, `model_ref`, `structure_hash`, `content_hash`, покрытие,
  семантика и `unknown`;
- `get_model_manifest`: паспорт в бюджете токенов;
- `export_model_text`: концы проводов, скрипты и субмодели;
- `load_model(sources=['topology'])`: протокол топологии;
- `inspect_model` / `list_blocks` / `get_block_params`: живой COM;
- `build_and_verify(spec=SystemSpec)`: компиляция спецификации и вердикт;
- `semantic_coverage`: причины неизвестности.

Краткое руководство сервера: `simintech://guides/workflow`.

## 2. Копия

Работайте с копией. Запишите `model_ref`, `structure_hash`, `content_hash`,
путь и версию среды. После любой правки снимок IR устарел: нужен новый
`load_model`. Оригинал не трогайте.

## 3. Вопрос -> источник

| Вопрос | Источник | Чем не является |
|---|---|---|
| Что с чем соединено | IR и топология | не причинность |
| Какие порты и параметры | живой COM и каталог | не концы проводов |
| Что делает скрипт | `export_model_text` | не гарантия поведения |
| Почему выход зависит от входа | `influence` и контракты | не структурная достижимость |
| Что происходит во времени | `simulate` и выходные файлы | не доказательство семантики |
| Что неизвестно | `semantic_coverage` | не ошибка, а результат |

Если вопрос не попадает в строку, ответ `unknown`, а не догадка.

## 4. Одно изменение

Сначала baseline, затем один вход, параметр или состояние. Ожидаемый эффект и
признак остановки задайте до запуска. После `SaveProjectXML` страничные
вызовы только после `reload_project`. При риске деградации страницы начните
новый процесс mmain.

## 5. Доказательство

Свидетельство действительно только с обоими хешами, условиями прогона,
изменением и наблюдением. Сравните `influence` с эталоном и экспериментом.
`unknown` оставьте `unknown`. Реальные модель и траектории не публикуйте.

## Законы

1. Структурный путь не равен причинному влиянию.
2. Любой слой неполон; граница покрытия называется явно.
3. После изменения модели снимок IR устарел.
4. После `SaveProjectXML` страничные вызовы только после переоткрытия.
5. Один эксперимент - одно изменение - один baseline.
6. Нет хешей и условий - нет свидетельства.
7. `unknown` это ответ, а не поражение.

## Чек-лист перед ответом

- [ ] карта источников собрана, покрытие прочитано;
- [ ] `model_ref` и оба хеша названы;
- [ ] вопрос сопоставлен строке матрицы;
- [ ] изменение одно, baseline есть;
- [ ] `influence` не выдан за структурную достижимость;
- [ ] `unknown` и границы названы;
- [ ] свидетельство содержит условия и хеши.
```

- [ ] **Step 4: Create the manifest**

Create `skills-catalog/simintech-universal-workflow/manifest.yaml` with exactly this content:

```yaml
name: simintech-universal-workflow
version: 0.1.0
description: "Универсальный порядок работы с SimInTech MCP: карта источников, копия, выбор источника по вопросу, одно изменение и доказательство с хешами."
requires:
  mcp_tools:
    - doctor
    - load_model
    - get_model_manifest
    - query_model
    - export_model_text
    - inspect_model
    - simulate
    - read_output_file
    - build_and_verify
source_of_truth:
  # Ресурс и инструкции MCP: канонический текст порядка.
  - https://github.com/producedbysavant/simintech-mcp/blob/main/simintech_mcp/tool_docs.py
  - https://github.com/producedbysavant/simintech-mcp/blob/main/simintech_mcp/app.py
  # Обёртка Model IR и поверхность инструментов.
  - https://github.com/producedbysavant/simintech-mcp/blob/main/simintech_mcp/tools/model_ir.py
  - https://github.com/producedbysavant/simintech-mcp/blob/main/docs/tool-surface.md
  # Факты о COM и Model IR, на которые опирается порядок.
  - https://github.com/producedbysavant/simintech-code/blob/main/docs/reference/com_api_inventory.md
  - https://github.com/producedbysavant/simintech-code/blob/main/docs/knowledge/11-model-ir.md
  - https://github.com/producedbysavant/simintech-code/blob/main/docs/superpowers/specs/2026-10-30-functional-semantics-pilot.md
verified: true
verified_note: >
  Процессный скилл: он не добавляет утверждений о поведении среды, а
  фиксирует порядок работы с уже проверенными инструментами. Структура,
  frontmatter и ссылки проверены unit-тестами каталога; живой приёмки не
  требует.
```

- [ ] **Step 5: Add the skill row to both READMEs**

In `README.md` and `skills-catalog/README.md`, add this row to the `## Состав` table, immediately after the `simintech-model-ir/` row:

```markdown
| `simintech-universal-workflow/` | Порядок работы с MCP: карта источников, копия модели, выбор источника, один эксперимент, доказательство |
```

The two README files must stay byte-identical. After editing one, copy it over the other:

```bash
cd /mnt/c/git/simintech-skill
cp README.md skills-catalog/README.md
```

- [ ] **Step 6: Run the skill tests**

Run:

```bash
cd /mnt/c/git/simintech-skill
SIMINTECH_CODE_DIR=/mnt/c/git/simintech-code \
  python3.11 -m pytest tests/unit -q -rs
python3.11 -m flake8 tests \
  --max-line-length=88 --extend-ignore=E203,W503
```

Expected: the full unit suite passes; the new test is collected and passes; flake8 exits 0. Network-dependent help checks may skip; `-rs` names each skip.

- [ ] **Step 7: Verify no em dash in the new skill files**

Run:

```bash
cd /mnt/c/git/simintech-skill
grep -n $'\u2014' skills-catalog/simintech-universal-workflow/SKILL.md \
  skills-catalog/simintech-universal-workflow/manifest.yaml \
  tests/unit/test_skills_catalog.py README.md | head
```

Expected: no output.

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/git/simintech-skill
git add skills-catalog/simintech-universal-workflow \
  tests/unit/test_skills_catalog.py README.md skills-catalog/README.md
git commit -m "feat(skill): универсальный порядок работы с SimInTech MCP" \
  -m "Скилл повторяет формулу карта -> копия -> вопрос -> изменение -> доказательство, матрицу источников и законы. Каталог и тест обновлены."
```

---

### Task 3: Cross-repository verification

**Files:**
- No file changes. This task verifies both artifacts and their commits.

- [ ] **Step 1: Run the MCP resource and surface tests**

Run:

```bash
cd /mnt/c/git/simintech-mcp
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
  /mnt/c/Users/a-savchenko/AppData/Local/Programs/Python/Python311/python.exe \
  -m pytest tests/unit/test_resources.py tests/unit/test_surface.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run the skill suite**

Run:

```bash
cd /mnt/c/git/simintech-skill
SIMINTECH_CODE_DIR=/mnt/c/git/simintech-code \
  python3.11 -m pytest tests/unit -q -rs
```

Expected: all tests pass; skips named with `-rs`.

- [ ] **Step 3: Check both commits and the working trees**

Run:

```bash
git -C /mnt/c/git/simintech-mcp status --short
git -C /mnt/c/git/simintech-mcp log --oneline -1
git -C /mnt/c/git/simintech-skill status --short
git -C /mnt/c/git/simintech-skill log --oneline -1
```

Expected: both working trees clean; the top commit in each repository is the task commit.

- [ ] **Step 4: Report, do not push**

Report the two commit hashes, the test results, and the branch names. Do not push, open issues, or open PRs until the owner explicitly asks.
