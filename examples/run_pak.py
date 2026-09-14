"""Запуск пака SimInTech (.pak) с потоковым логом.

Пример::

    python examples/run_pak.py /path/to/model.pak
    python examples/run_pak.py model.pak --log run.log

Требуется Windows-версия SimInTech и путь к `mmain.exe`: переменная окружения
`SIMINTECH_PATH` либо путь по умолчанию (см. `simintech_api.cli_runner`).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simintech_api.cli_runner import CLIAdapter  # noqa: E402


def main() -> int:
    """Запустить пак, дождаться останова, вывести вывод построчно."""
    parser = argparse.ArgumentParser(description="Запуск пака SimInTech.")
    parser.add_argument("pak", type=Path, help="файл пака (.pak)")
    parser.add_argument("--log", type=Path, default=None,
                        help="куда дублировать вывод")
    args = parser.parse_args()

    if not args.pak.is_file():
        print(f"Файл пака не найден: {args.pak}", file=sys.stderr)
        return 2

    cli = CLIAdapter()
    cmd = cli.build_cmd("/start", "/run", "/exitonstop", str(args.pak.resolve()))
    print("Запуск:", " ".join(cmd), flush=True)

    log = args.log.open("w", encoding="utf-8") if args.log else None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        for line in iter(proc.stdout.readline, b""):
            text = cli._decode(line).rstrip()
            if not text:
                continue
            print(text, flush=True)
            if log:
                log.write(text + "\n")
                log.flush()
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        print("\nПрервано пользователем", file=sys.stderr)
        return 130
    finally:
        if log:
            log.close()

    print(f"\nЗавершён с кодом {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
