"""Настройка тестов до импорта проверяемого пакета.

Путь к корню репозитория добавляется здесь, а не в каждом тест-модуле: pytest
импортирует `conftest.py` раньше, чем собирает модули, поэтому к моменту
`import simintech_mcp` путь уже на месте. Без этого пакет брался бы из
editable-установки, то есть из другого чекаута, и тесты проверяли бы не тот код.

Туда же добавляется `scripts/`: `check_pins.py` — инструмент сборки, а не часть
пакета, но его проверяет `test_check_pins.py`.
"""

import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


@pytest.fixture(autouse=True)
def _clear_experiment_ledger():
    """Ledger живёт в процессе: каждый тест начинает с пустого состояния."""
    from simintech_mcp import experiment_ledger

    experiment_ledger.clear()
    yield
    experiment_ledger.clear()


@pytest.fixture(autouse=True)
def _clear_session_file_stamps():
    """Отпечатки файлов проекта: состояние процесса, каждый тест с чистого."""
    from simintech_mcp import session

    previous = session._project_stamp
    session._known_stamps.clear()
    yield
    session._known_stamps.clear()
    session._project_stamp = previous
