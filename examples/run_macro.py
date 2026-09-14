"""Запуск макроса SimInTech с потоковым логом.

Пример::

    python examples/run_macro.py macros.txt
    python examples/run_macro.py macros.txt --log mmain_run.log

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
    """Запустить макрос и вывести его вывод построчно."""
    parser = argparse.ArgumentParser(description="Запуск макроса SimInTech.")
    parser.add_argument("macro", type=Path, help="файл макроса")
    parser.add_argument("--log", type=Path, default=None,
                        help="куда дублировать вывод")
    parser.add_argument("--timeout", type=int, default=600,
                        help="тайм-аут в секундах")
    args = parser.parse_args()

    if not args.macro.is_file():
        print(f"Файл макроса не найден: {args.macro}", file=sys.stderr)
        return 2

    cli = CLIAdapter()
    cmd = cli.build_cmd("/macros", str(args.macro.resolve()))
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
