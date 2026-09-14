"""Обёртка для привычного пути запуска.

Логика живёт в `simintech_api.catalog_tool` и доступна как точка входа
`simintech-generate-catalog`. Этот файл сохранён, чтобы команда
`python scripts/generate_block_catalog.py` продолжала работать.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simintech_api.catalog_tool import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
