#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""Tool Selection Guard -- encourages right flows for Claude Code.

PreToolUse hook that blocks suboptimal tool usage and provides constructive
guidance toward native tools, auto-approved commands, and simpler patterns.
Also enforces git safety rules (consolidated from git-safety-check.sh).
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

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
        None,
        "Use the Grep tool instead of `grep`. For `| wc -l` use `output_mode: 'count'`. "
        "For `| head` use `head_limit`.",
    ),
    (
        "rg",
        re.compile(r"^\s*rg\b"),
        None,
        "Use the Grep tool instead of `rg`. For `| wc -l` use `output_mode: 'count'`. "
        "For `| head` use `head_limit`.",
    ),
    (
        "find-name",
        re.compile(r"^\s*find\b.*-name"),
        None,
        "Use the Glob tool -- it's auto-approved and supports patterns like '**/*.py'.",
    ),
    (
        "ls-dir",
        re.compile(r"^\s*ls\s"),
        re.compile(r"\|"),
        "Use the Glob tool for file listings -- it's auto-approved and supports patterns "
        "like '**/*.py'. Use `ls` via Bash only when you need permissions/metadata.",
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
        "Use the Write tool for file content, or native tools (Grep/Read) "
        "for the downstream operation.",
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
        "python-json",
        re.compile(r"^\s*python3?\s+-c\s+.*\bjson\b"),
        re.compile(r"^\s*uv\s+run"),
        "Use `jq` for JSON processing instead of python. "
        "Example: `jq '.key'`, `jq -r '.[]'`, `jq -r '.items[] | .name'`. "
        "If jq can't handle the logic, use `uv run python -c '...'`.",
    ),
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
        "Use `prek` instead of `pre-commit`. "
        "Check for a `make` target or use `uvx prek run --all-files`.",
    ),
    (
        "prek",
        re.compile(r"^\s*(uvx\s+)?prek\b"),
        re.compile(r"^\s*make\b"),
        "Check for a `make` target (e.g. `make lint`, `make prek`) instead of "
        "running prek directly. If no make target, use `uvx prek run --all-files`.",
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
    # ── Rust tooling ──
    (
        "cargo-lint",
        re.compile(r"^\s*cargo\s+(check|clippy|fmt)\b"),
        None,
        "Check for a `make` target (e.g. `make rust-lint`, `make lint`) instead of "
        "running cargo directly. Makefile targets handle working directory and standard flags.",
    ),
    (
        "cargo-test",
        re.compile(r"^\s*cargo\s+(test|nextest)\b"),
        None,
        "Check for a `make` target (e.g. `make rust-test`, `make test`) instead of "
        "running cargo test directly.",
    ),
    (
        "cargo-build",
        re.compile(r"^\s*cargo\s+build\b"),
        None,
        "Check for a `make` target (e.g. `make rust-build`, `make build`) instead of "
        "running cargo build directly.",
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
        re.compile(r"hack/tmp"),
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

# Rules to skip when checking pipe segments — these redirect to native tools
# (Read, Grep, Glob, Edit, Write) that can't process piped command output.
# When grep/cat/head/etc. appear after |, they're filtering command output,
# not doing standalone file operations that native tools could replace.
_PIPE_SEGMENT_SKIP = frozenset(
    {
        # Category A: native tool redirections
        "cat-file",
        "head-file",
        "tail-file",
        "grep",
        "rg",
        "find-name",
        "ls-dir",
        "sed-i",
        "awk-redir",
        "echo-redir",
        "cat-heredoc",
        # Category D: echo/printf after a pipe feed downstream, not the user
        "echo-noop",
        "printf-noop",
    }
)


_SHELL_KEYWORD_PREFIX = re.compile(r"^\s*(do|then|else|elif|if|while|until)\s+")


def strip_shell_keyword(cmd):
    """Strip leading shell control keywords from a command fragment.

    When split_commands splits on ';', shell control structures like
    for/do/done and if/then/fi produce fragments with keyword prefixes:
      'do echo hi'   → 'echo hi'
      'then cat file' → 'cat file'
      'do if git reset --hard' → 'git reset --hard' (recursive)
    Keywords that don't prefix commands (for, case, done, fi, esac) are
    left alone — they pass through rule checks harmlessly.
    """
    while True:
        m = _SHELL_KEYWORD_PREFIX.match(cmd)
        if m:
            cmd = cmd[m.end() :]
        else:
            break
    return cmd


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
            stripped = stripped[m.end() :]
        else:
            break
    return stripped


def extract_bash_c(cmd):
    """Extract the inner command from bash -c '...' or sh -c '...' wrappers.

    Returns the inner command string, or None if not a bash -c invocation.
    """
    m = re.match(r"""^\s*(?:bash|sh)\s+-c\s+(['"])(.*?)\1\s*$""", cmd, re.DOTALL)
    if m:
        return m.group(2).strip()
    # Unquoted (rare but possible): bash -c command
    m = re.match(r"""^\s*(?:bash|sh)\s+-c\s+(\S+)""", cmd)
    if m:
        return m.group(1).strip()
    return None


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


def _split_respecting_quotes(text, is_delimiter):
    """Split text on unquoted delimiters while respecting single/double quotes.

    is_delimiter(text, i, current) -> int or None:
        Return the number of chars to skip (the delimiter width) if position i
        is a delimiter, or None if it is not. ``current`` is the accumulated
        characters for the segment being built (mutable list), allowing
        callers to implement backslash-continuation by inspecting/modifying it.
    """
    parts = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif not in_single and not in_double:
            skip = is_delimiter(text, i, current)
            if skip is not None:
                parts.append("".join(current).strip())
                current = []
                i += skip
                continue
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _is_pipe_delimiter(text, i, _current):
    if text[i] == "|":
        # Skip & in |&
        return 2 if i + 1 < len(text) and text[i + 1] == "&" else 1
    return None


def split_pipes(command):
    """Split a command on unquoted | while respecting quotes.

    Handles both | and |& as pipe separators. Since split_commands has
    already split on ||, any unquoted | in a subcmd is a pipe operator.
    """
    return _split_respecting_quotes(command, _is_pipe_delimiter)


# ── Git safety rules (consolidated from git-safety-check.sh) ──


def _has_force_flag(cmd):
    """Check if command contains --force (not --force-with-lease) or -f bundled."""
    if re.search(r"(^|\s)--force(\s|=|$)", cmd):
        return True
    return bool(re.search(r"(^|\s)-[a-zA-Z]*f[a-zA-Z]*(\s|$)", cmd))


def _has_force_with_lease(cmd):
    return bool(re.search(r"(^|\s)--force-with-lease(=[^\s]+)?(\s|$)", cmd))


def _get_push_target(cmd):
    parts = cmd.split()
    remote = ""
    branch = ""
    found_push = False
    for part in parts:
        if part == "push":
            found_push = True
            continue
        if found_push:
            if part.startswith("-"):
                continue
            if not remote:
                remote = part
                continue
            if not branch:
                branch = part
                break
    return remote, branch


def _parse_branch_creation(cmd):
    """Parse branch creation commands, returning (branch_name, start_point) or None.

    Handles:
      git switch -c/--create <name> [<start-point>]
      git checkout -b/-B <name> [<start-point>]
      git worktree add <path> -b <name> [<start-point>]

    Returns None if not a branch creation command.
    start_point is None if not specified.
    """
    parts = cmd.split()
    if not parts or parts[0] != "git" or len(parts) < 3:
        return None

    subcmd = parts[1]

    if subcmd == "switch":
        # Find -c or --create flag
        i = 2
        branch_name = None
        while i < len(parts):
            arg = parts[i]
            if arg == "--":
                i += 1
                continue
            if arg.startswith("--create="):
                branch_name = arg.split("=", 1)[1]
                i += 1
                break
            if arg in ("-c", "--create"):
                if i + 1 < len(parts):
                    branch_name = parts[i + 1]
                    i += 2
                    break
                return None  # malformed: -c with no branch name
            if arg.startswith("-"):
                i += 1
                continue
            # Non-flag positional before -c/--create: 'git switch <existing>'
            return None
        if branch_name is None:
            return None
        # Look for start point (next non-flag arg)
        while i < len(parts):
            if parts[i] == "--" or parts[i].startswith("-"):
                i += 1
                continue
            return (branch_name, parts[i])
        return (branch_name, None)

    if subcmd == "checkout":
        # Find -b or -B flag
        i = 2
        branch_name = None
        while i < len(parts):
            arg = parts[i]
            if arg == "--":
                i += 1
                continue
            if arg in ("-b", "-B"):
                if i + 1 < len(parts):
                    branch_name = parts[i + 1]
                    i += 2
                    break
                return None  # malformed
            if arg.startswith("-"):
                i += 1
                continue
            # Non-flag positional before -b/-B: 'git checkout <ref>'
            return None
        if branch_name is None:
            return None
        while i < len(parts):
            if parts[i] == "--" or parts[i].startswith("-"):
                i += 1
                continue
            return (branch_name, parts[i])
        return (branch_name, None)

    if subcmd == "worktree" and len(parts) > 2 and parts[2] == "add":
        # git worktree add <path> -b <name> [<start-point>]
        i = 3
        path_found = False
        branch_name = None
        while i < len(parts):
            arg = parts[i]
            if arg == "-b":
                if i + 1 < len(parts):
                    branch_name = parts[i + 1]
                    i += 2
                    break
                return None  # malformed
            if arg.startswith("-"):
                i += 1
                continue
            if not path_found:
                path_found = True
                i += 1
                continue
            # Second positional before -b: ambiguous, bail
            break
        if branch_name is None:
            return None
        while i < len(parts):
            if parts[i] == "--" or parts[i].startswith("-"):
                i += 1
                continue
            return (branch_name, parts[i])
        return (branch_name, None)

    return None


_PROTECTED_BRANCHES = frozenset({"main", "master"})

# Safe start points for branch creation (no stacking risk)
_SAFE_REMOTE_REFS = frozenset({"upstream/main", "origin/main", "upstream/master", "origin/master"})
# NOTE: HEAD is allowed as a safe start-point. While functionally identical to
# omitting the start-point (both branch from current position), specifying HEAD
# explicitly signals intentionality. The branch-no-base rule targets the common
# mistake of forgetting to specify a base, not deliberate use of HEAD.
_HEAD_PATTERN = re.compile(r"^HEAD([~^]\d*)*$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")


def _is_safe_start_point(ref):
    """Check if a start-point ref is safe (upstream remote, HEAD variant, or SHA)."""
    if ref in _SAFE_REMOTE_REFS:
        return True
    if _HEAD_PATTERN.match(ref):
        return True
    return bool(_SHA_PATTERN.match(ref))


def _is_branch_no_base(cmd):
    parsed = _parse_branch_creation(cmd)
    return parsed is not None and parsed[1] is None


def _is_branch_from_local_main(cmd):
    parsed = _parse_branch_creation(cmd)
    return parsed is not None and parsed[1] in _PROTECTED_BRANCHES


def _is_branch_from_non_upstream(cmd):
    parsed = _parse_branch_creation(cmd)
    return parsed is not None and parsed[1] is not None and not _is_safe_start_point(parsed[1])


def _setup_git_log():
    log_dir = Path.home() / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("git-safety")
    if not logger.handlers:
        handler = logging.FileHandler(log_dir / "git-safety-blocks.log")
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# Each rule: (name, check_function, message)
# check_function(cmd) -> bool
GIT_DENY_RULES = [
    (
        "reset-hard",
        lambda cmd: bool(re.search(r"git\s+reset\s+--hard", cmd)),
        "git reset --hard is FORBIDDEN. "
        "Use 'git reset --mixed' or 'git stash' to preserve changes.",
    ),
    (
        "push-force",
        lambda cmd: bool(re.search(r"git\s+push", cmd)) and _has_force_flag(cmd),
        "Force push (--force/-f) is FORBIDDEN. Use --force-with-lease for safer force pushing.",
    ),
    (
        "push-upstream",
        lambda cmd: bool(re.search(r"git\s+push", cmd)) and _get_push_target(cmd)[0] == "upstream",
        "Pushing to upstream is FORBIDDEN. Push to origin and create a PR instead.",
    ),
    (
        "fwl-main",
        lambda cmd: (
            bool(re.search(r"git\s+push", cmd))
            and _has_force_with_lease(cmd)
            and _get_push_target(cmd)[1] in _PROTECTED_BRANCHES
        ),
        "--force-with-lease to main/master is FORBIDDEN. Use feature branches for rebasing.",
    ),
    (
        "branch-D",
        lambda cmd: (
            bool(re.search(r"git\s+branch", cmd))
            and bool(re.search(r"(^|\s)-[a-zA-Z]*D[a-zA-Z]*(\s|$)", cmd))
        ),
        "git branch -D is FORBIDDEN. Use 'git branch -d' for safe deletion of merged branches.",
    ),
    (
        "branch-force",
        lambda cmd: bool(re.search(r"git\s+branch.*--force", cmd)),
        "git branch --force is FORBIDDEN. Force operations on branches must be done manually.",
    ),
    (
        "push-origin-main",
        lambda cmd: bool(re.search(r"git\s+push.*origin\s+(main|master)(\s|$)", cmd)),
        "Pushing directly to origin/main or origin/master is FORBIDDEN. "
        "Use feature branches and PRs.",
    ),
    (
        "no-verify",
        lambda cmd: bool(re.search(r"git\s+", cmd)) and "--no-verify" in cmd,
        "--no-verify flag is FORBIDDEN. Git hooks must run for all commits and pushes.",
    ),
    (
        "add-force",
        lambda cmd: bool(re.search(r"git\s+add", cmd)) and _has_force_flag(cmd),
        "git add --force is FORBIDDEN. Files are gitignored for a reason.",
    ),
    (
        "rm-cached-force",
        lambda cmd: (
            bool(re.search(r"git\s+rm", cmd))
            and "--cached" in cmd
            and (_has_force_flag(cmd) or "--force" in cmd)
        ),
        "git rm --cached --force is FORBIDDEN. Use 'git rm --cached' without --force.",
    ),
    (
        "rm-unsafe",
        lambda cmd: bool(re.search(r"git\s+rm", cmd)) and "--cached" not in cmd,
        "git rm is FORBIDDEN (deletes files). Use 'git rm --cached' to unstage only.",
    ),
    (
        "clean-ignored",
        lambda cmd: (
            bool(re.search(r"git\s+clean", cmd)) and bool(re.search(r"-[a-zA-Z]*[xX]", cmd))
        ),
        "git clean with -x or -X is FORBIDDEN. These delete ignored/untracked files irreversibly.",
    ),
    (
        "branch-no-base",
        _is_branch_no_base,
        "Branch creation without a start-point defaults to HEAD (which may be stale "
        "or another feature branch). Specify a base: "
        "git switch -c <name> upstream/main",
    ),
]

# ASK rules exit 1 (prompt user for confirmation)
GIT_ASK_RULES = [
    (
        "config-global-write",
        lambda cmd: (
            bool(re.search(r"git\s+config\s+--global", cmd))
            and not re.search(r"(--get|--list)(\s|$)", cmd)
            and not re.search(r"\s-l(\s|$)", cmd)
        ),
        "git config --global modifications require permission. "
        "Read operations (--get, --list) are allowed.",
    ),
    (
        "stash-drop",
        lambda cmd: bool(re.search(r"git\s+stash\s+drop", cmd)),
        "git stash drop permanently deletes a stash. Confirm this is intentional.",
    ),
    (
        "checkout-dash-dash",
        lambda cmd: bool(re.search(r"git\s+checkout\s+--", cmd)),
        "git checkout -- is destructive and deprecated. Consider using 'git restore' instead.",
    ),
    (
        "filter-branch",
        lambda cmd: bool(re.search(r"git\s+filter-branch", cmd)),
        "git filter-branch is dangerous and deprecated. Use git-filter-repo if truly needed.",
    ),
    (
        "reflog-delete-expire",
        lambda cmd: bool(re.search(r"git\s+reflog\s+(delete|expire)", cmd)),
        "git reflog delete/expire removes recovery points. Confirm this is intentional.",
    ),
    (
        "remote-remove",
        lambda cmd: bool(re.search(r"git\s+remote\s+(remove|rm)", cmd)),
        "Removing a git remote may break workflows. Confirm this is intentional.",
    ),
    (
        "branch-from-local-main",
        _is_branch_from_local_main,
        "Local main may be stale. Prefer upstream/main or run git fetch upstream main first.",
    ),
    (
        "branch-from-non-upstream",
        _is_branch_from_non_upstream,
        "Branching from a non-upstream ref risks branch stacking. Use upstream/main instead.",
    ),
]

_git_logger = None


def check_git_safety(cmd, fetch_seen=False):
    """Check a command against git safety rules. Exits 2 (deny) or 1 (ask) on match."""
    # Early exit: not a git command
    if not re.search(r"(^|\s)git\s", cmd):
        return

    global _git_logger
    if _git_logger is None:
        _git_logger = _setup_git_log()

    # DENY rules (exit 2)
    for name, check_fn, message in GIT_DENY_RULES:
        if check_fn(cmd):
            _git_logger.info("BLOCKED: %s | Rule: %s", cmd, name)
            print(message, file=sys.stderr)
            sys.exit(2)

    # Special case: commit to main/master (requires git rev-parse)
    if re.search(r"^\s*git\s+commit", cmd):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch = result.stdout.strip()
            if branch in _PROTECTED_BRANCHES:
                msg = (
                    f"Committing directly to {branch} is FORBIDDEN. "
                    "Create a feature branch: git switch -c feature/name"
                )
                _git_logger.info("BLOCKED: %s | Rule: commit-to-main", cmd)
                print(msg, file=sys.stderr)
                sys.exit(2)
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass  # If we can't determine branch, allow (fail open)

    # Special: branch from upstream without a preceding fetch in the command chain
    parsed = _parse_branch_creation(cmd)
    if (
        parsed is not None
        and parsed[1] is not None
        and parsed[1] in _SAFE_REMOTE_REFS
        and not fetch_seen
    ):
        _git_logger.info("ASK: %s | Rule: branch-needs-fetch", cmd)
        print(
            "No git fetch detected in this command chain. "
            "Fetch first: git fetch upstream main && "
            "git switch -c <name> upstream/main",
            file=sys.stderr,
        )
        sys.exit(1)

    # ASK rules (exit 1)
    for name, check_fn, message in GIT_ASK_RULES:
        if check_fn(cmd):
            _git_logger.info("ASK: %s | Rule: %s", cmd, name)
            print(message, file=sys.stderr)
            sys.exit(1)


def _is_command_delimiter(text, i, current):
    two = text[i : i + 2]
    if two in ("&&", "||"):
        return 2
    c = text[i]
    if c == ";":
        return 1
    if c == "\n":
        # Continuation line: \ before newline joins lines
        if current and current[-1] == "\\":
            current[-1] = " "
            return 1
        return 1
    return None


def split_commands(command):
    """Split a chained command on &&, ||, ;, and newlines while respecting quotes.

    This prevents agents from bundling blocked commands into chains
    to bypass per-command rule checks. Newlines are treated as command
    separators (like bash), but continuation lines (ending with \\) are joined.
    """
    return _split_respecting_quotes(command, _is_command_delimiter)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        logging.getLogger("git-safety").warning(
            "Hook received malformed/empty JSON input — failing open"
        )
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # ── /tmp/ guard for write-oriented tools (Write, Edit, NotebookEdit) ──
    # Read/Grep/Glob are allowed — Claude Code stores task output files in /tmp/.
    file_path = (
        tool_input.get("file_path", "")
        or tool_input.get("path", "")
        or tool_input.get("notebook_path", "")
    )
    if (
        file_path
        and "/tmp/" in file_path
        and "hack/tmp" not in file_path
        and tool_name in ("Write", "Edit", "NotebookEdit")
    ):
        print(
            "Use `hack/tmp/` (gitignored) instead of `/tmp/` for temporary files. "
            "Native tools (Read/Write/Edit) work on local files without extra permissions.",
            file=sys.stderr,
        )
        sys.exit(2)

    # ── EnterPlanMode redirect: use incremental-planning skill instead ──
    if tool_name == "EnterPlanMode":
        print(
            "Native plan mode writes and displays the full plan at once.\n"
            "Use the incremental-planning skill instead:\n"
            "  Invoke Skill tool with 'global-skills:incremental-planning'\n\n"
            "This skill asks clarifying questions first, writes the plan\n"
            "incrementally to a file, and provides research context in chat\n"
            "for informed feedback.",
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

    # Track git fetch upstream/origin across subcommands (order-aware).
    # The closure reads this; main() writes it before each check_rules call.
    fetch_seen = False

    def check_rules(cmd, skip_rules=None):
        """Check a command against all rules. Exits 2 (deny) or 1 (ask) on match.

        skip_rules: frozenset of rule names to skip (used for pipe segments
        where native-tool alternatives don't apply to piped output).
        """
        # Strip shell control keywords (do, then, else, etc.) so rules can
        # match commands inside for/while/if blocks split on ';'.
        cmd = strip_shell_keyword(cmd)
        # Git safety checks first (DENY exit 2, ASK exit 1)
        check_git_safety(cmd, fetch_seen=fetch_seen)
        # Tool selection rules (exit 2)
        normalized = strip_env_prefix(cmd)
        for name, pattern, exception, guidance in RULES:
            if skip_rules and name in skip_rules:
                continue
            target = normalized if pattern.pattern.startswith("^") else cmd
            if pattern.search(target):
                if exception and exception.search(cmd):
                    continue
                print(guidance, file=sys.stderr)
                sys.exit(2)

    for subcmd in subcmds:
        # Track git fetch before checking rules (order-aware: fetch must precede branch)
        if re.search(r"git\s+fetch\s+(upstream|origin)\b", subcmd):
            fetch_seen = True

        # Unwrap bash -c '...' / sh -c '...' and check the inner command
        inner_cmd = extract_bash_c(subcmd)
        if inner_cmd:
            # First check if inner command breaks any rules (e.g. bash -c 'cat file')
            for inner_sub in split_commands(inner_cmd):
                if re.search(r"git\s+fetch\s+(upstream|origin)\b", inner_sub):
                    fetch_seen = True
                check_rules(inner_sub)
            # If inner command is safe, STILL block — the bash -c wrapper itself
            # causes a permission prompt. Run the inner command directly instead.
            print(
                f"Run the command directly without the `bash -c` wrapper — "
                f"it causes a permission prompt. Just use: `{inner_cmd}`",
                file=sys.stderr,
            )
            sys.exit(2)
        else:
            check_rules(subcmd)
            # Check pipe segments to catch guarded commands mid-pipeline.
            # The full-command check above handles the first segment and pipe
            # exceptions. This catches commands in later segments (e.g.,
            # `oc get ... | python3 -c ...` catches the python3 usage).
            pipe_segments = split_pipes(subcmd)
            if len(pipe_segments) > 1:
                for segment in pipe_segments[1:]:
                    check_rules(segment, skip_rules=_PIPE_SEGMENT_SKIP)
        # Also check inside $() and `` substitutions
        for inner in extract_subshells(subcmd):
            if re.search(r"git\s+fetch\s+(upstream|origin)\b", inner):
                fetch_seen = True
            check_rules(inner)
            # Pipe-split subshell content too
            inner_pipes = split_pipes(inner)
            if len(inner_pipes) > 1:
                for segment in inner_pipes[1:]:
                    check_rules(segment, skip_rules=_PIPE_SEGMENT_SKIP)

    # Advisory (non-blocking): suggest Makefile targets for multi-step commands
    if len(subcmds) > 1 and os.path.exists("Makefile"):
        print(
            "TIP: A Makefile exists in this directory. "
            "Check if there's a `make` target before running raw commands."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
