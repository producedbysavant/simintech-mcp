"""Диагностика: как реально называются сигналы блока в COM.

Строит модель с Константой (Name='const_sig'), делает ProjectStart и
выводит: список сигналов (если есть), и результаты FindSignalData для
нескольких вариантов имени. Это определит, по какому имени читать сигнал.

Запуск (Windows): python tests/integration/debug_block_signals.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api import COMClient


def i64(v):
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
    client = COMClient(silent_mode=True).connect()

    prj_id = client.new_project()
    print(f"Проект создан: id={prj_id}")

    # Создаём блок Константа с именем const_sig
    block_id = i64(client.call("CreateBlock", prj_id, 0, 0, "Константа"))
    print(f"CreateBlock('Константа') -> {block_id}")
    client.call("InitBlock", block_id)
    r = client.call("SetBlockProp", block_id, "Name", "const_sig")
    print(f"SetBlockProp Name=const_sig -> {r!r}")
    client.call("SetBlockProp", block_id, "y0", "5")

    # Проект с блоком Усилитель
    gid = i64(client.call("CreateBlock", prj_id, 0, 0, "Усилитель"))
    client.call("InitBlock", gid)
    client.call("SetBlockProp", gid, "a", "2")

    # Попытка соединения
    try:
        op = i64(client.call("GetOutPort", block_id, 0))
        ip = i64(client.call("GetInPort", gid, 0))
        print(f"Порты: out={op}, in={ip}")
        wid = i64(client.call("CreateWire", prj_id, 0, 0, 0, -1, op, ip, 0))
        print(f"CreateWire -> {wid}")
    except Exception as exc:
        print(f"Соединение: {exc}")

    # Инициализация расчёта
    try:
        client.call("ProjectStart", prj_id)
        print("ProjectStart OK")
    except Exception as exc:
        print(f"ProjectStart: {exc}")

    # 1. Список сигналов
    try:
        list_id = i64(client.call("GetProjectSignalList", prj_id))
        count = i64(client.call("GetListCount", list_id))
        print(f"\nGetProjectSignalList -> list={list_id}, count={count}")
        for i in range(min(count, 20)):
            info = client.call("GetDataInfoFromList", list_id, i)
            print(f"  signal[{i}]: {info!r}")
    except Exception as exc:
        print(f"\nGetProjectSignalList: {exc}")

    # 2. FindSignalData по разным именам
    print("\nFindSignalData по вариантам имени:")
    candidates = [
        "const_sig", "Const", "const", "k0", "Константа",
        "const_sig.y0", "const_sig_1", "const_sig_2",
        "const_sig_3", "const_sig_4", "Усилитель", "const_sig_out",
        "const_sig_Out", "const_sig_1",
    ]
    for name in candidates:
        try:
            desc = client.call("FindSignalData", name, prj_id)
            data_id = getattr(desc, "DataId", None) if not isinstance(desc, (tuple, list)) else (desc[0] if desc else 0)
            dt = getattr(desc, "DataType", None) if not isinstance(desc, (tuple, list)) else (desc[1] if len(desc) > 1 else None)
            print(f"  '{name}': DataId={data_id!r}, DataType={dt!r}")
        except Exception as exc:
            print(f"  '{name}': ERROR {exc}")

    client.shutdown()
    print("\nГотово.")


if __name__ == "__main__":
    main()
