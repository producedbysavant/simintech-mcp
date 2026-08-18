"""Управление расчётом проекта/пакета SimInTech."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .com_client import COMClient


class Simulation:
    """Управление расчётом проекта (ProjectStart/Run/Step/...).

    Args:
        client: COM-клиент.
        project_id: id проекта (или пакета для Pack-методов).
    """

    def __init__(self, client: "COMClient", project_id: int):
        self._client = client
        self._id = project_id

    # ─── Проект ─────────────────────────────────────────────────────

    def start(self) -> "Simulation":
        """Инициализировать расчёт (ProjectStart)."""
        self._client.call("ProjectStart", self._id)
        return self

    def run(self) -> "Simulation":
        """Запустить непрерывный расчёт."""
        self._client.call("ProjectRun", self._id)
        return self

    def step(self) -> "Simulation":
        """Выполнить один шаг расчёта."""
        self._client.call("ProjectStep", self._id)
        return self

    def pause(self) -> "Simulation":
        """Приостановить расчёт."""
        self._client.call("ProjectPause", self._id)
        return self

    def stop(self) -> "Simulation":
        """Остановить расчёт."""
        self._client.call("ProjectStop", self._id)
        return self

    def run_to(self, target_time: float) -> bool:
        """Расчёт до заданного времени; вернуть True при достижении."""
        result = self._client.call("RunTo", self._id, float(target_time))
        if _as_int(result) == 0:
            # Result != 0 означает, что нужно ждать (см. WaitForTime)
            return self.wait_for_time(target_time)
        return True

    def wait_for_time(self, target_time: float) -> bool:
        """Дождаться достижения модельного времени (WaitForTime)."""
        result = self._client.call("WaitForTime", self._id, float(target_time))
        return _as_int(result) != 0

    def get_time(self) -> float:
        """Текущее модельное время проекта."""
        return float(self._client.call("GetProjectTime", self._id))

    def get_state(self) -> int:
        """Состояние проекта (флаги GetProjectStateFlag)."""
        return _as_int(self._client.call("GetProjectStateFlag", self._id))

    # ─── Пакет ──────────────────────────────────────────────────────

    def pack_start(self) -> "Simulation":
        """Инициализировать пакет."""
        self._client.call("PackStart", self._id)
        return self

    def pack_run(self) -> "Simulation":
        """Запустить расчёт пакета."""
        self._client.call("PackRun", self._id)
        return self

    def pack_step(self) -> "Simulation":
        """Шаг расчёта пакета."""
        self._client.call("PackStep", self._id)
        return self

    def pack_pause(self) -> "Simulation":
        """Пауза пакета."""
        self._client.call("PackPause", self._id)
        return self

    def pack_stop(self) -> "Simulation":
        """Остановить пакет."""
        self._client.call("PackStop", self._id)
        return self

    def run_to_pack(self, target_time: float) -> bool:
        """Расчёт пакета до заданного времени; True при достижении."""
        result = self._client.call("RunToPack", self._id, float(target_time))
        if _as_int(result) == 0:
            wait = self._client.call("WaitForTimePack", self._id, float(target_time))
            return _as_int(wait) != 0
        return True

    # ─── Реальное время ─────────────────────────────────────────────

    def set_realtime_delay(self, delay_flag: int, delay_scale: float) -> "Simulation":
        """Синхронизация с реальным временем."""
        self._client.call("SetProjectRealTimeDelay", self._id, delay_flag,
                          float(delay_scale))
        return self


def _as_int(value) -> int:
    if hasattr(value, "value"):
        return int(value.value)
    return int(value)
