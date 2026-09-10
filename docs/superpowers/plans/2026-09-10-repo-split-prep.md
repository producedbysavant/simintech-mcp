# Подготовка к разделению на три репозитория — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Привести текущий репозиторий в состояние, готовое к разделению: общие
конфиги, исправленная упаковка, перенесённый из `simintech-connector` код.

**Architecture:** Это первый из двух планов. Он не двигает файлы между
репозиториями — только готовит почву: добавляет общие файлы, чинит дефекты
упаковки и переносит в библиотеку полезные модули из устаревшего
`simintech-connector`. После него репозиторий содержит всё, что должно попасть
в `simintech-code`, и разделение становится механической операцией.

**Tech Stack:** Python 3.11+, PEP 621, setuptools (переход на hatchling — во
втором плане), pytest, comtypes (Windows-only).

**Область:** спецификация `docs/superpowers/specs/2026-09-10-simintech-repos-design.md`,
разделы §6 (роспуск connector), §7 (инструменты сборки), §8 шаги 1–5.

**Не входит:**

- Собственно разделение на репозитории — это план 2. Он зависит от результатов
  этого плана (точные пути и имена entry points).
- Переход на `hatchling` и `uv` (спецификация §7). Отложен сознательно: сейчас
  `pyproject.toml` описывает два пакета сразу, а после разделения их будет по
  одному на репозиторий. Переводить бэкенд дважды — лишняя работа, поэтому
  смена бэкенда делается в плане 2, при переписывании метаданных под каждый
  репозиторий.

---

## Структура файлов

| Файл | Ответственность | Действие |
|---|---|---|
| `LICENSE` | лицензия MIT, идентична во всех трёх репозиториях | создать |
| `.gitattributes` | нормализация EOL (Windows + WSL) | создать |
| `.editorconfig` | единый стиль редакторов | создать |
| `.gitignore` | игнор-правила, идентичные во всех трёх | изменить |
| `pyproject.toml` | метаданные и зависимости | изменить |
| `simintech_api/sdb.py` | чтение базы сигналов из XML | создать |
| `simintech_api/cli_runner.py` | запуск `mmain.exe` из WSL | создать |
| `simintech_api/catalog_tool.py` | генератор каталога блоков как entry point | создать |
| `scripts/generate_block_catalog.py` | тонкая обёртка над `catalog_tool` | изменить |
| `examples/run_macro.py` | пример запуска макроса | создать |
| `examples/run_pak.py` | пример запуска пака | создать |
| `tests/unit/test_sdb.py` | тесты разбора БД | создать |
| `tests/unit/test_cli_runner.py` | тесты CLI-обёртки | создать |

Эталон общих файлов — репозиторий `simintech-code-library`: там `.gitattributes`,
`.editorconfig`, `.gitignore`, `CONTRIBUTING.md` уже проработаны.

---

## Task 1: Общие файлы репозитория

**Files:**
- Create: `LICENSE`, `.gitattributes`, `.editorconfig`
- Modify: `.gitignore`

- [ ] **Step 1: Убедиться, что тесты зелёные, и поставить базовый тег**

Спецификация (§8, шаг 4) требует зафиксировать состояние до изменений, иначе
откатываться будет некуда.

Run:
```bash
cd /mnt/c/git/simintech-mcp && python3.11 -m pytest tests/unit -q
```
Expected: `141 passed`. Если тесты падают — сначала разобраться, тег не ставить.

Run:
```bash
git tag v0.1.0 && git push origin v0.1.0 && git tag -l
```
Expected: `v0.1.0` в списке тегов, тег отправлен.

- [ ] **Step 2: Создать `LICENSE`**

Файла нет, хотя в `pyproject.toml` заявлен MIT. Скопировать текст MIT с
заполненными полями (год 2026, правообладатель — как в существующих репозиториях
пользователя):

```
MIT License

Copyright (c) 2026 Anton Savchenko

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Создать `.gitattributes`**

Скопировать из `simintech-code-library/.gitattributes` без изменений:

```bash
cp /mnt/c/git/simintech-code-library/.gitattributes /mnt/c/git/simintech-mcp/.gitattributes
```

- [ ] **Step 4: Создать `.editorconfig`**

```bash
cp /mnt/c/git/simintech-code-library/.editorconfig /mnt/c/git/simintech-mcp/.editorconfig
```

- [ ] **Step 5: Расширить `.gitignore`**

Сейчас файл содержит только `__pycache__/`, `*.py[cod]`, `*.egg-info/`,
`.pytest_cache/`, `build/`, `dist/`, `.venv/`, `docs/build/`. Добавить в конец:

```gitignore

# Локальная память и временные файлы Claude Code
.remember/
.claude/

# Настройки редакторов: у каждого разработчика свои
.vscode/
.idea/
*.swp
*.swo

# Кэши инструментов
.ruff_cache/
.mypy_cache/

# Артефакты ОС
.DS_Store
Thumbs.db

# Временные файлы примеров и отладки
examples/*.xprt
examples/*.csv
```

- [ ] **Step 6: Проверить, что git видит новые файлы**

Run:
```bash
cd /mnt/c/git/simintech-mcp && git status --short
```
Expected: `?? LICENSE`, `?? .gitattributes`, `?? .editorconfig`, ` M .gitignore`.
Сгенерированные примеры (`examples/*.xprt`, `examples/*.csv`) в списке быть НЕ
должны.

- [ ] **Step 7: Commit**

```bash
git add LICENSE .gitattributes .editorconfig .gitignore
git commit -m "chore: общие файлы репозитория (LICENSE, gitattributes, editorconfig)"
```

---

## Task 2: Исправить дефекты упаковки

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Проверить текущее состояние**

Run: `grep -n "requires-python\|comtypes" pyproject.toml`
Expected:
```
10:requires-python = ">=3.9"
14:    "comtypes>=1.1.0",
```

- [ ] **Step 2: Поднять `requires-python`**

`mcp` и `fastmcp` требуют Python ≥3.10, в окружении 3.11 и 3.13, на 3.9 код
никогда не тестировался. Заменить строку 10:

```toml
requires-python = ">=3.11"
```

- [ ] **Step 3: Добавить маркер платформы для `comtypes`**

`comtypes` — Windows-only; без маркера установка на Linux/WSL тянет ненужную
зависимость. Заменить строку 14:

```toml
    "comtypes>=1.1.0; sys_platform == 'win32'",
```

- [ ] **Step 4: Проверить, что метаданные читаются**

Run:
```bash
python3.11 -c "
import tomllib, pathlib
d = tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))
print('requires-python:', d['project']['requires-python'])
print('deps:', d['project']['dependencies'])
"
```
Expected: `requires-python: >=3.11` и в списке зависимостей `comtypes>=1.1.0;
sys_platform == 'win32'`.

- [ ] **Step 5: Прогнать тесты**

Run: `python3.11 -m pytest tests/unit -q`
Expected: `141 passed`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "fix(packaging): маркер платформы для comtypes и requires-python >=3.11"
```

---

## Task 3: Генератор каталога как entry point

**Files:**
- Create: `simintech_api/catalog_tool.py`
- Modify: `scripts/generate_block_catalog.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_catalog_tool.py`

Смысл: сейчас генератор лежит в `scripts/` и использует хак
`sys.path.insert(0, parent.parent)`. После разделения репозиториев этот путь
сломается молча, а скиллам и MCP понадобится вызывать генератор, не зная
структуры каталогов.

- [ ] **Step 1: Написать падающий тест**

Create `tests/unit/test_catalog_tool.py`:

```python
"""Тесты точки входа генератора каталога (без COM)."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api import catalog_tool  # noqa: E402


def test_main_refuses_on_non_windows(monkeypatch, capsys):
    """Вне Windows генерация невозможна — понятный отказ, код возврата 2."""
    monkeypatch.setattr(sys, "platform", "linux")

    code = catalog_tool.main([])

    assert code == 2
    assert "Windows" in capsys.readouterr().err


def test_main_accepts_out_argument(monkeypatch, tmp_path):
    """--out принимается и передаётся в сохранение каталога."""
    monkeypatch.setattr(sys, "platform", "linux")

    code = catalog_tool.main(["--out", str(tmp_path / "c.json")])

    assert code == 2  # отказ по платформе, но аргумент разобран
```

- [ ] **Step 2: Запустить тест — должен упасть**

Run: `python3.11 -m pytest tests/unit/test_catalog_tool.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'simintech_api.catalog_tool'`

- [ ] **Step 3: Создать `simintech_api/catalog_tool.py`**

Перенести сюда содержимое `scripts/generate_block_catalog.py`, заменив хак с
`sys.path` на обычные относительные импорты:

```python
"""Генерация каталога свойств блоков из реального SimInTech (только Windows).

Точка входа пакета (`simintech-generate-catalog`). Создаёт по одному блоку
каждого класса из SUPPORTED_COM_BLOCK_CLASSES, экспортирует проект в `.xprt`
и разбирает секцию <custom_props> каждого объекта.

Зачем: в COM API нет перечисления свойств блока, а SetBlockProp не отвергает
неизвестное имя — ошибка в каталоге приводит к молчаливому отказу. Поэтому
каталог генерируется, а не пишется вручную.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .catalog import DEFAULT_CATALOG_PATH, generate_catalog


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа. Возвращает код возврата процесса."""
    parser = argparse.ArgumentParser(
        prog="simintech-generate-catalog",
        description="Сгенерировать каталог свойств блоков из SimInTech.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_CATALOG_PATH,
                        help="куда сохранить каталог")
    parser.add_argument("--keep-project", action="store_true",
                        help="не закрывать проект SimInTech после обхода")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("Генерация каталога требует Windows и mmain.exe /regserver.",
              file=sys.stderr)
        return 2

    from . import COMClient

    client = COMClient(silent_mode=True).connect()
    try:
        catalog = generate_catalog(client, keep_project=args.keep_project)
    finally:
        client.disconnect()

    path = catalog.save(args.out)
    print(f"Каталог сохранён: {path}")
    print(f"Классов с известными свойствами: {len(catalog)}")
    for class_name in catalog.classes():
        print(f"  {class_name}: {', '.join(catalog.props_for(class_name))}")

    failed = catalog.meta.get("failed") or []
    if failed:
        print(f"\nНе удалось создать (пропущены): {', '.join(failed)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `python3.11 -m pytest tests/unit/test_catalog_tool.py -v`
Expected: `2 passed`

- [ ] **Step 5: Заменить `scripts/generate_block_catalog.py` на обёртку**

Файл сохраняется ради привычного пути запуска, но перестаёт содержать логику:

```python
"""Обёртка для обратной совместимости. Логика — в simintech_api.catalog_tool."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simintech_api.catalog_tool import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Добавить entry point в `pyproject.toml`**

В секцию `[project.scripts]` добавить строку:

```toml
simintech-generate-catalog = "simintech_api.catalog_tool:main"
```

- [ ] **Step 7: Проверить, что точка входа работает**

Run:
```bash
python3.11 -c "from simintech_api.catalog_tool import main; raise SystemExit(main([]))"
```
Expected: код возврата `2` (вне Windows) и сообщение про Windows в stderr.

- [ ] **Step 8: Прогнать все тесты и линт**

Run:
```bash
python3.11 -m pytest tests/unit -q && python3.11 -m flake8 simintech_api/ scripts/ --max-line-length=88 --extend-ignore=E203,W503
```
Expected: `143 passed`, flake8 без вывода.

- [ ] **Step 9: Commit**

```bash
git add simintech_api/catalog_tool.py scripts/generate_block_catalog.py pyproject.toml tests/unit/test_catalog_tool.py
git commit -m "feat(catalog): генератор каталога как entry point пакета"
```

---

## Task 4: Перенести работу с базой сигналов

**Files:**
- Create: `simintech_api/sdb.py` (из `simintech-connector/simintech_connector/sdb_adapter.py`)
- Test: `tests/unit/test_sdb.py`

Модуль `sdb_adapter.py` не имеет внутренних зависимостей (только стандартная
библиотека), поэтому переносится как есть с переименованием.

- [ ] **Step 1: Скопировать модуль**

```bash
cp /mnt/c/git/simintech-connector/simintech_connector/sdb_adapter.py /mnt/c/git/simintech-mcp/simintech_api/sdb.py
```

- [ ] **Step 2: Заменить докстринг модуля**

В `simintech_api/sdb.py` первой строкой было `"""Адаптер для работы с Signal
Database (SDB) SimInTech.` — заменить шапку на:

```python
"""База сигналов SimInTech (SDB): разбор XML-выгрузки.

База трёхуровневая: Категория → Группа → Сигнал. Полное имя сигнала
собирается как ``<группа>_<сигнал>``; для поиска индексируется и составной
ключ ``<категория>.<группа>.<имя>``.

Структура XML и экспорт макросом описаны в
`automation/signal-db.md`. Перенесено из репозитория `simintech-connector`
(заархивирован 2026-09-10).
"""
```

- [ ] **Step 3: Написать тесты**

Create `tests/unit/test_sdb.py`:

```python
"""Тесты разбора базы сигналов SimInTech (без COM)."""

import os
import sys
import textwrap

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api.sdb import SignalDatabase  # noqa: E402

# Реальный формат выгрузки: значения обёрнуты в бэктики
SDB_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <root>
      <database>
        <category>
          <name>`Управление`</name>
          <nametemplate>`%s`</nametemplate>
          <group>
            <name>`Регулятор`</name>
            <signals>
              <data>
                <name>`Kp`</name>
                <caption>`Пропорциональный коэффициент`</caption>
                <type>`0`</type>
                <mode>`1`</mode>
                <value>`1.5`</value>
              </data>
              <data>
                <name>`Ki`</name>
                <caption>`Интегральный коэффициент`</caption>
                <type>`0</type>
                <mode>`1`</mode>
                <value>`0.5`</value>
              </data>
            </signals>
          </group>
        </category>
      </database>
    </root>
""")


def test_from_xml_loads_categories_and_signals(tmp_path):
    path = tmp_path / "signals.xml"
    path.write_text(SDB_XML, encoding="utf-8")

    db = SignalDatabase.from_xml(path)

    assert db.is_loaded is True
    assert [c["name"] for c in db.list_categories()] == ["Управление"]


def test_full_signal_name_is_group_underscore_name(tmp_path):
    """Полное имя сигнала — <группа>_<сигнал>, как его адресует SimInTech."""
    path = tmp_path / "signals.xml"
    path.write_text(SDB_XML, encoding="utf-8")

    info = SignalDatabase.from_xml(path).get_signal_info("Регулятор_Kp")

    assert info is not None
    assert info["name"] == "Kp"
    assert info["group"] == "Регулятор"


def test_find_signal_by_pattern(tmp_path):
    path = tmp_path / "signals.xml"
    path.write_text(SDB_XML, encoding="utf-8")

    found = SignalDatabase.from_xml(path).find_signal("K*")

    assert sorted(f["name"] for f in found) == ["Ki", "Kp"]


def test_signals_are_indexed_by_composite_key(tmp_path):
    """Составной ключ <категория>.<группа>.<имя> тоже в индексе."""
    path = tmp_path / "signals.xml"
    path.write_text(SDB_XML, encoding="utf-8")

    info = SignalDatabase.from_xml(path).get_signal_info("Управление.Регулятор.Kp")

    assert info is not None
    assert info["name"] == "Kp"
```

- [ ] **Step 4: Запустить тесты**

Run: `python3.11 -m pytest tests/unit/test_sdb.py -v`
Expected: `4 passed`. Если `get_signal_info` возвращает не словарь, а
`SDBSignalInfo` — привести тест к фактическому типу, а не менять модуль.

- [ ] **Step 5: Commit**

```bash
git add simintech_api/sdb.py tests/unit/test_sdb.py
git commit -m "feat(sdb): перенести работу с базой сигналов из simintech-connector"
```

---

## Task 5: Перенести запуск через командную строку

**Files:**
- Create: `simintech_api/cli_runner.py` (из `cli_adapter.py`)
- Test: `tests/unit/test_cli_runner.py`

`cli_adapter.py` зависит от `.models` (`SimulationResult`, `ProjectState`).
`ProjectState` в нём **не используется** — не переносить. `SimulationResult`
нужен; `DataType`, `SignalInfo`, `ProjectInfo` из `models.py` не нужны:
`DataType` дублирует `simintech_api.constants.DataType`, а `SignalInfo` —
другая сущность, чем `simintech_api.model.SignalInfo` (совпадение имён
опасно, не смешивать).

- [ ] **Step 1: Скопировать модуль**

```bash
cp /mnt/c/git/simintech-connector/simintech_connector/cli_adapter.py /mnt/c/git/simintech-mcp/simintech_api/cli_runner.py
```

- [ ] **Step 2: Разорвать зависимость от `models`**

В `simintech_api/cli_runner.py` заменить строку 13:

```python
from .models import SimulationResult, ProjectState
```

на:

```python
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CLIResult:
    """Результат запуска mmain.exe через командную строку.

    Args:
        success: завершился ли процесс успешно.
        message: описание результата.
        data: ``{"stdout": ..., "stderr": ...}`` при наличии вывода.
    """

    success: bool
    message: str = ""
    data: Optional[Any] = None
```

- [ ] **Step 3: Переименовать `SimulationResult` → `CLIResult` в модуле**

`SimulationResult` — имя из старого коннектора; у нас уже есть понятие
симуляции, и совпадение сбивает. Заменить все вхождения в файле:

```bash
cd /mnt/c/git/simintech-mcp && sed -i 's/SimulationResult/CLIResult/g' simintech_api/cli_runner.py
```

- [ ] **Step 4: Заменить докстринг модуля**

Шапка файла после копирования ссылается на старый контекст. Заменить на:

```python
"""Запуск SimInTech через командную строку (`mmain.exe`).

Резервный путь без COM: работает из WSL, где COM недоступен. Опции
документированы в справке SimInTech («Командная строка»). Вывод
`mmain.exe` приходит в cp1251/cp866 — декодируется в `_decode`.

Перенесено из репозитория `simintech-connector` (заархивирован 2026-09-10).
"""
```

- [ ] **Step 5: Написать тесты**

Create `tests/unit/test_cli_runner.py`:

```python
"""Тесты CLI-обёртки над mmain.exe (без запуска SimInTech)."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from simintech_api.cli_runner import CLIAdapter, CLIResult  # noqa: E402


def test_decode_handles_cp1251():
    """Вывод mmain.exe приходит в cp1251 — русский текст читается."""
    raw = "Ошибка расчёта".encode("cp1251")

    assert CLIAdapter._decode(raw) == "Ошибка расчёта"


def test_decode_falls_back_to_utf8():
    raw = "OK".encode("utf-8")

    assert CLIAdapter._decode(raw) == "OK"


def test_build_cmd_adds_silentmode():
    cli = CLIAdapter(mmain_path=__file__)  # существующий файл как заглушка пути

    cmd = cli.build_cmd("/start", "/run")

    assert cmd[0] == __file__
    assert cmd[1] == "/silentmode"
    assert cmd[2:] == ["/start", "/run"]


def test_build_cmd_without_silentmode():
    cli = CLIAdapter(mmain_path=__file__, silent=False)

    assert cli.build_cmd("/exit") == [__file__, "/exit"]


def test_resolve_mmain_path_accepts_directory(tmp_path):
    """Если передан каталог — ищется mmain.exe внутри."""
    (tmp_path / "mmain.exe").write_bytes(b"")

    cli = CLIAdapter(mmain_path=str(tmp_path))

    assert cli.mmain_path.endswith("mmain.exe")


def test_resolve_mmain_path_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("SIMINTECH_PATH", raising=False)
    monkeypatch.delenv("SIMINTECH", raising=False)

    with pytest.raises(FileNotFoundError):
        CLIAdapter(mmain_path=str(tmp_path / "нет" / "mmain.exe"))


def test_run_sync_reports_missing_binary():
    """Несуществующий бинарник — результат с success=False, а не исключение."""
    cli = CLIAdapter(mmain_path="/nonexistent/mmain.exe")

    result = cli.run_sync("/exit", timeout=5)

    assert isinstance(result, CLIResult)
    assert result.success is False
    assert "не найден" in result.message
```

- [ ] **Step 6: Запустить тесты**

Run: `python3.11 -m pytest tests/unit/test_cli_runner.py -v`
Expected: `7 passed`

- [ ] **Step 7: Commit**

```bash
git add simintech_api/cli_runner.py tests/unit/test_cli_runner.py
git commit -m "feat(cli): перенести запуск mmain.exe из simintech-connector"
```

---

## Task 6: Примеры запуска макроса и пака

**Files:**
- Create: `examples/run_macro.py`, `examples/run_pak.py`

Исходные `run_macro.py` и `run_pak.py` из коннектора непригодны как примеры:
жёстко зашиты пути `/mnt/c/git/sar/...` и `sys.path.insert` на старый
репозиторий. Переписать с параметрами командной строки.

- [ ] **Step 1: Создать `examples/run_macro.py`**

```python
"""Запуск макроса SimInTech с потоковым логом.

Пример:
    python examples/run_macro.py macros.txt
    python examples/run_macro.py macros.txt --log mmain_run.log

Требуется Windows-версия SimInTech и путь к mmain.exe (переменная окружения
SIMINTECH_PATH или путь по умолчанию, см. simintech_api.cli_runner).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simintech_api.cli_runner import CLIAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("macro", type=Path, help="файл макроса")
    parser.add_argument("--log", type=Path, default=None, help="файл лога")
    parser.add_argument("--timeout", type=int, default=600,
                        help="тайм-аут в секундах")
    args = parser.parse_args()

    cli = CLIAdapter()
    cmd = cli.build_cmd("/macros", str(args.macro.resolve()))
    print("Запуск:", " ".join(cmd), flush=True)

    log = args.log.open("w", encoding="utf-8") if args.log else None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        for line in iter(proc.stdout.readline, b""):
            text = cli._decode(line).rstrip()
            if not text:
                continue
            print(text, flush=True)
            if log:
                log.write(text + "\n")
                log.flush()
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        print("\nПрервано пользователем", file=sys.stderr)
        return 130
    finally:
        if log:
            log.close()

    print(f"\nЗавершён с кодом {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Создать `examples/run_pak.py`**

```python
"""Запуск пака SimInTech (.pak) с потоковым логом.

Пример:
    python examples/run_pak.py /path/to/model.pak

Требуется Windows-версия SimInTech и путь к mmain.exe (см.
simintech_api.cli_runner).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simintech_api.cli_runner import CLIAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pak", type=Path, help="файл пака")
    parser.add_argument("--log", type=Path, default=None, help="файл лога")
    args = parser.parse_args()

    cli = CLIAdapter()
    cmd = cli.build_cmd("/start", "/run", "/exitonstop", str(args.pak.resolve()))
    print("Запуск:", " ".join(cmd), flush=True)

    log = args.log.open("w", encoding="utf-8") if args.log else None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        for line in iter(proc.stdout.readline, b""):
            text = cli._decode(line).rstrip()
            if not text:
                continue
            print(text, flush=True)
            if log:
                log.write(text + "\n")
                log.flush()
        proc.wait()
    finally:
        if log:
            log.close()

    print(f"\nЗавершён с кодом {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Проверить, что примеры разбирают аргументы**

Run:
```bash
python3.11 examples/run_macro.py --help && python3.11 examples/run_pak.py --help
```
Expected: справка обоих скриптов, код возврата 0.

- [ ] **Step 4: Прогнать линт**

Run: `python3.11 -m flake8 examples/run_macro.py examples/run_pak.py --max-line-length=88 --extend-ignore=E203,W503`
Expected: без вывода.

- [ ] **Step 5: Commit**

```bash
git add examples/run_macro.py examples/run_pak.py
git commit -m "feat(examples): запуск макроса и пака через CLI"
```

---

## Task 7: Сверить `models.py` и завершить роспуск

**Files:**
- Modify: `docs/roadmap-agentic-ecosystem.md`

- [ ] **Step 1: Сверить остатки `models.py`**

Проверить, что из `simintech-connector/simintech_connector/models.py` ничего не
осталось неперенесённым:

Run:
```bash
grep -rn "ProjectInfo\|ProjectState" /mnt/c/git/simintech-mcp/simintech_api/ || echo "не используются — переносить нечего"
```
Expected: `не используются — переносить нечего`.
Обоснование: `DataType` дублирует `constants.DataType`, `SignalInfo` конфликтует
именем с `model.SignalInfo`, `ProjectInfo`/`ProjectState` не используются ни в
`cli_runner`, ни в `sdb`.

- [ ] **Step 2: Отметить выполненное в дорожной карте**

В `docs/roadmap-agentic-ecosystem.md` в разделе §6 таблицу роспуска дополнить
столбцом «Статус» со значениями:
- `sdb_adapter.py` → ✅ `simintech_api/sdb.py`
- `cli_adapter.py` → ✅ `simintech_api/cli_runner.py`
- `run_macro.py`, `run_pak.py` → ✅ переписаны в `examples/`
- `models.py` → ✅ разобран, переносить нечего
- `com_adapter.py`, `server.py`, `examples/basic_usage.py` → ⬜ удаляются в плане 2

- [ ] **Step 3: Полная проверка**

Run:
```bash
python3.11 -m pytest tests/unit -q
python3.11 -m flake8 simintech_api/ simintech_mcp/ scripts/ examples/ --max-line-length=88 --extend-ignore=E203,W503
```
Expected: все тесты проходят; flake8 без вывода для `simintech_api/`,
`simintech_mcp/`, `scripts/`. Для `examples/` допустимы унаследованные
замечания E402/E501/F401 в файлах, которые этот план не трогал
(`model1_amplifier.py`, `model2_pid.py`, `model3_complex.py`,
`feedback_model.py`, `rc_chain.py`, `pid_controller.py`) — они были до работы.

- [ ] **Step 4: Commit и тег**

```bash
git add docs/roadmap-agentic-ecosystem.md
git commit -m "docs: отметить перенос кода из simintech-connector"
git tag v0.2.0
git push origin main --tags
```

---

## Проверка результата

После выполнения всех задач репозиторий должен:

1. Содержать `LICENSE` (MIT), `.gitattributes`, `.editorconfig`, расширенный
   `.gitignore` — набор, который копируется в два других репозитория.
2. Иметь корректные метаданные: `requires-python >= 3.11`, `comtypes` с
   маркером платформы.
3. Экспортировать генератор каталога как `simintech-generate-catalog` и
   сохранять работоспособность `scripts/generate_block_catalog.py`.
4. Содержать `simintech_api/sdb.py` и `simintech_api/cli_runner.py` с тестами.
5. Содержать переписанные `examples/run_macro.py`, `examples/run_pak.py`.
6. Проходить `pytest tests/unit` (ожидается не менее 154 тестов) и flake8.

**Проверка на Windows** (обязательна перед планом 2):

```bash
python scripts/generate_block_catalog.py          # каталог генерируется
python -m pytest tests/unit -q                     # те же тесты на Windows
python -m pytest tests/integration -m integration  # 4 passed, 1 skipped
```

**Приёмка:** `mmain.exe` порождается только интеграционными тестами и
завершается фикстурой; после прогона в `sar/SAR/signals.dbconf` возможна
перезапись (побочный эффект открытия проекта) — проверять `git status` в `sar`
и не коммитить её.

---

## Что дальше (план 2)

Собственно разделение: переименование `simintech-code-library` →
`simintech-code`, переезд `simintech_api/` и `examples/` туда, создание
`simintech-skill`, перевязка зависимостей, раскладка общих конфигов,
удаление `C:\git\simintech-connector`. Пишется после того, как этот план
выполнен — точные списки файлов зависят от его результата.
