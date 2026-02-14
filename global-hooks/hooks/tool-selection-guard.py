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
    # ── Category A: Use native tools (no Bash permission needed) ──
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
        "Use the Write tool for file content, or native tools (Grep/Read) for the downstream operation.",
    ),
    (
        "pager",
        re.compile(r"^\s*(less|more)\b"),
        None,
        "Use the Read tool instead. Pagers are interactive and will hang in this environment.",
    ),
    (
        "editor",
        re.compile(r"^\s*(nano|vim|vi|emacs)\b"),
        None,
        "Use the Edit tool instead. Interactive editors will hang in this environment.",
    ),
    # ── Category B: Use right Python tooling (match auto-approve patterns) ──
    (
        "python",
        re.compile(r"^\s*python3?\s"),
        re.compile(r"^\s*uv\s+run"),
        "Use `uv run` instead -- it's auto-approved. Example: `uv run script.py`",
    ),
    (
        "pip",
        re.compile(r"^\s*pip3?\s+\w"),
        None,
        "Use `uv add` (install), `uv remove` (uninstall), or `uv pip` for other pip operations.",
    ),
    (
        "pytest",
        re.compile(r"^\s*pytest\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Check for a `make py-test` or use `uv run pytest` instead -- it's auto-approved.",
    ),
    (
        "black",
        re.compile(r"^\s*black\b"),
        None,
        "Formatting should be performed with ruff.",
    ),
    (
        "ruff",
        re.compile(r"^\s*ruff\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Check for a `make py-lint` or use `uv run ruff` instead -- it's auto-approved.",
    ),
    (
        "mypy",
        re.compile(r"^\s*mypy\b"),
        None,
        "Type checking should be performed with pyright.",
    ),
    (
        "pyright",
        re.compile(r"^\s*pyright\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Check for a `make py-lint` or use `uv run pyright` instead -- it's auto-approved.",
    ),
    (
        "pre-commit",
        re.compile(r"^\s*(uvx\s+|uv\s+run\s+)?pre-commit\b"),
        None,
        "Use `prek` instead of `pre-commit`. Check for a `make` target or use `uvx prek run --all-files`.",
    ),
    (
        "prek",
        re.compile(r"^\s*(uvx\s+)?prek\b"),
        re.compile(r"^\s*make\b"),
        "Check for a `make` target (e.g. `make lint`, `make prek`) instead of running prek directly. "
        "If no make target exists, use `uvx prek run --all-files`.",
    ),
    (
        "ipython",
        re.compile(r"^\s*ipython3?\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uv run ipython` instead -- it's auto-approved.",
    ),
    (
        "tox",
        re.compile(r"^\s*tox\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uvx tox` instead -- it's auto-approved.",
    ),
    (
        "isort",
        re.compile(r"^\s*isort\b"),
        None,
        "Import sorting should be performed with ruff.",
    ),
    (
        "flake8",
        re.compile(r"^\s*flake8\b"),
        None,
        "Linting should be performed with ruff.",
    ),
    # ── Category C: Encourage project conventions ──
    (
        "bash-script",
        re.compile(r"^\s*(bash|sh)\s+\S+\.sh\b"),
        re.compile(r"^\s*(bash|sh)\s+-"),
        "Check for a `make` target that wraps this script. If none exists, consider creating one.",
    ),
    (
        "direct-script",
        re.compile(r"^\s*[\w.~/-]+\.sh\b"),
        None,
        "Check for a `make` target that wraps this script. If none exists, consider creating one.",
    ),
    (
        "tmp-path",
        re.compile(r"/tmp/"),
        re.compile(r"\brm\b|hack/tmp"),
        "Use `hack/tmp/` (gitignored) instead of `/tmp/` for temporary files. "
        "Native tools (Read/Write/Edit) work without Bash permissions on local files. "
        "Clean up when done.",
    ),
    # ── Category D: Encourage simpler patterns ──
    (
        "echo-noop",
        re.compile(r"""^\s*echo\s+(['"].*['"]|[^|>&;$`]+)\s*$"""),
        None,
        "Output text directly in your response instead of using echo.",
    ),
    (
        "printf-noop",
        re.compile(r"""^\s*printf\s+(['"].*['"]|[^|>&;$`]+)\s*$"""),
        None,
        "Output text directly in your response instead of using printf.",
    ),
    # ── Category E: Interactive commands that will hang ──
    (
        "git-rebase-i",
        re.compile(r"^\s*git\s+rebase\s+.*(-i\b|--interactive\b)"),
        None,
        "Interactive rebase will hang. Use git-branchless: `git reword`, `git branchless move`.",
    ),
    (
        "git-add-interactive",
        re.compile(r"^\s*git\s+add\s+.*(-[pi]\b|--patch\b|--interactive\b)"),
        None,
        "Interactive git add will hang. Use `git add` with specific file paths instead.",
    ),
]


def strip_env_prefix(cmd):
    """Strip leading KEY=value pairs from a command.

    Bash allows `FOO=bar CMD args` where FOO is set for CMD's environment.
    Rules anchor on the command name, so we strip these prefixes first.
    Also strips variable assignments like `result=...` when followed by a command.
    """
    stripped = cmd
    while True:
        m = re.match(r"^\s*[A-Za-z_]\w*=\S*\s+", stripped)
        if m:
            stripped = stripped[m.end():]
        else:
            break
    return stripped


def extract_subshells(cmd):
    """Extract commands inside $() and `` substitutions for rule checking.

    Returns a list of inner commands found in subshell substitutions.
    """
    inner = []
    # $(...) — handles simple nesting by finding matched parens
    for m in re.finditer(r"\$\(", cmd):
        start = m.end()
        depth = 1
        pos = start
        while pos < len(cmd) and depth > 0:
            if cmd[pos] == "(":
                depth += 1
            elif cmd[pos] == ")":
                depth -= 1
            pos += 1
        if depth == 0:
            inner.append(cmd[start : pos - 1].strip())
    # `...` backticks (no nesting)
    for m in re.finditer(r"`([^`]+)`", cmd):
        inner.append(m.group(1).strip())
    return inner


def split_commands(command):
    """Split a chained command on &&, ||, ;, and newlines while respecting quotes.

    This prevents agents from bundling blocked commands into chains
    to bypass per-command rule checks. Newlines are treated as command
    separators (like bash), but continuation lines (ending with \\) are joined.
    """
    parts = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif not in_single and not in_double:
            if command[i : i + 2] in ("&&", "||"):
                parts.append("".join(current).strip())
                current = []
                i += 2
                continue
            elif c == ";":
                parts.append("".join(current).strip())
                current = []
            elif c == "\n":
                # Continuation line: \ before newline joins lines
                if current and current[-1] == "\\":
                    current[-1] = " "
                else:
                    parts.append("".join(current).strip())
                    current = []
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # ── /tmp/ guard for ALL tools (Read, Write, Edit, Grep, Glob, Bash) ──
    # Fires before the permission layer so the agent gets guidance, not a prompt.
    file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
    if file_path and "/tmp/" in file_path and "hack/tmp" not in file_path:
        print(
            "Use `hack/tmp/` (gitignored) instead of `/tmp/` for temporary files. "
            "Native tools (Read/Write/Edit) work on local files without extra permissions.",
            file=sys.stderr,
        )
        sys.exit(2)

    if tool_name != "Bash":
        sys.exit(0)

    command = tool_input.get("command", "").strip()
    if not command:
        sys.exit(0)

    # Andon cord: GUARD_BYPASS=1 prefix skips all rule checks.
    # The settings.json "ask" list ensures this still requires user approval.
    # Checked on the raw command — if the entire command is prefixed, skip everything.
    if command.startswith("GUARD_BYPASS=1 "):
        sys.exit(0)

    # Split chained commands and check each subcommand independently.
    # This prevents bundling blocked commands into && chains to bypass rules.
    subcmds = split_commands(command)

    def check_rules(cmd):
        """Check a command string against all rules. Exits 2 on first match."""
        normalized = strip_env_prefix(cmd)
        for _name, pattern, exception, guidance in RULES:
            target = normalized if pattern.pattern.startswith("^") else cmd
            if pattern.search(target):
                if exception and exception.search(cmd):
                    continue
                print(guidance, file=sys.stderr)
                sys.exit(2)

    for subcmd in subcmds:
        check_rules(subcmd)
        # Also check inside $() and `` substitutions
        for inner in extract_subshells(subcmd):
            check_rules(inner)

    # Advisory (non-blocking): suggest Makefile targets for multi-step commands
    if len(subcmds) > 1 and os.path.exists("Makefile"):
        print(
            "TIP: A Makefile exists in this directory. "
            "Check if there's a `make` target before running raw commands."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
