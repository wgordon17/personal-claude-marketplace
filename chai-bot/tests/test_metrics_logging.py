"""Tests for chai-bot/hooks/metrics.py.

Black-box subprocess tests: invoke metrics.py with synthetic PreToolUse /
PostToolUse JSON payloads on stdin, pointing GUARD_DB_PATH at tmp_path so
tests never touch the real ~/.claude/logs/dev-guard.db.

This covers the SIMPLIFIED (deviation) design: metrics.py creates ONLY the
`events` table (+ its two indexes) -- no `session_state` table, no
explicit/autonomous invocation-source tagging inside this hook at all.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from conftest import METRICS_SCRIPT, _events, run_metrics

ASK_PERSONA_TOOL = "mcp__plugin_chai-bot_ship-help__ask_persona"
SUBMIT_FEEDBACK_TOOL = "mcp__plugin_chai-bot_ship-help__submit_feedback"


def run_log_explicit_invoke(db_path: Path) -> subprocess.CompletedProcess:
    """Invoke metrics.py's `--log-explicit-invoke` CLI mode -- no stdin payload
    at all, matching commands/chai-bot.md's actual call:
    `uv run --no-project metrics.py --log-explicit-invoke`."""
    env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
    return subprocess.run(
        ["uv", "run", str(METRICS_SCRIPT), "--log-explicit-invoke"],
        capture_output=True,
        text=True,
        env=env,
    )


class TestSchemaCreation:
    def test_events_table_and_indexes_created(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "irrelevant"},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        assert cols == {
            "id",
            "ts",
            "session_id",
            "tool_use_id",
            "category",
            "rule",
            "action",
            "command",
            "detail",
        }
        index_names = {row[1] for row in conn.execute("PRAGMA index_list(events)").fetchall()}
        assert "idx_events_session" in index_names
        assert "idx_events_ts" in index_names

        # Deviation: NO session_state table should exist.
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "session_state" not in tables
        conn.close()

    def test_db_file_permissions_owner_only(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600


class TestPreToolUseLogging:
    def test_ask_persona_logs_pre_row(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "what is OSAC"},
            },
            db_path,
        )
        rows = _events(db_path)
        assert len(rows) == 1
        _, session_id, tool_use_id, category, rule, action, command, detail = rows[0]
        assert session_id == "sess-1"
        assert tool_use_id == "tu-1"
        assert category == "chai-bot"
        assert rule is None
        assert action == "pre"
        assert command == ASK_PERSONA_TOOL
        assert detail is None

    def test_other_chai_bot_tool_also_logs_pre_row(self, tmp_path):
        """submit_feedback etc. still get a metrics row -- only the permission decision differs."""
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-2",
                "tool_name": SUBMIT_FEEDBACK_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        rows = _events(db_path)
        assert len(rows) == 1
        assert rows[0][6] == SUBMIT_FEEDBACK_TOOL

    def test_invalid_session_id_is_dropped_not_crashed(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1; DROP TABLE events;--",
                "tool_use_id": "tu-3",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        assert result.returncode == 0
        rows = _events(db_path)
        assert len(rows) == 1
        assert rows[0][1] is None  # invalid session_id rejected, not stored verbatim


class TestPermissionScoping:
    """Security constraint: only ask_persona gets allow + advisory."""

    def test_ask_persona_emits_allow_and_advisory(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "what is OSAC"},
            },
            db_path,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"
        assert "additionalContext" in output
        assert "imperative" in output["additionalContext"].lower()
        assert "explicit" in output["additionalContext"].lower()
        assert "confirmation" in output["additionalContext"].lower()

    def test_other_chai_bot_tool_emits_no_permission_decision(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-2",
                "tool_name": SUBMIT_FEEDBACK_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestPostToolUseLogging:
    def test_post_row_has_latency_and_response_size(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "what is OSAC"},
            },
            db_path,
        )
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "what is OSAC"},
                "tool_response": {
                    "content": [{"type": "text", "text": "OSAC is a team."}],
                    "isError": False,
                },
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""  # PostToolUse never emits stdout

        rows = _events(db_path)
        assert len(rows) == 2
        post_row = rows[1]
        _, _, _, category, _, action, command, detail = post_row
        assert category == "chai-bot"
        assert action == "post"
        assert command == ASK_PERSONA_TOOL
        parsed_detail = json.loads(detail)
        assert parsed_detail["response_size"] == len("OSAC is a team.")
        assert isinstance(parsed_detail["latency_ms"], int | float)
        assert parsed_detail["latency_ms"] >= 0

    def test_no_question_or_answer_text_in_any_column(self, tmp_path):
        """Hard security constraint: the actual response text never lands in a DB column."""
        db_path = tmp_path / "dev-guard.db"
        secret_text = "UNIQUE_MARKER_the_actual_answer_content_12345"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": secret_text},
            },
            db_path,
        )
        run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": secret_text},
                "tool_response": {"content": [{"type": "text", "text": secret_text}]},
            },
            db_path,
        )
        rows = _events(db_path)
        for row in rows:
            for value in row:
                assert secret_text not in str(value)

    def test_post_without_matching_pre_still_logs_gracefully(self, tmp_path):
        """No prior 'pre' row for this tool_use_id -- latency_ms should be null, no crash."""
        db_path = tmp_path / "dev-guard.db"
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-orphan",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
                "tool_response": {"content": [{"type": "text", "text": "hi"}]},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 1
        detail = json.loads(rows[0][7])
        assert detail["latency_ms"] is None
        assert detail["response_size"] == len("hi")

    def test_post_with_differently_keyed_pre_row_still_degrades_gracefully(self, tmp_path):
        """QA-3: a 'pre' row DOES exist in the DB, but under a DIFFERENT
        tool_use_id than this post payload references -- the
        `WHERE tool_use_id = ?` lookup must not cross-match it. Pins the
        plan's accepted degradation: no speculative fallback to some other
        'pre' row when the exact tool_use_id key misses; latency_ms stays
        null and the call still exits 0. (The "no pre row at all" case is
        already covered by test_post_without_matching_pre_still_logs_gracefully
        above.)"""
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-OTHER",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "what is OSAC"},
            },
            db_path,
        )
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-MISMATCH",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
                "tool_response": {"content": [{"type": "text", "text": "hi"}]},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 2  # the tu-OTHER 'pre' row + this post row
        post_row = rows[-1]
        assert post_row[2] == "tu-MISMATCH"
        detail = json.loads(post_row[7])
        assert detail["latency_ms"] is None
        assert detail["response_size"] == len("hi")


class TestFailSilent:
    def test_malformed_json_input_exits_zero(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input="not valid json{{{",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0

    def test_empty_input_exits_zero(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input="",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0


class TestLogExplicitInvokeCliMode:
    """QA-1: `--log-explicit-invoke` CLI mode. No stdin payload at all --
    called directly by commands/chai-bot.md via
    `uv run --no-project metrics.py --log-explicit-invoke`, replacing the
    earlier inline-SQL `python -c "..."` design. Writes exactly one events
    row and reuses the same fail-silent, always-exit-0 contract."""

    def test_writes_single_explicit_invoke_row_with_no_leakage(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = run_log_explicit_invoke(db_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""  # CLI mode emits no hookSpecificOutput

        rows = _events(db_path)
        assert len(rows) == 1
        ts, session_id, tool_use_id, category, rule, action, command, detail = rows[0]
        assert ts  # non-empty ISO timestamp
        assert session_id is None
        assert tool_use_id is None
        assert category == "chai-bot"
        assert rule is None
        assert action == "explicit-invoke"
        assert command == "ask_persona"
        assert detail is None

    def test_two_invocations_write_two_independent_rows(self, tmp_path):
        """No dedup/idempotency across separate --log-explicit-invoke calls --
        each explicit /chai-bot invocation gets its own row."""
        db_path = tmp_path / "dev-guard.db"
        run_log_explicit_invoke(db_path)
        result = run_log_explicit_invoke(db_path)
        assert result.returncode == 0
        rows = _events(db_path)
        assert len(rows) == 2
        assert all(row[5] == "explicit-invoke" and row[6] == "ask_persona" for row in rows)


class TestCrossWriterAggregateIdentity:
    """STRUCT-02: pins the aggregate identity documented in metrics.py's
    module docstring --

        explicit    = count(category='chai-bot', action='explicit-invoke')
        total       = count(category='chai-bot', action='pre',
                             command = <EXACT ask_persona tool name>)
        autonomous  = max(0, total - explicit)

    `explicit` (written by --log-explicit-invoke) and `total` (written by
    this hook's own PreToolUse handler) come from two INDEPENDENT trigger
    paths with no per-call correlation -- both writers are exercised via
    their real subprocess entry points, not direct SQL inserts, so this also
    covers that the two writers actually populate the rows the aggregate
    query expects."""

    @staticmethod
    def _aggregate(db_path: Path) -> tuple[int, int, int]:
        conn = sqlite3.connect(str(db_path))
        explicit = conn.execute(
            "SELECT COUNT(*) FROM events WHERE category='chai-bot' AND action='explicit-invoke'"
        ).fetchone()[0]
        # EXACT match per STRUCT-04 -- NOT `LIKE '%__ask_persona'`, since "_"
        # is itself a LIKE wildcard character and can silently over-match.
        total = conn.execute(
            "SELECT COUNT(*) FROM events WHERE category='chai-bot' AND action='pre' "
            "AND command = ?",
            (ASK_PERSONA_TOOL,),
        ).fetchone()[0]
        conn.close()
        autonomous = max(0, total - explicit)
        return explicit, total, autonomous

    def test_explicit_plus_autonomous_identity_with_like_wildcard_distractor(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"

        # One explicit invocation immediately followed by its matching
        # ask_persona 'pre' row (one explicit call).
        run_log_explicit_invoke(db_path)
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-explicit-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "explicit call"},
            },
            db_path,
        )

        # One autonomous ask_persona 'pre' row -- no matching explicit-invoke.
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-autonomous-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "autonomous call"},
            },
            db_path,
        )

        # LIKE-wildcard distractor: `mcp__plugin_chai-bot_ship-help__ask9persona`
        # matches the naive pattern `%__ask_persona` (each "_" there is a
        # single-char wildcard, so "9" satisfies the "_" between "ask" and
        # "persona") but must NOT satisfy the exact-match query.
        distractor_tool = "mcp__plugin_chai-bot_ship-help__ask9persona"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-distractor-1",
                "tool_name": distractor_tool,
                "tool_input": {},
            },
            db_path,
        )

        explicit, total, autonomous = self._aggregate(db_path)
        assert explicit == 1
        assert total == 2  # distractor excluded by the exact-match query
        assert autonomous == 1

    def test_explicit_invoke_without_matching_pre_clamps_autonomous_to_zero(self, tmp_path):
        """DIVERGENCE case: an explicit-invoke row with no matching 'pre' row
        at all (e.g. the matching PreToolUse row never landed) -- autonomous
        must clamp to 0, never go negative."""
        db_path = tmp_path / "dev-guard.db"
        run_log_explicit_invoke(db_path)

        explicit, total, autonomous = self._aggregate(db_path)
        assert explicit == 1
        assert total == 0
        assert autonomous == 0
