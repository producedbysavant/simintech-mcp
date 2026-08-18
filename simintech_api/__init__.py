"""simintech_api — Python-библиотека управления SimInTech через COM API.

Быстрый старт (только Windows):
    from simintech_api import COMClient, Project

    client = COMClient(silent_mode=True).connect()
    prj = Project.new(client)
    page = prj.get_main_page()
    b1 = page.create_block("Константа", 0, 0)
    b2 = page.create_block("Усилитель", 200, 0)
    b1.connect(b2)
    prj.save_xml("model.xprt")
"""

from .core.com_client import COMClient
from .core.project import Project
from .core.page import Page
from .core.block import Block
from .core.port import Port
from .core.wire import Wire
from .core.signal import Signal
from .core.simulation import Simulation
from .constants import DataType, PortSide
from .exceptions import (
    BlockError,
    ComCallError,
    ComConnectionError,
    PageError,
    PortError,
    ProjectError,
    SignalError,
    SimInTechError,
    SimulationError,
    UnsupportedBlockError,
    WireError,
)

__all__ = [
    "COMClient",
    "Project",
    "Page",
    "Block",
    "Port",
    "Wire",
    "Signal",
    "Simulation",
    "DataType",
    "PortSide",
    "SimInTechError",
    "ComConnectionError",
    "ComCallError",
    "ProjectError",
    "PageError",
    "BlockError",
    "UnsupportedBlockError",
    "PortError",
    "WireError",
    "SignalError",
    "SimulationError",
]

__version__ = "0.1.0"
