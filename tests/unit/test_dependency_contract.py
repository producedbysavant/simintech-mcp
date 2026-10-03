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
    ("simintech_api", "SessionOwnership"),
    ("simintech_api", "Project"),
    ("simintech_api", "Wire"),
    # Блоки и сигналы — типы аннотаций инструментов (layout/blocks/simulation).
    ("simintech_api", "Block"),
    ("simintech_api", "Signal"),
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
    # Скрипт блока ЯП из выгрузки (tools/blocks.py, `get_block_script`).
    ("simintech_api.catalog", "parse_xprt_block_script"),
    ("simintech_api.constants", "SUPPORTED_COM_BLOCK_CLASSES"),
    ("simintech_api.constants", "default_output_dir"),
    ("simintech_api.constants", "standard_block_size"),
    ("simintech_api.constants", "DataType"),
    ("simintech_api.dbconf", "load_db_config"),
    ("simintech_api.sdb", "SignalDatabase"),
    ("simintech_api.layout", "LayeredPlacer"),
    ("simintech_api.exceptions", "SimInTechError"),
    # Мост контура (tools/*.py): отказ ScriptBridge — «состояние проекта
    # неопределённо», инструменты превращают его в ToolError с причиной.
    ("simintech_api.exceptions", "ScriptBridgeError"),
    ("simintech_api.utils.xprt_signals", "XprtSignalReader"),
    ("simintech_api.utils.converters", "value_to_prop_string"),
    # Проверка исчезновения процесса в disconnect («завершён» — по факту).
    ("simintech_api.utils.processes", "wait_for_pid_exit"),
    # Контур языкового слоя: исходы, страница и тела операций.
    ("simintech_api", "PageRunResult"),
    ("simintech_api", "classify_page_result"),
    ("simintech_api", "OUTCOME_OK"),
    ("simintech_api", "OUTCOME_MODEL_NOT_RUNNING"),
    ("simintech_api", "OUTCOME_ABORTED"),
    ("simintech_api", "OUTCOME_NOT_COMPILED"),
    ("simintech_api", "OUTCOME_SECTION_NOT_RUN"),
    ("simintech_api.model_operations", "build_export_model_text_body"),
    ("simintech_api.model_operations", "build_import_model_text_body"),
    ("simintech_api.model_operations", "build_inject_submodel_script_body"),
    # Пакет проектов (tools/pack.py): класс Pack и клиентские методы, которые
    # инструменты пакета зовут напрямую. Клиент в тестах пакета — подделка,
    # поэтому без этой строки переименование в библиотеке всплыло бы только
    # на Windows, у клиента (ревью mcp#25).
    ("simintech_api", "Pack"),
    # Писатель пакета (tools/pack.py, `create_pack`) — формат .pak собирается
    # в библиотеке; переименование обязано падать здесь, а не у клиента.
    ("simintech_api.pak", "write_pack"),
    ("simintech_api.pak", "PackEntry"),
]

#: Методы, вызываемые сервером у `Project`.
PROJECT_METHODS = [
    "from_template", "open", "close", "get_main_page", "repaint",
    "set_calc_end_time", "set_calc_setting", "calc_settings", "simulation",
    "list_signals", "signal", "export_db_to_xml", "show_form",
    "save_xml", "save_binary",
]

#: Методы, вызываемые сервером у `COMClient` напрямую (пакетный путь идёт
#: мимо `Project`; `shutdown` — управляемое завершение в `disconnect`).
CLIENT_METHODS = [
    "open_pack", "get_opened_file_name", "get_process_id", "shutdown",
]

#: Методы, вызываемые сервером у `Pack` (tools/pack.py).
PACK_METHODS = [
    "project_ids", "start", "run", "step", "stop", "close",
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


@pytest.mark.parametrize("method", CLIENT_METHODS)
def test_com_client_has_method(method):
    """`COMClient` умеет то, что сервер у него вызывает (пакет и процесс)."""
    from simintech_api import COMClient

    assert callable(getattr(COMClient, method, None)), \
        f"COMClient.{method} отсутствует"


#: Свойства `COMClient`, которые читает сервер (гейт владения и адрес
#: процесса в ответах инструментов). Не методы: `callable(...)` их не поймает.
CLIENT_PROPERTIES = ["session_pid", "ownership"]


@pytest.mark.parametrize("attr", CLIENT_PROPERTIES)
def test_com_client_has_property(attr):
    """`COMClient` отдаёт свойства, на которые опирается гейт владения."""
    from simintech_api import COMClient

    assert hasattr(COMClient, attr), f"COMClient.{attr} отсутствует"


@pytest.mark.parametrize("method", PACK_METHODS)
def test_pack_has_method(method):
    """`Pack` умеет то, что сервер у него вызывает (tools/pack.py)."""
    from simintech_api import Pack

    assert callable(getattr(Pack, method, None)), f"Pack.{method} отсутствует"


#: Методы `ScriptBridge`, которые зовёт сервер. Проверяются отдельно от
#: модульных символов: метод — не атрибут модуля, и переименование в нём
#: `hasattr(module, ...)` не поймает.
SCRIPT_BRIDGE_METHODS = [
    "run_probe", "run_page_script", "read_page_script", "install_script",
]


def test_script_bridge_methods_exist():
    """У `ScriptBridge` есть методы, на которые опирается сервер.

    Второй класс того же дефекта, что и мёртвый пин: коммит существует, а
    нужного метода в нём нет. Ошибка всплыла бы у клиента — в момент вызова
    инструмента, а не при сборке.
    """
    from simintech_api.core.script_bridge import ScriptBridge

    missing = [name for name in SCRIPT_BRIDGE_METHODS
               if not callable(getattr(ScriptBridge, name, None))]

    assert not missing, f"ScriptBridge: нет методов {missing}"
