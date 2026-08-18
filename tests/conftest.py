"""Общие фикстуры pytest.

Integration-тесты (маркер integration) требуют Windows с зарегистрированным
COM-сервером SimInTech (mmain.exe /regserver). В другом окружении они
пропускаются.

Очистка процессов: session-фикстура запоминает PID'ы mmain.exe ДО прогона
и завершает ТОЛЬКО появившиеся после (процессы, порождённые тестами).
Предсуществующие процессы SimInTech (запущенные пользователем) НЕ трогаются.

Чтобы принудительно запустить тесты даже если авто-детект не сработал:
    pytest tests/integration -m integration --run-simintech
"""

import os
import sys

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-simintech",
        action="store_true",
        default=False,
        help="принудительно запускать integration-тесты, даже если "
             "авто-детект COM недоступен",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: тесты, требующие реального COM-сервера SimInTech на Windows",
    )
    config.addinivalue_line(
        "markers",
        "performance: бенчмарки производительности placer/router",
    )


@pytest.fixture(scope="session")
def simintech_available(pytestconfig) -> bool:
    """True, если COM-сервер SimInTech доступен."""
    if pytestconfig.getoption("--run-simintech"):
        return True
    if sys.platform != "win32":
        print("\n[simintech] COM недоступен: платформа != Windows. "
              "Запустите integration-тесты на Windows.", file=sys.stderr)
        return False
    try:
        from simintech_api import COMClient
        client = COMClient(silent_mode=True)
        client.connect()
        client.disconnect()
        return True
    except Exception as exc:
        print(f"\n[simintech] COM недоступен: {exc}", file=sys.stderr)
        return False


@pytest.fixture(scope="session", autouse=True)
def _cleanup_mmain_processes():
    """Завершить процессы mmain.exe, появившиеся в ходе тестов.

    Собирает PID'ы ДО прогона; после — убивает ТОЛЬКО разность
    (появившиеся). Предсуществующие процессы SimInTech не трогаются.

    Отключение очистки (если нужно сохранить процессы): переменная
    окружения SIMINTECH_KEEP_MMAIN=1.

    Полная очистка ВСЕХ процессов mmain.exe (включая предсуществующие,
    например накопленный мусор прошлых прогонов): переменная окружения
    SIMINTECH_KILL_ALL_MMAIN=1. Осторожно: убьёт и процессы, запущенные
    вручную.
    """
    if os.environ.get("SIMINTECH_KEEP_MMAIN") == "1":
        yield
        return
    if sys.platform != "win32":
        yield
        return
    from simintech_api.utils.processes import get_mmain_pids, kill_pids
    import time

    kill_all = os.environ.get("SIMINTECH_KILL_ALL_MMAIN") == "1"
    before = set(get_mmain_pids())
    print(f"\n[simintech] mmain.exe ДО тестов: "
          f"{sorted(before) if before else '(не обнаружены)'}", file=sys.stderr)
    yield
    time.sleep(1.0)  # дать процессам завершить работу
    after = set(get_mmain_pids())

    if kill_all:
        # Полная очистка: все процессы mmain.exe
        print(f"[simintech] SIMINTECH_KILL_ALL_MMAIN: завершаю ВСЕ: "
              f"{sorted(after) if after else '(нет)'}", file=sys.stderr)
        kill_pids(after)
        time.sleep(0.5)
        still = set(get_mmain_pids())
        if still:
            print(f"[simintech] ВНИМАНИЕ: не удалось завершить: "
                  f"{sorted(still)}", file=sys.stderr)
        return

    print(f"[simintech] mmain.exe ПОСЛЕ тестов: "
          f"{sorted(after) if after else '(не обнаружены)'}", file=sys.stderr)
    new = after - before
    if new:
        print(f"[simintech] Завершаю процессы, порождённые тестами: "
              f"{sorted(new)}", file=sys.stderr)
        kill_pids(new)
        time.sleep(0.5)
        # Повторная проверка — сообщаем, если что-то осталось
        still = set(get_mmain_pids()) - before
        if still:
            print(f"[simintech] ВНИМАНИЕ: не удалось завершить: "
                  f"{sorted(still)}", file=sys.stderr)
    else:
        print("[simintech] Новых процессов не обнаружено — очистка не требуется.",
              file=sys.stderr)


@pytest.fixture()
def client(simintech_available):
    """Подключённый COMClient (только integration).

    В teardown — только disconnect. Процессы завершает session-фикстура
    _cleanup_mmain_processes (по новизне PID), чтобы не трогать
    предсуществующие процессы SimInTech.
    """
    if not simintech_available:
        pytest.skip("COM SimInTech недоступен (нужна Windows + mmain.exe /regserver)")
    from simintech_api import COMClient
    c = COMClient(silent_mode=True)
    c.connect()
    yield c
    try:
        c.disconnect()
    except Exception:
        pass
