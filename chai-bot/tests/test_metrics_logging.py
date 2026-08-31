"""Tests for chai-bot/hooks/metrics.py.

Black-box subprocess tests: invoke metrics.py with synthetic PreToolUse /
PostToolUse JSON payloads on stdin, pointing GUARD_DB_PATH at tmp_path so
tests never touch the real ~/.claude/logs/dev-guard.db.

This covers metrics.py's design: it creates ONLY the `events` table (+ its
indexes) -- no `session_state` table, no explicit/autonomous
invocation-source tagging inside this hook at all.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from _helpers import METRICS_SCRIPT, _events, run_metrics

ASK_PERSONA_TOOL = "mcp__plugin_chai-bot_ship-help__ask_persona"
SUBMIT_FEEDBACK_TOOL = "mcp__plugin_chai-bot_ship-help__submit_feedback"
COMMAND_FILE = METRICS_SCRIPT.parent.parent / "commands" / "chai-bot.md"
GUARD_SCRIPT = (
    METRICS_SCRIPT.parent.parent.parent / "dev-guard" / "hooks" / "tool-selection-guard.py"
)


def run_log_explicit_invoke(db_path: Path) -> subprocess.CompletedProcess:
    """Invoke metrics.py's `--log-explicit-invoke` CLI mode -- no stdin payload
    at all, matching commands/chai-bot.md's actual call:
    `uv run --no-project metrics.py --log-explicit-invoke`."""
    env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
    return subprocess.run(
        ["uv", "run", "--no-project", str(METRICS_SCRIPT), "--log-explicit-invoke"],
        capture_output=True,
        text=True,
        env=env,
    )


def run_dev_guard(payload: dict, db_path: Path) -> subprocess.CompletedProcess:
    """Invoke dev-guard's tool-selection-guard.py against the SAME
    GUARD_DB_PATH chai-bot's metrics.py uses -- both plugins' PreToolUse
    hooks fire independently for the same mcp__plugin_chai-bot_.* call (see
    metrics.py's _init_db docstring), scoped down to a plain PreToolUse
    mcp__ payload."""
    env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
    return subprocess.run(
        ["uv", "run", str(GUARD_SCRIPT)],
        input=json.dumps(payload),
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


class TestBaseUrlSchemeEnforcement:
    """ask_persona is DENIED (not auto-approved) when CHAI_BOT_BASE_URL would
    send the CHAI_TOKEN bearer header over cleartext. This is the real
    call-time control: Claude Code's MCP transport connects to
    CHAI_BOT_BASE_URL (from .mcp.json) directly, independent of
    check-availability.sh (which only gates the nudge and the /chai-bot
    command)."""

    _PRE = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-1",
        "tool_use_id": "tu-scheme",
        "tool_name": ASK_PERSONA_TOOL,
        "tool_input": {"question": "x"},
    }

    def _run(self, db_path, base_url):
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        if base_url is None:
            env.pop("CHAI_BOT_BASE_URL", None)
        else:
            env["CHAI_BOT_BASE_URL"] = base_url
        return subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input=json.dumps(self._PRE),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_non_https_base_url_denies_ask_persona(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = self._run(db_path, "http://insecure.example.com")
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        # the metrics 'pre' row is still logged even when the call is denied
        rows = _events(db_path)
        assert len(rows) == 1
        assert rows[0][5] == "pre"

    def test_userinfo_injection_base_url_denies_ask_persona(self, tmp_path):
        """A crafted loopback-userinfo URL whose real host is remote must DENY,
        not slip through as loopback."""
        db_path = tmp_path / "dev-guard.db"
        result = self._run(db_path, "http://127.0.0.1:1234@attacker.test/")
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"

    def test_https_base_url_allows_ask_persona(self, tmp_path):
        db_path = tmp_path / "dev-guard.db"
        result = self._run(db_path, "https://ship-help.internal.example.com")
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"
        assert "additionalContext" in output

    def test_loopback_http_base_url_allows_ask_persona(self, tmp_path):
        """Loopback http:// (local testing) is safe -> allow, not deny."""
        db_path = tmp_path / "dev-guard.db"
        result = self._run(db_path, "http://127.0.0.1:8080")
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"

    def test_unset_base_url_allows_ask_persona(self, tmp_path):
        """Unset CHAI_BOT_BASE_URL -> no resolvable remote host, nothing to
        protect -> allow (the MCP call would just fail to connect)."""
        db_path = tmp_path / "dev-guard.db"
        result = self._run(db_path, None)
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"

    def test_non_https_base_url_denies_non_ask_persona_chai_bot_tool(self, tmp_path):
        """The deny covers EVERY chai-bot MCP tool (all carry the server-level
        Bearer token), not just ask_persona -- submit_feedback with an unsafe
        base URL is denied too, rather than falling through to manual."""
        db_path = tmp_path / "dev-guard.db"
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sess-1",
            "tool_use_id": "tu-sf",
            "tool_name": SUBMIT_FEEDBACK_TOOL,
            "tool_input": {},
        }
        env = {
            **os.environ,
            "GUARD_DB_PATH": str(db_path),
            "CHAI_BOT_BASE_URL": "http://insecure.example.com",
        }
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"

    def test_newline_in_base_url_denies(self, tmp_path):
        """A CR/LF-bearing base URL is rejected as unsafe (it would otherwise
        make the bash ERE and Python regex diverge on newline handling)."""
        db_path = tmp_path / "dev-guard.db"
        result = self._run(db_path, "http://localhost\n")
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"


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

    def test_content_list_with_no_text_blocks_records_zero_not_whole_payload(self, tmp_path):
        """A `content` list with only non-text blocks (e.g. an image
        block) must record response_size == 0 -- it must NOT fall through to
        serializing the whole tool_response payload, which could leak
        non-text content (e.g. base64 image data) into the detail column."""
        db_path = tmp_path / "dev-guard.db"
        secret_payload_marker = "UNIQUE_IMAGE_DATA_MARKER_should_never_leak_98765"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-image-only",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-image-only",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
                "tool_response": {"content": [{"type": "image", "data": secret_payload_marker}]},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 2
        post_row = rows[-1]
        detail = json.loads(post_row[7])
        assert detail["response_size"] == 0
        for row in rows:
            for value in row:
                assert secret_payload_marker not in str(value)

    def test_post_with_differently_keyed_pre_row_still_degrades_gracefully(self, tmp_path):
        """A 'pre' row DOES exist in the DB, but under a DIFFERENT
        tool_use_id than this post payload references -- the
        `WHERE tool_use_id = ?` lookup must not cross-match it. Pins the
        accepted degradation: no speculative fallback to some other
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

    def test_missing_tool_response_records_zero_size(self, tmp_path):
        """tool_response entirely absent from the PostToolUse payload --
        _extract_response_size(None) must return 0, never crash."""
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-no-response",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-no-response",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 2
        detail = json.loads(rows[-1][7])
        assert detail["response_size"] == 0

    def test_bare_string_tool_response_records_string_length(self, tmp_path):
        """tool_response as a plain string (not a dict) --
        response_size == len(the string), the isinstance(..., str) branch."""
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-string-response",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-string-response",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
                "tool_response": "a bare string response",
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 2
        detail = json.loads(rows[-1][7])
        assert detail["response_size"] == len("a bare string response")

    def test_dict_without_content_key_falls_back_to_json_dumps_length(self, tmp_path):
        """tool_response is a dict with NO 'content' key -- falls through to
        len(json.dumps(tool_response)), not 0 and not a crash. Pins the exact
        fallback shape so a future change can't silently swap in something
        that leaks content."""
        db_path = tmp_path / "dev-guard.db"
        run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-no-content-key",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
            },
            db_path,
        )
        tool_response = {"result": "some text", "isError": False}
        result = run_metrics(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-no-content-key",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {},
                "tool_response": tool_response,
            },
            db_path,
        )
        assert result.returncode == 0, result.stderr
        rows = _events(db_path)
        assert len(rows) == 2
        detail = json.loads(rows[-1][7])
        assert detail["response_size"] == len(json.dumps(tool_response))


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

    def test_oversized_stdin_input_triggers_10mb_cap_fail_open(self, tmp_path):
        """_parse_hook_input's _MAX_INPUT_BYTES (10MB) cap -- a payload over
        the cap must be discarded wholesale (fail-open to {}) rather than
        parsed, even though it's syntactically valid JSON with a real
        ask_persona tool_name. The resulting row must show the EMPTY-dict
        fallback shape (command == ""), not the real tool_name, proving the
        >10MB branch fired rather than some unrelated parse failure."""
        db_path = tmp_path / "dev-guard.db"
        oversized_padding = "x" * (10 * 1024 * 1024 + 1024)
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-1",
                "tool_use_id": "tu-oversized",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"padding": oversized_padding},
            }
        )
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""  # no allow/advisory -- tool_name never parsed
        rows = _events(db_path)
        assert len(rows) == 1
        _, session_id, tool_use_id, category, rule, action, command, detail = rows[0]
        assert command == ""  # real tool_name discarded by the size cap, not read
        assert session_id is None
        assert tool_use_id is None
        assert action == "pre"

    def test_json_null_input_exits_zero(self, tmp_path):
        """Valid JSON but not a dict -- 'null' parses to None, whose .get(...)
        raises AttributeError, caught only by main()'s broad outer except.
        Must stay exit 0, and (unlike the {}-fallback cases) no DB row should
        ever be written since the exception fires before any DB interaction."""
        db_path = tmp_path / "dev-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input="null",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert not db_path.exists()

    def test_json_array_input_exits_zero(self, tmp_path):
        """Valid JSON but not a dict -- '[]' parses to a list, whose .get(...)
        raises AttributeError -- same fail-silent contract as the null case,
        no DB touch at all."""
        db_path = tmp_path / "dev-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input="[]",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert not db_path.exists()

    def test_json_integer_input_exits_zero(self, tmp_path):
        """Valid JSON but not a dict -- '42' parses to a bare int, whose
        .get(...) raises AttributeError -- same fail-silent contract, no DB
        touch at all."""
        db_path = tmp_path / "dev-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        result = subprocess.run(
            ["uv", "run", str(METRICS_SCRIPT)],
            input="42",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert not db_path.exists()


class TestLogExplicitInvokeCliMode:
    """`--log-explicit-invoke` CLI mode. No stdin payload at all --
    called directly by commands/chai-bot.md via
    `uv run --no-project metrics.py --log-explicit-invoke`, rather than
    embedding the INSERT as inline SQL in the command markdown. Writes
    exactly one events row and reuses the same fail-silent, always-exit-0
    contract."""

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


class TestCommandFileInvokesNoProject:
    """The `uv run --no-project metrics.py --log-explicit-invoke` form
    must actually be present in the command file's real explicit-invoke call --
    pins against a future edit silently dropping the `--no-project` flag. The
    flag is defensive-in-depth: metrics.py carries a PEP 723 inline-metadata
    block so `uv run` already isolates it from the invoking repo's project, but
    `--no-project` keeps that isolation explicit and correct even if the inline
    metadata is ever removed."""

    def test_no_project_flag_present_in_explicit_invoke_call(self):
        assert COMMAND_FILE.exists(), f"expected command file at {COMMAND_FILE}"
        content = COMMAND_FILE.read_text()
        assert "uv run --no-project" in content


class TestCrossWriterAggregateIdentity:
    """Pins the aggregate identity documented in metrics.py's
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
        # EXACT match required -- NOT `LIKE '%__ask_persona'`, since "_"
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


class TestCrossPluginSharedDb:
    """dev-guard's tool-selection-guard.py and chai-bot's metrics.py both
    create/touch the SAME `events` table in the SAME GUARD_DB_PATH sqlite
    file -- metrics.py's _init_db docstring documents this as intentional
    (both plugins' PreToolUse hooks fire independently for the same
    mcp__plugin_chai-bot_.* call, e.g. ask_persona). These tests verify that
    coexistence: both scripts' _init_db() against a shared file in either
    order, plus concurrent metrics.py writers confirming the WAL +
    busy_timeout=1000ms hardening survives contention."""

    _EXPECTED_EVENTS_COLUMNS = {
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

    def test_dev_guard_first_then_chai_bot_share_events_schema(self, tmp_path):
        """dev-guard's _init_db() runs first (creating its full schema,
        including `events`), then chai-bot's metrics.py runs second against
        the same file -- its `CREATE TABLE IF NOT EXISTS events` must be a
        no-op against the already-existing table, not a conflict."""
        db_path = tmp_path / "dev-guard.db"
        guard_result = run_dev_guard(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-order-1",
                "tool_use_id": "tu-guard-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "irrelevant"},
            },
            db_path,
        )
        assert guard_result.returncode == 0, guard_result.stderr

        chai_result = run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-order-1",
                "tool_use_id": "tu-chai-1",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "irrelevant"},
            },
            db_path,
        )
        assert chai_result.returncode == 0, chai_result.stderr

        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        conn.close()
        assert cols == self._EXPECTED_EVENTS_COLUMNS

        rows = _events(db_path)
        assert len(rows) == 2
        categories = {row[3] for row in rows}
        assert categories == {"guard", "chai-bot"}
        guard_row = next(r for r in rows if r[3] == "guard")
        assert guard_row[4] == "mcp-unknown"  # rule
        assert guard_row[5] == "mcp-passthrough"  # action
        chai_row = next(r for r in rows if r[3] == "chai-bot")
        assert chai_row[5] == "pre"  # action

    def test_chai_bot_first_then_dev_guard_share_events_schema(self, tmp_path):
        """Reverse order: chai-bot's schema (events table only) runs first,
        then dev-guard's fuller schema runs second against the same file --
        dev-guard's `CREATE TABLE IF NOT EXISTS events` must be a no-op
        against chai-bot's already-existing table."""
        db_path = tmp_path / "dev-guard.db"
        chai_result = run_metrics(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-order-2",
                "tool_use_id": "tu-chai-2",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "irrelevant"},
            },
            db_path,
        )
        assert chai_result.returncode == 0, chai_result.stderr

        guard_result = run_dev_guard(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sess-order-2",
                "tool_use_id": "tu-guard-2",
                "tool_name": ASK_PERSONA_TOOL,
                "tool_input": {"question": "irrelevant"},
            },
            db_path,
        )
        assert guard_result.returncode == 0, guard_result.stderr

        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert cols == self._EXPECTED_EVENTS_COLUMNS
        # dev-guard's fuller schema still laid down its own tables
        # alongside chai-bot's pre-existing `events` table.
        assert "session_state" in tables
        assert "trusted_rules" in tables

        rows = _events(db_path)
        assert len(rows) == 2
        categories = {row[3] for row in rows}
        assert categories == {"guard", "chai-bot"}

    def test_concurrent_metrics_subprocesses_coexist_without_corruption(self, tmp_path):
        """Fires several independent metrics.py subprocesses at the same
        GUARD_DB_PATH essentially simultaneously (each process is started and
        fed its stdin payload before any is waited on, so their _init_db()/
        _insert_event() calls race for real). metrics.py is best-effort and
        fail-silent (WAL + busy_timeout=1000ms, sqlite errors suppressed), so
        the guaranteed contract under contention is: every process still exits
        0 (a logging failure never blocks the tool call) and the shared DB is
        never corrupted -- every row that lands is a well-formed chai-bot 'pre'
        row carrying one of the expected tool_use_ids, with no duplicates or
        garbage. Row delivery itself is best-effort (a writer that exceeds the
        busy_timeout drops its row silently, by design), so this asserts DB
        integrity, not an exact row count."""
        db_path = tmp_path / "dev-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        env.pop("CHAI_BOT_BASE_URL", None)
        n = 8
        procs = []
        for i in range(n):
            proc = subprocess.Popen(
                ["uv", "run", str(METRICS_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            payload = json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sess-concurrent",
                    "tool_use_id": f"tu-concurrent-{i}",
                    "tool_name": ASK_PERSONA_TOOL,
                    "tool_input": {},
                }
            )
            proc.stdin.write(payload)
            proc.stdin.close()
            procs.append(proc)

        for proc in procs:
            # stdin was already written + closed above to launch all writers
            # racing, so communicate() (which re-flushes stdin) can't be used;
            # drain both pipes then wait. Draining stdout prevents a child from
            # blocking on a full stdout pipe.
            proc.stdout.read()
            stderr = proc.stderr.read()
            proc.wait(timeout=30)
            assert proc.returncode == 0, stderr

        rows = _events(db_path)
        expected_ids = {f"tu-concurrent-{i}" for i in range(n)}
        landed_ids = [row[2] for row in rows]
        # DB integrity under contention: every landed row is a well-formed
        # chai-bot 'pre' row with an expected tool_use_id, no duplicates, and at
        # least one writer got through. Delivery is best-effort, so the count
        # may be < n without indicating corruption.
        assert rows, "at least one concurrent writer's row must land"
        assert all(row[3] == "chai-bot" and row[5] == "pre" for row in rows)
        assert set(landed_ids) <= expected_ids
        assert len(landed_ids) == len(set(landed_ids)), "no duplicate/garbled rows"
