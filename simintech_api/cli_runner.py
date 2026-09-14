"""Запуск SimInTech через командную строку (`mmain.exe`).

Резервный путь без COM: работает из WSL, где COM недоступен. Опции
документированы в справке SimInTech («Командная строка»). Вывод `mmain.exe`
приходит в cp1251 или cp866 — декодируется в `_decode`.

Перенесено из репозитория `simintech-connector` (заархивирован 2026-09-10).
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class CLIResult:
    """Результат запуска mmain.exe через командную строку.

    Args:
        success: завершился ли процесс успешно.
        message: описание результата.
        data: ``{"stdout": ..., "stderr": ...}`` при наличии вывода.
    """

    success: bool
    message: str = ""
    data: Optional[Any] = None


#: Путь по умолчанию для окружения WSL. Вынесен в константу, чтобы его
#: можно было подменить в тестах: на машине с установленным SimInTech этот
#: файл существует, и проверка «mmain.exe не найден» иначе недостижима.
DEFAULT_MMAIN_PATH = Path("/mnt/c/SimInTech64/bin/mmain.exe")


def _check_arg(value: str, name: str, *, option_like: bool = False) -> str:
    """Отклонить значение, которое mmain.exe разберёт как лишний ключ.

    Часть опций передаётся одной строкой (`/saveas <путь>`,
    `/setparameter <имя> <значение>`), а mmain.exe разбирает собственную
    командную строку сам. Поэтому пробел или перевод строки внутри значения
    становится разделителем, и остаток превращается в отдельные опции —
    например, `out.xprt /close /exit` закрыло бы и завершило процесс.

    Args:
        value: проверяемое значение.
        name: имя параметра — попадает в сообщение об ошибке.
        option_like: отклонять ли значения, начинающиеся с `/` или `-`.
            Включается там, где ожидается имя или число, а не путь: путь
            может быть абсолютным (в WSL начинается с `/`).
    """
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError(
            f"{name}: пробелы и управляющие символы недопустимы — "
            f"mmain.exe разберёт их как разделители аргументов"
        )
    if option_like and value.startswith(("/", "-")):
        raise ValueError(
            f"{name}: значение не должно начинаться с '/' или '-' "
            f"(будет принято за опцию mmain.exe)"
        )
    return value


class CLIAdapter:
    """Адаптер для управления SimInTech через командную строку."""

    def __init__(self, mmain_path: Optional[str] = None, silent: bool = True):
        self.mmain_path = self._resolve_mmain_path(mmain_path)
        self.silent = silent
        self._project_path: Optional[str] = None

    @staticmethod
    def _resolve_mmain_path(custom_path: Optional[str]) -> str:
        if custom_path:
            p = Path(custom_path)
            if p.is_file():
                return str(p.absolute())
            elif p.is_dir():
                candidate = p / "mmain.exe"
                if candidate.exists():
                    return str(candidate)
        env_path = os.environ.get("SIMINTECH_PATH")
        if env_path:
            candidate = Path(env_path) / "mmain.exe"
            if candidate.exists():
                return str(candidate)
        env_home = os.environ.get("SIMINTECH")
        if env_home:
            candidate = Path(env_home) / "mmain.exe"
            if candidate.exists():
                return str(candidate)
        if DEFAULT_MMAIN_PATH.exists():
            return str(DEFAULT_MMAIN_PATH)
        msg = (
            "mmain.exe не найден. Укажите путь через SIMINTECH_PATH "
            "или передайте mmain_path в конструктор."
        )
        raise FileNotFoundError(msg)

    @staticmethod
    def _decode(data: bytes) -> str:
        """Декодировать вывод mmain.exe (Windows-1251 → UTF-8)."""
        for enc in ("cp1251", "cp866", "utf-8"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    @property
    def is_available(self) -> bool:
        """Проверить, доступен ли mmain.exe."""
        return Path(self.mmain_path).exists()

    def build_cmd(self, *args: str) -> list[str]:
        """Собрать команду запуска mmain.exe с опциями."""
        cmd = [self.mmain_path]
        if self.silent:
            cmd.append("/silentmode")
        cmd.extend(args)
        return cmd

    def run_sync(self, *args: str, timeout: int = 300) -> CLIResult:
        """Запустить mmain.exe с опциями и дождаться завершения."""
        cmd = self.build_cmd(*args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=False,  # binary mode — mmain.exe под WSL выводит в cp1251
                timeout=timeout,
            )
            stdout = self._decode(result.stdout)
            stderr = self._decode(result.stderr)
            if result.returncode == 0:
                return CLIResult(
                    success=True,
                    message="Команда выполнена успешно",
                    data={"stdout": stdout, "stderr": stderr},
                )
            return CLIResult(
                success=False,
                message=f"mmain.exe завершился с кодом {result.returncode}",
                data={"stdout": stdout, "stderr": stderr},
            )
        except subprocess.TimeoutExpired:
            return CLIResult(
                success=False,
                message=f"Тайм-аут ({timeout}с): mmain.exe не завершился вовремя",
            )
        except FileNotFoundError:
            return CLIResult(
                success=False,
                message=f"mmain.exe не найден: {self.mmain_path}",
            )

    def open_and_run(self, project_path: str, timeout: int = 300) -> CLIResult:
        """Открыть проект и запустить расчёт."""
        return self.run_sync(
            project_path,
            "/start",
            "/run",
            "/exitonstop",
            timeout=timeout,
        )

    def open_project(self, project_path: str, timeout: int = 30) -> CLIResult:
        """Открыть проект без запуска расчёта."""
        return self.run_sync(project_path, timeout=timeout)

    def run_project(self, project_path: str, timeout: int = 300) -> CLIResult:
        """Открыть проект, запустить расчёт, дождаться останова."""
        return self.open_and_run(project_path, timeout=timeout)

    def run_step(self, project_path: str, timeout: int = 60) -> CLIResult:
        """Выполнить один шаг синхронизации."""
        return self.run_sync(
            project_path,
            "/start",
            "/runstep",
            "/exitonstop",
            timeout=timeout,
        )

    def save_as(self, project_path: str, output_path: str,
                timeout: int = 30) -> CLIResult:
        """Открыть проект и сохранить в другом формате."""
        _check_arg(output_path, "output_path")
        return self.run_sync(
            project_path,
            f"/saveas {output_path}",
            "/close",
            "/exit",
            timeout=timeout,
        )

    def set_parameter(self, project_path: str, param: str, value: str,
                      timeout: int = 30) -> CLIResult:
        """Установить параметр проекта из командной строки."""
        _check_arg(param, "param", option_like=True)
        _check_arg(value, "value", option_like=True)
        return self.run_sync(
            project_path,
            f"/setparameter {param} {value}",
            "/close",
            "/exit",
            timeout=timeout,
        )

    # ─── Работа с макросами ────────────────────────────────────────

    def run_macro_file(self, macro_path: str, timeout: int = 300) -> CLIResult:
        """Запустить файл макроса SimInTech (/macros)."""
        macro_abs = str(Path(macro_path).absolute())
        _check_arg(macro_abs, "macro_path")
        return self.run_sync(f"/macros {macro_abs}", timeout=timeout)

    def run_macro(self, macro_content: str, timeout: int = 300) -> CLIResult:
        """Запустить произвольный макрос (создаёт временный файл)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(macro_content)
            tmp_path = f.name
        try:
            return self.run_macro_file(tmp_path, timeout=timeout)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ─── Работа с Linux/Wine ────────────────────────────────────────

    def run_with_wine(self, *args: str, timeout: int = 300) -> CLIResult:
        """Запустить mmain.exe через Wine (для Linux без WSL)."""
        cmd = ["wine", self.mmain_path]
        if self.silent:
            cmd.append("/silentmode")
        cmd.extend(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "WINEDLLOVERRIDES": "winemenubuilder.exe=d"},
            )
            return CLIResult(
                success=result.returncode == 0,
                message=f"Wine mmain.exe завершился с кодом {result.returncode}",
                data={"stdout": result.stdout, "stderr": result.stderr},
            )
        except FileNotFoundError:
            return CLIResult(
                success=False,
                message="Wine не найден. Установите wine для запуска "
                        "mmain.exe под Linux."
            )
        except subprocess.TimeoutExpired:
            return CLIResult(
                success=False,
                message=f"Тайм-аут Wine ({timeout}с)"
            )
