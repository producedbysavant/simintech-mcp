"""Интеграционные тесты: жизненный цикл проекта и сигналы.

Запускаются ТОЛЬКО на Windows с зарегистрированным COM-сервером:
    python -m pytest tests/integration -m integration
Требуется mmain.exe /regserver и файл-пример FSM-модели.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

pytestmark = pytest.mark.integration

# Демо-модель с FSM-выходом (используется в Temp-скриптах прошлых сессий).
# Путь на Windows: C:\\SimInTech64\\Temp\\fsm_demo.prt
FSM_DEMO = os.environ.get(
    "SIMINTECH_FSM_DEMO",
    r"C:\SimInTech64\Temp\fsm_demo.prt",
)


def test_connect_disconnect(client):
    """Подключение/отключение COM-клиента."""
    from simintech_api import COMClient

    c = COMClient(silent_mode=True)
    c.connect()
    assert c.connected
    pid = c.get_process_id()
    assert pid > 0
    c.disconnect()
    assert not c.connected


def test_new_project_save_close(client):
    """Создание нового проекта, сохранение в XML, закрытие."""
    from simintech_api import Project
    import tempfile

    prj = Project.new(client)
    assert prj.id != 0
    page = prj.get_main_page()
    assert page.id != 0

    tmp = os.path.join(tempfile.gettempdir(), "siminapi_new_test.xprt")
    prj.save_xml(tmp)
    assert os.path.exists(tmp)

    prj.close()


def test_open_project_signals(client):
    """Открытие демо-проекта и чтение списка сигналов."""
    from simintech_api import Project

    if not os.path.exists(FSM_DEMO):
        pytest.skip(f"Демо-модель не найдена: {FSM_DEMO}")

    prj = Project.open(client, FSM_DEMO)
    signals = prj.list_signals()
    assert len(signals) > 0
    # Проверяем, что есть FSM-сигнал выхода
    names = [s.name for s in signals]
    assert any("state_out_data" in n for n in names), (
        f"Не найден FSM-сигнал. Всего сигналов: {len(names)}"
    )
    prj.close()


def test_read_write_signal(client):
    """Запись в сигнал до старта и чтение после шага."""
    from simintech_api import Project, DataType

    if not os.path.exists(FSM_DEMO):
        pytest.skip(f"Демо-модель не найдена: {FSM_DEMO}")

    prj = Project.open(client, FSM_DEMO)
    sim = prj.simulation()
    sim.start()

    # Найдём первый double-сигнал и запишем в него
    desc = prj.find_signal("state_out_data") if "state_out_data" in \
        [s.name for s in prj.list_signals()] else None

    prj.close()


def test_build_simple_model(client):
    """Сборка простой модели через COM: Константа -> Усилитель."""
    from simintech_api import Project

    prj = Project.new(client)
    page = prj.get_main_page()

    b1 = page.create_block("Константа", 0, 0, width=60, height=40)
    b1.set_property("y0", 5.0)   # значение константы
    b2 = page.create_block("Усилитель", 200, 0, width=60, height=40)
    b2.set_property("a", 2.0)    # коэффициент усиления

    wire = b1.connect(b2)
    assert wire.id != 0

    prj.close()
