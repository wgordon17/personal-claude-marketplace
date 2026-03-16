#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["anthropic[vertex]>=0.40.0"]
# ///
"""Stop Hook LLM Evaluator -- Sonnet quality gate via Vertex AI.

Receives context JSON on stdin from stop-hook.py, calls claude-sonnet-4-6
via Vertex AI with an adaptive prompt based on trigger reasons, and returns
a pass/fail decision JSON on stdout.

Stdin schema (from stop-hook.py):
  {
    "first_user_message": str | null,
    "new_tool_calls": list[str],
    "recent_assistant_messages": list[str],
    "git_diff_stat": str | null,
    "trigger_reasons": list[str],
    "work_type": "code_config | planning | research | question | conversation | mixed"
  }

Stdout schema:
  {
    "decision": "pass" | "fail",
    "reasoning": str,
    "findings": list[str] | null   -- null on pass, specific issues on fail
  }

Exit codes:
  0 -- pass (allow stop)
  2 -- fail (block stop, Claude should continue)

Fails open (exits 0) on any infrastructure error (import, auth, timeout, parse).

Environment variables:
  ANTHROPIC_VERTEX_PROJECT_ID      -- GCP project ID (required)
  CLOUD_ML_REGION                  -- Vertex AI region (default: us-east5)
  ANTHROPIC_DEFAULT_SONNET_MODEL   -- model override (default: claude-sonnet-4-6)
"""

import json
import os
import re
import sys
from typing import NoReturn

_MAX_INPUT = 1 * 1024 * 1024  # 1 MB — context is small
# Strip Claude Code context-window suffixes like [1m] — Vertex AI doesn't accept them
_RAW_MODEL = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
_MODEL = re.sub(r"\[.*\]$", "", _RAW_MODEL)
_MAX_TOKENS = 1024
_TIMEOUT = 50  # seconds — stop-hook.py allows 60, leave buffer


def _fail_open(reason: str) -> NoReturn:
    """Emit a pass decision and exit 0 (fail-open on infrastructure errors)."""
    output = {
        "decision": "pass",
        "reasoning": f"LLM evaluator failed open: {reason}",
        "findings": None,
    }
    print(json.dumps(output))
    sys.exit(0)


def _parse_stdin() -> dict:
    """Read and parse context JSON from stdin."""
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
    except OSError as e:
        _fail_open(f"stdin read error: {e}")
    if len(raw) > _MAX_INPUT:
        _fail_open("stdin too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            _fail_open("stdin is not a JSON object")
        return data
    except (json.JSONDecodeError, ValueError) as e:
        _fail_open(f"stdin JSON parse error: {e}")


def _build_prompt(ctx: dict) -> str:
    """Build an adaptive evaluation prompt based on trigger reasons and work type."""
    first_user_msg = ctx.get("first_user_message") or "(not available)"
    tool_calls = ctx.get("new_tool_calls") or []
    recent_msgs = ctx.get("recent_assistant_messages") or []
    diff_stat = ctx.get("git_diff_stat")
    trigger_reasons = ctx.get("trigger_reasons") or []
    work_type = ctx.get("work_type", "conversation")

    # Build context section
    lines = [
        "You are a quality gate for a Claude Code session. "
        "Evaluate whether the work is complete and correct.",
        "",
        "## Session Context",
        f"**Original user request:** {first_user_msg}",
        f"**Work type:** {work_type}",
        f"**Trigger reasons:** {', '.join(trigger_reasons) if trigger_reasons else 'none'}",
        f"**Tools used this turn:** {', '.join(tool_calls) if tool_calls else 'none'}",
    ]

    if diff_stat:
        lines += [
            "",
            "## Git Changes",
            "```",
            diff_stat,
            "```",
        ]

    if recent_msgs:
        lines += ["", "## Recent Assistant Messages"]
        for i, msg in enumerate(recent_msgs[-3:], 1):
            # Truncate very long messages for prompt efficiency
            preview = msg[:800] + "..." if len(msg) > 800 else msg
            lines += [f"**Message {i}:**", preview, ""]

    # Add work-type-specific evaluation criteria
    lines += ["", "## Evaluation Criteria"]

    criteria: list[str] = []

    # Universal criteria
    criteria.append(
        "COMPLETENESS: Was every part of the original user request addressed? "
        "Not partially, not deferred without explicit user agreement."
    )
    criteria.append(
        "IDENTIFIED-BUT-UNFIXED: Did the assistant mention issues ('could be improved', "
        "'consider', 'potential issue', 'follow-up', 'out of scope') without fixing them? "
        "If yes, that is incomplete work. "
        "DEFERRAL-TO-USER is the same failure: phrases like 'should be verified', "
        "'needs to be confirmed', 'you should check', 'verify against your', "
        "'please verify', 'you may want to update' mean the assistant identified "
        "work that needs doing and punted it. If it should be verified, verify it. "
        "If it needs checking, check it. Do not defer work to the user."
    )

    if work_type in ("code_config", "mixed"):
        criteria.append(
            "CODE QUALITY: Were tests run after code changes? "
            "Are there any TODOs or FIXMEs left in modified code?"
        )
        criteria.append(
            "BRANCH SAFETY: Are changes on a feature branch (not main/master) "
            "or appropriately staged?"
        )

    if work_type in ("research", "mixed") or "research" in trigger_reasons:
        criteria.append(
            "VERIFICATION: Were factual claims, API schemas, or technical recommendations "
            "backed by primary source research (WebSearch/WebFetch), or stated from memory? "
            "Flag unverified specific claims."
        )

    if work_type in ("question",) or "completion_claim" in trigger_reasons:
        criteria.append(
            "UNCERTAINTY: Were uncertainties flagged with 'I don't know' rather than "
            "confident guesses? Were limitations acknowledged?"
        )

    if "planning" in trigger_reasons:
        criteria.append(
            "PLANNING ARTIFACTS: Were plan files written to hack/plans/? "
            "Was TODO.md updated with new tasks?"
        )

    if "subagent" in trigger_reasons:
        criteria.append(
            "SUBAGENT RESULTS: Were subagent results verified? "
            "Did the orchestrating turn confirm the work was complete?"
        )

    for i, criterion in enumerate(criteria, 1):
        lines.append(f"{i}. {criterion}")

    lines += [
        "",
        "## Decision",
        "Respond with ONLY a JSON object — no prose, no markdown fences:",
        (
            '{"decision": "pass" | "fail", "reasoning": "brief explanation", '
            '"findings": ["issue1", "issue2"] | null}'
        ),
        "",
        "- Set decision=pass if ALL applicable criteria are satisfied.",
        "- Set decision=fail if ANY criterion is clearly violated. "
        "findings must list specific, actionable issues.",
        "- findings must be null when decision=pass.",
        "- Be precise. Do not fail for cosmetic issues or minor style choices.",
        "- When in doubt, pass. The goal is catching genuinely incomplete or incorrect work.",
    ]

    return "\n".join(lines)


def _call_vertex(prompt: str) -> dict:
    """Call claude-sonnet-4-6 via Vertex AI. Returns parsed response dict."""
    try:
        from anthropic import AnthropicVertex
    except ImportError as e:
        _fail_open(f"anthropic[vertex] not available: {e}")

    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
    region = os.environ.get("CLOUD_ML_REGION", "us-east5")

    if not project_id:
        _fail_open("ANTHROPIC_VERTEX_PROJECT_ID env var not set")

    try:
        client = AnthropicVertex(project_id=project_id, region=region)
        message = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            timeout=_TIMEOUT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        _fail_open(f"Vertex AI call failed: {type(e).__name__}: {e}")

    # Extract text content
    text = ""
    for block in message.content:
        if hasattr(block, "text"):
            text = block.text.strip()
            break

    if not text:
        _fail_open("empty response from Vertex AI")

    # Parse JSON — model may occasionally wrap in fences, strip them
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.startswith("```")).strip()

    try:
        result = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        _fail_open(f"model returned non-JSON: {text[:200]}")

    if not isinstance(result, dict):
        _fail_open("model response is not a JSON object")

    return result


def _validate_response(result: dict) -> tuple[str, str, list[str] | None]:
    """Validate and extract fields from the model response.

    Returns (decision, reasoning, findings).
    Falls back to pass on malformed responses.
    """
    decision = result.get("decision", "")
    if decision not in ("pass", "fail"):
        # Malformed — fail-open
        _fail_open(f"unexpected decision value: {decision!r}")

    reasoning = result.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""

    findings = result.get("findings")
    if decision == "pass":
        findings = None
    elif not isinstance(findings, list):
        findings = [reasoning] if reasoning else ["Quality check failed."]
    else:
        # Filter to non-empty strings
        findings = [str(f) for f in findings if str(f).strip()]
        if not findings:
            findings = [reasoning] if reasoning else ["Quality check failed."]

    return decision, reasoning, findings


def main() -> None:
    ctx = _parse_stdin()
    prompt = _build_prompt(ctx)
    raw_result = _call_vertex(prompt)
    decision, reasoning, findings = _validate_response(raw_result)

    output = {
        "decision": decision,
        "reasoning": reasoning,
        "findings": findings,
    }
    print(json.dumps(output))

    if decision == "fail":
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
