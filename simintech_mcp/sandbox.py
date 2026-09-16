"""Песочница результатов расчёта: каталог и ограниченное чтение.

`read_output_file`, `summarize_output_file` и `inspect_project_file` читают
только каталог результатов — всегда. Предел объёма применяется **до** чтения
(`_read_bounded` читает не больше предела + 1 байта): проверка «прочитать,
потом отказать по размеру» защитой не является, память к моменту проверки уже
израсходована.
"""

from __future__ import annotations

import os
from typing import Tuple

from fastmcp.exceptions import ToolError
from simintech_api.constants import (
    default_output_dir as simintech_default_output_dir,
)


# ─── Результаты расчёта ───────────────────────────────────────────

#: Переменная окружения: каталог, за пределы которого не выходит
#: `read_output_file`.
OUTPUT_DIR_ENV = "SIMINTECH_OUTPUT_DIR"

#: Предел объёма, отдаваемого в контекст: защита от чтения большого
#: двоичного файла вместо текстового результата.
MAX_OUTPUT_BYTES = 2 * 1024 * 1024

#: Стандартный каталог результатов и его подкаталог берутся из библиотеки:
#: это общее соглашение, а не деталь MCP. Пример `examples/run_to_file.py`
#: пишет туда же, поэтому файл удаётся прочитать через `read_output_file`.
default_output_dir = simintech_default_output_dir


def _ensure_default_output_dir() -> str:
    """Создать стандартный каталог результатов, не следуя по чужой ссылке.

    Каталог лежит в предсказуемом месте, поэтому его мог заранее создать другой
    процесс — **символической ссылкой** на выбранный им каталог. `realpath` тогда
    увёл бы песочницу туда, и ограничение стало бы фиктивным. Поэтому каталог
    создаётся только когда его нет (`os.mkdir` без `exist_ok`), с правами 0o700,
    и подмена ссылкой отвергается явно.
    """
    path = default_output_dir()
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ToolError(
            f"Не удалось создать каталог результатов «{path}»: {exc}"
        ) from exc
    if os.path.islink(path):
        raise ToolError(
            f"«{path}» — символическая ссылка (возможна подмена каталога "
            f"результатов) — чтение запрещено"
        )
    if not os.path.isdir(path):
        raise ToolError(f"«{path}» не является каталогом — чтение запрещено")
    return os.path.realpath(path)


def output_root() -> str:
    """Каталог, из которого разрешено читать результаты. Ограничение всегда есть.

    Порядок: ``SIMINTECH_OUTPUT_DIR``, иначе стандартный каталог
    (`default_output_dir()`, создаётся при необходимости).

    Если переменная задана, но каталога нет — это ошибка конфигурации и чтение
    запрещено (fail closed): иначе инструмент отказывал бы с невнятной причиной
    «путь вне разрешённого каталога», хотя проблема в настройке.
    """
    raw = os.environ.get(OUTPUT_DIR_ENV)
    if raw:
        root = os.path.realpath(raw)
        if not os.path.isdir(root):
            raise ToolError(
                f"{OUTPUT_DIR_ENV}=«{raw}» не является каталогом — чтение "
                f"результатов запрещено"
            )
        return root
    return _ensure_default_output_dir()


def _safe_output_root() -> str:
    """Каталог результатов для справки: не падает на ошибке конфигурации."""
    try:
        return output_root()
    except ToolError as exc:
        return f"<ошибка конфигурации {OUTPUT_DIR_ENV}: {exc}>"


def _is_inside(root: str, path: str) -> bool:
    """Лежит ли `path` внутри `root` (оба уже realpath'нуты)."""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:                  # разные диски (Windows)
        return False


def _read_bounded(resolved: str, max_bytes: int) -> Tuple[bytes, bool]:
    """Прочитать файл не более `max_bytes` байт: `(данные, обрезано)`.

    Ограничение обязательно именно **до** чтения. Проверка «прочитать целиком,
    потом отказать по размеру» защитой не является: файл в каталоге
    результатов мог создать кто угодно, и память к моменту проверки уже
    израсходована. По той же причине нельзя опираться на построчное чтение:
    одна строка без переводов поднялась бы в память целиком.

    Обрезка может прийтись на середину многобайтового символа: текстовые
    читатели этого модуля декодируют с `errors="replace"`, а разбор `.xprt`
    отвергает обрезанный файл ещё до декодирования.
    """
    with open(resolved, "rb") as fh:
        raw = fh.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return raw[:max_bytes], True
    return raw, False


#: Отказ, когда файла результата нет. Один и тот же текст у всех, кто читает
#: файл блока «В файл»: расхождение формулировок путало бы агента.
_MISSING_RESULT_FILE = ("ERROR: файла нет: {path}. Проверьте свойство "
                        "`filename` блока «В файл» и что расчёт действительно "
                        "прошёл.")

#: Отказ, когда нет сохранённого проекта: подсказка здесь своя.
_MISSING_PROJECT_FILE = ("ERROR: файла нет: {path}. Сохраните проект через "
                         "`save_project` в каталог результатов.")


def _load_result_file(path: str, max_bytes: int,
                      missing: str) -> Tuple[bytes, bool, str]:
    """Разрешить путь в песочнице и прочитать файл не больше `max_bytes`.

    Читатели файлов (`read_output_file`, `summarize_output_file`,
    `inspect_project_file`) делают одно и то же — разрешают путь, проверяют
    наличие, читают ограниченно и переводят сбой в текст отказа. Пока это
    было скопировано трижды, копии успели разойтись в формулировках и в том,
    что считать отказом.

    Args:
        path: путь от клиента (абсолютный или относительный — к каталогу).
        max_bytes: предел чтения; применяется **до** чтения (см.
            `_read_bounded`).
        missing: текст отказа при отсутствующем файле; в него подставляется
            `path` (у разных инструментов подсказка разная).

    Returns:
        `(data, truncated, error)`: `error` непуст, если читать нечего; иначе
        `data` — прочитанное (возможно, обрезанное) содержимое.
    """
    resolved = _resolve_output_path(path)
    if not os.path.isfile(resolved):
        return b"", False, missing.format(path=path)
    try:
        data, truncated = _read_bounded(resolved, max_bytes)
    except OSError as exc:
        return b"", False, f"ERROR: {exc}"
    return data, truncated, ""


def _resolve_output_path(path: str) -> str:
    """Разрешить путь внутри каталога результатов (см. `read_output_file`).

    Относительный путь ищется внутри каталога результатов — так запись и
    чтение не расходятся. `realpath` выполняется до проверки, поэтому `..`
    и символические ссылки наружу не выводят.

    Raises:
        ToolError: путь ведёт за пределы каталога результатов.
    """
    root = output_root()
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    resolved = os.path.realpath(candidate)
    if not _is_inside(root, resolved):
        raise ToolError(
            f"Чтение разрешено только из каталога «{root}» (переопределяется "
            f"переменной {OUTPUT_DIR_ENV}). Блок «В файл» должен писать "
            f"внутрь него — задайте filename с этим каталогом."
        )
    return resolved
