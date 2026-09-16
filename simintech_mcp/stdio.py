"""Изоляция stdout: транспорт пишет JSON-RPC в `sys.stdout.buffer`.

`_StdoutGuard` отводит `sys.stdout.write()` в stderr, оставляя `.buffer`
настоящим — через него `mcp/server/stdio.py` пишет протокол. Без этого любая
случайная печать в stdout (или чужой `print`) рвёт транспорт.
"""

from __future__ import annotations

import sys
from typing import Any


# ─── Изоляция stdout ──────────────────────────────────────────────

class _StdoutGuard:
    """Прокси sys.stdout, защищающий JSON-RPC-транспорт от постороннего вывода.

    Транспорт MCP (`mcp/server/stdio.py`) пишет протокол через
    `sys.stdout.buffer`. Любая запись в `sys.stdout.write()` — `print()` из
    библиотеки, логов или предупреждений — уйдёт в тот же поток и повредит
    JSON-RPC (симптом плавающий: транспорт ломается на ровном месте).

    Прокси отводит `write()` в stderr, но оставляет `.buffer` настоящим,
    поэтому протокол продолжает работать.

    Ограничение: не перехватывает запись напрямую в файловый дескриптор 1
    из нативного кода. Для SimInTech это неактуально: `mmain.exe` —
    out-of-proc COM-сервер со своей консолью.
    """

    def __init__(self, real: Any, err: Any):
        self._buffer = getattr(real, "buffer", None)
        self._err = err

    @property
    def buffer(self):
        """Настоящий бинарный буфер stdout — им пользуется транспорт MCP."""
        return self._buffer

    def write(self, text: str) -> int:
        return self._err.write(text)

    def writelines(self, lines) -> None:
        return self._err.writelines(lines)

    def flush(self) -> None:
        return self._err.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._err.fileno()

    def __getattr__(self, name: str):
        # Прочее (encoding, errors, ...) — как у stderr. Приватные имена не
        # делегируем, иначе возможна рекурсия при неинициализированном _err.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._err, name)


def isolate_stdout() -> None:
    """Отвести посторонний вывод из stdout в stderr (см. `_StdoutGuard`).

    Идемпотентна: повторный вызов не оборачивает прокси второй раз.
    """
    real = sys.stdout
    if isinstance(real, _StdoutGuard):
        return
    sys.stdout = _StdoutGuard(real, sys.stderr)
