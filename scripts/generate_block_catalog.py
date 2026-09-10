"""Генерация каталога свойств блоков из реального SimInTech (только Windows).

Создаёт по одному блоку каждого класса из `SUPPORTED_COM_BLOCK_CLASSES`,
экспортирует проект в `.xprt` и вычитывает фактические имена свойств с
их значениями по умолчанию.

Зачем: в COM API нет перечисления свойств блока, а `SetBlockProp` не отвергает
неизвестное имя — ошибка в каталоге приводит к молчаливому отказу (параметр
«установлен», расчёт идёт по старому значению). Поэтому каталог генерируется,
а не пишется вручную.

Запуск (Windows, после `mmain.exe /regserver`):

    python scripts/generate_block_catalog.py
    python scripts/generate_block_catalog.py --out /tmp/catalog.json
    python scripts/generate_block_catalog.py --keep-project   # не закрывать проект
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simintech_api import COMClient                      # noqa: E402
from simintech_api.catalog import (                      # noqa: E402
    DEFAULT_CATALOG_PATH,
    generate_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_CATALOG_PATH,
                        help="куда сохранить каталог")
    parser.add_argument("--keep-project", action="store_true",
                        help="не закрывать проект SimInTech после обхода")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("Генерация каталога требует Windows и mmain.exe /regserver.",
              file=sys.stderr)
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
