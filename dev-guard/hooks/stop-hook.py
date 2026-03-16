#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""Stop Hook Main -- zero-dependency fast triage for Claude Code Stop events.

Reads Stop hook stdin JSON, manages per-session state in /tmp, performs
deterministic triage (loop guard, transcript delta, git diff, signal detection,
question classification, work-type determination), and either exits 0 (allow
stop) or delegates to stop-hook-llm.py for LLM evaluation.

Exit codes:
  0 -- allow stop (fast-exit or LLM pass)
  2 -- block stop, Claude should continue (LLM fail)

State file: /tmp/claude-stop-hook-state.json (overridable via STOP_HOOK_STATE_PATH)
"""

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

# ── Constants ────────────────────────────────────────────────────────────────

_MAX_INPUT = 10 * 1024 * 1024  # 10 MB
_STATE_PATH = Path(os.environ.get("STOP_HOOK_STATE_PATH", "/tmp/claude-stop-hook-state.json"))
_STATE_TTL_SECONDS = 24 * 3600  # 24 hours
_GIT_TIMEOUT = 5  # seconds
_LLM_TIMEOUT = 60  # seconds
_SHORT_RESPONSE_CHARS = 200
_RECENT_MESSAGES_LIMIT = 5

# MCP tool names that are read-only (do NOT trigger write-signal detection)
_MCP_READ_ONLY = frozenset(
    {
        "find_symbol",
        "get_symbols_overview",
        "search_for_pattern",
        "list_dir",
        "list_memories",
        "read_memory",
        "check_onboarding_performed",
        "resolve-library-id",
        "query-docs",
        "sequentialthinking",
        "browser_snapshot",
        "browser_console_messages",
        "browser_network_requests",
        "browser_tabs",
    }
)

# think_about_* prefix — checked separately
_MCP_THINK_PREFIX = "think_about_"

# Write tool names (native Claude tools)
_WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

# Agent/task tool names
_AGENT_TOOLS = frozenset({"Agent", "Task", "TeamCreate"})

# Factual question patterns (no ML)
_FACTUAL_PATTERNS = re.compile(
    r"\b(version|api|schema|support|require|format|compatible|"
    r"how\s+to|what\s+is|does\s+\S+\s+work|syntax|documentation|"
    r"library|protocol)\b",
    re.IGNORECASE,
)

# Opinion question patterns
_OPINION_PATTERNS = re.compile(
    r"\b(should|better|prefer|recommend|which\s+\S+\s+choose|advice)\b",
    re.IGNORECASE,
)

# Meta question patterns (conversation management — exit 0)
_META_PATTERNS = re.compile(
    r"\b(ready|done|good|continue|merge|commit|next|proceed|lgtm)\b",
    re.IGNORECASE,
)

# Action words in user messages that indicate a work request
_ACTION_WORDS = re.compile(
    r"\b(create|add|fix|implement|update|change|delete|remove|build|"
    r"deploy|run|test|write)\b",
    re.IGNORECASE,
)

# Completion claim patterns — must appear at END of message
# ($ = end-of-string, no MULTILINE).
# This prevents false positives on mid-paragraph claims like
# "I've completed X.\nHowever, these remain:".
_COMPLETION_CLAIM_PATTERNS = [
    re.compile(
        r"(?:^|[.!?]\s+)(?:I(?:'ve| have)\s+"
        r"(?:completed|finished|done|implemented|added|fixed|updated|created|"
        r"removed|deleted|written|built))[^.!?]*[.!?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!?]\s+)(?:all\s+done|that\s+should\s+do\s+it|"
        r"everything\s+is\s+ready|that(?:'s| is)\s+(?:it|done|complete|all))"
        r"[^.!?]*[.!?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!?]\s+)(?:the\s+(?:changes|implementation|fix|update|task)\s+"
        r"(?:is|are)\s+(?:complete|done|finished|ready))[^.!?]*[.!?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!?]\s+)(?:you\s+(?:can|should|may)\s+now)[^.!?]*[.!?]?\s*$",
        re.IGNORECASE,
    ),
]

# Research tool names
_RESEARCH_TOOLS = frozenset({"WebSearch", "WebFetch"})


# ── State Management ─────────────────────────────────────────────────────────


def _load_state() -> dict:
    """Load state file. Returns empty dict on missing or corrupted file."""
    try:
        if _STATE_PATH.exists():
            text = _STATE_PATH.read_text()
            data = json.loads(text)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def _save_state(state: dict) -> None:
    """Save state file. Silently ignores errors."""
    with contextlib.suppress(OSError):
        _STATE_PATH.write_text(json.dumps(state))


def _clean_stale_sessions(state: dict) -> dict:
    """Remove sessions older than 24 hours."""
    now = time.time()
    cutoff = now - _STATE_TTL_SECONDS
    return {
        sid: sdata
        for sid, sdata in state.items()
        if isinstance(sdata, dict) and sdata.get("last_fire_timestamp", 0) >= cutoff
    }


def _get_session_state(state: dict, session_id: str) -> dict | None:
    """Return state for a session or None if not present."""
    entry = state.get(session_id)
    if isinstance(entry, dict):
        return entry
    return None


def _update_session_state(
    state: dict,
    session_id: str,
    diff_hash: str,
    transcript_line_count: int,
) -> dict:
    """Write updated session state and return the full state dict."""
    state[session_id] = {
        "last_diff_hash": diff_hash,
        "last_fire_timestamp": time.time(),
        "last_transcript_line_count": transcript_line_count,
    }
    return state


# ── Stdin Parsing ────────────────────────────────────────────────────────────


def _parse_hook_input() -> dict:
    """Read and parse JSON hook payload from stdin. Exits 0 on failure."""
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
    except OSError:
        sys.exit(0)
    if len(raw) > _MAX_INPUT:
        sys.exit(0)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            sys.exit(0)
        return data
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)


# ── Transcript Parsing ───────────────────────────────────────────────────────


def _parse_transcript(
    transcript_path: str, start_line: int
) -> tuple[list[str], list[str], str | None, str | None, int]:
    """Parse JSONL transcript from start_line onward.

    Returns:
        (new_tool_calls, recent_assistant_messages, latest_user_message,
         first_user_message, total_line_count)
    """
    new_tool_calls: list[str] = []
    assistant_messages: list[str] = []
    latest_user_message: str | None = None
    first_user_message: str | None = None
    total_lines = start_line

    try:
        path = Path(transcript_path)
        if not path.exists():
            return [], [], None, None, 0

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)

        # Scan all lines for first_user_message (from the beginning)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue
            role = entry.get("role", "")
            if role == "user" and first_user_message is None:
                content = entry.get("content", "")
                if isinstance(content, str) and content.strip():
                    first_user_message = content.strip()
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text.strip():
                                first_user_message = text.strip()
                                break

        # Parse new lines (from start_line onward)
        for line in lines[start_line:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue

            role = entry.get("role", "")
            msg_type = entry.get("type", "")

            # Tool use entries (flat format: top-level type == "tool_use")
            if msg_type == "tool_use":
                tool_name = entry.get("name", "")
                if tool_name:
                    new_tool_calls.append(tool_name)

            # Assistant messages (inline tool_use blocks live here, not in flat tool_use entries)
            if role == "assistant":
                content = entry.get("content", "")
                if isinstance(content, str) and content.strip():
                    assistant_messages.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text.strip():
                                assistant_messages.append(text.strip())
                        elif isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name:
                                new_tool_calls.append(name)

            # User messages (track latest)
            if role == "user":
                content = entry.get("content", "")
                if isinstance(content, str) and content.strip():
                    latest_user_message = content.strip()
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text.strip():
                                latest_user_message = text.strip()
                                break

    except (OSError, UnicodeDecodeError):
        pass

    # Deduplicate tool calls while preserving order
    seen: set[str] = set()
    deduped_tool_calls: list[str] = []
    for tc in new_tool_calls:
        if tc not in seen:
            seen.add(tc)
            deduped_tool_calls.append(tc)

    recent = assistant_messages[-_RECENT_MESSAGES_LIMIT:]
    return deduped_tool_calls, recent, latest_user_message, first_user_message, total_lines


# ── Git Checks ───────────────────────────────────────────────────────────────


def _git_diff_hash(cwd: str) -> str:
    """Return a hash of 'git diff' output. Empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            cwd=cwd,
        )
        if result.returncode == 0:
            return hashlib.sha256(result.stdout).hexdigest()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _git_diff_stat(cwd: str) -> str | None:
    """Return 'git diff --stat' output or None on failure."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            cwd=cwd,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


# ── Signal Detection ─────────────────────────────────────────────────────────


def _is_mcp_write_tool(tool_name: str) -> bool:
    """Return True if this MCP tool call represents a write operation."""
    if not tool_name.startswith("mcp__"):
        return False
    # Strip mcp__<server>__ prefix to get the function name
    parts = tool_name.split("__", 2)
    func_name = parts[2] if len(parts) >= 3 else ""
    if func_name in _MCP_READ_ONLY:
        return False
    return not func_name.startswith(_MCP_THINK_PREFIX)


def _detect_write_signals(tool_calls: list[str]) -> list[str]:
    """Return list of signal names detected from tool calls.

    Note: Bash tool calls not classified as writes — we only have tool names, not inputs.
    Git diff change detection handles the Bash-wrote-files case.
    """
    signals: list[str] = []

    for tc in tool_calls:
        if tc in _WRITE_TOOLS:
            signals.append("write_tool")
            break

    for tc in tool_calls:
        if tc in _AGENT_TOOLS:
            signals.append("subagent")
            break

    for tc in tool_calls:
        if _is_mcp_write_tool(tc):
            signals.append("mcp_write")
            break

    return signals


def _detect_completion_claim(message: str | None) -> bool:
    """Return True if message contains a completion claim at end/standalone."""
    if not message:
        return False
    return any(pattern.search(message) for pattern in _COMPLETION_CLAIM_PATTERNS)


def _detect_research_tools(tool_calls: list[str]) -> bool:
    """Return True if any research tool was used."""
    return any(tc in _RESEARCH_TOOLS for tc in tool_calls)


def _check_hack_dir_modified(cwd: str) -> dict[str, bool]:
    """Check if hack/plans/ or hack/research/ files were recently modified."""
    result = {"plans": False, "research": False}
    now = time.time()
    recent_threshold = 300  # 5 minutes
    try:
        hack_path = Path(cwd) / "hack"
        if not hack_path.is_dir():
            return result
        for subdir, key in [("plans", "plans"), ("research", "research")]:
            sub_path = hack_path / subdir
            if sub_path.is_dir():
                for fpath in sub_path.iterdir():
                    if fpath.is_file():
                        mtime = fpath.stat().st_mtime
                        if now - mtime <= recent_threshold:
                            result[key] = True
                            break
    except OSError:
        pass
    return result


# ── Question Classification ──────────────────────────────────────────────────


def _classify_question(user_message: str | None) -> str | None:
    """Classify the user's question type.

    Returns: 'meta', 'opinion', 'factual', or None (not a question / unknown)
    Priority: META > OPINION > FACTUAL
    """
    if not user_message:
        return None
    if _META_PATTERNS.search(user_message):
        return "meta"
    if _OPINION_PATTERNS.search(user_message):
        return "opinion"
    if _FACTUAL_PATTERNS.search(user_message):
        return "factual"
    if "?" in user_message:
        return "factual"  # Default: unknown question type → treat as factual
    return None


# ── Work Type Determination ──────────────────────────────────────────────────


def _determine_work_type(
    tool_calls: list[str],
    diff_changed: bool,
    hack_modified: dict[str, bool],
    user_message: str | None,
    write_signals: list[str],
) -> str:
    """Determine the type of work performed."""
    types: list[str] = []

    if diff_changed or write_signals:
        types.append("code_config")

    if hack_modified.get("plans"):
        types.append("planning")

    if hack_modified.get("research") or _detect_research_tools(tool_calls):
        types.append("research")

    has_question = bool(user_message and "?" in user_message)
    if has_question and not write_signals:
        types.append("question")

    if not types:
        return "conversation"
    if len(types) == 1:
        return types[0]
    return "mixed"


# ── LLM Delegation ───────────────────────────────────────────────────────────


def _invoke_llm(
    context: dict,
    plugin_root: str,
) -> tuple[str, list[str] | None]:
    """Invoke stop-hook-llm.py subprocess.

    Returns (decision, findings) where decision is 'pass' or 'fail'.
    Falls back to 'pass' (fail-open) on any error.
    """
    llm_script = Path(plugin_root) / "hooks" / "stop-hook-llm.py"

    try:
        result = subprocess.run(
            ["uv", "run", str(llm_script)],
            input=json.dumps(context).encode(),
            capture_output=True,
            timeout=_LLM_TIMEOUT,
        )
        if result.returncode not in (0, 2):
            # Unexpected exit code — fail-open
            return "pass", None

        stdout = result.stdout.strip()
        if not stdout:
            return "pass", None

        try:
            response = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return "pass", None

        if not isinstance(response, dict):
            return "pass", None

        decision = response.get("decision", "pass")
        if decision not in ("pass", "fail"):
            decision = "pass"

        findings = response.get("findings")
        if not isinstance(findings, list):
            findings = None

        return decision, findings

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Fail-open: any infrastructure error → allow stop
        return "pass", None


# ── Exit Helpers ─────────────────────────────────────────────────────────────


def _exit_pass(message: str | None = None) -> NoReturn:
    """Exit 0, optionally printing a guidance message to stdout."""
    if message:
        print(message)
    sys.exit(0)


def _exit_block(findings: list[str] | None) -> NoReturn:
    """Exit 2 with findings printed to stderr."""
    if findings:
        print("Stop hook findings:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
    else:
        print("Stop hook: quality check failed. Please review your work.", file=sys.stderr)
    sys.exit(2)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    data = _parse_hook_input()

    # ── Fast-exit 1: Loop guard ──────────────────────────────────────────────
    if data.get("stop_hook_active") is True:
        _exit_pass()

    session_id = data.get("session_id", "")
    transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", ".")
    last_assistant_message = (data.get("last_assistant_message") or "").strip()

    # ── Load + clean state ───────────────────────────────────────────────────
    state = _load_state()
    state = _clean_stale_sessions(state)
    session_state = _get_session_state(state, session_id)

    # ── Fast-exit 2: First fire — initialize state ───────────────────────────
    if session_state is None:
        diff_hash = _git_diff_hash(cwd)
        # Set line_count to 0 — next invocation will parse from line 0, catching everything
        state = _update_session_state(state, session_id, diff_hash, 0)
        _save_state(state)
        _exit_pass()

    last_transcript_line_count = session_state.get("last_transcript_line_count", 0)
    last_diff_hash = session_state.get("last_diff_hash", "")

    # ── Parse transcript delta ───────────────────────────────────────────────
    (
        new_tool_calls,
        recent_assistant_messages,
        latest_user_message,
        first_user_message,
        total_lines,
    ) = _parse_transcript(transcript_path, last_transcript_line_count)

    # ── Fast-exit 3: Zero new transcript lines ───────────────────────────────
    if total_lines <= last_transcript_line_count:
        # No new lines — nothing changed, reuse last_diff_hash to avoid git subprocess
        state = _update_session_state(state, session_id, last_diff_hash, total_lines)
        _save_state(state)
        _exit_pass()

    # ── Git diff check ───────────────────────────────────────────────────────
    current_diff_hash = _git_diff_hash(cwd)
    diff_changed = bool(current_diff_hash and current_diff_hash != last_diff_hash)

    # ── Detect signals ───────────────────────────────────────────────────────
    write_signals = _detect_write_signals(new_tool_calls)
    completion_claim = _detect_completion_claim(last_assistant_message)
    research_used = _detect_research_tools(new_tool_calls)
    hack_modified = _check_hack_dir_modified(cwd)

    # ── Question classification ──────────────────────────────────────────────
    question_type = _classify_question(latest_user_message)

    # ── Fast-exit 4: META question ───────────────────────────────────────────
    if question_type == "meta" and not write_signals and not diff_changed:
        state = _update_session_state(state, session_id, current_diff_hash, total_lines)
        _save_state(state)
        _exit_pass()

    # ── Fast-exit 5: OPINION question ────────────────────────────────────────
    if question_type == "opinion" and not write_signals and not diff_changed:
        state = _update_session_state(state, session_id, current_diff_hash, total_lines)
        _save_state(state)
        _exit_pass()

    # ── Fast-exit 6: Short response with no signals ──────────────────────────
    response_is_short = len(last_assistant_message) < _SHORT_RESPONSE_CHARS
    has_question_in_message = bool(latest_user_message and "?" in latest_user_message)
    user_requested_action = bool(latest_user_message and _ACTION_WORDS.search(latest_user_message))

    if (
        response_is_short
        and not new_tool_calls
        and not diff_changed
        and not has_question_in_message
        and not user_requested_action
    ):
        state = _update_session_state(state, session_id, current_diff_hash, total_lines)
        _save_state(state)
        _exit_pass()

    # ── Exit-with-guidance: Research + short response ────────────────────────
    if research_used and response_is_short and not write_signals and not diff_changed:
        state = _update_session_state(state, session_id, current_diff_hash, total_lines)
        _save_state(state)
        _exit_pass("Research done, verify external claims if any.")

    # ── Exit-with-guidance: Read-only + question ─────────────────────────────
    if (
        not write_signals
        and not diff_changed
        and not completion_claim
        and question_type in ("factual",)
        and not new_tool_calls
        and has_question_in_message
    ):
        state = _update_session_state(state, session_id, current_diff_hash, total_lines)
        _save_state(state)
        _exit_pass("Answer based on code exploration.")

    # ── Build trigger reasons ────────────────────────────────────────────────
    trigger_reasons: list[str] = []
    if completion_claim:
        trigger_reasons.append("completion_claim")
    if diff_changed or "write_tool" in write_signals:
        trigger_reasons.append("code_change")
    if research_used:
        trigger_reasons.append("research")
    if "subagent" in write_signals:
        trigger_reasons.append("subagent")
    if hack_modified.get("plans"):
        trigger_reasons.append("planning")
    if "mcp_write" in write_signals:
        trigger_reasons.append("mcp_write")
    if user_requested_action and not write_signals:
        trigger_reasons.append("action_requested_no_tools")

    # ── Fast-exit 7: No triggers at all ─────────────────────────────────────
    if not trigger_reasons:
        state = _update_session_state(state, session_id, current_diff_hash, total_lines)
        _save_state(state)
        _exit_pass()

    # ── Determine work type ──────────────────────────────────────────────────
    work_type = _determine_work_type(
        new_tool_calls, diff_changed, hack_modified, latest_user_message, write_signals
    )

    # ── Build LLM context ────────────────────────────────────────────────────
    diff_stat = _git_diff_stat(cwd) if diff_changed else None

    llm_context = {
        "first_user_message": first_user_message,
        "new_tool_calls": new_tool_calls,
        "recent_assistant_messages": recent_assistant_messages,
        "git_diff_stat": diff_stat,
        "trigger_reasons": trigger_reasons,
        "work_type": work_type,
    }

    # ── Invoke LLM ───────────────────────────────────────────────────────────
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    decision, findings = _invoke_llm(llm_context, plugin_root)

    # ── Update state regardless of decision ─────────────────────────────────
    state = _update_session_state(state, session_id, current_diff_hash, total_lines)
    _save_state(state)

    # ── Exit based on LLM decision ───────────────────────────────────────────
    if decision == "fail":
        _exit_block(findings)
    else:
        _exit_pass()


if __name__ == "__main__":
    main()
