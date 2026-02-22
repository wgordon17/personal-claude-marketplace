#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""Tool Selection Guard -- encourages right flows for Claude Code.

PreToolUse hook that blocks suboptimal tool usage and provides constructive
guidance toward native tools, auto-approved commands, and simpler patterns.
Also enforces git safety rules (consolidated from git-safety-check.sh).
"""

import contextlib
import datetime
import functools
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple


class CommandRule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    exception: re.Pattern[str] | None
    guidance: str
    action: str = "block"


class URLRule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    guidance: str
    action: str = "block"


class GitRule(NamedTuple):
    name: str
    check_fn: Callable[[str], bool]
    message: str


# Pattern matches → candidate for blocking.
# Exception matches → allow (skip the block).
RULES: list[CommandRule] = [
    # ── Category A: Use native tools (no Bash permission needed) ──
    CommandRule(
        "cat-file",
        re.compile(r"^\s*cat\s+(?!<<)\S"),
        re.compile(r"\|"),
        "Use the Read tool instead of `cat`. It's always available -- no Bash permission needed.",
    ),
    CommandRule(
        "head-file",
        re.compile(r"^\s*head\s+"),
        re.compile(r"\|"),
        "Use the Read tool with `limit` parameter instead of `head`.",
    ),
    CommandRule(
        "tail-file",
        re.compile(r"^\s*tail\s+"),
        re.compile(r"\|"),
        "Use the Read tool with `offset` parameter instead of `tail`.",
    ),
    CommandRule(
        "grep",
        re.compile(r"^\s*grep\b"),
        None,
        "Use the Grep tool instead of `grep`. For `| wc -l` use `output_mode: 'count'`. "
        "For `| head` use `head_limit`.",
    ),
    CommandRule(
        "rg",
        re.compile(r"^\s*rg\b"),
        None,
        "Use the Grep tool instead of `rg`. For `| wc -l` use `output_mode: 'count'`. "
        "For `| head` use `head_limit`.",
    ),
    CommandRule(
        "find-name",
        re.compile(r"^\s*find\b.*-name"),
        None,
        "Use the Glob tool -- it's auto-approved and supports patterns like '**/*.py'.",
    ),
    CommandRule(
        "ls-dir",
        re.compile(r"^\s*ls\s"),
        re.compile(r"\|"),
        "Use the Glob tool for file listings -- it's auto-approved and supports patterns "
        "like '**/*.py'. Use `ls` via Bash only when you need permissions/metadata.",
    ),
    CommandRule(
        "sed-i",
        re.compile(r"^\s*sed\b.*\s-i"),
        None,
        "Use the Edit tool instead of `sed -i`. It's native -- no Bash permission needed.",
    ),
    CommandRule(
        "awk-redir",
        re.compile(r"^\s*awk\b.*>\s*\S"),
        None,
        "Use the Edit tool instead of awk with redirect.",
    ),
    CommandRule(
        "echo-redir",
        re.compile(r"^\s*(echo|printf)\b.*[^2]>\s*[^&/\s]"),
        re.compile(r">\s*/dev/"),
        "Use the Write tool instead of redirect. It's native -- no permission needed.",
    ),
    CommandRule(
        "cat-heredoc",
        re.compile(r"^\s*cat\s*<<"),
        None,
        "Use the Write tool for file content, or native tools (Grep/Read) "
        "for the downstream operation.",
    ),
    CommandRule(
        "pager",
        re.compile(r"^\s*(less|more)\b"),
        None,
        "Use the Read tool instead. Pagers are interactive and will hang in this environment.",
    ),
    CommandRule(
        "editor",
        re.compile(r"^\s*(nano|vim|vi|emacs)\b"),
        None,
        "Use the Edit tool instead. Interactive editors will hang in this environment.",
    ),
    # ── Category B: Use right Python tooling (match auto-approve patterns) ──
    CommandRule(
        "python-json",
        re.compile(r"^\s*python3?\s+-c\s+.*\bjson\b"),
        re.compile(r"^\s*uv\s+run"),
        "Use `jq` for JSON processing instead of python. "
        "Example: `jq '.key'`, `jq -r '.[]'`, `jq -r '.items[] | .name'`. "
        "If jq can't handle the logic, use `uv run python -c '...'`.",
    ),
    CommandRule(
        "python",
        re.compile(r"^\s*python3?\s"),
        re.compile(r"^\s*uv\s+run"),
        "Use `uv run` instead -- it's auto-approved. Example: `uv run script.py`",
    ),
    CommandRule(
        "pip",
        re.compile(r"^\s*pip3?\s+\w"),
        None,
        "Use `uv add` (install), `uv remove` (uninstall), or `uv pip` for other pip operations.",
    ),
    CommandRule(
        "pytest",
        re.compile(r"^\s*pytest\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Check for a `make py-test` or use `uv run pytest` instead -- it's auto-approved.",
    ),
    CommandRule(
        "black",
        re.compile(r"^\s*black\b"),
        None,
        "Formatting should be performed with ruff.",
    ),
    CommandRule(
        "ruff",
        re.compile(r"^\s*ruff\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Check for a `make py-lint` or use `uv run ruff` instead -- it's auto-approved.",
    ),
    CommandRule(
        "mypy",
        re.compile(r"^\s*mypy\b"),
        None,
        "Type checking should be performed with pyright.",
    ),
    CommandRule(
        "pyright",
        re.compile(r"^\s*pyright\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Check for a `make py-lint` or use `uv run pyright` instead -- it's auto-approved.",
    ),
    CommandRule(
        "pre-commit",
        re.compile(r"^\s*(uvx\s+|uv\s+run\s+)?pre-commit\b"),
        None,
        "Use `prek` instead of `pre-commit`. "
        "Check for a `make` target or use `uvx prek run --all-files`.",
    ),
    CommandRule(
        "prek",
        re.compile(r"^\s*(uvx\s+)?prek\b"),
        re.compile(r"^\s*make\b"),
        "Check for a `make` target (e.g. `make lint`, `make prek`) instead of "
        "running prek directly. If no make target, use `uvx prek run --all-files`.",
    ),
    CommandRule(
        "ipython",
        re.compile(r"^\s*ipython3?\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uv run ipython` instead -- it's auto-approved.",
    ),
    CommandRule(
        "tox",
        re.compile(r"^\s*tox\b"),
        re.compile(r"^\s*(uvx|uv\s+run)"),
        "Use `uvx tox` instead -- it's auto-approved.",
    ),
    CommandRule(
        "isort",
        re.compile(r"^\s*isort\b"),
        None,
        "Import sorting should be performed with ruff.",
    ),
    CommandRule(
        "flake8",
        re.compile(r"^\s*flake8\b"),
        None,
        "Linting should be performed with ruff.",
    ),
    # ── Rust tooling ──
    CommandRule(
        "cargo-lint",
        re.compile(r"^\s*cargo\s+(check|clippy|fmt)\b"),
        None,
        "Check for a `make` target (e.g. `make rust-lint`, `make lint`) instead of "
        "running cargo directly. Makefile targets handle working directory and standard flags.",
    ),
    CommandRule(
        "cargo-test",
        re.compile(r"^\s*cargo\s+(test|nextest)\b"),
        None,
        "Check for a `make` target (e.g. `make rust-test`, `make test`) instead of "
        "running cargo test directly.",
    ),
    CommandRule(
        "cargo-build",
        re.compile(r"^\s*cargo\s+build\b"),
        None,
        "Check for a `make` target (e.g. `make rust-build`, `make build`) instead of "
        "running cargo build directly.",
    ),
    # ── Category C: Encourage project conventions ──
    CommandRule(
        "bash-script",
        re.compile(r"^\s*(bash|sh)\s+\S+\.sh\b"),
        re.compile(r"^\s*(bash|sh)\s+-"),
        "Check for a `make` target that wraps this script. If none exists, consider creating one.",
    ),
    CommandRule(
        "direct-script",
        re.compile(r"^\s*[\w.~/-]+\.sh\b"),
        None,
        "Check for a `make` target that wraps this script. If none exists, consider creating one.",
    ),
    CommandRule(
        "tmp-path",
        re.compile(r"/tmp/"),
        re.compile(r"hack/tmp"),
        "Use `hack/tmp/` (gitignored) instead of `/tmp/` for temporary files. "
        "Native tools (Read/Write/Edit) work without Bash permissions on local files. "
        "Clean up when done.",
    ),
    # ── Category D: Encourage simpler patterns ──
    CommandRule(
        "echo-noop",
        re.compile(r"""^\s*echo\s+(['"].*['"]|[^|>&;$`]+)\s*$"""),
        None,
        "Output text directly in your response instead of using echo.",
    ),
    CommandRule(
        "printf-noop",
        re.compile(r"""^\s*printf\s+(['"].*['"]|[^|>&;$`]+)\s*$"""),
        None,
        "Output text directly in your response instead of using printf.",
    ),
    # ── Category E: Interactive commands that will hang ──
    CommandRule(
        "git-rebase-i",
        re.compile(r"^\s*git\s+rebase\s+.*(-i\b|--interactive\b)"),
        None,
        "Interactive rebase will hang. Use git-branchless: `git reword`, `git branchless move`.",
    ),
    CommandRule(
        "git-add-interactive",
        re.compile(r"^\s*git\s+add\s+.*(-[pi]\b|--patch\b|--interactive\b)"),
        None,
        "Interactive git add will hang. Use `git add` with specific file paths instead.",
    ),
]

# Action field for user-defined rules: "block", "ask", or "allow".
# These strings are stored directly in rule tuples and passed to _exit_with_decision().

_db_conn = None

_MAX_INPUT_BYTES = 10 * 1024 * 1024
_MAX_COMMAND_LEN = 100_000  # 100KB — generous for any real command
_MAX_MANIFEST_BYTES = 1_048_576
_HOOK_EVENT_NAME = "PreToolUse"
_SESSION_ID_KEY = "last_session_id"
_DB_TIMEOUT_SEC = 5
_DB_BUSY_TIMEOUT_MS = 1000
_LOG_ACTION_FOR: dict[str, str] = {"ask": "asked", "block": "blocked", "allow": "allowed"}

_DB_PATH = Path(
    os.environ.get("GUARD_DB_PATH", str(Path.home() / ".claude" / "logs" / "dev-guard.db"))
)
_GUARD_LOG_LEVEL = os.environ.get("GUARD_LOG_LEVEL", "actions").lower()
_session_id = None
_tool_use_id = None

_SECRET_PATTERN = re.compile(
    r"((?:password|token|secret|key|auth|bearer|api[_-]?key|credentials)"
    r"[\s=:]+)\S{8,}",
    re.IGNORECASE,
)


def _redact_secrets(text: str | None) -> str | None:
    """Redact potential credentials from text before audit logging."""
    if text is None:
        return None
    return _SECRET_PATTERN.sub(r"\1[REDACTED]", text)


def _init_db() -> sqlite3.Connection | None:
    """Create/open the SQLite audit database with WAL mode.

    Creates all tables upfront. Caches connection in _db_conn.
    Returns connection or None on error.
    """
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Restrict parent dir if we just created it
        with contextlib.suppress(OSError):
            os.chmod(str(_DB_PATH.parent), 0o700)
        # Set umask so WAL/SHM files are also owner-only
        old_umask = os.umask(0o177)
        try:
            conn = sqlite3.connect(str(_DB_PATH), timeout=_DB_TIMEOUT_SEC)
        finally:
            os.umask(old_umask)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={int(_DB_BUSY_TIMEOUT_MS)}")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                session_id TEXT,
                tool_use_id TEXT,
                category TEXT NOT NULL,
                rule TEXT,
                action TEXT NOT NULL,
                command TEXT,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS trusted_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT NOT NULL,
                match_pattern TEXT,
                scope TEXT NOT NULL,
                session_id TEXT,
                created_ts TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_trust_rule_match_scope
                ON trusted_rules(rule_name, COALESCE(match_pattern, ''), scope);
        """)
        conn.commit()
        os.chmod(str(_DB_PATH), 0o600)  # Owner-only file access
        _db_conn = conn
        return _db_conn
    except (OSError, sqlite3.Error):
        return None


def _log_event(
    category: str,
    action: str,
    *,
    rule: str | None = None,
    command: str | None = None,
    detail: object | None = None,
) -> None:
    """Log an event to the SQLite audit database.

    Respects _GUARD_LOG_LEVEL: "off" = no logging, "actions" = skip allowed,
    "all" = log everything.
    """
    if _GUARD_LOG_LEVEL == "off":
        return
    if _GUARD_LOG_LEVEL == "actions" and action == "allowed":
        return
    try:
        conn = _init_db()
        if conn is None:
            return
        detail_str = json.dumps(detail) if detail is not None else None
        conn.execute(
            "INSERT INTO events "
            "(ts, session_id, tool_use_id, category, rule, action, command, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                _session_id,
                _tool_use_id,
                category,
                rule,
                action,
                _redact_secrets(command),
                detail_str,
            ),
        )
        conn.commit()
    except (sqlite3.Error, OSError):
        pass


def _hook_output(decision: str, reason: str) -> str:
    """Build hookSpecificOutput JSON string for PreToolUse decisions."""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": _HOOK_EVENT_NAME,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
    )


def _exit_with_decision(
    guidance: str,
    action: str,
    *,
    rule_name: str | None = None,
    matched_segment: str | None = None,
) -> None:
    """Exit with guidance, using the correct mechanism for the action type.

    action is one of "allow", "ask", or "block":
      "allow": outputs hookSpecificOutput JSON with permissionDecision "allow", exits 0.
      "ask":   checks trust first, then outputs hookSpecificOutput JSON with
               permissionDecision "ask", exits 0 (prompts user for confirmation).
      "block": prints guidance to stderr, exits 2.
    """
    if action == "allow":
        # Allow: log and output allow decision
        _log_event("guard", "allowed", rule=rule_name, command=matched_segment)
        print(_hook_output("allow", guidance))
        sys.exit(0)
    elif action == "ask":
        # Ask: check trust first
        if rule_name and _check_trust(rule_name, matched_segment, _session_id):
            _log_event("guard", "trusted", rule=rule_name, command=matched_segment)
            reason = f"[trusted] [{rule_name}] {guidance}" if rule_name else f"[trusted] {guidance}"
            print(_hook_output("allow", reason))
            sys.exit(0)

        # Build enhanced reason with trust hint
        parts = []
        if rule_name:
            parts.append(f"[{rule_name}] {guidance}")
        else:
            parts.append(guidance)
        if matched_segment:
            parts.append(f"Matched: {matched_segment}")
        if rule_name:
            sid_hint = f" --session-id {_session_id}" if _session_id else ""
            parts.append(
                f"To trust: /dev-guard trust add {rule_name}"
                f" [--match <pattern>]"
                f" [--scope session{sid_hint}|--scope always]"
            )
        enhanced_reason = "\n".join(parts)

        _log_event("guard", "ask", rule=rule_name, command=matched_segment)
        print(_hook_output("ask", enhanced_reason))
        sys.exit(0)
    else:
        # Block
        msg = f"[{rule_name}] {guidance}" if rule_name else guidance
        _log_event("guard", "blocked", rule=rule_name, command=matched_segment)
        print(msg, file=sys.stderr)
        sys.exit(2)


# ── Category F: Authenticated URL fetch guard ──
# Each rule: (name, url_pattern, guidance_message)
# url_pattern: regex matched against the full URL
AUTH_URL_RULES: list[URLRule] = [
    # GitHub
    URLRule(
        "github-api",
        re.compile(r"api\.github\.com"),
        "This URL targets the GitHub API which requires authentication. "
        "Use the `gh` CLI instead. Example: `gh api repos/OWNER/REPO`.",
    ),
    URLRule(
        "github-auth-content",
        re.compile(r"github\.com/[^/]+/[^/]+/(settings|pulls|issues|actions|security)"),
        "This GitHub URL requires authentication to return useful content. "
        "Use the `gh` CLI instead. Example: `gh pr list`, `gh issue list`, `gh run list`.",
    ),
    # GitLab (public)
    URLRule(
        "gitlab-api",
        re.compile(r"gitlab\.com/api/"),
        "This URL targets the GitLab API which requires authentication. "
        "Use `glab api` instead. Example: `glab api projects/:id/issues`.",
    ),
    URLRule(
        "gitlab-raw",
        re.compile(r"gitlab\.com/.+/(-/raw/|-/blob/)"),
        "This GitLab URL points to raw/blob content which may require authentication. "
        "Use `glab` CLI or clone the repo locally.",
    ),
    # Google
    URLRule(
        "google-docs",
        re.compile(r"docs\.google\.com/document/"),
        "Google Docs requires authentication. "
        "Use a Google MCP tool or `gcloud` CLI to access document content.",
    ),
    URLRule(
        "google-drive",
        re.compile(r"drive\.google\.com/(file|drive)/"),
        "Google Drive requires authentication. "
        "Use a Google MCP tool or `gcloud` CLI to access files.",
    ),
    URLRule(
        "google-sheets",
        re.compile(r"sheets\.google\.com/"),
        "Google Sheets requires authentication. "
        "Use a Google MCP tool or `gcloud` CLI to access spreadsheet data.",
    ),
    # Atlassian / Jira
    URLRule(
        "atlassian-api",
        re.compile(r"[a-z0-9-]+\.atlassian\.net/(rest/api|wiki)/"),
        "This Atlassian URL requires authentication. Use the Atlassian MCP tools instead.",
    ),
    URLRule(
        "jira-server",
        re.compile(r"jira\.[a-z0-9-]+\.(com|org|net)/"),
        "This Jira server URL requires authentication. "
        "Use the `jira` CLI or Atlassian MCP tools instead.",
    ),
    # Slack
    URLRule(
        "slack-api",
        re.compile(r"(api|hooks)\.slack\.com/"),
        "This Slack URL requires authentication. "
        "Use the Slack MCP tools or access Slack via Playwright MCP.",
    ),
]


def _load_extra_rules(
    env_var: str,
    entry_to_rule: Callable[[dict], CommandRule | URLRule],
) -> list:
    """Load extra rules from a JSON file specified by env_var.

    The env var should point to a JSON file containing an array of rule
    objects. Each object is converted using entry_to_rule.
    Returns an empty list on missing env var or any parse error.
    """
    rules_path = os.environ.get(env_var)
    if not rules_path:
        return []
    try:
        with open(rules_path) as f:
            raw = json.load(f)
        return [entry_to_rule(entry) for entry in raw]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError, re.error):
        return []  # Fail silently — bad config should not break the guard


def _url_rule_from_entry(entry: dict) -> URLRule:
    """Convert a JSON dict to a URLRule."""
    return URLRule(
        entry["name"],
        re.compile(entry["pattern"]),
        entry["message"],
        entry.get("action", "block"),
    )


def _cmd_rule_from_entry(entry: dict) -> CommandRule:
    """Convert a JSON dict to a CommandRule."""
    exception = re.compile(entry["exception"]) if entry.get("exception") else None
    return CommandRule(
        entry["name"],
        re.compile(entry["pattern"]),
        exception,
        entry["message"],
        entry.get("action", "block"),
    )


def _log_url_event(
    url: str, rule_name: str | None, action: str, tool: str, phase: str = "pre", **extra: object
) -> None:
    """Log a URL guard event to the unified SQLite audit log."""
    detail = {"tool": tool, "phase": phase}
    if extra:
        detail.update(extra)
    _log_event("url", action, rule=rule_name, command=url, detail=detail)


def _extract_urls(text: str) -> list[str]:
    """Extract URLs from a string (command line or text)."""
    return re.findall(r"https?://[^\s\"'<>]+", text)


def _check_url_rules(url: str) -> tuple[str, str, str] | None:
    """Check a URL against AUTH_URL_RULES. Returns (name, guidance, action) or None."""
    for rule in AUTH_URL_RULES:
        if rule.pattern.search(url):
            return (rule.name, rule.guidance, rule.action)
    return None


def _check_fetch_command(cmd: str) -> bool:
    """Check curl/wget commands for authenticated URLs. Exits on block or ask match.

    Returns True if the command was a curl/wget (so caller knows to log),
    or False if not a fetch command.
    """
    normalized = strip_env_prefix(cmd)
    if not re.match(r"^\s*(curl|wget)\b", normalized):
        return False

    # ALLOW_FETCH=1 bypass — agent has considered alternatives
    if re.search(r"(?:^|\s)ALLOW_FETCH=1(?:\s|$)", cmd):
        urls = _extract_urls(cmd)
        for url in urls:
            _log_url_event(url, None, "bypassed", "Bash")
        return True

    urls = _extract_urls(cmd)
    for url in urls:
        result = _check_url_rules(url)
        if result:
            rule_name, guidance, action = result
            _log_url_event(url, rule_name, _LOG_ACTION_FOR.get(action, action), "Bash")
            full_guidance = (
                f"{guidance}\n"
                "If you've confirmed raw fetch is appropriate, "
                "prefix with `ALLOW_FETCH=1`."
            )
            _exit_with_decision(full_guidance, action, rule_name=rule_name, matched_segment=cmd)
        else:
            _log_url_event(url, None, "allowed", "Bash")
    return True


# Patterns indicating authentication failure in HTTP responses
_AUTH_FAIL_PATTERNS = [
    re.compile(r"HTTP/[\d.]+ 401\b"),
    re.compile(r"HTTP/[\d.]+ 403\b"),
    re.compile(r"HTTP/[\d.]+ 407\b"),
    re.compile(r"curl: \(22\).*40[1379]"),
    re.compile(r"Unauthorized", re.IGNORECASE),
    re.compile(r"Access Denied", re.IGNORECASE),
    re.compile(r"Login Required", re.IGNORECASE),
    re.compile(r"sign.?in", re.IGNORECASE),
    re.compile(r"SSO.*redirect", re.IGNORECASE),
]

_HTTP_STATUS_RE = re.compile(r"HTTP/[\d.]+ (\d{3})\b")


def _detect_auth_failure(text: str) -> tuple[bool, int | None]:
    """Check response text for auth failure indicators.

    Returns (auth_failed: bool, status_code: int | None).
    """
    if not text:
        return False, None

    status_match = _HTTP_STATUS_RE.search(text)
    status_code = int(status_match.group(1)) if status_match else None

    for pattern in _AUTH_FAIL_PATTERNS:
        if pattern.search(text):
            return True, status_code

    return False, status_code


def _check_response_for_auth_failure(text: str, url: str, tool_name: str) -> None:
    """Check tool response text for authentication failure indicators and log result."""
    auth_failed, status_code = _detect_auth_failure(text)
    _log_url_event(
        url,
        None,
        "auth_failed" if auth_failed else "success",
        tool_name,
        phase="post",
        response_code=status_code,
        auth_failed=auth_failed,
    )


def _handle_post_tool_use(data: dict) -> None:
    """Handle PostToolUse events: log response codes for fetch commands."""
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        normalized = strip_env_prefix(command)
        if not re.match(r"^\s*(curl|wget)\b", normalized):
            return
        urls = _extract_urls(command)
        response_text = ""
        if isinstance(tool_response, dict):
            response_text = str(tool_response.get("stdout", "")) + str(
                tool_response.get("stderr", "")
            )
        elif isinstance(tool_response, str):
            response_text = tool_response
        for url in urls:
            _check_response_for_auth_failure(response_text, url, "Bash")
    elif tool_name == "WebFetch":
        url = tool_input.get("url", "")
        if not url:
            return
        response_text = ""
        if isinstance(tool_response, dict):
            response_text = str(tool_response)
        elif isinstance(tool_response, str):
            response_text = tool_response
        _check_response_for_auth_failure(response_text, url, "WebFetch")


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


def strip_shell_keyword(cmd: str) -> str:
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


def strip_env_prefix(cmd: str) -> str:
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


def extract_bash_c(cmd: str) -> str | None:
    """Extract the inner command from `bash -c '...'` or `sh -c '...'`.

    Returns the inner command string, or None if not a bash -c invocation.

    Limitation: Uses simple quote matching that may truncate inner commands
    containing the same quote character (e.g., bash -c 'echo "it's fine"').
    When truncation occurs, the outer command is still checked as a whole,
    maintaining safety.
    """
    m = re.match(r"""^\s*(?:bash|sh)\s+-c\s+(['"])(.*?)\1\s*$""", cmd, re.DOTALL)
    if m:
        return m.group(2).strip()
    # Unquoted (rare but possible): bash -c command
    m = re.match(r"""^\s*(?:bash|sh)\s+-c\s+(\S+)""", cmd)
    if m:
        return m.group(1).strip()
    return None


def extract_subshells(cmd: str) -> list[str]:
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


def _split_respecting_quotes(
    text: str,
    is_delimiter: Callable[[str, int, list[str]], int | None],
) -> list[str]:
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


def _is_pipe_delimiter(text: str, i: int, _current: list[str]) -> int | None:
    """Pipe delimiter: | and |& (split_commands already handled ||)."""
    if text[i] == "|":
        return 2 if i + 1 < len(text) and text[i + 1] == "&" else 1
    return None


split_pipes = functools.partial(_split_respecting_quotes, is_delimiter=_is_pipe_delimiter)


# ── Git safety rules (consolidated from git-safety-check.sh) ──


def _has_force_flag(cmd: str) -> bool:
    """Check if command contains --force (not --force-with-lease) or -f bundled."""
    if re.search(r"(^|\s)--force(\s|=|$)", cmd):
        return True
    return bool(re.search(r"(^|\s)-[a-zA-Z]*f[a-zA-Z]*(\s|$)", cmd))


def _has_force_with_lease(cmd: str) -> bool:
    return bool(re.search(r"(^|\s)--force-with-lease(=[^\s]+)?(\s|$)", cmd))


def _get_push_target(cmd: str) -> tuple[str, str]:
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


def _next_positional(parts: list[str], start: int) -> str | None:
    """Return the first non-flag argument at or after *start*, or None."""
    i = start
    while i < len(parts):
        if parts[i] == "--" or parts[i].startswith("-"):
            i += 1
            continue
        return parts[i]
    return None


def _find_flag_branch(
    parts: list[str], start: int, flags: set[str], equals_prefix: str | None = None
) -> tuple[str, int] | None:
    """Scan *parts* from *start* for a branch-creation flag.

    *flags* is a set of short/long flags (e.g. {"-c", "--create"}).
    *equals_prefix* is an optional ``--flag=`` prefix (e.g. "--create=").

    Returns ``(branch_name, next_index)`` on success, or ``None`` if the
    flag is absent or malformed.  A bare positional before the flag means
    this is not a creation command — returns ``None``.
    """
    i = start
    while i < len(parts):
        arg = parts[i]
        if arg == "--":
            i += 1
            continue
        if equals_prefix and arg.startswith(equals_prefix):
            return arg.split("=", 1)[1], i + 1
        if arg in flags:
            if i + 1 < len(parts):
                return parts[i + 1], i + 2
            return None  # malformed: flag with no branch name
        if arg.startswith("-"):
            i += 1
            continue
        return None  # positional before flag → not a creation command
    return None


def _parse_branch_creation(cmd: str) -> tuple[str, str | None] | None:
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
        result = _find_flag_branch(parts, 2, {"-c", "--create"}, "--create=")
        if result is None:
            return None
        branch_name, i = result
        return (branch_name, _next_positional(parts, i))

    if subcmd == "checkout":
        result = _find_flag_branch(parts, 2, {"-b", "-B"})
        if result is None:
            return None
        branch_name, i = result
        return (branch_name, _next_positional(parts, i))

    if subcmd == "worktree" and len(parts) > 2 and parts[2] == "add":
        # git worktree add <path> -b <name> [<start-point>]
        # Skip the path positional before looking for -b
        i = 3
        path_found = False
        while i < len(parts):
            arg = parts[i]
            if arg == "-b":
                if i + 1 < len(parts):
                    branch_name = parts[i + 1]
                    return (branch_name, _next_positional(parts, i + 2))
                return None  # malformed
            if arg.startswith("-"):
                i += 1
                continue
            if not path_found:
                path_found = True
                i += 1
                continue
            break  # second positional before -b: ambiguous
        return None

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


def _is_safe_start_point(ref: str) -> bool:
    """Check if a start-point ref is safe (upstream remote, HEAD variant, or SHA)."""
    if ref in _SAFE_REMOTE_REFS:
        return True
    if _HEAD_PATTERN.match(ref):
        return True
    return bool(_SHA_PATTERN.match(ref))


def _is_branch_no_base(cmd: str) -> bool:
    """Check if cmd creates a branch without a start-point.

    Satisfies ``Callable[[str], bool]`` for use as ``GitRule.check_fn``.
    """
    parsed = _parse_branch_creation(cmd)
    return parsed is not None and parsed[1] is None


def _is_branch_from_local_main(cmd: str) -> bool:
    """Check if cmd creates a branch from local main/master.

    Satisfies ``Callable[[str], bool]`` for use as ``GitRule.check_fn``.
    """
    parsed = _parse_branch_creation(cmd)
    return parsed is not None and parsed[1] in _PROTECTED_BRANCHES


def _is_branch_from_non_upstream(cmd: str) -> bool:
    """Check if cmd creates a branch from a non-upstream ref.

    Satisfies ``Callable[[str], bool]`` for use as ``GitRule.check_fn``.
    """
    parsed = _parse_branch_creation(cmd)
    return parsed is not None and parsed[1] is not None and not _is_safe_start_point(parsed[1])


# Each rule: (name, check_function, message)
# check_function(cmd) -> bool
GIT_DENY_RULES: list[GitRule] = [
    GitRule(
        "reset-hard",
        lambda cmd: bool(re.search(r"git\s+reset\s+--hard", cmd)),
        "git reset --hard is FORBIDDEN. "
        "Use 'git reset --mixed' or 'git stash' to preserve changes.",
    ),
    GitRule(
        "push-force",
        lambda cmd: bool(re.search(r"git\s+push", cmd)) and _has_force_flag(cmd),
        "Force push (--force/-f) is FORBIDDEN. Use --force-with-lease for safer force pushing.",
    ),
    GitRule(
        "push-upstream",
        lambda cmd: bool(re.search(r"git\s+push", cmd)) and _get_push_target(cmd)[0] == "upstream",
        "Pushing to upstream is FORBIDDEN. Push to origin and create a PR instead.",
    ),
    GitRule(
        "fwl-main",
        lambda cmd: (
            bool(re.search(r"git\s+push", cmd))
            and _has_force_with_lease(cmd)
            and _get_push_target(cmd)[1] in _PROTECTED_BRANCHES
        ),
        "--force-with-lease to main/master is FORBIDDEN. Use feature branches for rebasing.",
    ),
    GitRule(
        "branch-D",
        lambda cmd: (
            bool(re.search(r"git\s+branch", cmd))
            and bool(re.search(r"(^|\s)-[a-zA-Z]*D[a-zA-Z]*(\s|$)", cmd))
        ),
        "git branch -D is FORBIDDEN. Use 'git branch -d' for safe deletion of merged branches.",
    ),
    GitRule(
        "branch-force",
        lambda cmd: bool(re.search(r"git\s+branch.*--force", cmd)),
        "git branch --force is FORBIDDEN. Force operations on branches must be done manually.",
    ),
    GitRule(
        "push-origin-main",
        lambda cmd: bool(re.search(r"git\s+push.*origin\s+(main|master)(\s|$)", cmd)),
        "Pushing directly to origin/main or origin/master is FORBIDDEN. "
        "Use feature branches and PRs.",
    ),
    GitRule(
        "no-verify",
        lambda cmd: bool(re.search(r"git\s+", cmd)) and "--no-verify" in cmd,
        "--no-verify flag is FORBIDDEN. Git hooks must run for all commits and pushes.",
    ),
    GitRule(
        "add-force",
        lambda cmd: bool(re.search(r"git\s+add", cmd)) and _has_force_flag(cmd),
        "git add --force is FORBIDDEN. Files are gitignored for a reason.",
    ),
    GitRule(
        "rm-cached-force",
        lambda cmd: (
            bool(re.search(r"git\s+rm", cmd))
            and "--cached" in cmd
            and (_has_force_flag(cmd) or "--force" in cmd)
        ),
        "git rm --cached --force is FORBIDDEN. Use 'git rm --cached' without --force.",
    ),
    GitRule(
        "rm-unsafe",
        lambda cmd: bool(re.search(r"git\s+rm", cmd)) and "--cached" not in cmd,
        "git rm is FORBIDDEN (deletes files). Use 'git rm --cached' to unstage only.",
    ),
    GitRule(
        "clean-ignored",
        lambda cmd: (
            bool(re.search(r"git\s+clean", cmd)) and bool(re.search(r"-[a-zA-Z]*[xX]", cmd))
        ),
        "git clean with -x or -X is FORBIDDEN. These delete ignored/untracked files irreversibly.",
    ),
    GitRule(
        "branch-no-base",
        _is_branch_no_base,
        "Branch creation without a start-point defaults to HEAD (which may be stale "
        "or another feature branch). Specify a base: "
        "git switch -c <name> upstream/main",
    ),
]

# ASK rules prompt user for confirmation (permissionDecision "ask")
GIT_ASK_RULES: list[GitRule] = [
    GitRule(
        "config-global-write",
        lambda cmd: (
            bool(re.search(r"git\s+config\s+--global", cmd))
            and not re.search(r"(--get|--list)(\s|$)", cmd)
            and not re.search(r"\s-l(\s|$)", cmd)
        ),
        "git config --global modifications require permission. "
        "Read operations (--get, --list) are allowed.",
    ),
    GitRule(
        "stash-drop",
        lambda cmd: bool(re.search(r"git\s+stash\s+drop", cmd)),
        "git stash drop permanently deletes a stash. Confirm this is intentional.",
    ),
    GitRule(
        "checkout-dash-dash",
        lambda cmd: bool(re.search(r"git\s+checkout\s+--", cmd)),
        "git checkout -- is destructive and deprecated. Consider using 'git restore' instead.",
    ),
    GitRule(
        "filter-branch",
        lambda cmd: bool(re.search(r"git\s+filter-branch", cmd)),
        "git filter-branch is dangerous and deprecated. Use git-filter-repo if truly needed.",
    ),
    GitRule(
        "reflog-delete-expire",
        lambda cmd: bool(re.search(r"git\s+reflog\s+(delete|expire)", cmd)),
        "git reflog delete/expire removes recovery points. Confirm this is intentional.",
    ),
    GitRule(
        "remote-remove",
        lambda cmd: bool(re.search(r"git\s+remote\s+(remove|rm)", cmd)),
        "Removing a git remote may break workflows. Confirm this is intentional.",
    ),
    GitRule(
        "branch-from-local-main",
        _is_branch_from_local_main,
        "Local main may be stale. Prefer upstream/main or run git fetch upstream main first.",
    ),
    GitRule(
        "branch-from-non-upstream",
        _is_branch_from_non_upstream,
        "Branching from a non-upstream ref risks branch stacking. Use upstream/main instead.",
    ),
]


def check_git_safety(cmd: str, fetch_seen: bool = False) -> None:
    """Check a command against git safety rules. Exits on block or ask match."""
    # Early exit: not a git command
    if not re.search(r"(^|\s)git\s", cmd):
        return

    # DENY rules (block)
    for name, check_fn, message in GIT_DENY_RULES:
        if check_fn(cmd):
            _exit_with_decision(message, "block", rule_name=name, matched_segment=cmd)

    # Special case: commit to main/master (requires git rev-parse)
    if re.search(r"^\s*git\s+commit", cmd):
        try:
            # _GUARD_TEST_BRANCH is only honoured during test runs
            _test_branch = None
            if os.environ.get("PYTEST_CURRENT_TEST"):
                _test_branch = os.environ.get("_GUARD_TEST_BRANCH")
            branch = (
                _test_branch
                or subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
            )
            if branch in _PROTECTED_BRANCHES:
                msg = (
                    f"Committing directly to {branch} is FORBIDDEN. "
                    "Create a feature branch: git switch -c feature/name"
                )
                _exit_with_decision(msg, "block", rule_name="commit-to-main", matched_segment=cmd)
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
        _exit_with_decision(
            "No git fetch detected in this command chain. "
            "Fetch first: git fetch upstream main && "
            "git switch -c <name> upstream/main",
            "ask",
            rule_name="branch-needs-fetch",
            matched_segment=cmd,
        )

    # ASK rules — prompt user for confirmation
    for name, check_fn, message in GIT_ASK_RULES:
        if check_fn(cmd):
            _exit_with_decision(message, "ask", rule_name=name, matched_segment=cmd)


def _is_command_delimiter(text: str, i: int, current: list[str]) -> int | None:
    """Command delimiter: &&, ||, ;, newline (with backslash continuation)."""
    two = text[i : i + 2]
    if two in ("&&", "||"):
        return 2
    c = text[i]
    if c == ";":
        return 1
    if c == "\n":
        if current and current[-1] == "\\":
            current[-1] = " "
        return 1
    return None


split_commands = functools.partial(_split_respecting_quotes, is_delimiter=_is_command_delimiter)


def _guard_tmp_path(tool_name: str, tool_input: dict) -> None:
    """Block write-oriented tools targeting /tmp/ paths, or return."""
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
        _exit_with_decision(
            "Use `hack/tmp/` (gitignored) instead of `/tmp/` for temporary files. "
            "Native tools (Read/Write/Edit) work on local files without extra permissions.",
            "block",
            rule_name="tmp-path-write",
            matched_segment=file_path,
        )


def _guard_plan_mode(tool_name: str) -> None:
    """Block EnterPlanMode, redirecting to incremental-planning skill."""
    if tool_name == "EnterPlanMode":
        _exit_with_decision(
            "Native plan mode writes and displays the full plan at once.\n"
            "Use the incremental-planning skill instead:\n"
            "  Invoke Skill tool with 'dev-essentials:incremental-planning'\n\n"
            "This skill asks clarifying questions first, writes the plan\n"
            "incrementally to a file, and provides research context in chat\n"
            "for informed feedback.",
            "block",
            rule_name="plan-mode-blocked",
            matched_segment="EnterPlanMode",
        )


_FETCH_PATTERN = re.compile(r"git\s+fetch\s+(upstream|origin)\b")


def _check_rules(cmd: str, fetch_seen: bool, skip_rules: frozenset[str] | None = None) -> None:
    """Check a command against all rules. Exits on match.

    Block rules: stderr + exit 2.
    Ask rules: hookSpecificOutput JSON + exit 0 (prompts user).

    skip_rules: frozenset of rule names to skip (used for pipe segments
    where native-tool alternatives don't apply to piped output).
    """
    cmd = strip_shell_keyword(cmd)
    check_git_safety(cmd, fetch_seen=fetch_seen)
    normalized = strip_env_prefix(cmd)
    for rule in RULES:
        if skip_rules and rule.name in skip_rules:
            continue
        target = normalized if rule.pattern.pattern.startswith("^") else cmd
        if rule.pattern.search(target):
            if rule.exception and rule.exception.search(cmd):
                continue
            _exit_with_decision(
                rule.guidance, rule.action, rule_name=rule.name, matched_segment=cmd
            )


def _check_pipes(
    cmd: str, fetch_seen: bool, skip_rules: frozenset[str] | None = _PIPE_SEGMENT_SKIP
) -> None:
    """Check later pipe segments of a command for guarded patterns.

    NOTE: _check_oc_introspection is NOT called on individual pipe segments.
    oc introspection runs on full subcommands in _check_subcmd. Pipe segments
    like "cat file | oc apply -f -" are checked by _check_rules only.
    """
    pipe_segments = split_pipes(cmd)
    if len(pipe_segments) > 1:
        for segment in pipe_segments[1:]:
            _check_rules(segment, fetch_seen, skip_rules=skip_rules)


def _check_subcmd(subcmd: str, fetch_seen: bool) -> bool:
    """Analyze one subcommand: unwrap bash -c, check pipes, check subshells.

    Returns updated fetch_seen.
    """
    if _FETCH_PATTERN.search(subcmd):
        fetch_seen = True

    inner_cmd = extract_bash_c(subcmd)
    if inner_cmd:
        for inner_sub in split_commands(inner_cmd):
            if _FETCH_PATTERN.search(inner_sub):
                fetch_seen = True
            _check_rules(inner_sub, fetch_seen)
        # bash -c wrapper itself causes a permission prompt — block it
        print(
            f"Run the command directly without the `bash -c` wrapper — "
            f"it causes a permission prompt. Just use: `{inner_cmd}`",
            file=sys.stderr,
        )
        sys.exit(2)

    # Process substitution <(...) triggers Claude Code's built-in shell-operator
    # detector ("false positive").  Block early with actionable guidance so the
    # user never sees the cryptic built-in message.
    if re.search(r"<\(", subcmd):
        _exit_with_decision(
            "Process substitution `<(...)` triggers a Claude Code permission prompt. "
            "Run each command separately and diff the output files instead:\n"
            "  cmd1 > /tmp/a.txt && cmd2 > /tmp/b.txt && diff /tmp/a.txt /tmp/b.txt",
            "block",
            rule_name="process-substitution",
            matched_segment=subcmd[:200],
        )

    # Multiline `python -c` triggers Claude Code's "empty quotes before dash"
    # heuristic when the inline code contains flag-like strings (e.g. --scope).
    # Block and redirect to a temp-file workflow.
    if re.match(r"^\s*(?:uv\s+run\s+)?python[3]?\s+-c\s+", subcmd) and "\n" in subcmd:
        _exit_with_decision(
            "Multiline `python -c` triggers a Claude Code permission prompt "
            "(inline flags hit the built-in argument validator). "
            "Write the code to a file and run it with `uv run python3 <file>` instead.",
            "block",
            rule_name="multiline-python-c",
            matched_segment=subcmd[:200],
        )

    # Check pipe segments and subshells FIRST — deny/ask on dangerous segments
    # must happen before any "allow" rule on the full command can short-circuit.
    _check_pipes(subcmd, fetch_seen)

    for inner in extract_subshells(subcmd):
        if _FETCH_PATTERN.search(inner):
            fetch_seen = True
        _check_rules(inner, fetch_seen)
        _check_pipes(inner, fetch_seen)

    # Now check the full subcommand (including allow rules that exit 0)
    _check_rules(subcmd, fetch_seen)

    # oc/kubectl introspection — after user-defined rules (which take priority)
    normalized = strip_env_prefix(strip_shell_keyword(subcmd))
    if re.match(r"^\s*(oc|kubectl)\b", normalized):
        _check_oc_introspection(subcmd)

    return fetch_seen


# ── Trust management ──


def _check_trust(rule_name: str, command: str | None, session_id: str | None) -> bool:
    """Check if a rule is trusted. Returns True if trusted, False otherwise."""
    try:
        conn = _init_db()
        if conn is None:
            return False
        cursor = conn.execute(
            "SELECT match_pattern, scope, session_id FROM trusted_rules WHERE rule_name = ?",
            (rule_name,),
        )
        for match_pattern, scope, trust_session_id in cursor.fetchall():
            # Session-scoped: check session matches
            if scope == "session" and trust_session_id != session_id:
                continue
            # Match pattern: case-insensitive substring check
            if match_pattern and command and match_pattern.lower() not in command.lower():
                continue
            return True
        return False
    except (sqlite3.Error, OSError):
        return False


def _add_trust(
    rule_name: str, match_pattern: str | None, scope: str, session_id: str | None
) -> tuple[bool, str]:
    """Add a trust rule. Returns (success: bool, message: str)."""
    try:
        conn = _init_db()
        if conn is None:
            return False, "Failed to open database"
        conn.execute(
            "INSERT OR REPLACE INTO trusted_rules "
            "(rule_name, match_pattern, scope, session_id, created_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                rule_name,
                match_pattern,
                scope,
                session_id if scope == "session" else None,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        desc = f"rule={rule_name!r}"
        if match_pattern:
            desc += f" match={match_pattern!r}"
        desc += f" scope={scope}"
        return True, f"Trusted: {desc}"
    except (sqlite3.Error, OSError) as e:
        return False, f"Failed to add trust: {e}"


def _remove_trust(rule_name: str, match_pattern: str | None = None) -> tuple[bool, int]:
    """Remove trust rule(s). Returns (success: bool, count: int)."""
    try:
        conn = _init_db()
        if conn is None:
            return False, 0
        if match_pattern is not None:
            cursor = conn.execute(
                "DELETE FROM trusted_rules WHERE rule_name = ? AND COALESCE(match_pattern, '') = ?",
                (rule_name, match_pattern),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM trusted_rules WHERE rule_name = ?",
                (rule_name,),
            )
        conn.commit()
        return True, cursor.rowcount
    except (sqlite3.Error, OSError):
        return False, 0


def _list_trust() -> list[dict[str, str]]:
    """List all trust rules. Returns list of dicts."""
    try:
        conn = _init_db()
        if conn is None:
            return []
        cursor = conn.execute(
            "SELECT rule_name, match_pattern, scope, session_id, created_ts "
            "FROM trusted_rules ORDER BY created_ts"
        )
        return [
            {
                "rule_name": row[0],
                "match_pattern": row[1],
                "scope": row[2],
                "session_id": row[3],
                "created_ts": row[4],
            }
            for row in cursor.fetchall()
        ]
    except (sqlite3.Error, OSError):
        return []


# ── oc/kubectl introspection ──

_OC_RISK_LEVELS = {
    "critical": {
        "namespace",
        "project",
        "clusterrole",
        "clusterrolebinding",
        "node",
        "persistentvolume",
        "customresourcedefinition",
        "crd",
        "apiservice",
        "mutatingwebhookconfiguration",
        "validatingwebhookconfiguration",
    },
    "high": {
        "deployment",
        "statefulset",
        "daemonset",
        "replicaset",
        "service",
        "ingress",
        "route",
        "configmap",
        "secret",
        "serviceaccount",
        "role",
        "rolebinding",
        "networkpolicy",
        "persistentvolumeclaim",
        "pvc",
        "job",
        "cronjob",
    },
    "medium": {
        "pod",
        "replicationcontroller",
        "endpoints",
        "event",
        "horizontalpodautoscaler",
        "hpa",
        "poddisruptionbudget",
        "pdb",
        "limitrange",
        "resourcequota",
    },
    "low": {
        "build",
        "buildconfig",
        "imagestream",
        "imagestreamtag",
        "template",
        "catalog",
        "packagemanifest",
    },
}

_OC_MUTATING_VERBS = {
    "create",
    "apply",
    "delete",
    "patch",
    "replace",
    "set",
    "edit",
    "scale",
    "rollout",
    "expose",
    "label",
    "annotate",
    "taint",
    "adm",
    "policy",
}

_OC_EXEC_VERBS = {"exec", "rsh", "debug", "attach", "port-forward", "cp"}

_SECURITY_FIELDS = {
    "privileged",
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "hostPath",
    "runAsRoot",
    "allowPrivilegeEscalation",
    "capabilities",
    "securityContext",
    "serviceAccountName",
    "automountServiceAccountToken",
}


def _parse_oc_command(cmd: str) -> dict | None:
    """Parse an oc/kubectl command string into structured components.

    Returns dict with: tool, verb, resource_type, namespace, filename, flags.
    """
    parts = cmd.split()
    if not parts:
        return None

    # Find the tool (oc or kubectl)
    tool = None
    tool_idx = -1
    for i, p in enumerate(parts):
        if p in ("oc", "kubectl"):
            tool = p
            tool_idx = i
            break
    if tool is None:
        return None

    result = {
        "tool": tool,
        "verb": None,
        "resource_type": None,
        "namespace": None,
        "filename": None,
        "flags": [],
    }

    remaining = parts[tool_idx + 1 :]
    if not remaining:
        return result

    # Extract verb
    for part in remaining:
        if not part.startswith("-"):
            result["verb"] = part.lower()
            break

    # Extract flags, namespace, filename, resource type
    i = 0
    verb_seen = False
    while i < len(remaining):
        arg = remaining[i]
        if arg in ("-n", "--namespace") and i + 1 < len(remaining):
            result["namespace"] = remaining[i + 1]
            i += 2
            continue
        if arg.startswith("--namespace="):
            result["namespace"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg in ("-f", "--filename") and i + 1 < len(remaining):
            result["filename"] = remaining[i + 1]
            i += 2
            continue
        if arg.startswith("--filename="):
            result["filename"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg.startswith("-"):
            result["flags"].append(arg)
            i += 1
            continue
        if not verb_seen:
            verb_seen = True
            i += 1
            continue
        # First positional after verb is resource type
        if result["resource_type"] is None:
            result["resource_type"] = arg.lower().split("/")[0]
        i += 1

    return result


def _classify_oc_risk(parsed: dict | None) -> tuple[str, str | None]:
    """Classify the risk level of a parsed oc/kubectl command.

    Returns (risk_level, reason) where risk_level is one of:
    "critical", "high", "medium", "low", "safe".
    """
    if parsed is None or parsed["verb"] is None:
        return "safe", None

    verb = parsed["verb"]
    resource = parsed["resource_type"]
    flags = parsed["flags"]

    # Exec/debug commands are always high risk
    if verb in _OC_EXEC_VERBS:
        return "high", f"{verb} provides direct container access"

    # Read-only verbs are safe
    if verb in ("get", "describe", "logs", "status", "explain", "api-resources", "api-versions"):
        return "safe", None

    # Dry-run flag makes commands safe
    if any(f.startswith("--dry-run") for f in flags):
        return "safe", "dry-run mode"

    # Delete is always at least high
    if verb == "delete":
        if resource and resource in _OC_RISK_LEVELS.get("critical", set()):
            return "critical", f"deleting critical resource type: {resource}"
        return "high", f"deleting resource{f': {resource}' if resource else ''}"

    # Check resource risk level for mutating verbs
    if verb in _OC_MUTATING_VERBS and resource:
        for level in ("critical", "high", "medium", "low"):
            if resource in _OC_RISK_LEVELS.get(level, set()):
                return level, f"{verb} on {level}-risk resource: {resource}"

    # Mutating verb with no recognized resource — medium risk
    if verb in _OC_MUTATING_VERBS:
        return "medium", f"mutating verb: {verb}"

    return "safe", None


def _inspect_manifest(file_path: str) -> list[dict]:
    """Inspect a YAML/JSON manifest file. Returns list of resource info dicts.

    Bail on missing, oversized (>1MB), or binary files.
    """
    try:
        path = Path(file_path).resolve()
        # Restrict to cwd, home, or temp directory
        cwd = Path.cwd().resolve()
        home = Path.home().resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
        if not (path.is_relative_to(cwd) or path.is_relative_to(home) or path.is_relative_to(tmp)):
            return [{"error": "path outside allowed directories", "path": str(path)}]
        if not path.exists():
            return []
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            return [{"error": "file too large", "path": str(path)}]
        text = path.read_text(errors="replace")
        # Check for binary content
        if "\x00" in text[:1024]:
            return [{"error": "binary file", "path": str(path)}]
    except OSError:
        return []

    if file_path.endswith(".json"):
        return _parse_json_manifest(text)
    return _parse_yaml_manifests(text)


def _parse_json_manifest(text: str) -> list[dict]:
    """Parse a JSON manifest. Returns list of resource info dicts."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(obj, dict):
        # Could be a List kind
        if obj.get("kind") == "List" and isinstance(obj.get("items"), list):
            return [_extract_manifest_info(item) for item in obj["items"] if isinstance(item, dict)]
        return [_extract_manifest_info(obj)]
    if isinstance(obj, list):
        return [_extract_manifest_info(item) for item in obj if isinstance(item, dict)]
    return []


def _extract_manifest_info(obj: dict) -> dict:
    """Extract key fields from a manifest object."""
    info = {
        "kind": obj.get("kind", "Unknown"),
        "name": None,
        "namespace": None,
        "security_fields": [],
    }
    metadata = obj.get("metadata", {})
    if isinstance(metadata, dict):
        info["name"] = metadata.get("name")
        info["namespace"] = metadata.get("namespace")

    found = set()
    _collect_security_fields(obj, found, depth=0)
    info["security_fields"] = sorted(found)
    return info


def _collect_security_fields(obj: object, found: set[str], depth: int) -> None:
    """Recursively collect security-relevant field names from a manifest."""
    if depth > 10:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _SECURITY_FIELDS:
                found.add(key)
            _collect_security_fields(value, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_security_fields(item, found, depth + 1)


def _parse_yaml_manifests(text: str) -> list[dict]:
    """Parse YAML manifests using line-based regex parser.

    Handles multi-document YAML with --- separators.
    """
    docs = text.split("\n---")
    results = []
    for doc in docs:
        doc = doc.strip()
        if not doc or doc == "---":
            continue
        info = _parse_yaml_doc(doc)
        if info:
            results.append(info)
    return results


def _parse_yaml_doc(text: str) -> dict:
    """Parse a single YAML document using line-based regex.

    Extracts kind, metadata.name, metadata.namespace, and security fields.
    """
    info = {
        "kind": "Unknown",
        "name": None,
        "namespace": None,
        "security_fields": [],
    }
    found_security = set()
    in_metadata = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level fields (no indentation)
        indent = len(line) - len(line.lstrip())

        if indent == 0:
            in_metadata = False
            m = re.match(r"^kind:\s*(.+)", stripped)
            if m:
                info["kind"] = m.group(1).strip().strip("'\"")
                continue
            if stripped.startswith("metadata:"):
                in_metadata = True
                continue

        if in_metadata and indent > 0:
            m = re.match(r"^name:\s*(.+)", stripped)
            if m:
                info["name"] = m.group(1).strip().strip("'\"")
                continue
            m = re.match(r"^namespace:\s*(.+)", stripped)
            if m:
                info["namespace"] = m.group(1).strip().strip("'\"")
                continue

        # Strip inline comments before security scan
        content_part = stripped.split(" #")[0] if " #" in stripped else stripped
        # Flag YAML anchors/aliases as potentially hiding security fields
        if re.search(r"[&*]\w+", content_part):
            found_security.add("_yaml_anchor_alias")
        # Check for security fields anywhere
        for field in _SECURITY_FIELDS:
            if re.search(rf"\b{field}\b", content_part):
                found_security.add(field)

    info["security_fields"] = sorted(found_security)
    return info


def _inspect_pipe_source(cmd: str) -> str | None:
    """Extract filename from pipe source patterns like `cat file | ...` or `< file ...`."""
    # cat file | ...
    m = re.match(r"^\s*cat\s+([^\s|]+)\s*\|", cmd)
    if m:
        return m.group(1)
    # < file ...
    m = re.search(r"<\s*([^\s<>|]+)", cmd)
    if m:
        return m.group(1)
    return None


def _risk_order(level: str) -> int:
    """Return numeric order for risk levels (higher = more risky)."""
    return {"safe": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get(level, 0)


def _check_oc_introspection(cmd: str) -> None:
    """Orchestrate oc/kubectl command introspection.

    Returns None to let normal flow continue, or exits with ask/allow decision.
    """
    parsed = _parse_oc_command(cmd)
    if parsed is None:
        return None

    risk_level, reason = _classify_oc_risk(parsed)

    # Inspect manifest file if present
    manifest_info = []
    manifest_risk = "safe"
    manifest_reason = None

    file_path = parsed.get("filename")
    if not file_path:
        file_path = _inspect_pipe_source(cmd)

    if file_path:
        manifest_info = _inspect_manifest(file_path)
        # Determine manifest risk from resource kinds and security fields
        for info in manifest_info:
            kind = info.get("kind", "").lower()
            sec_fields = info.get("security_fields", [])

            if sec_fields and manifest_risk in ("safe", "low", "medium"):
                manifest_risk = "high"
                manifest_reason = f"manifest contains security fields: {', '.join(sec_fields)}"

            for level in ("critical", "high", "medium", "low"):
                if kind in _OC_RISK_LEVELS.get(level, set()):
                    if _risk_order(level) > _risk_order(manifest_risk):
                        manifest_risk = level
                        if not manifest_reason:
                            manifest_reason = f"manifest defines {level}-risk resource: {kind}"
                    break

    # Combine: highest risk wins
    if _risk_order(risk_level) >= _risk_order(manifest_risk):
        combined_risk = risk_level
    else:
        combined_risk = manifest_risk
    combined_reason = reason or manifest_reason

    # Dry-run: allow immediately
    if combined_risk == "safe":
        return None

    # Build detailed reason
    parts = [combined_reason or f"{combined_risk}-risk operation"]
    if manifest_info:
        resources = [
            f"{info.get('kind', '?')}/{info.get('name', '?')}"
            for info in manifest_info
            if not info.get("error")
        ]
        if resources:
            parts.append(f"Resources: {', '.join(resources[:5])}")
        errors = [info["error"] for info in manifest_info if info.get("error")]
        if errors:
            parts.append(f"Warnings: {', '.join(errors)}")

    detail = "; ".join(parts)

    if combined_risk in ("critical", "high", "medium"):
        _exit_with_decision(
            f"oc/kubectl {combined_risk}-risk: {detail}",
            "ask",
            rule_name=f"oc-{combined_risk}",
            matched_segment=cmd,
        )

    # low risk: allow (return None)
    return None


# ── Trust CLI handler ──


def _get_askable_rule_names() -> set[str]:
    """Return set of rule names that are ask-type (eligible for trust)."""
    names = set()
    # Built-in git ask rules
    names.update(rule.name for rule in GIT_ASK_RULES)
    # User-defined rules with action=ask
    for rule in RULES:
        if rule.action == "ask":
            names.add(rule.name)
    # User-defined URL rules with action=ask
    for rule in AUTH_URL_RULES:
        if rule.action == "ask":
            names.add(rule.name)
    # oc introspection rules (dynamic)
    names.update(["oc-critical", "oc-high", "oc-medium"])
    # Branch-needs-fetch (dynamic git ask)
    names.add("branch-needs-fetch")
    return names


def _handle_trust_command(argv: list[str]) -> int:
    """Handle --trust CLI commands. Returns exit code."""
    import argparse

    trust_idx = argv.index("--trust")
    args = argv[trust_idx + 1 :]

    parser = argparse.ArgumentParser(prog="dev-guard --trust", exit_on_error=False)
    subs = parser.add_subparsers(dest="action")

    subs.add_parser("list")

    add_p = subs.add_parser("add")
    add_p.add_argument("rule_name")
    add_p.add_argument("--match", dest="match_pattern", default=None)
    add_p.add_argument("--scope", choices=["session", "always"], default="always")
    add_p.add_argument("--session", dest="scope", action="store_const", const="session")
    add_p.add_argument("--always", dest="scope", action="store_const", const="always")
    add_p.add_argument("--session-id", dest="session_id_override", default=None)

    remove_p = subs.add_parser("remove")
    remove_p.add_argument("rule_name")
    remove_p.add_argument("--match", dest="match_pattern", default=None)

    try:
        ns = parser.parse_args(args)
    except (SystemExit, argparse.ArgumentError) as e:
        msg = str(e)
        if "invalid choice" in msg and "action" in msg:
            bad = args[0] if args else "?"
            print(
                f"Unknown trust action: {bad!r}. Use add, remove, or list.",
                file=sys.stderr,
            )
        else:
            print(f"Usage error: {e}", file=sys.stderr)
        return 2

    if hasattr(ns, "match_pattern") and ns.match_pattern and len(ns.match_pattern) > 500:
        print("--match pattern too long (max 500 chars).", file=sys.stderr)
        return 2

    if not ns.action:
        print(
            "Usage: --trust add|remove|list [rule_name] "
            "[--match <pattern>] [--scope session|always]",
            file=sys.stderr,
        )
        return 2

    if ns.action == "list":
        rules = _list_trust()
        if not rules:
            print("No trusted rules configured.")
            return 0
        print(f"{'Rule':<30} {'Pattern':<20} {'Scope':<10} {'Created'}")
        print("-" * 80)
        for r in rules:
            pat = r["match_pattern"] or "(any)"
            print(f"{r['rule_name']:<30} {pat:<20} {r['scope']:<10} {r['created_ts']}")
        return 0

    elif ns.action == "add":
        askable = _get_askable_rule_names()
        if ns.rule_name not in askable:
            print(
                f"Rule {ns.rule_name!r} is not a known ask-type rule. "
                f"Trustable rules: {', '.join(sorted(askable))}",
                file=sys.stderr,
            )
            return 2

        sid = None
        if ns.scope == "session":
            sid = ns.session_id_override
            if not sid:
                # Fallback: read from DB (works in single-session case)
                try:
                    conn = _init_db()
                    if conn:
                        cursor = conn.execute(
                            "SELECT value FROM session_state WHERE key = ?",
                            (_SESSION_ID_KEY,),
                        )
                        row = cursor.fetchone()
                        if row:
                            sid = row[0]
                except (sqlite3.Error, OSError):
                    pass
            if not sid:
                print(
                    "No session ID found. Run a guard check first, "
                    "provide --session-id, or use --scope always.",
                    file=sys.stderr,
                )
                return 2

        ok, msg = _add_trust(ns.rule_name, ns.match_pattern, ns.scope, sid)
        print(msg, file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 2

    elif ns.action == "remove":
        ok, count = _remove_trust(ns.rule_name, ns.match_pattern)
        if ok:
            print(f"Removed {count} trust rule(s) for {ns.rule_name!r}.")
        else:
            print(f"Failed to remove trust rules for {ns.rule_name!r}.", file=sys.stderr)
        return 0 if ok else 2

    return 2


def _validate_rules_file(path: str, env_var: str, is_url: bool = False) -> tuple[list[str], int]:
    """Validate a rules JSON file. Returns (issues, count) tuple."""
    issues = []
    if not os.path.exists(path):
        issues.append(f"{env_var}: file not found: {path}")
        return issues, 0
    try:
        with open(path) as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        issues.append(f"{env_var}: invalid JSON: {e}")
        return issues, 0
    except OSError as e:
        issues.append(f"{env_var}: cannot read file: {e}")
        return issues, 0
    if not isinstance(raw, list):
        issues.append(f"{env_var}: expected JSON array, got {type(raw).__name__}")
        return issues, 0
    if not raw:
        issues.append(f"{env_var}: file contains empty array (no rules)")
        return issues, 0

    required = {"name", "pattern", "message"}
    valid_actions = {"block", "ask", "allow"}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            issues.append(f"{env_var}[{i}]: expected object, got {type(entry).__name__}")
            continue
        name = entry.get("name", f"entry {i}")
        pfx = f"{env_var}[{i}] ({name!r})"
        for field in required:
            if field not in entry:
                issues.append(f"{pfx}: missing required field '{field}'")
            elif not isinstance(entry[field], str):
                issues.append(
                    f"{pfx}: '{field}' must be a string, got {type(entry[field]).__name__}"
                )
        if "pattern" in entry and isinstance(entry["pattern"], str):
            if not entry["pattern"]:
                issues.append(f"{pfx}: 'pattern' is empty (will match ALL commands/URLs)")
            else:
                try:
                    re.compile(entry["pattern"])
                except re.error as e:
                    issues.append(f"{pfx}: invalid regex in 'pattern': {e}")
        if not is_url and "exception" in entry:
            exc = entry["exception"]
            if exc is not None and not isinstance(exc, str):
                issues.append(
                    f"{pfx}: 'exception' must be a string or null, got {type(exc).__name__}"
                )
            elif isinstance(exc, str):
                if not exc:
                    issues.append(
                        f"{pfx}: 'exception' is empty (matches everything, disabling this rule)"
                    )
                else:
                    try:
                        re.compile(exc)
                    except re.error as e:
                        issues.append(f"{pfx}: invalid regex in 'exception': {e}")
        if "action" in entry:
            action = entry["action"]
            if not isinstance(action, str):
                issues.append(f"{pfx}: 'action' must be a string, got {type(action).__name__}")
            elif action not in valid_actions:
                issues.append(f"{pfx}: 'action' must be 'block', 'ask', or 'allow', got {action!r}")
    return issues, len(raw)


def _validate_config() -> int:
    """Validate extra rules config files.

    Output channels follow hook conventions:
      - Success (exit 0): stdout (shown in transcript)
      - Failure (exit 2): stderr (fed back to Claude)
    """
    url_path = os.environ.get("URL_GUARD_EXTRA_RULES")
    cmd_path = os.environ.get("COMMAND_GUARD_EXTRA_RULES")
    if not url_path and not cmd_path:
        return 0  # Nothing configured, nothing to validate

    all_issues = []
    loaded = []
    if url_path:
        issues, count = _validate_rules_file(url_path, "URL_GUARD_EXTRA_RULES", is_url=True)
        if issues:
            all_issues.extend(issues)
        else:
            loaded.append(f"URL rules: {count} rule(s) from {url_path}")
    if cmd_path:
        issues, count = _validate_rules_file(cmd_path, "COMMAND_GUARD_EXTRA_RULES", is_url=False)
        if issues:
            all_issues.extend(issues)
        else:
            loaded.append(f"Command rules: {count} rule(s) from {cmd_path}")

    if all_issues:
        # stderr + exit 2: fed back to Claude for explanation
        print("Custom guard rules — validation failed:", file=sys.stderr)
        for issue in all_issues:
            print(f"  ✗ {issue}", file=sys.stderr)
        if loaded:
            for msg in loaded:
                print(f"  ✓ {msg}", file=sys.stderr)
        return 2
    # stdout + exit 0: shown in transcript
    for msg in loaded:
        print(f"Custom guard rules — {msg}")
    return 0


def _parse_hook_input() -> dict:
    """Read and parse JSON hook input from stdin. Exits 0 on error (fail-open)."""
    try:
        raw_input = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw_input) > _MAX_INPUT_BYTES:
            sys.exit(0)  # Fail open — oversized input
        return json.loads(raw_input)
    except (json.JSONDecodeError, EOFError, ValueError):
        print("Hook received malformed/empty JSON input — failing open", file=sys.stderr)
        sys.exit(0)


def _handle_webfetch(tool_input: dict) -> None:
    """Check WebFetch URLs against auth rules. Exits after handling."""
    url = tool_input.get("url", "")
    if url:
        result = _check_url_rules(url)
        if result:
            rule_name, guidance, action = result
            _log_url_event(url, rule_name, _LOG_ACTION_FOR.get(action, action), "WebFetch")
            _exit_with_decision(guidance, action, rule_name=rule_name, matched_segment=url)
        else:
            _log_url_event(url, None, "allowed", "WebFetch")
    sys.exit(0)


def _handle_bash_command(command: str) -> None:
    """Process a Bash command through all guard checks."""
    if not command:
        sys.exit(0)

    if len(command) > _MAX_COMMAND_LEN:
        _log_event("guard", "oversized", command=command[:500])
        print("Command too large for guard analysis.", file=sys.stderr)
        sys.exit(2)

    # Andon cord: GUARD_BYPASS=1 prefix skips tool-selection and ask rules,
    # but still enforces hard deny rules (git safety) and URL auth guards.
    if command.startswith("GUARD_BYPASS=1 "):
        _log_event("bypass", "bypassed", command=command)
        real_cmd = command[len("GUARD_BYPASS=1 ") :]
        _check_fetch_command(real_cmd)
        subcmds = split_commands(real_cmd)
        for subcmd in subcmds:
            stripped = strip_env_prefix(strip_shell_keyword(subcmd))
            for name, check_fn, message in GIT_DENY_RULES:
                if check_fn(stripped):
                    _exit_with_decision(message, "block", rule_name=name, matched_segment=stripped)
        sys.exit(0)

    # Check curl/wget commands for authenticated URLs (before rule checks)
    _check_fetch_command(command)

    subcmds = split_commands(command)
    fetch_seen = False
    for subcmd in subcmds:
        fetch_seen = _check_subcmd(subcmd, fetch_seen)

    # Advisory (non-blocking): suggest Makefile targets for multi-step commands
    if len(subcmds) > 1 and os.path.exists("Makefile"):
        print(
            "TIP: A Makefile exists in this directory. "
            "Check if there's a `make` target before running raw commands."
        )

    # Pass-through logging when log level is "all"
    if _GUARD_LOG_LEVEL == "all":
        _log_event("guard", "allowed", command=command)

    sys.exit(0)


def main() -> None:
    global _session_id, _tool_use_id

    # Load user-defined extra rules (deferred from module level to avoid
    # side effects during import and to keep rule loading in one place)
    AUTH_URL_RULES.extend(_load_extra_rules("URL_GUARD_EXTRA_RULES", _url_rule_from_entry))
    RULES.extend(_load_extra_rules("COMMAND_GUARD_EXTRA_RULES", _cmd_rule_from_entry))

    if "--trust" in sys.argv:
        sys.exit(_handle_trust_command(sys.argv))

    if "--validate" in sys.argv:
        sys.exit(_validate_config())

    data = _parse_hook_input()

    # Extract session/tool context for logging
    _session_id = data.get("session_id")
    _tool_use_id = data.get("tool_use_id")
    if _session_id:
        try:
            conn = _init_db()
            if conn:
                conn.execute(
                    "INSERT OR REPLACE INTO session_state (key, value, updated_ts) "
                    "VALUES (?, ?, ?)",
                    (
                        _SESSION_ID_KEY,
                        _session_id,
                        datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
        except (sqlite3.Error, OSError):
            pass

    # PostToolUse: observational logging only (no blocking)
    hook_event = data.get("hook_event_name", _HOOK_EVENT_NAME)
    if hook_event == "PostToolUse":
        _handle_post_tool_use(data)
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    _guard_tmp_path(tool_name, tool_input)
    _guard_plan_mode(tool_name)

    if tool_name == "WebFetch":
        _handle_webfetch(tool_input)

    if tool_name != "Bash":
        sys.exit(0)

    command = tool_input.get("command", "").strip()
    _handle_bash_command(command)


if __name__ == "__main__":
    main()
