#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""CMUX Integration Hook -- bridges Claude Code events to cmux CLI.

Routes hook events (session lifecycle, notifications, agent completion)
to the cmux terminal multiplexer for desktop notifications.

NOTE: Sidebar commands (set-status, clear-status, log, set-progress) are
documented in CMUX's API reference but not yet available in the CLI as of
v0.60.0. See https://github.com/manaflow-ai/cmux/issues/375. When shipped,
re-enable the sidebar handlers and PreToolUse/PostToolUse hooks.
"""

import contextlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_CMUX_AVAILABLE: bool | None = None

_MAX_INPUT = 10 * 1024 * 1024  # 10 MB


def _cmux_available() -> bool:
    """Check if cmux CLI is reachable. Caches result for the process lifetime."""
    global _CMUX_AVAILABLE
    if _CMUX_AVAILABLE is not None:
        return _CMUX_AVAILABLE
    try:
        result = subprocess.run(
            ["cmux", "ping"],
            capture_output=True,
            timeout=2,  # noqa: S603, S607
        )
        _CMUX_AVAILABLE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _CMUX_AVAILABLE = False
    return _CMUX_AVAILABLE


def _cmux(*args: str) -> None:
    """Fire-and-forget cmux CLI call. Never raises."""
    with contextlib.suppress(Exception):
        subprocess.run(  # noqa: S603, S607
            ["cmux", *args], capture_output=True, timeout=5
        )


# Sentence-boundary pattern: period/exclamation/question followed by whitespace.
_SENTENCE_END = re.compile(r"[.!?]\s")


def _first_sentence(text: str | None) -> str:
    """Extract the first sentence from *text*, truncated to 120 chars."""
    if not text:
        return ""
    m = _SENTENCE_END.search(text)
    sentence = text[: m.start() + 1] if m else text
    sentence = sentence.strip()
    if len(sentence) > 120:
        sentence = sentence[:117] + "..."
    return sentence


def _parse_hook_input() -> dict:
    """Read and parse JSON hook payload from stdin."""
    raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
    if len(raw) > _MAX_INPUT:
        sys.exit(0)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print("cmux-hook: failed to parse hook input", file=sys.stderr)
        sys.exit(0)


# ── Event Handlers ──────────────────────────────────────────────────────────


def _handle_notification(data: dict) -> None:
    notification_type = data.get("notification_type", "")
    message = data.get("message", "")
    title_map = {
        "permission_prompt": "Permission Needed",
        "idle_prompt": "Claude Idle",
        "auth_success": "Auth Success",
        "elicitation_dialog": "Claude Code",
    }
    title = title_map.get(notification_type) or data.get("title") or "Claude Code"
    _cmux("notify", "--title", title, "--body", message)


def _handle_subagent_stop(data: dict) -> None:
    agent_type = data.get("agent_type") or "unknown"
    msg = _first_sentence(data.get("last_assistant_message"))
    body = f"Agent {agent_type} finished: {msg}" if msg else f"Agent {agent_type} finished"
    _cmux("notify", "--title", "Agent Complete", "--body", body)


def _handle_stop(data: dict) -> None:
    if data.get("stop_hook_active") is True:
        return
    msg = _first_sentence(data.get("last_assistant_message"))
    body = f"Work complete: {msg}" if msg else "Work complete"
    _cmux("notify", "--title", "Claude Code", "--body", body)


def _handle_session_start(data: dict) -> None:
    legacy_hook = Path.home() / ".claude" / "hooks" / "cmux-notify.sh"
    if legacy_hook.exists():
        print(
            "Warning: ~/.claude/hooks/cmux-notify.sh detected. "
            "This may cause duplicate CMUX notifications. "
            "Consider removing it — the cmux-integration plugin handles all CMUX notifications.",
            file=sys.stderr,
        )


def _handle_session_end(data: dict) -> None:
    pass  # No-op until sidebar commands are available in cmux CLI


# ── Dispatcher ──────────────────────────────────────────────────────────────

_DISPATCH: dict[str, Callable[[dict], None]] = {
    "Notification": _handle_notification,
    "SubagentStop": _handle_subagent_stop,
    "Stop": _handle_stop,
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
}


def main() -> None:
    data = _parse_hook_input()
    if not _cmux_available():
        sys.exit(0)
    event = data.get("hook_event_name", "")
    handler = _DISPATCH.get(event)
    if handler:
        handler(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
