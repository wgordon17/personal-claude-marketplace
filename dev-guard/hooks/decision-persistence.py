#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# ///
"""Decision Persistence Hook — remembers Fix/Defer decisions across sessions.

PreToolUse: checks stored decisions and auto-answers AskUserQuestion via updatedInput.
PostToolUse: captures Fix/Defer decisions to {memory_dir}/review-decisions.json.

Decisions are persisted as fingerprinted records (file + category + line window) so
that repeated reviews of the same finding auto-apply the prior Fix/Defer decision
without re-prompting the user.
"""

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ── Constants ──

VALID_DECISIONS: frozenset[str] = frozenset({"Fix", "Defer"})
_MAX_FIELD_LEN = 512
_MAX_DECISION_AGE_DAYS = 30

METADATA_PREFIX = "▸dp:"
DECISIONS_FILENAME = "review-decisions.json"
MEMORY_DIR_CANDIDATES = ("hack", ".local", "scratch", ".dev")
_CODE_HASH_WINDOW = 5  # ±N lines around finding location for staleness detection


# ── Git root detection ──

_cached_git_root: Path | None = None


def _find_git_root() -> Path:
    """Find the git root by walking up from CWD. Falls back to CWD if no .git found.

    Cached per-process — CWD cannot change within a hook subprocess invocation.
    """
    global _cached_git_root  # noqa: PLW0603
    if _cached_git_root is not None:
        return _cached_git_root
    cwd = Path.cwd()
    git_root = cwd
    while git_root != git_root.parent:
        if (git_root / ".git").exists():
            _cached_git_root = git_root
            return git_root
        git_root = git_root.parent
    _cached_git_root = cwd
    return cwd  # no .git found — fall back to CWD


# ── Memory directory detection ──

_memory_dir_resolved = False
_cached_memory_dir: Path | None = None


def _find_memory_dir() -> Path | None:
    """Find the project memory directory (hack/, .local/, scratch/, .dev/).

    Finds git root by walking up from CWD, then checks for candidates at root level.
    Cached per-process alongside _find_git_root.
    """
    global _memory_dir_resolved, _cached_memory_dir  # noqa: PLW0603
    if _memory_dir_resolved:
        return _cached_memory_dir
    git_root = _find_git_root()

    core_files = ("PROJECT.md", "TODO.md", "SESSIONS.md", "NEXT.md", "LESSONS.md")

    for candidate in MEMORY_DIR_CANDIDATES:
        candidate_path = git_root / candidate
        if candidate_path.is_dir():
            core_count = sum(1 for f in core_files if (candidate_path / f).is_file())
            if core_count < 2:
                continue  # not a validated memory directory
            _cached_memory_dir = candidate_path
            _memory_dir_resolved = True
            return candidate_path
    _memory_dir_resolved = True
    return None


def _decisions_path() -> Path | None:
    """Return the path to the decisions file, or None if no memory dir."""
    mem_dir = _find_memory_dir()
    if mem_dir is None:
        return None
    return mem_dir / DECISIONS_FILENAME


# ── Decision storage ──


def _load_decisions(path: Path) -> dict:
    """Load decisions from the JSON file."""
    if not path.exists():
        return {"version": 1, "decisions": []}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "decisions": []}


def _save_decisions(path: Path, data: dict) -> None:
    """Save decisions to the JSON file (atomic write via tmp + rename)."""
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(
            f"[decision-persistence] WARNING: failed to save decisions to {path}: {exc}",
            file=sys.stderr,
        )


def _fingerprint(file: str, category: str, line: str = "0", skill: str = "unknown") -> str:
    """Create a stable fingerprint for a finding.

    Uses file + category + line_number + skill. All fields come from
    deterministic ▸dp: metadata, not LLM prose. Uses exact line numbers
    (not windowed) to prevent collisions between distinct findings at
    nearby lines. Code-hash staleness handles minor line shifts caused
    by edits above the finding — the hash changes, the decision is
    invalidated, and the user is re-prompted.
    """
    line_num = int(line) if line.isdigit() else 0
    normalized = f"{file}|{category}|{line_num}|{skill}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def _compute_code_hash(file_path: str, line: str = "0") -> str | None:
    """Hash ±N lines around a finding location for staleness detection.

    Returns a 32-char hex digest, or None if the file can't be read.
    Only reads lines in the target window (not the full file).
    Rejects paths that resolve outside the git root (symlink boundary check).
    """
    git_root = _find_git_root()
    resolved = git_root / file_path
    try:
        canonical = resolved.resolve()
        if not canonical.is_relative_to(git_root.resolve()):
            return None
    except (OSError, ValueError):
        return None

    target = int(line) if line.isdigit() else 0
    center = max(0, target - 1)  # line numbers are 1-based
    start = max(0, center - _CODE_HASH_WINDOW)
    end = center + _CODE_HASH_WINDOW + 1

    try:
        window_lines: list[str] = []
        with open(resolved) as f:
            for i, file_line in enumerate(f):
                if i >= end:
                    break
                if i >= start:
                    window_lines.append(file_line.rstrip("\n"))
    except (OSError, UnicodeDecodeError):
        return None
    if not window_lines:
        return None
    return hashlib.sha256("\n".join(window_lines).encode()).hexdigest()[:32]


# ── Metadata parsing ──


def _sanitize_path_field(value: str) -> str:
    """Normalize and bound a file path field from metadata.

    Rejects path traversal sequences and absolute paths. Strips
    non-path characters and enforces a length limit.
    """
    if any(part == ".." for part in Path(value).parts) or value.startswith("/"):
        return "<invalid>"
    clean = re.sub(r"[^\w./_-]", "_", value)
    return clean[:_MAX_FIELD_LEN]


def _parse_metadata(question_text: str) -> dict | None:
    """Extract structured metadata from a question's ▸dp: suffix.

    Expected format: ▸dp:file=path,line=N,cat=Category,skill=skill-name
    Returns dict with keys: file, line, cat, skill, or None if not found.

    NOTE: file paths containing commas will cause parse failure because
    the metadata format uses comma-delimited key=value pairs. Producer
    skills must avoid commas in file paths.
    """
    if METADATA_PREFIX not in question_text:
        return None

    _, _, meta_str = question_text.rpartition(METADATA_PREFIX)
    meta_str = meta_str.strip()

    result = {}
    for pair in meta_str.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        result[key.strip()] = value.strip()

    if not result.get("file") or not result.get("cat"):
        return None

    # Sanitize path-like fields
    result["file"] = _sanitize_path_field(result["file"])
    if result["file"] == "<invalid>":
        return None

    return result


def _extract_description_snippet(question_text: str) -> str:
    """Extract the description portion of the question text.

    Strips the leading [id] [category] prefix and the ▸dp: suffix,
    then takes the first line as the description snippet.
    """
    # Remove metadata suffix (last occurrence, consistent with rpartition in _parse_metadata)
    text = question_text.rsplit(METADATA_PREFIX, 1)[0].strip()
    # Remove leading [bracketed] prefixes
    first_line = text.split("\n")[0].strip()
    # Strip [id] and [Category] prefixes
    while first_line.startswith("["):
        close = first_line.find("]")
        if close == -1:
            break
        first_line = first_line[close + 1 :].strip()
    return first_line


def _is_fix_defer_question(question: dict) -> bool:
    """Check if a question has Fix/Defer options (our target pattern)."""
    options = question.get("options", [])
    if len(options) < 2:
        return False
    labels = {opt.get("label", "").lower() for opt in options}
    return "fix" in labels and "defer" in labels


# ── PreToolUse handler ──


def _handle_pre_tool_use(data: dict) -> None:
    """Check stored decisions and auto-answer if all questions have prior decisions."""
    tool_input = data.get("tool_input", {})
    questions = tool_input.get("questions", [])

    if not questions:
        sys.exit(0)  # passthrough

    # Only process Fix/Defer questions — bail if batch is mixed-type
    fix_defer_questions = [q for q in questions if _is_fix_defer_question(q)]
    if not fix_defer_questions:
        sys.exit(0)  # passthrough — not our kind of question
    if len(fix_defer_questions) != len(questions):
        sys.exit(0)  # mixed-type batch: answers dict would only cover Fix/Defer subset

    # Parse metadata for all Fix/Defer questions (deduplicated per question text)
    parsed: dict[str, dict] = {}
    for q in fix_defer_questions:
        q_text = q.get("question", "")
        meta = _parse_metadata(q_text)
        if meta is None:
            sys.exit(0)  # some questions lack metadata — passthrough
        parsed[q_text] = meta

    # Load stored decisions
    dec_path = _decisions_path()
    if dec_path is None:
        sys.exit(0)  # no memory dir — passthrough

    decisions_data = _load_decisions(dec_path)
    cutoff = (datetime.now(UTC) - timedelta(days=_MAX_DECISION_AGE_DAYS)).isoformat()
    stored = {
        d["fingerprint"]: d
        for d in decisions_data.get("decisions", [])
        if "fingerprint" in d and d.get("decided_at", "") >= cutoff
    }

    if not stored:
        sys.exit(0)  # no prior decisions (or all expired) — passthrough

    # Try to match each Fix/Defer question
    answers = {}
    all_matched = True
    code_hash_cache: dict[tuple[str, str], str | None] = {}

    for q in fix_defer_questions:
        q_text = q.get("question", "")
        meta = parsed[q_text]
        skill = meta.get("skill", "unknown")
        fp = _fingerprint(meta["file"], meta["cat"], meta.get("line", "0"), skill)

        if fp in stored:
            prior = stored[fp]
            decision = prior["decision"]
            if decision not in VALID_DECISIONS:
                all_matched = False
                break  # corrupted/unexpected stored value — re-ask
            # Staleness check: verify code hasn't changed since decision was made
            stored_hash = prior.get("code_hash")
            if stored_hash is not None:
                cache_key = (meta["file"], meta.get("line", "0"))
                if cache_key not in code_hash_cache:
                    code_hash_cache[cache_key] = _compute_code_hash(*cache_key)
                current_hash = code_hash_cache[cache_key]
                if current_hash != stored_hash:
                    all_matched = False
                    break  # code changed — decision is stale, re-ask
            answers[q_text] = decision
        else:
            all_matched = False
            break

    if not all_matched:
        # Partial batch auto-answer is architecturally blocked: updatedInput
        # requires permissionDecision to take effect (anthropics/claude-code#32060),
        # so there's no "allow some, prompt for rest" mode. Passthrough all.
        sys.exit(0)

    # All questions matched — build context message
    context_lines = [
        f"Decision persistence: auto-applying {len(answers)} prior decision(s).",
    ]
    for q_text, decision in answers.items():
        meta = parsed.get(q_text)
        if meta is not None:
            context_lines.append(
                f"  {meta['file']}:{meta.get('line', '?')} [{meta['cat']}] → {decision}"
            )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "questions": questions,  # echo back original questions
                "answers": answers,
            },
            "additionalContext": "\n".join(context_lines),
        }
    }

    print(json.dumps(output))
    sys.exit(0)


# ── PostToolUse handler ──


def _handle_post_tool_use(data: dict) -> None:
    """Capture Fix/Defer decisions from AskUserQuestion responses."""
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})

    questions = tool_input.get("questions", [])
    # Answers come from tool_response (user's actual answers).
    # tool_input.answers is populated by PreToolUse updatedInput — those are
    # auto-applied decisions, not new user decisions. Reading from tool_input
    # first would re-capture already-stored decisions with a fresh timestamp,
    # misleading future staleness detection.
    answers = {}
    answers_from_tool_response = True
    if isinstance(tool_response, dict):
        answers = tool_response.get("answers", {})
    if not answers:
        # Fallback: may contain auto-applied answers from PreToolUse updatedInput.
        # Flag so we can skip re-capture of unchanged decisions below.
        answers = tool_input.get("answers", {})
        answers_from_tool_response = False

    if not questions or not answers:
        sys.exit(0)

    # Pre-scan: parse metadata for all Fix/Defer questions (deduplicated)
    parsed: dict[str, dict | None] = {}
    for q in questions:
        if _is_fix_defer_question(q):
            q_text = q.get("question", "")
            parsed[q_text] = _parse_metadata(q_text)

    capturable = any(meta is not None for meta in parsed.values())
    if not capturable:
        sys.exit(0)  # nothing to capture — skip disk I/O

    # Find the decisions file
    dec_path = _decisions_path()
    if dec_path is None:
        sys.exit(0)  # no memory dir — can't persist

    decisions_data = _load_decisions(dec_path)
    # Use a dict keyed by fingerprint for O(1) upserts
    stored_by_fp: dict[str, dict] = {
        d["fingerprint"]: d for d in decisions_data.get("decisions", []) if "fingerprint" in d
    }

    mutated = False
    code_hash_cache: dict[tuple[str, str], str | None] = {}
    for q in questions:
        if not _is_fix_defer_question(q):
            continue

        q_text = q.get("question", "")
        meta = parsed.get(q_text)
        if meta is None:
            continue

        answer = answers.get(q_text)
        if answer is None or answer not in VALID_DECISIONS:
            continue  # discard unexpected answers

        skill = meta.get("skill", "unknown")
        fp = _fingerprint(meta["file"], meta["cat"], meta.get("line", "0"), skill)

        # Skip re-capture of auto-applied decisions (prevents timestamp refresh)
        already_stored = fp in stored_by_fp and stored_by_fp[fp].get("decision") == answer
        if not answers_from_tool_response and already_stored:
            continue

        snippet = _extract_description_snippet(q_text)
        line_val = re.sub(r"[^\d]", "", meta.get("line", "0"))[:_MAX_FIELD_LEN] or "0"
        cache_key = (meta["file"], meta.get("line", "0"))
        if cache_key not in code_hash_cache:
            code_hash_cache[cache_key] = _compute_code_hash(*cache_key)
        code_hash = code_hash_cache[cache_key]

        decision_record = {
            "fingerprint": fp,
            "file": meta["file"],
            "line": line_val,
            "category": re.sub(r"[^\w./_-]", "_", meta["cat"])[:_MAX_FIELD_LEN],
            "skill": re.sub(r"[^\w./_-]", "_", meta.get("skill", "unknown"))[:_MAX_FIELD_LEN],
            "decision": answer,
            "description_snippet": re.sub(r"[\x00-\x1f\x7f]", " ", snippet)[:80],
            "decided_at": datetime.now(UTC).isoformat(),
        }
        if code_hash is not None:
            decision_record["code_hash"] = code_hash

        stored_by_fp[fp] = decision_record  # O(1) upsert
        mutated = True

    if not mutated:
        sys.exit(0)  # no mutations — skip unnecessary write

    # Prune expired decisions on write
    cutoff = (datetime.now(UTC) - timedelta(days=_MAX_DECISION_AGE_DAYS)).isoformat()
    stored_by_fp = {fp: d for fp, d in stored_by_fp.items() if d.get("decided_at", "") >= cutoff}

    decisions_data["decisions"] = list(stored_by_fp.values())
    _save_decisions(dec_path, decisions_data)
    sys.exit(0)


# ── Entrypoint ──


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        sys.exit(0)  # passthrough on parse failure

    tool_name = data.get("tool_name", "")
    if tool_name != "AskUserQuestion":
        sys.exit(0)  # not our tool — passthrough

    hook_event = data.get("hook_event_name", "")

    if hook_event == "PreToolUse":
        _handle_pre_tool_use(data)
    elif hook_event == "PostToolUse":
        _handle_post_tool_use(data)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
