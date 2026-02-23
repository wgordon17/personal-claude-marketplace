#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""CMUX Integration Hook -- bridges Claude Code events to cmux CLI.

Routes hook events (session lifecycle, tool use, notifications, agent completion)
to the cmux terminal multiplexer for sidebar status, desktop notifications,
and activity logging.
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
    _cmux("log", body, "--level", "success", "--source", "claude")


def _handle_stop(data: dict) -> None:
    if data.get("stop_hook_active") is True:
        return
    msg = _first_sentence(data.get("last_assistant_message"))
    _cmux("set-status", "claude-session", "complete", "--icon", "✓", "--color", "green")
    _cmux("clear-status", "claude-activity")
    body = f"Work complete: {msg}" if msg else "Work complete"
    _cmux("notify", "--title", "Claude Code", "--body", body)
    _cmux("log", "Claude stopped", "--level", "info", "--source", "claude")


def _handle_session_start(data: dict) -> None:
    legacy_hook = Path.home() / ".claude" / "hooks" / "cmux-notify.sh"
    if legacy_hook.exists():
        print(
            "Warning: ~/.claude/hooks/cmux-notify.sh detected. "
            "This may cause duplicate CMUX notifications. "
            "Consider removing it — the cmux-integration plugin handles all CMUX notifications.",
            file=sys.stderr,
        )
    _cmux("set-status", "claude-session", "active", "--icon", "●", "--color", "green")
    _cmux("log", "Session started", "--level", "info", "--source", "claude")


def _handle_session_end(data: dict) -> None:
    _cmux("clear-status", "claude-session")
    _cmux("clear-status", "claude-activity")
    _cmux("log", "Session ended", "--level", "info", "--source", "claude")


def _handle_pre_tool_use(data: dict) -> None:
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")[:60]
        _cmux("set-status", "claude-activity", f"Running: {cmd}", "--icon", "⏳")
    elif tool_name == "Task":
        desc = tool_input.get("description", "agent")[:60]
        _cmux("set-status", "claude-activity", f"Spawning: {desc}", "--icon", "🚀")


def _handle_post_tool_use(data: dict) -> None:
    _cmux("clear-status", "claude-activity")
    response = data.get("tool_response", "")
    if isinstance(response, str):
        summary = _first_sentence(response)
    elif isinstance(response, dict):
        summary = _first_sentence(str(response.get("stdout", ""))[:500])
    else:
        summary = ""
    msg = f"Task completed: {summary}" if summary else "Task completed"
    _cmux("log", msg, "--level", "success", "--source", "claude")


# ── Dispatcher ──────────────────────────────────────────────────────────────

_DISPATCH: dict[str, Callable[[dict], None]] = {
    "Notification": _handle_notification,
    "SubagentStop": _handle_subagent_stop,
    "Stop": _handle_stop,
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
    "PreToolUse": _handle_pre_tool_use,
    "PostToolUse": _handle_post_tool_use,
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
