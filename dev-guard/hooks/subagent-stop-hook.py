#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# ///
"""SubagentStop Hook -- validates FixSummary structural completeness.

Fires on every SubagentStop event. Parses the subagent's transcript JSONL to
find FixSummary content. If no FixSummary is found, the subagent is not a Fixer
and the stop is approved. If a FixSummary is found, it validates structural
completeness. Invalid FixSummary blocks the stop. A loop guard (3 consecutive
blocks per state key) fails open (approves) to prevent infinite retries. All
errors fail open.

Exit codes:
  0 -- always. Approve stop (plain exit) or block via JSON {"decision":"block","reason":"..."}

State file: ~/.claude/subagent-stop-hook-state.json
           (overridable via SUBAGENT_STOP_HOOK_STATE_PATH)
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import NoReturn

# ── Constants ────────────────────────────────────────────────────────────────

_MAX_INPUT = 10 * 1024 * 1024  # 10 MB
_MAX_PARSE_BYTES = 512 * 1024  # 512 KB tail window for transcript
_MAX_CONSECUTIVE_BLOCKS = 3
_STATE_PATH = Path(
    os.environ.get(
        "SUBAGENT_STOP_HOOK_STATE_PATH",
        str(Path.home() / ".claude" / "subagent-stop-hook-state.json"),
    )
)
_STATE_TTL_SECONDS = 24 * 3600  # 24 hours


# ── Exit Helpers ─────────────────────────────────────────────────────────────


def _exit_approve() -> NoReturn:
    """Exit 0, no output (approve the stop)."""
    sys.exit(0)


def _exit_block(message: str) -> NoReturn:
    """Exit 0 with {decision: block, reason: ...} JSON on stdout."""
    output = {"decision": "block", "reason": message}
    print(json.dumps(output))
    sys.exit(0)


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


def _extract_text_from_content(content: object) -> str:
    """Extract all text from a content value (string, list of blocks, or tool_use input)."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif block_type == "tool_use":
                inp = block.get("input", {})
                if isinstance(inp, dict):
                    # Flatten tool_use input fields as JSON for FixSummary detection
                    parts.append(json.dumps(inp))
        return "\n".join(parts)

    if isinstance(content, dict):
        return json.dumps(content)

    return ""


def _try_parse_fix_summary(text: str) -> dict | None:
    """Attempt to extract a FixSummary dict from a text fragment using raw_decode."""
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        # Scan for opening brace
        brace = text.find("{", pos)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, brace)
            if isinstance(obj, dict) and (
                obj.get("schema") == "FixSummary" or "findings_fixed" in obj
            ):
                return obj
            pos = end
        except (json.JSONDecodeError, ValueError):
            pos = brace + 1
    return None


def _find_fix_summary(transcript_path: str) -> dict | None:
    """Tail-read transcript JSONL and search for FixSummary content.

    Returns first valid FixSummary dict found (most recent first), or None.
    """
    try:
        path = Path(transcript_path)
        if not path.exists() or not path.is_file():
            return None
        file_size = path.stat().st_size
        if file_size == 0:
            return None

        seek_to = max(0, file_size - _MAX_PARSE_BYTES)

        lines: list[str] = []
        with open(path, encoding="utf-8", errors="replace") as f:
            if seek_to > 0:
                f.seek(seek_to)
                f.readline()  # discard partial first line after seek
            while True:
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    lines.append(line)

        # Reverse iterate (most recent first)
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue

            # Resolve content from either transcript format:
            #   Real: {"type": "assistant", "message": {"role": "assistant", "content": ...}}
            #   Test: {"role": "assistant", "content": ...}
            message = entry.get("message")
            if isinstance(message, dict):
                role = message.get("role", "")
                content = message.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            # Also check flat tool_use entries ({"type": "tool_use", "input": {...}})
            msg_type = entry.get("type", "")
            if msg_type == "tool_use":
                inp = entry.get("input", {})
                if isinstance(inp, dict):
                    if inp.get("schema") == "FixSummary" or "findings_fixed" in inp:
                        return inp
                    # Check if input contains a FixSummary in a nested field (e.g. SendMessage)
                    text = json.dumps(inp)
                    if "FixSummary" in text or "findings_fixed" in text:
                        result = _try_parse_fix_summary(text)
                        if result is not None:
                            return result

            # Only examine assistant messages for FixSummary
            is_assistant = msg_type == "assistant" or role == "assistant"
            if not is_assistant:
                continue

            text = _extract_text_from_content(content)
            if not text:
                continue

            # Quick check before expensive parse
            if "FixSummary" not in text and "findings_fixed" not in text:
                continue

            result = _try_parse_fix_summary(text)
            if result is not None:
                return result

    except (OSError, UnicodeDecodeError):
        return None

    return None


# ── Validation ───────────────────────────────────────────────────────────────


def _validate_fix_summary(fix_summary: dict) -> tuple[bool, str]:
    """Validate FixSummary structural completeness.

    Returns (is_valid, message).
    """
    findings_fixed = fix_summary.get("findings_fixed", [])
    needs_input_items = fix_summary.get("needs_input_items", [])
    user_deferred = fix_summary.get("user_deferred", [])

    if not isinstance(findings_fixed, list):
        findings_fixed = []
    if not isinstance(needs_input_items, list):
        needs_input_items = []
    if not isinstance(user_deferred, list):
        user_deferred = []

    total_accounted = len(findings_fixed) + len(needs_input_items) + len(user_deferred)

    if total_accounted == 0:
        return False, "all arrays empty — no findings accounted for"

    # Validate findings_fixed items: must be non-empty strings
    for item in findings_fixed:
        if isinstance(item, str):
            if not item.strip():
                return False, "findings_fixed contains whitespace-only string"
        elif isinstance(item, dict):
            if not item:
                return False, "findings_fixed contains empty dict"
        else:
            return False, f"findings_fixed contains unexpected type: {type(item).__name__}"

    # Validate needs_input_items: must be non-empty dicts
    for item in needs_input_items:
        if isinstance(item, dict):
            if not item:
                return False, "needs_input_items contains empty dict"
        elif isinstance(item, str):
            if not item.strip():
                return False, "needs_input_items contains whitespace-only string"
        else:
            return False, f"needs_input_items contains unexpected type: {type(item).__name__}"

    # Validate user_deferred: must be non-empty dicts
    for item in user_deferred:
        if isinstance(item, dict):
            if not item:
                return False, "user_deferred contains empty dict"
        elif isinstance(item, str):
            if not item.strip():
                return False, "user_deferred contains whitespace-only string"
        else:
            return False, f"user_deferred contains unexpected type: {type(item).__name__}"

    return True, "valid"


# ── State Management ─────────────────────────────────────────────────────────


def _load_state() -> dict:
    """Load state file. Returns empty dict on missing or corrupted file.

    Also prunes entries older than _STATE_TTL_SECONDS.
    """
    state: dict = {}
    try:
        if _STATE_PATH.exists():
            text = _STATE_PATH.read_text()
            data = json.loads(text)
            if isinstance(data, dict):
                state = data
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # Prune stale entries
    now = time.time()
    cutoff = now - _STATE_TTL_SECONDS
    return {
        key: entry
        for key, entry in state.items()
        if isinstance(entry, dict) and entry.get("last_block_timestamp", 0) >= cutoff
    }


def _save_state(state: dict) -> None:
    """Save state file atomically. Silently ignores errors."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, _STATE_PATH)
    except OSError:
        pass


def _check_loop_guard(state: dict, state_key: str) -> tuple[bool, int]:
    """Check consecutive block count for state_key.

    Returns (should_fail_open, count) where should_fail_open means we've hit
    the max and should approve instead of blocking.
    """
    entry = state.get(state_key)
    if not isinstance(entry, dict):
        return False, 0
    count = entry.get("consecutive_blocks", 0)
    return count >= _MAX_CONSECUTIVE_BLOCKS, count


def _record_block(state: dict, state_key: str) -> None:
    """Increment consecutive_blocks counter for state_key."""
    entry = state.get(state_key)
    if isinstance(entry, dict):
        count = entry.get("consecutive_blocks", 0)
        state[state_key] = {
            "consecutive_blocks": count + 1,
            "last_block_timestamp": time.time(),
        }
    else:
        state[state_key] = {
            "consecutive_blocks": 1,
            "last_block_timestamp": time.time(),
        }


def _record_pass(state: dict, state_key: str) -> None:
    """Delete state_key entry if present (counter reset)."""
    state.pop(state_key, None)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    data = _parse_hook_input()

    session_id = data.get("session_id", "")
    transcript_path = data.get("transcript_path", "")

    # No transcript -> nothing to validate
    if not transcript_path:
        _exit_approve()

    fix_summary = _find_fix_summary(transcript_path)

    # No FixSummary -> not a Fixer subagent
    if fix_summary is None:
        _exit_approve()

    is_valid, message = _validate_fix_summary(fix_summary)

    state_key = f"{session_id}:{transcript_path}" if session_id else transcript_path
    state = _load_state()

    if is_valid:
        _record_pass(state, state_key)
        _save_state(state)
        _exit_approve()

    should_fail_open, _ = _check_loop_guard(state, state_key)
    if should_fail_open:
        _record_block(state, state_key)
        _save_state(state)
        _exit_approve()  # Fail open after 3 consecutive blocks

    _record_block(state, state_key)
    _save_state(state)
    _exit_block(
        f"FixSummary validation failed: {message}. "
        "Review your FixSummary output and ensure all findings are accounted for."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
