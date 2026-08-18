"""Общие фикстуры pytest.

Integration-тесты (маркер integration) требуют Windows с зарегистрированным
COM-сервером SimInTech (mmain.exe /regserver). В другом окружении они
пропускаются через pytest.importorskip.
"""

import os
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: тесты, требующие реального COM-сервера SimInTech на Windows",
    )


@pytest.fixture(scope="session")
def simintech_available() -> bool:
    """True, если окружение Windows и COM-сервер доступен.

    Проверка по платформе + попытка подключения. Если сервер не
    зарегистрирован — integration-тесты пропускаются.
    """
    if sys.platform != "win32":
        return False
    try:
        from simintech_api import COMClient
        client = COMClient(silent_mode=True)
        client.connect()
        client.disconnect()
        return True
    except Exception:
        return False


@pytest.fixture()
def client(simintech_available):
    """Подключённый COMClient (только integration)."""
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
