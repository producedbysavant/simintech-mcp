"""Тесты Block без COM: определение класса, параметры, число входов."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from simintech_api.catalog import BlockCatalog  # noqa: E402
from simintech_api.core.block import Block  # noqa: E402
from simintech_api.exceptions import PortError  # noqa: E402


class FakeClient:
    """Поддельный COM-клиент: отвечает на GetBlockPropAsString и считает вызовы."""

    def __init__(self, props=None, ports=None):
        self.props = dict(props or {})
        self.ports = list(ports or [])
        self.calls = []

    def call(self, method, *args):
        self.calls.append((method, args))
        if method == "GetBlockPropAsString":
            return self.props.get(args[1], "")
        if method == "GetBlockPluginName":
            return "TMBTYBlock"
        if method == "GetInPort":
            index = args[1]
            return 100 + index if index < len(self.ports) else 0
        if method == "SetPortCount":
            # SimInTech: параметр Count — общее число портов, включая выход,
            # поэтому входов становится Count - 1.
            count = args[1]
            self.ports = list(range(max(0, count - 1)))
            return 0
        if method == "GetPortCount":
            return len(self.ports) + 1
        return 0


class FakeProject:
    def __init__(self, client):
        self._client = client

    @property
    def client(self):
        return self._client


def _block(client, class_name=None):
    return Block(FakeProject(client), 1, class_name=class_name)


# ─── class_name ───────────────────────────────────────────────────

def test_class_name_prefers_classname_property():
    """Класс берётся из свойства ClassName, а не из GetBlockPluginName.

    GetBlockPluginName возвращает внутреннее имя плагина (TMBTYBlock),
    которое с именами классов в каталоге не совпадает — из-за этого
    get_block_params не находил параметры (проверено на реальном SimInTech).
    """
    client = FakeClient({"ClassName": "Усилитель"})

    assert _block(client).class_name == "Усилитель"


def test_class_name_falls_back_to_plugin_name():
    """Если ClassName недоступен — используется имя плагина."""
    client = FakeClient({})

    assert _block(client).class_name == "TMBTYBlock"


def test_class_name_cached_when_given():
    """Имя, переданное при создании, не перечитывается."""
    client = FakeClient({"ClassName": "Что-то другое"})

    assert _block(client, "Константа").class_name == "Константа"
    assert client.calls == []


# ─── get_properties ───────────────────────────────────────────────

def test_get_properties_reads_catalog_names():
    """Читаются имена из каталога; реально существующие дают значения."""
    catalog = BlockCatalog(classes={"Усилитель": {"a": "1"}})
    client = FakeClient({"ClassName": "Усилитель", "a": "2.5"})

    props = _block(client).get_properties(catalog=catalog)

    assert props == {"a": "2.5", "Name": ""}


def test_get_properties_cannot_detect_missing_name():
    """Отсутствующее свойство неотличимо от пустого.

    GetBlockPropAsString на неизвестное имя возвращает пустую строку без
    ошибки (проверено на реальном SimInTech), поэтому в результат оно
    попадает как ''. Именно поэтому имена берутся из сгенерированного
    каталога, а не подбираются.
    """
    catalog = BlockCatalog(classes={"Усилитель": {"a": "1", "нет_такого": ""}})
    client = FakeClient({"ClassName": "Усилитель", "a": "2.5"})

    props = _block(client).get_properties(catalog=catalog)

    assert props["нет_такого"] == ""


def test_get_properties_empty_for_unknown_class():
    """Класс вне каталога — только общие свойства."""
    catalog = BlockCatalog(classes={})
    client = FakeClient({"ClassName": "Чего-то"})

    assert set(_block(client).get_properties(catalog=catalog)) == {"Name"}


# ─── порты ────────────────────────────────────────────────────────

def test_set_in_port_count_adds_inputs():
    """set_in_port_count(3) даёт три входа (SetPortCount получает 4)."""
    client = FakeClient({"ClassName": "Сумматор"})
    block = _block(client)
    client.ports = [0, 1]           # по умолчанию у «Сумматора» два входа

    block.set_in_port_count(3)

    assert block.get_in_port_count() == 3
    assert ("SetPortCount", (1, 4, 0, 0, 0)) in client.calls


def test_set_in_port_count_rejects_zero():
    client = FakeClient()

    with pytest.raises(PortError):
        _block(client).set_in_port_count(0)


def test_get_in_port_count_stops_at_missing():
    """Перебор прекращается на первом отсутствующем порту."""
    client = FakeClient({"ClassName": "Сумматор"})
    client.ports = [0, 1]

    assert _block(client).get_in_port_count() == 2
