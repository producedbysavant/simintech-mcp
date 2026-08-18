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
    """
    if sys.platform != "win32":
        yield
        return
    from simintech_api.utils.processes import get_mmain_pids, kill_pids
    before = set(get_mmain_pids())
    yield
    import time
    time.sleep(0.3)  # дать процессам завершить работу
    after = set(get_mmain_pids())
    new = after - before
    if new:
        print(f"\n[simintech] Завершаю процессы mmain.exe, порождённые "
              f"тестами: {sorted(new)}", file=sys.stderr)
        kill_pids(new)


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
