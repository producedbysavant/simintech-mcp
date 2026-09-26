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

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
