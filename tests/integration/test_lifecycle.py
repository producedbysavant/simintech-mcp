"""Интеграционные тесты: жизненный цикл проекта и сигналы.

Запускаются ТОЛЬКО на Windows с зарегистрированным COM-сервером:
    python -m pytest tests/integration -m integration
Требуется mmain.exe /regserver и файл-пример FSM-модели.

После прогона процесс SimInTech завершается принудительно (fixture client
вызывает shutdown()), чтобы mmain.exe не оставался висеть.
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


def test_connect_disconnect():
    """Подключение/отключение COM-клиента."""
    from simintech_api import COMClient

    c = COMClient(silent_mode=True)
    c.connect()
    assert c.connected
    pid = c.get_process_id()
    assert pid > 0
    c.disconnect()
    assert not c.connected
    c.shutdown()


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
    assert len(signals) > 0, "Список сигналов пуст"
    # Проверяем, что у каждого сигнала есть имя и дескриптор
    for info in signals[:10]:
        assert info.name, "Сигнал без имени"
        assert info.descriptor is not None
        assert info.descriptor.is_valid
    prj.close()


def test_read_write_signal(client):
    """Чтение и запись сигнала в демо-проекте."""
    from simintech_api import Project, Signal

    if not os.path.exists(FSM_DEMO):
        pytest.skip(f"Демо-модель не найдена: {FSM_DEMO}")

    prj = Project.open(client, FSM_DEMO)
    signals = prj.list_signals()
    assert len(signals) > 0

    # Берём первый сигнал, читаемый как float (DataType 0)
    sig = None
    for info in signals:
        if info.descriptor and info.descriptor.DataType == 0:
            sig = prj.signal(info.name)
            break
    if sig is None:
        pytest.skip("В демо-модели нет double-сигналов")

    sim = prj.simulation()
    sim.start()

    # Чтение
    val = sig.read()
    assert isinstance(val, (int, float)), f"ReadAsFloat вернул {type(val)}"

    # Запись (сигнал константы/входа допускает запись)
    try:
        sig.write(1.0)
        sim.step()
        val2 = sig.read()
    except Exception as exc:
        pytest.skip(f"Запись в сигнал не поддерживается моделью: {exc}")
    else:
        assert val2 is not None

    sim.stop()
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
