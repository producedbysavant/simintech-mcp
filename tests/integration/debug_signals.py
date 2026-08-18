"""Диагностика: почему GetProjectSignalList возвращает пустой список.

Запуск (Windows):
    python tests/integration/debug_signals.py [путь_к_модели]

Выводит raw-результаты COM-вызовов на каждом шаге, чтобы понять, где
теряются сигналы. Пробует несколько последовательностей.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api import COMClient


def dump(client, prj_id, label):
    print(f"\n--- {label} ---")
    try:
        list_raw = client.call("GetProjectSignalList", prj_id)
        print(f"  GetProjectSignalList -> {list_raw!r} (type={type(list_raw).__name__})")
        list_id = _i64(list_raw)
        count_raw = client.call("GetListCount", list_id)
        print(f"  GetListCount({list_id}) -> {count_raw!r} (type={type(count_raw).__name__})")
        count = _i64(count_raw)
        for i in range(min(count, 5)):
            info = client.call("GetDataInfoFromList", list_id, i)
            print(f"  GetDataInfoFromList[{i}] -> {info!r} (type={type(info).__name__})")
    except Exception as exc:
        print(f"  ОШИБКА: {type(exc).__name__}: {exc}")


def _i64(v):
    if v is None:
        return 0
    if hasattr(v, "value"):
        return int(v.value)
    if isinstance(v, (tuple, list)):
        return int(v[0]) if v else 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\SimInTech64\Temp\fsm_demo.prt"
    if not os.path.exists(path):
        print(f"Модель не найдена: {path}")
        sys.exit(1)

    client = COMClient(silent_mode=True).connect()
    prj_id = client.open_project(path)
    print(f"Проект открыт: id={prj_id}")

    # 1. Сразу после открытия (без старта)
    dump(client, prj_id, "сразу после OpenProject")

    # 2. После ProjectStart
    client.call("ProjectStart", prj_id)
    dump(client, prj_id, "после ProjectStart")

    # 3. После ProjectRun
    client.call("ProjectRun", prj_id)
    dump(client, prj_id, "после ProjectRun")

    # 4. После ProjectStep
    client.call("ProjectStep", prj_id)
    dump(client, prj_id, "после ProjectStep")

    # 5. GetMainPage + SetCurrentPage, затем снова список
    try:
        page_id = _i64(client.call("GetMainPage", prj_id))
        res = client.call("SetCurrentPage", prj_id, page_id)
        print(f"\nGetMainPage -> {page_id}, SetCurrentPage -> {res!r}")
        dump(client, prj_id, "после SetCurrentPage(GetMainPage)")
    except Exception as exc:
        print(f"\n  Ошибка SetCurrentPage: {exc}")

    client.shutdown()
    print("\nДиагностика завершена.")


if __name__ == "__main__":
    main()
