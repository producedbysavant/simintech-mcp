"""Контракт simintech-api: символы, на которые опирается сервер, существуют.

Второй класс той же ошибки, что и мёртвый пин: коммит существует, а нужного
API в нём нет. Импорт на уровне модулей ловит только часть (`language`,
`catalog`, `constants`), а ленивые импорты внутри инструментов (`dbconf`,
`sdb`, `layout`, `utils.xprt_signals`) — нет: их отсутствие всплыло бы в
рантайме, у клиента, и выглядело бы поломкой инструмента.

Список — по факту использования в `simintech_mcp`, а не «всё, что есть в
библиотеке»: проверка обязана падать на смене API, но не на его расширении.
"""

from __future__ import annotations

import importlib

import pytest

#: (модуль, атрибут) — используется сервером.
REQUIRED = [
    ("simintech_api", "COMClient"),
    ("simintech_api", "Project"),
    ("simintech_api", "Wire"),
    ("simintech_api", "ComCallError"),
    ("simintech_api", "ComConnectionError"),
    # `language` — подмодуль: атрибутом пакета он становится лишь после
    # импорта, поэтому проверяются его собственные функции (см. resources.py).
    ("simintech_api.language", "language_functions"),
    ("simintech_api.language", "find_function"),
    ("simintech_api.language", "registry_meta"),
    ("simintech_api.catalog", "load_default_catalog"),
    ("simintech_api.catalog", "decode_xprt"),
    ("simintech_api.catalog", "parse_xprt_block_props"),
    ("simintech_api.catalog", "parse_xprt_readonly"),
    ("simintech_api.constants", "SUPPORTED_COM_BLOCK_CLASSES"),
    ("simintech_api.constants", "default_output_dir"),
    ("simintech_api.constants", "standard_block_size"),
    ("simintech_api.constants", "DataType"),
    ("simintech_api.dbconf", "load_db_config"),
    ("simintech_api.sdb", "SignalDatabase"),
    ("simintech_api.layout", "LayeredPlacer"),
    ("simintech_api.exceptions", "SimInTechError"),
    ("simintech_api.utils.xprt_signals", "XprtSignalReader"),
    ("simintech_api.utils.converters", "value_to_prop_string"),
]

#: Методы, вызываемые сервером у `Project`.
PROJECT_METHODS = [
    "from_template", "open", "close", "get_main_page", "repaint",
    "set_calc_end_time", "set_calc_setting", "calc_settings", "simulation",
    "list_signals", "signal", "export_db_to_xml", "show_form",
    "save_xml", "save_binary",
]


@pytest.mark.parametrize("module_name,attr", REQUIRED)
def test_required_symbol_exists(module_name, attr):
    """Символ есть в установленной библиотеке."""
    module = importlib.import_module(module_name)
    assert hasattr(module, attr), f"{module_name}.{attr} отсутствует"


@pytest.mark.parametrize("method", PROJECT_METHODS)
def test_project_has_method(method):
    """`Project` умеет то, что сервер у него вызывает."""
    from simintech_api import Project

    assert callable(getattr(Project, method, None)), f"Project.{method} отсутствует"
