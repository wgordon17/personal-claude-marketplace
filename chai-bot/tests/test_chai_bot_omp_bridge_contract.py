"""Contract-level tests for chai-bot's OMP bridge (chai-bot/omp-extension.ts).

Mirrors dev-guard/tests/test_omp_bridge_contract.py's approach: pins the
exact JSON payload SHAPES omp-extension.ts's `tool_call`/`tool_result`
handlers construct and send to hooks/metrics.py on stdin, since those shapes
are hand-written in TypeScript with no shared schema against the Python
side -- a silent shape drift on either side would otherwise go undetected
until a live OMP session broke.

Two things are covered here:

  1. Python-side contract (TestOmpBridgePreToolUsePayloadShape /
     TestOmpBridgePostToolUsePayloadShape): metrics.py must accept the exact
     payload shape the bridge's runMetrics() constructs --
     {session_id, tool_use_id, hook_event_name, tool_name, tool_input[,
     tool_response]} -- with tool_name already re-encoded into the
     Claude-Code-shaped "mcp__plugin_chai-bot_ship-help__ask_persona" form
     (per reencodeMcpToolName()), and correctly fire the ask_persona-scoped
     permission allow + log a metrics row. tool_response for PostToolUse
     matches toolResponseForMetrics()'s exact {content: [{type, text}],
     isError} shape.

  2. TS-side reencodeMcpToolName() round-trip (TestReencodeMcpToolNameRoundTrip):
     extracts the ACTUAL reencodeMcpToolName()/isChaiBotMcpTool() source --
     a verbatim substring of chai-bot/omp-extension.ts, not reimplemented in
     Python -- and executes it under `bun`, confirming OMP's live tool-name
     form for chai-bot's ask_persona tool
     ("mcp__chai_bot_ship_help_ask_persona") re-encodes to exactly
     "mcp__plugin_chai-bot_ship-help__ask_persona", which is then fed
     straight into the real metrics.py to confirm the round trip parses to
     'ask_persona' and fires the ask_persona-scoped allow.

     This class is skipped when `bun` is not on PATH (this repo's CI has no
     JS runtime configured -- see .github/workflows/ci.yml). In that case
     only the Python-side contract above runs; the re-encoded name string is
     still exercised there via the hardcoded ASK_PERSONA_REENCODED constant,
     but the TypeScript reencodeMcpToolName() logic itself is NOT executed
     in CI. This mirrors the identical trade-off dev-guard's own contract
     test file documents for its MCP server re-encoding (see that file's
     TestMcpReencodingPayloadShape docstrings) -- NOT live-verified against
     a real OMP install either way (chai-bot's OMP server name mapping is
     unconfirmed, per omp-extension.ts's own module docstring).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from _helpers import REPO_ROOT, _events, run_metrics

OMP_EXTENSION = REPO_ROOT / "chai-bot" / "omp-extension.ts"

# The Claude-Code-shaped MCP tool name reencodeMcpToolName() is expected to
# produce from OMP's live tool-name form for chai-bot's ask_persona tool --
# traced from omp-extension.ts's CHAI_BOT_OMP_SERVER / CHAI_BOT_CLAUDE_CODE_SERVER
# constants and reencodeMcpToolName()'s body (not reconstructed from memory).
ASK_PERSONA_REENCODED = "mcp__plugin_chai-bot_ship-help__ask_persona"
ASK_PERSONA_OMP_NATIVE = "mcp__chai_bot_ship_help_ask_persona"
SUBMIT_FEEDBACK_REENCODED = "mcp__plugin_chai-bot_ship-help__submit_feedback"


class TestOmpBridgePreToolUsePayloadShape:
    """The bridge's `tool_call` handler (omp-extension.ts's runMetrics() call
    site) sends exactly: {session_id, tool_use_id,
    hook_event_name: 'PreToolUse', tool_name: <reencoded>, tool_input}."""

    def test_ask_persona_payload_fires_allow_and_logs(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "session_id": "omp-sess-1",
                "tool_use_id": "omp-tc-1",
                "hook_event_name": "PreToolUse",
                "tool_name": ASK_PERSONA_REENCODED,
                "tool_input": {"question": "what is OSAC"},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"
        assert "additionalContext" in output

        rows = _events(db_path)
        assert len(rows) == 1
        _, session_id, tool_use_id, category, _, action, command, _ = rows[0]
        assert session_id == "omp-sess-1"
        assert tool_use_id == "omp-tc-1"
        assert category == "chai-bot"
        assert action == "pre"
        assert command == ASK_PERSONA_REENCODED

    def test_submit_feedback_payload_logs_but_no_permission_decision(self, tmp_path):
        """A non-ask_persona chai-bot tool under the same OMP-shaped payload:
        still logged, but no permissionDecision -- same ask_persona-only
        scoping rule the Claude-Code-native path enforces."""
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "session_id": "omp-sess-1",
                "tool_use_id": "omp-tc-2",
                "hook_event_name": "PreToolUse",
                "tool_name": SUBMIT_FEEDBACK_REENCODED,
                "tool_input": {},
            },
            db_path,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        rows = _events(db_path)
        assert len(rows) == 1
        assert rows[0][6] == SUBMIT_FEEDBACK_REENCODED


class TestOmpBridgePostToolUsePayloadShape:
    """The bridge's `tool_result` handler sends: {session_id, tool_use_id,
    hook_event_name: 'PostToolUse', tool_name: <reencoded>, tool_input,
    tool_response: toolResponseForMetrics(event)}, where
    toolResponseForMetrics() builds {content: [{type: 'text', text}],
    isError} from OMP's ToolResultEvent text blocks."""

    def test_post_payload_shape_logs_latency_and_size(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "session_id": "omp-sess-1",
                "tool_use_id": "omp-tc-3",
                "hook_event_name": "PreToolUse",
                "tool_name": ASK_PERSONA_REENCODED,
                "tool_input": {"question": "what is OSAC"},
            },
            db_path,
        )
        result = run_metrics(
            {
                "session_id": "omp-sess-1",
                "tool_use_id": "omp-tc-3",
                "hook_event_name": "PostToolUse",
                "tool_name": ASK_PERSONA_REENCODED,
                "tool_input": {"question": "what is OSAC"},
                "tool_response": {
                    "content": [{"type": "text", "text": "OSAC is a team."}],
                    "isError": False,
                },
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 2
        detail = json.loads(rows[1][7])
        assert detail["response_size"] == len("OSAC is a team.")
        assert isinstance(detail["latency_ms"], int | float)


def _bun_available() -> bool:
    return shutil.which("bun") is not None


@pytest.mark.skipif(
    not _bun_available(),
    reason="bun not on PATH -- TS-side reencodeMcpToolName() not exercised here "
    "(this repo's CI has no JS runtime configured); the Python-side contract "
    "tests above still cover metrics.py's acceptance of the reencoded name",
)
class TestReencodeMcpToolNameRoundTrip:
    """Executes the ACTUAL reencodeMcpToolName()/isChaiBotMcpTool() source,
    extracted verbatim from omp-extension.ts (not reimplemented in Python),
    under `bun`. Confirms the OMP-native tool name for chai-bot's ask_persona
    tool re-encodes to exactly the string metrics.py's _tool_func_name()
    parses to 'ask_persona' -- the value gating the ask_persona-scoped
    permission allow.

    NOT live-verified against a real OMP install (same accepted caveat as
    omp-extension.ts's own module comment on CHAI_BOT_OMP_SERVER): this only
    pins that the function AS WRITTEN produces the expected output, not that
    OMP's real live tool-name form for chai-bot actually matches
    ASK_PERSONA_OMP_NATIVE.
    """

    @staticmethod
    def _extract_reencode_source() -> str:
        src = OMP_EXTENSION.read_text()
        start = src.index("const CHAI_BOT_OMP_SERVER")
        divider = "\n// " + ("─" * 3)
        end = src.index(divider, start)
        return src[start:end]

    def _run_reencode(self, tmp_path: Path, omp_tool_name: str) -> str:
        snippet = self._extract_reencode_source()
        runner = tmp_path / "reencode_runner.ts"
        runner.write_text(snippet + "\nconsole.log(reencodeMcpToolName(process.argv[2]));\n")
        result = subprocess.run(
            ["bun", "run", str(runner), omp_tool_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def test_ask_persona_omp_native_name_reencodes_to_claude_code_form(self, tmp_path):
        reencoded = self._run_reencode(tmp_path, ASK_PERSONA_OMP_NATIVE)
        assert reencoded == ASK_PERSONA_REENCODED

    def test_reencoded_name_round_trips_through_real_metrics_py_to_ask_persona(self, tmp_path):
        """End-to-end round trip: bun's REAL reencodeMcpToolName() output is
        fed directly into the REAL metrics.py, and the ask_persona-scoped
        allow fires -- confirming _tool_func_name()'s
        `tool_name.split('__')[-1]` correctly parses the bridge's actual
        (not hardcoded) output."""
        reencoded = self._run_reencode(tmp_path, ASK_PERSONA_OMP_NATIVE)
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "session_id": "omp-sess-2",
                "tool_use_id": "omp-tc-4",
                "hook_event_name": "PreToolUse",
                "tool_name": reencoded,
                "tool_input": {},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"

    def _run_is_chai_bot_mcp_tool(self, tmp_path: Path, omp_tool_name: str) -> bool:
        """Execute the ACTUAL isChaiBotMcpTool() source (extracted verbatim from
        omp-extension.ts alongside reencodeMcpToolName, via the same
        _extract_reencode_source() used above) under bun, returning its boolean
        verdict for one OMP tool name. This is the bridge's real chai-bot-tool
        boundary gate -- the check that decides whether metrics/advisory dispatch
        fires at all -- which the reencode round-trip tests above never exercise.
        """
        snippet = self._extract_reencode_source()
        runner = tmp_path / "is_chai_bot_runner.ts"
        runner.write_text(snippet + "\nconsole.log(isChaiBotMcpTool(process.argv[2]));\n")
        result = subprocess.run(
            ["bun", "run", str(runner), omp_tool_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip() == "true"

    @pytest.mark.parametrize(
        ("omp_tool_name", "expected"),
        [
            # Exact server key, no tool suffix -> `rest === CHAI_BOT_OMP_SERVER` branch.
            ("mcp__chai_bot_ship_help", True),
            # Server key + tool suffix -> the `startsWith(`${server}_`)` branch.
            ("mcp__chai_bot_ship_help_ask_persona", True),
            ("mcp__chai_bot_ship_help_submit_feedback", True),
            # A DIFFERENT MCP server must not match.
            ("mcp__github_mcp_github_list_issues", False),
            # Non-mcp__ tool names are never chai-bot's.
            ("ask_persona", False),
            ("bash", False),
            # Prefix near-miss: shares chai_bot_ship_help's leading chars but has
            # no `_` boundary -> must NOT match (guards against a hypothetical
            # sibling server whose sanitized name starts with this prefix).
            ("mcp__chai_bot_ship_helpX", False),
            # Shorter than the server key.
            ("mcp__chai_bot_ship_hel", False),
        ],
    )
    def test_is_chai_bot_mcp_tool_boundary_cases(self, tmp_path, omp_tool_name, expected):
        """Exercises the bridge's isChaiBotMcpTool() gate directly under bun --
        the one piece of real, OMP-runtime-independent bridge logic the existing
        reencode round-trip never runs. The Bun.spawn/pi.on dispatch handlers,
        runScript, parseHookOutput, and toolResponseForMetrics need a live OMP
        runtime and remain covered only by the manual checklist (same accepted
        trade-off dev-guard documents in OMP-COMPAT.md's 'Manual verification
        checklist' section)."""
        assert self._run_is_chai_bot_mcp_tool(tmp_path, omp_tool_name) is expected
