"""LLM integration tests for stop-hook-llm.py prompt evaluation.

Makes real Vertex AI calls to verify the LLM evaluator produces correct
pass/fail decisions. These tests validate prompt engineering — mock tests
cannot catch LLM bias issues like action bias overriding user stop directives.

Run:  make test-llm
Skip: make test  (excludes llm marker by default)

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

# Shared diff stats for realistic context
_CODE_DIFF = (
    " src/handler.py | 15 +++++++++------\n 1 file changed, 9 insertions(+), 6 deletions(-)"
)
_HOOK_DIFF = " hooks/stop-hook.py | 10 +++++++---\n 1 file changed, 7 insertions(+), 3 deletions(-)"


def _call_evaluator(ctx: dict) -> dict:
    """Build prompt from context and call Vertex AI. Returns parsed response dict."""
    mod = _get_mod()
    prompt = mod._build_prompt(ctx)

    from anthropic import AnthropicVertex

    project_id = os.environ["ANTHROPIC_VERTEX_PROJECT_ID"]
    region = os.environ.get("CLOUD_ML_REGION", "global")

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


def _ctx(
    *,
    user_msgs: list[str],
    assistant_msg: str,
    triggers: list[str],
    work_type: str = "code_config",
    tools: list[str] | None = None,
    diff: str | None = None,
    prior_assistant_msgs: list[str] | None = None,
) -> dict:
    return {
        "recent_user_messages": user_msgs,
        "last_assistant_message": assistant_msg,
        "recent_assistant_messages": (prior_assistant_msgs or []) + [assistant_msg],
        "new_tool_calls": tools or [],
        "git_diff_stat": diff,
        "trigger_reasons": triggers,
        "work_type": work_type,
    }


def _assert_pass(result: dict, scenario: str) -> None:
    assert result["decision"] == "pass", (
        f"Expected PASS for '{scenario}' but got FAIL: {result.get('findings')}"
    )


def _assert_fail(result: dict, scenario: str) -> None:
    assert result["decision"] == "fail", (
        f"Expected FAIL for '{scenario}' but got PASS: {result.get('reasoning')}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# PASS scenarios: user stop directives
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg",
    [
        # Direct stop commands
        pytest.param("stop", "Understood, stopping.", id="bare-stop"),
        pytest.param("STOP", "Stopping.", id="caps-stop"),
        pytest.param("stop working", "Stopped.", id="stop-working"),
        # Colloquial / slang
        pytest.param(
            "Ok, slow your roll",
            "Stopping here. What's on your mind?",
            id="slow-your-roll",
        ),
        pytest.param("chill", "Pausing.", id="chill"),
        pytest.param(
            "hey pump the brakes here",
            "Pausing. What would you like to discuss?",
            id="pump-the-brakes",
        ),
        pytest.param("whoa whoa whoa", "Stopping.", id="whoa-repeated"),
        pytest.param("easy there", "Pausing.", id="easy-there"),
        # Polite / conversational
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
            "ok thanks, I need to step away for a bit",
            "No problem. I'll be here when you're ready.",
            id="stepping-away",
        ),
        pytest.param(
            "let me digest this first before we continue",
            "Take your time.",
            id="digest-first",
        ),
        # Implicit stop — user wants to pause and redirect
        pytest.param(
            "actually hold on, I just realized something",
            "Sure, what did you realize?",
            id="hold-on-realized",
        ),
        pytest.param(
            "I need to think about this differently",
            "Of course. Let me know when you're ready.",
            id="think-differently",
        ),
    ],
)
def test_stop_directive_passes(user_msg: str, assistant_msg: str) -> None:
    """User explicitly tells agent to stop → LLM should PASS."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[
                "Fix the performance issues in the test suite.",
                user_msg,
            ],
            assistant_msg=assistant_msg,
            triggers=["code_change", "completion_claim"],
            tools=["Read", "Grep", "Edit"],
            diff=_HOOK_DIFF,
            prior_assistant_msgs=[
                "I found 3 performance issues. Let me fix them now.",
            ],
        )
    )
    _assert_pass(result, user_msg)


# ═════════════════════════════════════════════════════════════════════════════
# PASS scenarios: NOT stop directives — redirections that mention stop words
# These use "stop/wait/hold" vocabulary but are task instructions, not pauses.
# The LLM should still PASS because the assistant correctly follows the
# redirection (stops current work and acknowledges the new direction).
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg, tools",
    [
        # User corrects tool usage — assistant already switched (completed action)
        pytest.param(
            "stop using sed and use the Edit tool instead",
            "Right, switched to Edit. Applied the refactor and tests pass.",
            ["Edit", "Bash"],
            id="stop-using-sed",
        ),
        # User redirects — assistant already fixed the import
        pytest.param(
            "hold on, fix the import error first",
            "Good catch. Fixed the import in `src/handler.py:3`.",
            ["Read", "Edit"],
            id="hold-on-fix-import",
        ),
    ],
)
def test_redirection_with_stop_words_passes(
    user_msg: str, assistant_msg: str, tools: list[str]
) -> None:
    """Redirections that use stop vocabulary — assistant completed the redirection."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[
                "Refactor the authentication module.",
                user_msg,
            ],
            assistant_msg=assistant_msg,
            triggers=["code_change"],
            tools=tools,
            diff=_CODE_DIFF,
            prior_assistant_msgs=[
                "I'll refactor the auth module now.",
            ],
        )
    )
    _assert_pass(result, user_msg)


# ═════════════════════════════════════════════════════════════════════════════
# PASS scenarios: legitimate completion
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg, tools",
    [
        pytest.param(
            "Fix the null pointer in the handler.",
            "Fixed the null check in `src/handler.py:42`. Tests pass.",
            ["Read", "Edit", "Bash"],
            id="fix-with-tests-run",
        ),
        pytest.param(
            "What does the Read tool do?",
            "It reads files from the local filesystem.",
            [],
            id="factual-answer",
        ),
    ],
)
def test_legitimate_completion_passes(user_msg: str, assistant_msg: str, tools: list[str]) -> None:
    """Work genuinely completed or question answered → PASS."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[user_msg],
            assistant_msg=assistant_msg,
            triggers=["completion_claim", "code_change"] if tools else ["research"],
            work_type="code_config" if tools else "question",
            tools=tools,
            diff=_CODE_DIFF if tools else None,
        )
    )
    _assert_pass(result, user_msg)


# ═════════════════════════════════════════════════════════════════════════════
# FAIL scenarios: incomplete work
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg, tools, triggers",
    [
        # Completion claim but no tests run
        pytest.param(
            "Fix the authentication bug in the login handler.",
            "I've completed the fix. The changes are ready.",
            ["Edit"],
            ["completion_claim", "code_change"],
            id="completion-claim-no-tests",
        ),
        # Deferred work — punting verification to user
        pytest.param(
            "Update the API endpoint to return paginated results.",
            ("I've updated the endpoint. You should verify it works with your test suite."),
            ["Edit"],
            ["completion_claim", "code_change"],
            id="deferred-verification-to-user",
        ),
        # Partial completion with remaining items acknowledged
        pytest.param(
            "Implement the caching layer for the API.",
            (
                "I've added the cache middleware. "
                "I still need to add cache invalidation and TTL support."
            ),
            ["Edit", "Write"],
            ["completion_claim", "code_change"],
            id="partial-completion-remaining-items",
        ),
        # "Please verify" punt
        pytest.param(
            "Add input validation to the form handler.",
            ("I've added validation. Please verify the regex patterns match your requirements."),
            ["Edit"],
            ["completion_claim", "code_change"],
            id="please-verify-punt",
        ),
    ],
)
def test_incomplete_work_fails(
    user_msg: str,
    assistant_msg: str,
    tools: list[str],
    triggers: list[str],
) -> None:
    """Genuinely incomplete work → LLM should FAIL."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[user_msg],
            assistant_msg=assistant_msg,
            triggers=triggers,
            tools=tools,
            diff=_CODE_DIFF,
        )
    )
    _assert_fail(result, assistant_msg[:60])


# ═════════════════════════════════════════════════════════════════════════════
# FAIL scenarios: stop word + continuation directive = redirection, not stop
# The assistant should follow the redirection but stopped and deferred → FAIL.
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg",
    [
        pytest.param(
            "hold on, tell me what's going on, then get back to work",
            "I was comparing the two plugins but I'm not sure what to focus on. "
            "Can you clarify what you'd like me to investigate?",
            id="hold-on-then-get-back-to-work",
        ),
        pytest.param(
            "wait, explain your approach, then continue with the implementation",
            "I was going to refactor the module. Should I proceed?",
            id="wait-explain-then-continue",
        ),
    ],
)
def test_stop_with_continuation_directive_fails(user_msg: str, assistant_msg: str) -> None:
    """Stop word + continuation directive — assistant deferred instead of continuing → FAIL."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[
                "Investigate the safety-net vs dev-guard overlap.",
                user_msg,
            ],
            assistant_msg=assistant_msg,
            triggers=["completion_claim"],
            work_type="code_config",
            tools=["Read", "Grep"],
            prior_assistant_msgs=[
                "I'll compare the two plugins now.",
            ],
        )
    )
    _assert_fail(result, user_msg[:60])


# ═════════════════════════════════════════════════════════════════════════════
# FAIL scenarios: stop words in technical context (NOT stop directives)
# These messages use "stop/wait/pause" as technical vocabulary, not directives.
# The assistant should have done work but didn't → FAIL.
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg",
    [
        pytest.param(
            "How long is that wait supposed to run for before it stops?",
            "I'm not sure, let me check.",
            id="technical-wait-stops-question",
        ),
        pytest.param(
            "The pause between retries seems too long, can you fix it?",
            "I've looked at it but haven't made changes yet.",
            id="technical-pause-no-action",
        ),
    ],
)
def test_technical_stop_vocabulary_not_treated_as_directive(
    user_msg: str, assistant_msg: str
) -> None:
    """Stop words used as technical vocabulary — work not done → FAIL."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[user_msg],
            assistant_msg=assistant_msg,
            triggers=["action_requested_no_tools"],
            work_type="code_config",
            tools=["Read"],
        )
    )
    _assert_fail(result, user_msg[:60])


# ═════════════════════════════════════════════════════════════════════════════
# FAIL scenarios: negation — user says "don't stop" but work NOT done
# The assistant should continue working; if it didn't do the work → FAIL.
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "user_msg, assistant_msg",
    [
        pytest.param(
            "don't stop, finish it",
            "I've looked at it but haven't made changes yet.",
            id="dont-stop-finish",
        ),
        pytest.param(
            "no wait, keep going",
            "I'm not sure what to do next.",
            id="no-wait-keep-going",
        ),
        pytest.param(
            "actually no, keep working on the tests",
            "I've paused for now.",
            id="actually-no-keep-working",
        ),
    ],
)
def test_negation_of_stop_not_treated_as_directive(user_msg: str, assistant_msg: str) -> None:
    """Negated stop words — user wants work to continue, but it wasn't done → FAIL."""
    result = _call_evaluator(
        _ctx(
            user_msgs=[
                "Fix the performance issues in the test suite.",
                user_msg,
            ],
            assistant_msg=assistant_msg,
            triggers=["action_requested_no_tools"],
            work_type="code_config",
            tools=["Read"],
            prior_assistant_msgs=[
                "I found 3 performance issues. Let me fix them now.",
            ],
        )
    )
    _assert_fail(result, user_msg[:60])
