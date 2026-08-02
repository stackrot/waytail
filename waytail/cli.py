from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import __version__
from .backend import (
    WaytailError,
    clear_exit_node,
    connect,
    disconnect,
    load_status,
    refresh_waybar,
    set_exit_node,
    waybar_payload,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="waytail")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("waybar")
    commands.add_parser("status")
    commands.add_parser("panel")
    commands.add_parser("connect")
    commands.add_parser("disconnect")
    commands.add_parser("clear-exit")
    exit_command = commands.add_parser("set-exit")
    exit_command.add_argument("ip")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "waybar":
        print(json.dumps(waybar_payload(), ensure_ascii=False))
        return 0
    if args.command == "panel":
        from .panel import run_panel

        return run_panel()
    try:
        if args.command == "status":
            print(json.dumps(asdict(load_status()), indent=2, ensure_ascii=False))
        elif args.command == "connect":
            connect()
        elif args.command == "disconnect":
            disconnect()
        elif args.command == "set-exit":
            set_exit_node(args.ip)
        elif args.command == "clear-exit":
            clear_exit_node()
        refresh_waybar()
        return 0
    except WaytailError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
