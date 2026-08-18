"""Исключения библиотеки simintech_api."""


class SimInTechError(Exception):
    """Базовое исключение библиотеки."""


class ComConnectionError(SimInTechError):
    """Не удалось подключиться к COM-серверу SimInTech (mmain.exe)."""


class ComCallError(SimInTechError):
    """Ошибка вызова метода COM-сервера.

    Args:
        method: имя вызванного метода.
        hr: HRESULT, если был получен.
        message: текст ошибки.
    """

    def __init__(self, method: str, hr: int = None, message: str = ""):
        self.method = method
        self.hr = hr
        self.message = message
        detail = f" [{hr:#010x}]" if hr is not None else ""
        super().__init__(f"COM-вызов {method} завершился ошибкой{detail}: {message}")


class ProjectError(SimInTechError):
    """Ошибка при работе с проектом/пакетом (создание, открытие, сохранение)."""


class PageError(SimInTechError):
    """Ошибка при работе со страницей."""


class BlockError(SimInTechError):
    """Ошибка при работе с блоком (создание, свойства, порты)."""


class UnsupportedBlockError(BlockError):
    """Класс блока не создаётся через COM CreateBlock."""


class PortError(SimInTechError):
    """Ошибка при работе с портом."""


class WireError(SimInTechError):
    """Ошибка при работе с линией связи."""


class SignalError(SimInTechError):
    """Ошибка при работе с сигналом (не найден, неверный тип, чтение/запись)."""


class SimulationError(SimInTechError):
    """Ошибка управления расчётом."""


class LayoutError(SimInTechError):
    """Ошибка алгоритмов размещения/трассировки (нет пути, конфликт)."""
