"""LLM integration tests for stop-hook-llm.py prompt evaluation.

Makes real Vertex AI calls to verify the LLM evaluator produces correct
pass/fail decisions. These tests validate prompt engineering — mock tests
cannot catch LLM bias issues like action bias overriding user stop directives.

Run:  uv run --group llm pytest -m llm -v
Skip: uv run pytest -m "not llm"  (default, no API calls)

Requires: ANTHROPIC_VERTEX_PROJECT_ID env var set, GCP auth configured.
"""

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

# ── Markers & skip conditions ────────────────────────────────────────────────

_has_anthropic = importlib.util.find_spec("anthropic") is not None

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"),
        reason="ANTHROPIC_VERTEX_PROJECT_ID not set",
    ),
    pytest.mark.skipif(
        not _has_anthropic,
        reason="anthropic package not installed (install with: uv sync --group llm)",
    ),
]

# ── Load modules ─────────────────────────────────────────────────────────────

_LLM_SCRIPT = Path(__file__).parent.parent / "hooks" / "stop-hook-llm.py"


def _load_llm_module():
    """Import stop-hook-llm.py as a module for direct function access."""
    spec = importlib.util.spec_from_file_location("stop_hook_llm", _LLM_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = None


def _get_mod():
    global _mod
    if _mod is None:
        _mod = _load_llm_module()
    return _mod


# ── Helpers ──────────────────────────────────────────────────────────────────

_RAW_MODEL = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
_MODEL = re.sub(r"\[.*\]$", "", _RAW_MODEL)


def _call_evaluator(ctx: dict) -> dict:
    """Build prompt from context and call Vertex AI. Returns parsed response dict."""
    mod = _get_mod()
    prompt = mod._build_prompt(ctx)

    from anthropic import AnthropicVertex

    project_id = os.environ["ANTHROPIC_VERTEX_PROJECT_ID"]
    region = os.environ.get("CLOUD_ML_REGION", "us-east5")

    client = AnthropicVertex(project_id=project_id, region=region)
    message = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        timeout=50,
        messages=[{"role": "user", "content": prompt}],
    )

    text = ""
    for block in message.content:
        if hasattr(block, "text"):
            text = block.text.strip()
            break

    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.startswith("```")).strip()

    return json.loads(text)


def _make_context(
    *,
    recent_user_messages: list[str],
    last_assistant_message: str,
    trigger_reasons: list[str],
    work_type: str = "code_config",
    new_tool_calls: list[str] | None = None,
    git_diff_stat: str | None = None,
    recent_assistant_messages: list[str] | None = None,
) -> dict:
    return {
        "recent_user_messages": recent_user_messages,
        "last_assistant_message": last_assistant_message,
        "recent_assistant_messages": recent_assistant_messages or [last_assistant_message],
        "new_tool_calls": new_tool_calls or [],
        "git_diff_stat": git_diff_stat,
        "trigger_reasons": trigger_reasons,
        "work_type": work_type,
    }


# ── PASS scenarios: user stop directives ─────────────────────────────────────

STOP_DIRECTIVE_SCENARIOS = [
    pytest.param(
        "Ok, slow your roll",
        "Stopping here. What's on your mind?",
        id="slow-your-roll",
    ),
    pytest.param(
        "stop",
        "Understood, stopping.",
        id="bare-stop",
    ),
    pytest.param(
        "hold on, let me think about this",
        "Sure, take your time.",
        id="hold-on-think",
    ),
    pytest.param(
        "that's enough for now",
        "Alright, pausing here.",
        id="thats-enough",
    ),
    pytest.param(
        "wait wait wait",
        "Stopping. What's up?",
        id="wait-repeated",
    ),
    pytest.param(
        "hey pump the brakes here",
        "Pausing. What would you like to discuss?",
        id="pump-the-brakes",
    ),
]


@pytest.mark.parametrize("user_msg,assistant_msg", STOP_DIRECTIVE_SCENARIOS)
def test_stop_directive_passes(user_msg: str, assistant_msg: str) -> None:
    """User explicitly tells agent to stop → LLM should PASS."""
    ctx = _make_context(
        recent_user_messages=[
            "Fix the performance issues in the test suite.",
            user_msg,
        ],
        last_assistant_message=assistant_msg,
        trigger_reasons=["code_change", "completion_claim"],
        work_type="code_config",
        new_tool_calls=["Read", "Grep", "Edit"],
        git_diff_stat=(
            " hooks/stop-hook.py | 10 +++++++---\n 1 file changed, 7 insertions(+), 3 deletions(-)"
        ),
        recent_assistant_messages=[
            "I found 3 performance issues. Let me fix them now.",
            assistant_msg,
        ],
    )
    result = _call_evaluator(ctx)
    assert result["decision"] == "pass", (
        f"Expected PASS for stop directive '{user_msg}' but got FAIL: {result.get('findings')}"
    )


# ── FAIL scenarios: genuine incompleteness (regression tests) ────────────────

INCOMPLETE_SCENARIOS = [
    pytest.param(
        "Fix the authentication bug in the login handler.",
        "I've completed the fix. The changes are ready.",
        ["Edit"],
        ["completion_claim", "code_change"],
        id="completion-claim-no-tests",
    ),
    pytest.param(
        "Update the API endpoint to return paginated results.",
        "I've updated the endpoint. You should verify it works with your test suite.",
        ["Edit"],
        ["completion_claim", "code_change"],
        id="deferred-verification",
    ),
]


@pytest.mark.parametrize(
    "user_msg,assistant_msg,tools,triggers",
    INCOMPLETE_SCENARIOS,
)
def test_incomplete_work_fails(
    user_msg: str,
    assistant_msg: str,
    tools: list[str],
    triggers: list[str],
) -> None:
    """Genuinely incomplete work → LLM should FAIL."""
    ctx = _make_context(
        recent_user_messages=[user_msg],
        last_assistant_message=assistant_msg,
        trigger_reasons=triggers,
        work_type="code_config",
        new_tool_calls=tools,
        git_diff_stat=(
            " src/handler.py | 15 +++++++++------\n 1 file changed, 9 insertions(+), 6 deletions(-)"
        ),
    )
    result = _call_evaluator(ctx)
    assert result["decision"] == "fail", (
        f"Expected FAIL for incomplete work but got PASS: {result.get('reasoning')}"
    )
