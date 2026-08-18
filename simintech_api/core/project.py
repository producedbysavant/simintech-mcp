"""Управление проектом SimInTech."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from ..exceptions import ProjectError, SignalError
from ..model import SignalInfo, TDataDescriptor

if TYPE_CHECKING:
    from .com_client import COMClient
    from .page import Page
    from .simulation import Simulation


class Project:
    """Проект SimInTech (обёртка над ProjectId).

    Создаётся через Project.new() или Project.open(), не напрямую.
    """

    def __init__(self, client: "COMClient", project_id: int):
        self._client = client
        self._id = project_id

    # ─── Фабричные методы ───────────────────────────────────────────

    @classmethod
    def new(cls, client: "COMClient") -> "Project":
        """Создать новый (пустой) проект."""
        project_id = client.new_project()
        if project_id == 0:
            raise ProjectError("NewProject вернул нулевой ProjectId")
        return cls(client, project_id)

    @classmethod
    def open(cls, client: "COMClient", path: str) -> "Project":
        """Открыть проект (.prt/.xprt) по пути."""
        project_id = client.open_project(path)
        if project_id == 0:
            raise ProjectError(f"OpenProject не удалось открыть '{path}'")
        return cls(client, project_id)

    # ─── Свойства ───────────────────────────────────────────────────

    @property
    def id(self) -> int:
        return self._id

    @property
    def client(self) -> "COMClient":
        return self._client

    # ─── Жизненный цикл ─────────────────────────────────────────────

    def close(self) -> None:
        """Закрыть проект."""
        self._client.call("CloseProject", self._id)

    def save_xml(self, path: str) -> None:
        """Сохранить проект в XML-формат (.xprt)."""
        self._client.call("SaveProjectXML", self._id, path)

    def save_binary(self, path: str) -> None:
        """Сохранить проект в бинарный формат (.prt)."""
        self._client.call("SaveProjectBinary", self._id, path)

    # ─── Страницы ───────────────────────────────────────────────────

    def get_main_page(self) -> "Page":
        """Получить главную страницу проекта."""
        from .page import Page
        page_id = self._client.call("GetMainPage", self._id)
        return Page(self, _as_i64(page_id))

    def get_current_page(self) -> "Page":
        """Получить текущую страницу проекта."""
        from .page import Page
        page_id = self._client.call("GetCurentPage", self._id)
        return Page(self, _as_i64(page_id))

    # ─── Сигналы ────────────────────────────────────────────────────

    def find_signal(self, name: str) -> TDataDescriptor:
        """Найти сигнал по имени блока; вернуть TDataDescriptor (или SignalError).

        Работает через FindSignalData — сигнал ищется по имени блока
        независимо от GetProjectSignalList. Как правило доступен сразу после
        открытия проекта; при необходимости инициализации вызывайте
        sim.start() перед поиском.
        """
        desc = self._client.find_signal(name, self._id)
        if not desc.is_valid:
            raise SignalError(f"Сигнал '{name}' не найден в проекте")
        return desc

    def signal(self, name: str) -> "Signal":  # noqa: F821 — Signal импортируется ниже
        """Вернуть объект Signal по имени сигнала."""
        from .signal import Signal
        return Signal(self, self.find_signal(name), name)

    def list_signals(self) -> List[SignalInfo]:
        """Получить список ВНЕШНИХ (обменных) сигналов проекта.

        GetProjectSignalList возвращает только сигналы, зарегистрированные
        для обмена (блоки «Вход/Выход алгоритма»). Внутренние сигналы блоков
        в этот список не входят — для них используйте find_signal(name) /
        signal(name) по имени блока. Для моделей без внешних интерфейсов
        список будет пуст (это нормально).

        Важно: список может требовать предварительной инициализации проекта
        (sim.start()).
        """
        from ..utils.converters import _to_descriptor
        list_id = _as_i64(self._client.call("GetProjectSignalList", self._id))
        count = _as_i64(self._client.call("GetListCount", list_id))
        result: List[SignalInfo] = []
        for i in range(count):
            # comtypes возвращает [out] (Name, Caption, DataDesc) в порядке объявления
            name, caption, desc = self._client.call("GetDataInfoFromList", list_id, i)
            result.append(SignalInfo(
                name=_as_str(name),
                caption=_as_str(caption),
                descriptor=_to_descriptor(desc),
            ))
        return result

    # ─── Расчёт ─────────────────────────────────────────────────────

    def simulation(self) -> "Simulation":
        """Получить объект управления расчётом проекта."""
        from .simulation import Simulation
        return Simulation(self._client, self._id)

    def run(self) -> None:
        """Запустить расчёт (удобная обёртка)."""
        self.simulation().start().run()

    def stop(self) -> None:
        """Остановить расчёт."""
        self.simulation().stop()


def _as_i64(value) -> int:
    if hasattr(value, "value"):
        return int(value.value)
    return int(value)


def _as_str(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)
