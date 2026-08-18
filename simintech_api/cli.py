"""CLI-интерфейс simintech-api.

Позволяет выполнять команды ИИ-агента из консоли или скриптов:

    simintech-cli "create project \"Model\"" "add block \"Константа\" as k1 with y0=5"

Запуск в интерактивном режиме — без аргументов:
    simintech-cli

Работает только на Windows (требует mmain.exe /regserver).
"""

from __future__ import annotations

import argparse
import sys

from .agent import SimInTechAgent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="simintech-cli",
        description="Управление SimInTech через COM API текстовыми командами.",
    )
    parser.add_argument(
        "commands", nargs="*",
        help="одна или несколько команд (выполняются последовательно); "
             "без команд — интерактивный режим",
    )
    parser.add_argument(
        "--no-silent", action="store_true",
        help="не скрывать UI SimInTech",
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("simintech-cli работает только на Windows (COM API SimInTech).",
              file=sys.stderr)
        return 2

    agent = SimInTechAgent()
    agent.client.set_silent_mode(0 if args.no_silent else 1)

    if args.commands:
        return _run_batch(agent, args.commands)

    return _run_interactive(agent)


def _run_batch(agent: SimInTechAgent, commands) -> int:
    for cmd in commands:
        result = agent.execute(cmd)
        print(result)
        if not result.ok:
            return 1
    return 0


def _run_interactive(agent: SimInTechAgent) -> int:
    print("simintech-cli: интерактивный режим. Наберите 'help' или 'exit'.")
    while True:
        try:
            line = input("sit> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "выход"):
            break
        result = agent.execute(line)
        print(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
