"""Command-line interface for standup-tool."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for standup-tool."""
    return argparse.ArgumentParser(
        prog="standup-tool",
        description="Daily standup data collector, digest formatter, and helper CLI.",
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the standup-tool console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.print_help(sys.stderr)
        sys.exit(2)
