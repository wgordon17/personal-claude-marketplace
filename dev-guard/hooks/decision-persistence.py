#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# ///
"""Decision Persistence Hook — remembers Fix/Defer decisions across sessions.

PreToolUse: checks stored decisions and auto-answers AskUserQuestion via updatedInput.
PostToolUse: captures Fix/Defer decisions to {memory_dir}/review-decisions.json.

Spike implementation — validates the updatedInput + answers mechanism for
auto-resolving previously-decided review findings.
"""

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# ── Constants ──

VALID_DECISIONS: frozenset[str] = frozenset({"Fix", "Defer"})
_MAX_FIELD_LEN = 512

METADATA_PREFIX = "▸dp:"
DECISIONS_FILENAME = "review-decisions.json"
MEMORY_DIR_CANDIDATES = ("hack", ".local", "scratch", ".dev")


# ── Memory directory detection ──


def _find_memory_dir() -> Path | None:
    """Find the project memory directory (hack/, .local/, scratch/, .dev/).

    Walks from CWD up to git root looking for a memory dir candidate.
    """
    cwd = Path.cwd()
    # Find git root
    git_root = cwd
    while git_root != git_root.parent:
        if (git_root / ".git").exists():
            break
        git_root = git_root.parent
    else:
        git_root = cwd  # fallback to cwd if no git root

    for candidate in MEMORY_DIR_CANDIDATES:
        candidate_path = git_root / candidate
        if candidate_path.is_dir():
            return candidate_path
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
    except OSError:
        tmp.unlink(missing_ok=True)


def _fingerprint(file: str, category: str, line: str = "0") -> str:
    """Create a stable fingerprint for a finding.

    Uses file + category + line_window (rounded to nearest 10 lines).
    All three fields come from deterministic ▸dp: metadata, not LLM prose.
    The line_window groups nearby lines so minor line shifts don't
    invalidate a decision (e.g., line 42 and 47 both map to window 40).

    Note: `category` comes from ▸dp:cat= which carries different semantics
    per skill — reviewer name (pr-review, plan-review, quality-gate) vs
    issue type (map-reduce, file-audit). Decisions are scoped per-skill
    so cross-skill collisions don't occur in practice.
    """
    line_window = (int(line) // 10) * 10 if line.isdigit() else 0
    normalized = f"{file}|{category}|{line_window}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Metadata parsing ──


def _sanitize_path_field(value: str) -> str:
    """Normalize and bound a file path field from metadata.

    Rejects path traversal sequences and absolute paths. Strips
    non-path characters and enforces a length limit.
    """
    if ".." in value or value.startswith("/"):
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

    _, _, meta_str = question_text.partition(METADATA_PREFIX)
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
    # Remove metadata suffix
    text = question_text.split(METADATA_PREFIX)[0].strip()
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

    # Only process Fix/Defer questions
    fix_defer_questions = [q for q in questions if _is_fix_defer_question(q)]
    if not fix_defer_questions:
        sys.exit(0)  # passthrough — not our kind of question

    # Parse metadata for all Fix/Defer questions (single pass, cached)
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
    stored = {
        d["fingerprint"]: d for d in decisions_data.get("decisions", []) if "fingerprint" in d
    }

    if not stored:
        sys.exit(0)  # no prior decisions — passthrough

    # Try to match each Fix/Defer question
    answers = {}
    all_matched = True

    for q in fix_defer_questions:
        q_text = q.get("question", "")
        meta = parsed[q_text]
        fp = _fingerprint(meta["file"], meta["cat"], meta.get("line", "0"))

        if fp in stored:
            prior = stored[fp]
            decision = prior["decision"]
            if decision not in VALID_DECISIONS:
                all_matched = False
                break  # corrupted/unexpected stored value — re-ask
            answers[q_text] = decision
        else:
            all_matched = False
            break

    if not all_matched:
        # Can't auto-answer partial batches reliably — passthrough for all
        # TODO(v2): test whether partial answers work with AskUserQuestion
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
    # Answers come from tool_input.answers (populated by user interaction)
    # or from tool_response depending on Claude Code version
    answers = tool_input.get("answers", {})
    if not answers and isinstance(tool_response, dict):
        answers = tool_response.get("answers", {})

    if not questions or not answers:
        sys.exit(0)

    # Pre-scan: any capturable Fix/Defer questions with metadata?
    capturable = any(
        _is_fix_defer_question(q) and _parse_metadata(q.get("question", "")) is not None
        for q in questions
    )
    if not capturable:
        sys.exit(0)  # nothing to capture — skip disk I/O

    # Find the decisions file
    dec_path = _decisions_path()
    if dec_path is None:
        sys.exit(0)  # no memory dir — can't persist

    decisions_data = _load_decisions(dec_path)
    stored = decisions_data.get("decisions", [])
    existing_fps = {d["fingerprint"] for d in stored if "fingerprint" in d}

    new_decisions = []
    mutated = False
    for q in questions:
        if not _is_fix_defer_question(q):
            continue

        q_text = q.get("question", "")
        meta = _parse_metadata(q_text)
        if meta is None:
            continue

        answer = answers.get(q_text)
        if answer is None or answer not in VALID_DECISIONS:
            continue  # discard unexpected answers

        fp = _fingerprint(meta["file"], meta["cat"], meta.get("line", "0"))
        snippet = _extract_description_snippet(q_text)

        decision_record = {
            "fingerprint": fp,
            "file": meta["file"],
            "line": meta.get("line"),
            "category": meta["cat"][:_MAX_FIELD_LEN],
            "skill": meta.get("skill", "unknown")[:_MAX_FIELD_LEN],
            "decision": answer,
            "description_snippet": snippet[:80],
            "decided_at": datetime.now(UTC).isoformat(),
        }

        if fp in existing_fps:
            stored = [d if d.get("fingerprint") != fp else decision_record for d in stored]
        else:
            new_decisions.append(decision_record)
            existing_fps.add(fp)
        mutated = True

    if not mutated:
        sys.exit(0)  # no mutations — skip unnecessary write

    if new_decisions:
        stored.extend(new_decisions)

    decisions_data["decisions"] = stored
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
