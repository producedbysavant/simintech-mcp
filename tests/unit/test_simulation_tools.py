"""Расчёт и живые сигналы."""

from __future__ import annotations

import pytest

from simintech_mcp import runtime, session
from simintech_mcp.server import mcp
from simintech_mcp.tools import simulation as simulation_tools

from _support import _error, _install_fake_simulation, _text


@pytest.mark.anyio
async def test_run_reports_failure_when_time_did_not_move(monkeypatch):
    """run отказывает, если расчёт не дошёл, — а не сообщает об успехе.

    Текстом это было бы неотличимо для клиента, доверяющего `isError`:
    недостижение отметки — неудача, и она обязана прийти отказом.
    """

    sim = _install_fake_simulation(monkeypatch, [0.0, 0.0])
    sim.run_to_result = False

    text = await _error("run", {"to_time": 1.0})

    assert "не дошёл" in text
    assert "0.000" in text
    assert sim.run_to_calls == [(1.0, simulation_tools.CALC_WAIT_SECONDS,
                                 simulation_tools.CALC_STALL_SECONDS)]


@pytest.mark.anyio
async def test_run_reports_success_with_actual_time(monkeypatch):
    """Успешный расчёт подтверждается фактическим временем."""
    _install_fake_simulation(monkeypatch, [1.0])

    text = _text(await mcp.call_tool("run", {"to_time": 1.0}))

    assert "завершён" in text
    assert "1.000" in text


@pytest.mark.anyio
async def test_run_passes_waits_to_library(monkeypatch):
    """Параметры ожидания уходят в библиотечный run_to, а не игнорируются."""
    sim = _install_fake_simulation(monkeypatch, [1.0])

    await mcp.call_tool("run", {"to_time": 1.0, "wait_timeout": 5.0,
                                "stall_seconds": 0.5})

    assert sim.run_to_calls == [(1.0, 5.0, 0.5)]


@pytest.mark.anyio
async def test_run_without_target_warns_no_confirmation(monkeypatch):
    """Неблокирующий запуск не выдаётся за подтверждённый результат."""
    _install_fake_simulation(monkeypatch, [0.0])

    text = _text(await mcp.call_tool("run", {}))

    assert "неблокирующий" in text


@pytest.mark.anyio
async def test_step_reports_stalled_time(monkeypatch):
    """step — тот же класс, что run: время не сдвинулось, значит расчёт стоит.

    И это отказ: шаги, которых не было, не должны выглядеть успехом.
    """
    sim = _install_fake_simulation(monkeypatch, [0.0, 0.0])

    text = await _error("step", {"count": 3})

    assert "не сдвинулось" in text
    assert sim.stepped == 3


@pytest.mark.anyio
async def test_step_reports_time_delta(monkeypatch):
    """Успешные шаги подтверждаются дельтой времени."""
    _install_fake_simulation(monkeypatch, [0.0, 0.003])

    text = _text(await mcp.call_tool("step", {"count": 3}))

    assert "0.000 → 0.003" in text


@pytest.mark.anyio
async def test_step_rejects_non_positive_count(monkeypatch):
    _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("step", {"count": 0})

    assert "положительным" in text


@pytest.mark.anyio
async def test_run_rejects_excessive_wait(monkeypatch):
    """Ожидание дольше таймаута COM-вызова отвергается.

    Инструмент выполняется в единственном выделенном потоке, а COM-вызов по
    таймауту не прерывается: с `wait_timeout` в миллиарды клиент получил бы
    отказ, но поток остался бы занят опросом до `deadline`, и все последующие
    COM-инструменты падали бы по таймауту до перезапуска mmain.exe. Это тот же
    класс, от которого защищает `MAX_STEP_COUNT`, — без проверки предел
    обходится параметрами `run`.
    """
    sim = _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("run", {"to_time": 10.0,
                                "wait_timeout": runtime.COM_CALL_TIMEOUT * 10})

    assert "больше предела" in text
    assert sim.run_to_calls == [], "до COM-вызовов дело доходить не должно"


@pytest.mark.anyio
async def test_run_rejects_non_positive_waits(monkeypatch):
    """Нулевое ожидание — отказ, а не мгновенный опрос в цикле."""
    sim = _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("run", {"to_time": 10.0, "wait_timeout": 0})

    assert "положительными" in text
    assert sim.run_to_calls == []


@pytest.mark.anyio
async def test_step_rejects_absurd_count(monkeypatch):
    """Абсурдное число шагов отвергается до первого COM-вызова.

    Каждый шаг — отдельный COM-вызов, а вызов в своём потоке по таймауту не
    прерывается: неограниченный `count` занял бы выделенный поток надолго, и
    сервер остался бы пригоден только до перезапуска mmain.exe.
    """
    sim = _install_fake_simulation(monkeypatch, [0.0])

    text = await _error("step", {"count": simulation_tools.MAX_STEP_COUNT + 1})

    assert "больше предела" in text
    assert str(simulation_tools.MAX_STEP_COUNT) in text
    assert sim.stepped == 0, "до COM-вызовов дело доходить не должно"


# ─── База сигналов ────────────────────────────────────────────────

SIGNAL_DB_XML = """<?xml version="1.0" encoding="utf-8"?>
<root><database><category>
  <name>`Управление`</name>
  <group><name>`Регулятор`</name><signals>
    <data><name>`Kp`</name><caption>`Коэффициент`</caption>
      <type>`0`</type><mode>`1`</mode><value>`1.5`</value></data>
    <data><name>`Ki`</name><caption>`Интеграл`</caption>
      <type>`0`</type><mode>`1`</mode><value>`0.1`</value></data>
  </signals></group>
</category></database></root>
"""

EMPTY_SIGNAL_DB_XML = """<?xml version="1.0" encoding="utf-8"?>
<root><database><category><name>`Пусто`</name></category></database></root>
"""


class _DbProject:
    """Проект, который «выгружает» базу заранее известным файлом."""

    def __init__(self, text: str):
        self._text = text
        self.written = None

    def export_db_to_xml(self, path: str) -> None:
        import pathlib as _pathlib

        _pathlib.Path(path).write_text(self._text, encoding="utf-8")
        self.written = path


@pytest.mark.anyio
async def test_export_signal_db_summarizes_categories(tmp_path, monkeypatch):
    """Инструмент отдаёт сводку базы, а не только путь к файлу."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_project", _DbProject(SIGNAL_DB_XML))

    text = _text(await mcp.call_tool("export_signal_db", {"path": "db.xml"}))

    assert "категорий 1" in text
    assert "сигналов 2" in text
    assert (tmp_path / "db.xml").is_file()


@pytest.mark.anyio
async def test_export_signal_db_empty_states_it_plainly(tmp_path, monkeypatch):
    """Пустая база называется пустой, а не подаётся как успех.

    У модели из `create_project` базы нет: файл запишется, но сигналов в нём
    не будет. Молчаливый «успех» здесь читался бы как «база есть».
    """
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_project", _DbProject(EMPTY_SIGNAL_DB_XML))

    text = _text(await mcp.call_tool("export_signal_db", {"path": "db.xml"}))

    assert "База пуста" in text


@pytest.mark.anyio
async def test_export_signal_db_refuses_path_outside_sandbox(tmp_path, monkeypatch):
    """Путь наружу песочницы отвергается — как и у чтения результатов."""
    monkeypatch.setenv("SIMINTECH_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_project", _DbProject(SIGNAL_DB_XML))

    text = await _error("export_signal_db", {"path": "../outside.xml"})

    assert "каталога" in text


# ─── Массивы сигналов ─────────────────────────────────────────────

class _FakeSignal:
    """Сигнал: скаляр или массив, с записью того, что в него писали."""

    def __init__(self, data_type, value=0.0, array=()):
        self.data_type = data_type
        self._value = value
        self._array = list(array)
        self.written = []

    def read(self):
        return self._value

    def read_array_element(self, index):
        return self._array[index]

    def array_count(self):
        return len(self._array)

    def write(self, value):
        self.written.append(("scalar", value))

    def set_array_element(self, index, value):
        self.written.append((index, value))


class _SignalProject:
    def __init__(self, signal):
        self._signal = signal

    def signal(self, block):
        return self._signal


def _install_signal(monkeypatch, signal):
    monkeypatch.setattr(session, "_project", _SignalProject(signal))
    return signal


@pytest.mark.anyio
async def test_get_signal_array_reports_size_and_truncates(monkeypatch):
    """Массив отдаётся не целиком: элементы читаются по одному вызову COM.

    Библиотека тянет массив поэлементно, а COM-поток один — поэтому в ответ
    идёт сколько запрошено и полный размер, а не весь массив.
    """
    from simintech_api.constants import DataType

    signal = _install_signal(
        monkeypatch, _FakeSignal(DataType.ARRAY, array=[1.0, 2.0, 3.0, 4.0, 5.0]))

    text = _text(await mcp.call_tool("get_signal",
                                     {"block": "a_0", "max_items": 2}))

    assert "[5]" in text
    assert "1.0, 2.0" in text
    assert "ещё 3" in text
    assert signal.written == []


@pytest.mark.anyio
async def test_get_signal_array_element_by_index(monkeypatch):
    """Остальные элементы массива читаются по индексу."""
    from simintech_api.constants import DataType

    _install_signal(monkeypatch,
                    _FakeSignal(DataType.ARRAY, array=[1.0, 2.0, 3.0, 4.0]))

    text = _text(await mcp.call_tool("get_signal",
                                     {"block": "a_0", "index": 3}))

    assert "a_0[3] = 4.0" in text


@pytest.mark.anyio
async def test_get_signal_refuses_too_many_items(monkeypatch):
    """Предел числа элементов — защита COM-потока, а не удобство.

    Число элементов приходит от клиента, и каждый — отдельный COM-вызов:
    без предела длинный массив занял бы единственный поток надолго.
    """
    text = await _error("get_signal", {"block": "a_0", "max_items": 100000})

    assert "вне предела" in text


@pytest.mark.anyio
async def test_get_signal_index_on_scalar_is_refused(monkeypatch):
    """Индекс к скаляру — отказ, а не молчаливое чтение скаляра."""
    from simintech_api.constants import DataType

    _install_signal(monkeypatch, _FakeSignal(DataType.DOUBLE, value=2.5))

    text = await _error("get_signal", {"block": "k_0", "index": 1})

    assert "не массив" in text


@pytest.mark.anyio
async def test_set_signal_array_element(monkeypatch):
    """Запись идёт в один элемент массива — и только по индексу."""
    from simintech_api.constants import DataType

    signal = _install_signal(
        monkeypatch, _FakeSignal(DataType.ARRAY, array=[0.0, 0.0, 0.0]))

    text = _text(await mcp.call_tool("set_signal",
                                     {"block": "a_0", "value": 7.5, "index": 2}))

    assert "a_0[2] = 7.5" in text
    assert signal.written == [(2, 7.5)]


@pytest.mark.anyio
async def test_set_signal_index_out_of_range_is_refused(monkeypatch):
    from simintech_api.constants import DataType

    _install_signal(monkeypatch, _FakeSignal(DataType.ARRAY, array=[0.0]))

    text = await _error("set_signal",
                        {"block": "a_0", "value": 1.0, "index": 5})

    assert "вне массива" in text


@pytest.mark.anyio
async def test_set_signal_index_on_scalar_is_refused(monkeypatch):
    from simintech_api.constants import DataType

    _install_signal(monkeypatch, _FakeSignal(DataType.DOUBLE, value=1.0))

    text = await _error("set_signal",
                        {"block": "k_0", "value": 1.0, "index": 0})

    assert "не массив" in text
