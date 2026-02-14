#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""Tool Selection Guard -- encourages right flows for Claude Code.

PreToolUse hook that blocks suboptimal tool usage and provides constructive
guidance toward native tools, auto-approved commands, and simpler patterns.
"""
import json
import os
import re
import sys

# Each rule: (name, pattern, exception_pattern_or_None, guidance_message)
# Pattern: if command matches this, consider blocking
# Exception: if command ALSO matches this, allow it (skip the block)
RULES = [
    # Category A: Use native tools (no Bash permission needed)
    (
        "cat-file",
        re.compile(r"^\s*cat\s+(?!<<)\S"),
        re.compile(r"\|"),
        "Use the Read tool instead of `cat`. It's always available -- no Bash permission needed.",
    ),
    (
        "head-file",
        re.compile(r"^\s*head\s+"),
        re.compile(r"\|"),
        "Use the Read tool with `limit` parameter instead of `head`.",
    ),
    (
        "tail-file",
        re.compile(r"^\s*tail\s+"),
        re.compile(r"\|"),
        "Use the Read tool with `offset` parameter instead of `tail`.",
    ),
    (
        "grep",
        re.compile(r"^\s*grep\b"),
        re.compile(r"\|"),
        "Use the Grep tool instead -- it's auto-approved with optimized access.",
    ),
    (
        "rg",
        re.compile(r"^\s*rg\b"),
        re.compile(r"\|"),
        "Use the Grep tool instead -- it's auto-approved with optimized access.",
    ),
    (
        "find-name",
        re.compile(r"^\s*find\b.*-name"),
        None,
        "Use the Glob tool -- it's auto-approved and supports patterns like '**/*.py'.",
    ),
    (
        "sed-i",
        re.compile(r"^\s*sed\b.*\s-i"),
        None,
        "Use the Edit tool instead of `sed -i`. It's native -- no Bash permission needed.",
    ),
    (
        "awk-redir",
        re.compile(r"^\s*awk\b.*>\s*\S"),
        None,
        "Use the Edit tool instead of awk with redirect.",
    ),
    (
        "echo-redir",
        re.compile(r"^\s*(echo|printf)\b.*[^2]>\s*[^&/\s]"),
        re.compile(r">\s*/dev/"),
        "Use the Write tool instead of redirect. It's native -- no permission needed.",
    ),
    (
        "cat-heredoc",
        re.compile(r"^\s*cat\s*<<"),
        None,
        "Use the Write tool instead of cat heredoc.",
    ),
    # Category B: Use right Python tooling (match auto-approve patterns)
    (
        "python",
        re.compile(r"^\s*python3?\s"),
        re.compile(r"^\s*uv\s+run"),
        "Use `uv run` instead -- it's auto-approved. Example: `uv run script.py`",
    ),
    (
        "pip",
        re.compile(r"^\s*pip3?\s+install\b"),
        None,
        "Use `uv add` instead -- it's auto-approved.",
    ),
    (
        "pytest",
        re.compile(r"^\s*pytest\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uvx pytest` instead -- it's auto-approved.",
    ),
    (
        "black",
        re.compile(r"^\s*black\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uvx black` instead -- it's auto-approved.",
    ),
    (
        "ruff",
        re.compile(r"^\s*ruff\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uvx ruff` instead -- it's auto-approved.",
    ),
    (
        "mypy",
        re.compile(r"^\s*mypy\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uvx mypy` instead -- it's auto-approved.",
    ),
    # Category C: Encourage simpler patterns
    (
        "echo-noop",
        re.compile(r"""^\s*echo\s+(['"].*['"]|[^|>&;]+)\s*$"""),
        None,
        "Output text directly in your response instead of using echo.",
    ),
]


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "").strip()
    if not command:
        sys.exit(0)

    # Check blocking rules
    for _name, pattern, exception, guidance in RULES:
        if pattern.search(command):
            if exception and exception.search(command):
                continue
            print(guidance, file=sys.stderr)
            sys.exit(2)

    # Advisory (non-blocking): suggest Makefile targets for multi-step commands
    if ("&&" in command or ";" in command) and os.path.exists("Makefile"):
        print(
            "TIP: A Makefile exists in this directory. "
            "Check if there's a `make` target before running raw commands."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
