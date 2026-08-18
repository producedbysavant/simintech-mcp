"""Общие фикстуры pytest.

Integration-тесты (маркер integration) требуют Windows с зарегистрированным
COM-сервером SimInTech (mmain.exe /regserver). В другом окружении они
пропускаются через pytest.importorskip.

Чтобы принудительно запустить их даже если авто-детект не сработал:
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
    """True, если COM-сервер SimInTech доступен.

    При --run-simintech всегда True (пользователь берёт ответственность).
    Иначе проверка платформы + попытка подключения. При недоступности —
    выводит причину в stderr (для диагностики), тест пропускается.
    """
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


@pytest.fixture()
def client(simintech_available):
    """Подключённый COMClient (только integration).

    В teardown — принудительное завершение процесса SimInTech, чтобы
    после тестов не оставался висеть mmain.exe.
    """
    if not simintech_available:
        pytest.skip("COM SimInTech недоступен (нужна Windows + mmain.exe /regserver)")
    from simintech_api import COMClient
    c = COMClient(silent_mode=True)
    c.connect()
    yield c
    try:
        c.shutdown()          # disconnect + taskkill по PID
    except Exception:
        try:
            c.disconnect()
        except Exception:
            pass
