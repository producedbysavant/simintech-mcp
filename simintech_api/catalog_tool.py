"""Генерация каталога свойств блоков из реального SimInTech (только Windows).

Точка входа пакета (`simintech-generate-catalog`). Создаёт по одному блоку
каждого класса из `SUPPORTED_COM_BLOCK_CLASSES`, экспортирует проект в `.xprt`
и разбирает секцию ``<custom_props>`` каждого объекта.

Зачем: в COM API нет перечисления свойств блока, а `SetBlockProp` не отвергает
неизвестное имя — ошибка в каталоге приводит к молчаливому отказу (параметр
«установлен», расчёт идёт по прежнему значению). Поэтому каталог генерируется,
а не пишется вручную.

Запуск (Windows, после `mmain.exe /regserver`)::

    simintech-generate-catalog
    simintech-generate-catalog --out /tmp/catalog.json
    simintech-generate-catalog --keep-project
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
