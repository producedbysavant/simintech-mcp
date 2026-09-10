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
        from ..utils.converters import descriptor_is_valid

        desc = self._client.find_signal(name, self._id)
        if not descriptor_is_valid(desc):
            raise SignalError(
                f"Сигнал '{name}' не найден в проекте. Обмен данными идёт "
                f"через список сигналов проекта и подключённую базу сигналов; "
                f"у проекта без базы их нет, и имя блока сигналом не является. "
                f"Проверьте GetProjectDB и список сигналов "
                f"(list_signals): записи с source='xml' — это имена блоков, "
                f"они не читаются."
            )
        return desc

    def signal(self, name: str) -> "Signal":  # noqa: F821 — Signal импортируется ниже
        """Вернуть объект Signal по имени сигнала."""
        from .signal import Signal
        return Signal(self, self.find_signal(name), name)

    def list_signals(self) -> List[SignalInfo]:
        """Получить список сигналов проекта.

        Два источника, различимых по `SignalInfo.source`:

        1. ``"com"`` — `GetProjectSignalList` → `GetListCount` →
           `GetDataInfoFromList`. Это настоящие сигналы, у них есть
           дескриптор, и `Signal.read()` по ним работает.
        2. ``"xml"`` — если COM-список пуст, имена извлекаются из .xprt.
           Это **имена блоков, а не сигналы**: `readable` у них ``False``,
           прочитать значение нельзя. Возвращаются как подсказка о том, что
           есть на схеме, а не как пригодные к чтению сигналы.

        У проекта, созданного `Project.new()`, список обычно пуст: обмен
        идёт через базу сигналов, а её к такому проекту не подключают
        (`GetProjectDB` → `(None, None)`). Проверено на SimInTech64.
        """
        from ..utils.converters import _to_descriptor
        result: List[SignalInfo] = []
        list_id = _as_i64(self._client.call("GetProjectSignalList", self._id))
        count = _as_i64(self._client.call("GetListCount", list_id))
        for i in range(count):
            # comtypes возвращает [out] (Name, Caption, DataDesc) в порядке объявления
            name, caption, desc = self._client.call("GetDataInfoFromList", list_id, i)
            result.append(SignalInfo(
                name=_as_str(name),
                caption=_as_str(caption),
                descriptor=_to_descriptor(desc),
            ))
        if result:
            return result
        # Запасной путь: имена блоков из XML. Это НЕ сигналы — помечаем
        # источником "xml", чтобы вызывающий код не принял их за читаемые.
        try:
            from ..utils.xprt_signals import extract_signal_names_from_project
            names = extract_signal_names_from_project(self)
            result = [
                SignalInfo(name=nm, caption="", descriptor=None, source="xml")
                for nm in names
            ]
        except Exception:
            pass
        return result

    def get_signal_names_from_xml(self) -> List[str]:
        """Извлечь имена сигналов из XML-представления проекта (.xprt).

        Экспортирует проект во временный файл и парсит имена блоков
        (кандидатов в сигналы). Полезно, когда GetProjectSignalList пуст
        (модель без блоков «Вход/Выход алгоритма»).
        """
        from ..utils.xprt_signals import extract_signal_names_from_project
        return extract_signal_names_from_project(self)

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
