"""Command-line interface for standup-tool."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from standup_tool.config import ConfigError, config_dir, load_config

_SECRET_FIELD_NAMES = frozenset({"token", "cookie", "password"})


def _serialize_config_value(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, tuple | list):
        return [_serialize_config_value(item) for item in obj]
    if isinstance(obj, dict):
        return {
            str(k): _serialize_config_value(v)
            for k, v in obj.items()
            if str(k).lower() not in _SECRET_FIELD_NAMES
        }
    return obj


def _strip_secret_keys(data: Any) -> Any:
    """Recursively convert Paths/tuples and strip any key named token, cookie, or password."""
    return _serialize_config_value(data)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for standup-tool."""
    parser = argparse.ArgumentParser(
        prog="standup-tool",
        description="Daily standup data collector, digest formatter, and helper CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser(
        "config",
        help="Inspect or validate standup configuration.",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser(
        "show",
        help="Print effective configuration as JSON (without requiring repo paths to exist).",
    )
    config_subparsers.add_parser(
        "validate",
        help="Validate configuration file and repository paths.",
    )
    config_parser.set_defaults(_config_parser=config_parser)

    return parser


def _handle_config(args: argparse.Namespace) -> None:
    subcmd = getattr(args, "config_command", None)
    if subcmd is None:
        config_parser: argparse.ArgumentParser | None = getattr(args, "_config_parser", None)
        if config_parser is not None:
            config_parser.print_help(sys.stderr)
        sys.exit(2)

    if subcmd == "show":
        cfg = load_config(require_repos=False)
        raw = dataclasses.asdict(cfg)
        raw["config_dir"] = str(config_dir())
        cleaned = _strip_secret_keys(raw)
        sys.stdout.write(json.dumps(cleaned, indent=2) + "\n")
        return

    if subcmd == "validate":
        load_config(require_repos=True)
        sys.stdout.write("ok\n")
        return


def main(argv: list[str] | None = None) -> None:
    """Entry point for the standup-tool console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command is None:
        parser.print_help(sys.stderr)
        sys.exit(2)

    try:
        if command == "config":
            _handle_config(args)
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
