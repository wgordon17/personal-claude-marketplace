"""Contract-level tests for the OMP bridge (dev-guard/omp-extension.ts).

This file does NOT retest the hook scripts' own internal logic — that's
already covered by test_tool_selection_guard.py, test_decision_persistence.py,
test_stop_hook.py, and test_subagent_stop_hook.py. Instead it pins the exact
JSON payload SHAPES omp-extension.ts constructs and sends to each script,
since those shapes are hand-written in TypeScript with no shared schema or
type-check against the Python side — a silent shape drift on either side
would otherwise go undetected until a live OMP session broke.

Every payload constructed below is copied field-for-field from
dev-guard/omp-extension.ts's actual runGuard()/runDecisionPersistence()
call sites, translateToolNameForGuard()/translateToolInputForGuard(),
reencodeMcpToolName(), and the tool_result/session_start/session_shutdown/
agent_end handlers — not reconstructed from the plan or from memory.

Manual TS verification checklist
=================================
omp-extension.ts itself has no automated TypeScript test coverage (this
repo has zero pre-existing TS test/CI infrastructure, and standing one up
for a single thin adapter file would be disproportionate — see the plan's
"Trade-offs Accepted" section). The contract tests below cover the Python
side of every payload shape the bridge constructs; re-run this checklist
against a live OMP install before each dev-guard release that touches
omp-extension.ts. (Transcribed verbatim into dev-guard/OMP-COMPAT.md by
the docs task — keep both copies in sync if this list changes.)

  1. Session lifecycle: start a session under OMP with dev-guard installed.
     Confirm session_start dispatches --validate and both inject-reference.sh
     calls (shared-feedback.md, token-efficiency.md) concurrently via
     Promise.all -- verify all three deliver content into the model's
     context (ask it to quote injected text back verbatim), and that
     shared-feedback.md's content still lands before token-efficiency.md's
     (message order is preserved by Promise.all's result-array ordering, not
     by which subprocess resolves first). Confirm session end fires
     session_shutdown -> --session-end with no crash, and the session_state
     DB row is updated.
  2. tool_call/tool_result dispatch, one round-trip per tool type:
       - bash: a blocked case (a printf/echo-noop-style rule) fires with
         exit code 2, and the block reason reaches the model as a tool
         error, not a passthrough. An allowed case (e.g. `ls`) succeeds.
         Separately, confirm a `permissionDecision: ask` response (e.g. from
         a git-ask-rule like `git stash drop`) is correctly folded into a
         hard block, not passed through as an allow.
       - write, edit: an allowed case succeeds. Separately, a case that WOULD
         hit one of _guard_tmp_path/_guard_comment_narration's real
         hard-block paths still succeeds if the bridge's own subprocess call
         is broken (fail-open on subprocess failure, not because these
         checks are advisory).
       - read: both a plain file path (Read-shaped) and a
         "https://..." path (WebFetch-shaped) route to the correct Claude
         Code tool_name in the guard's stdin payload. Confirm a
         `.claire/...` path is rewritten to `.claude/...` and the corrected
         path is what the tool actually reads (not just what the guard's
         stdout computed — ToolCallEventResult.input must carry it).
       - an MCP tool from a server listed in OMP_TO_CLAUDE_CODE_MCP_SERVER
         (e.g. context7's resolve-library-id) auto-approves with no ask
         prompt; an MCP tool from an unrecognized server passes through
         without crashing.
  3. AskUserQuestion round-trip: register/call the custom tool under OMP,
     confirm no name collision with the built-in "ask" tool, confirm
     ctx.ui.askDialog() is used when reachable (TUI mode) and
     ctx.ui.select() as a fallback otherwise (print/RPC/subagent modes).
     Confirm a two-question batch answered in reverse order records each
     decision against the correct fingerprint (matched by question text,
     not array position).
  4. Stop/SubagentStop: confirm agent_end dispatches both stop-hook.py and
     subagent-stop-hook.py without crashing, and that the agentEnd
     telemetry counters (logged via pi.logger.debug on session_shutdown)
     increment as expected.

Last run against: OMP v17.4.2 (see hack/research/omp-spike-findings.md,
gitignored/local-only, for the full live-verification evidence trail).
"""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

TOOL_SELECTION_GUARD = Path(__file__).parent.parent / "hooks" / "tool-selection-guard.py"
DECISION_PERSISTENCE = Path(__file__).parent.parent / "hooks" / "decision-persistence.py"
VALIDATE_COMMIT_MESSAGE = Path(__file__).parent.parent / "hooks" / "validate-commit-message.sh"
STOP_HOOK = Path(__file__).parent.parent / "hooks" / "stop-hook.py"
SUBAGENT_STOP_HOOK = Path(__file__).parent.parent / "hooks" / "subagent-stop-hook.py"

# Needed so `from mcp_constants import MCP_READ_ONLY` resolves when this file
# is run standalone (not just as a side effect of test_tool_selection_guard.py's
# module-level importlib load of tool-selection-guard.py, which happens to
# insert this same path but only when that file is also collected).
sys.path.insert(0, str(TOOL_SELECTION_GUARD.parent))

METADATA_PREFIX = "▸dp:"


# ── Subprocess helpers ───────────────────────────────────────────────────────


def run_guard(
    payload: dict, *, cwd: Path | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Invoke tool-selection-guard.py with a bridge-shaped payload on stdin."""
    return subprocess.run(
        ["uv", "run", str(TOOL_SELECTION_GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        timeout=30,
    )


def run_guard_validate(payload: dict, *, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke tool-selection-guard.py --validate, matching the bridge's session_start call."""
    return subprocess.run(
        ["uv", "run", str(TOOL_SELECTION_GUARD), "--validate"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )


def run_guard_session_end(payload: dict, *, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke tool-selection-guard.py --session-end, matching the bridge's session_shutdown call."""
    return subprocess.run(
        ["uv", "run", str(TOOL_SELECTION_GUARD), "--session-end"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )


def run_decision_persistence(payload: dict, *, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke decision-persistence.py with a bridge-shaped payload on stdin."""
    return subprocess.run(
        ["uv", "run", str(DECISION_PERSISTENCE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )


def run_validate_commit_message(payload: dict, *, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke validate-commit-message.sh with the bridge's exact PostToolUse Bash payload shape."""
    return subprocess.run(
        [str(VALIDATE_COMMIT_MESSAGE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )


def run_stop_hook(
    payload: dict, *, cwd: Path, state_path: Path, plugin_root: str | None = None
) -> subprocess.CompletedProcess:
    """Invoke stop-hook.py with the bridge's agent_end-derived payload shape."""
    import os

    env = os.environ.copy()
    env["STOP_HOOK_STATE_PATH"] = str(state_path)
    env["GUARD_DB_PATH"] = str(state_path.parent / "test-guard.db")
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    return subprocess.run(
        ["uv", "run", str(STOP_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )


def run_subagent_stop_hook(payload: dict, *, state_path: Path) -> subprocess.CompletedProcess:
    """Invoke subagent-stop-hook.py with the bridge's agent_end-derived payload shape."""
    import os

    env = os.environ.copy()
    env["SUBAGENT_STOP_HOOK_STATE_PATH"] = str(state_path)
    return subprocess.run(
        ["uv", "run", str(SUBAGENT_STOP_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def make_dp_question(
    description: str,
    *,
    file: str = "src/auth.py",
    line: str = "42",
    cat: str = "Security",
    skill: str = "pr-review",
) -> dict:
    """Return an AskUserQuestion question dict with Fix/Defer options and ▸dp: metadata,
    matching the shape the bridge's AskUserQuestion custom tool passes through unchanged.
    """
    q_text = f"{description} {METADATA_PREFIX}file={file},line={line},cat={cat},skill={skill}"
    return {"question": q_text, "options": [{"label": "Fix"}, {"label": "Defer"}]}


def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    return tmp_path


def seed_stop_hook_state(state_path: Path, session_id: str) -> None:
    """Seed stop-hook.py's state file so a payload is treated as a second fire,
    not the first-fire fast-exit that only initializes state (see test_stop_hook.py's
    identically-shaped seed_state() helper — duplicated here rather than imported
    since this file intentionally stays import-independent from the hook-logic
    test modules it complements, per this file's module docstring)."""
    state = {
        session_id: {
            "last_diff_hash": "",
            "last_fire_timestamp": time.time(),
            "evaluated_tool_count": 0,
            "last_file_size": 0,
        }
    }
    state_path.write_text(json.dumps(state))


def write_mock_stop_hook_llm(plugin_root: Path, *, decision: str, findings: list[str]) -> None:
    """Write a stub stop-hook-llm.py to plugin_root/hooks/ so stop-hook.py's LLM
    delegation is deterministic (no live LLM call, matching this repo's default
    test suite excluding llm-marked tests) — same stub pattern as test_stop_hook.py's
    write_mock_llm(), duplicated here for the same import-independence reason as
    seed_stop_hook_state() above."""
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    result_json = json.dumps({"decision": decision, "reasoning": "mock", "findings": findings})
    stub = textwrap.dedent(f"""\
        #!/usr/bin/env -S uv run
        # /// script
        # requires-python = ">=3.13"
        # ///
        import json, sys
        result = json.loads({result_json!r})
        print(json.dumps(result))
        sys.exit(2 if result["decision"] == "fail" else 0)
    """)
    llm_script = hooks_dir / "stop-hook-llm.py"
    llm_script.write_text(stub)
    llm_script.chmod(0o755)


# ── Bash: PreToolUse translation (omp-extension.ts translateToolNameForGuard/
#    translateToolInputForGuard for "bash") ─────────────────────────────────


class TestBashPreToolUsePayloadShape:
    def test_blocked_command_exits_2(self, tmp_path):
        """The bridge's exact Bash PreToolUse payload: {session_id, tool_use_id,
        hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: {command}}.
        A GIT_DENY_RULES-matching command must exit 2 (dual block-signal —
        the bridge checks this BEFORE parsing stdout JSON)."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git reset --hard HEAD"},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 2, (
            f"expected hard block, got stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_allowed_command_exits_0(self, tmp_path):
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-2",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0


# ── Regression: `ask` permissionDecision must fold into a hard block, not a
#    silent passthrough (omp-extension.ts's tool_call handler, ~lines
#    531-533 — OMP's ToolCallEventResult has no "pause and prompt" outcome,
#    so the bridge treats "ask" the same as "deny") ─────────────────────────


class TestAskDecisionSignal:
    """`ask` decisions are live-reachable (e.g. a git-ask-rule like `git
    stash drop`, or an MCP fetchaller POST call) and exit 0 with a
    hookSpecificOutput JSON payload — a third block signal alongside exit
    code 2 (TestDualBlockSignalRegression) and the "block"/"deny" JSON. A
    bridge that doesn't check for `permissionDecision === "ask"` would treat
    a dev-guard "confirm this is intentional" prompt as a silent allow. This
    pins the exact stdout shape omp-extension.ts's tool_call handler reads
    to make that fold decision."""

    def test_git_stash_drop_signals_ask_not_block_or_allow(self, tmp_path):
        # Isolate from the real ~/.claude/dev-guard.json: a local git_trusted_dirs
        # allowlist would otherwise make _check_git_trusted_dirs hard-block this
        # command (tmp_path is untrusted) before the ASK rule ever runs, same
        # isolation pattern test_tool_selection_guard.py uses throughout.
        env = {**os.environ, "DEV_GUARD_CONFIG": str(tmp_path / "nonexistent-config.json")}
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-18",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git stash drop"},
        }
        result = run_guard(payload, cwd=tmp_path, env=env)
        # Exits 0, unlike the exit-code-2 hard block signal — "ask" is only
        # distinguishable from "allow" by parsing this stdout JSON.
        assert result.returncode == 0
        output = json.loads(result.stdout)
        hook = output["hookSpecificOutput"]
        assert hook["permissionDecision"] == "ask"
        assert hook["permissionDecisionReason"]


# ── Write/Edit: PreToolUse translation (input passed through unchanged) ─────


class TestWriteEditPreToolUsePayloadShape:
    def test_write_no_hard_block_for_benign_input(self, tmp_path):
        """Write's OMP input is passed through unchanged (translateToolInputForGuard
        does not rename fields for write/edit). This benign write must not exit 2 —
        it doesn't trip _guard_tmp_path (not a /tmp/ path) or _guard_comment_narration
        (no narrative comment text). The Write matcher DOES have real hard-block
        paths of its own (see the "Fail-open / fail-closed policy" section of
        OMP-COMPAT.md) — this test only pins that a plain, non-triggering write
        passes through."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-3",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "out.txt"), "content": "hello"},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode != 2

    def test_edit_no_hard_block_for_benign_input(self, tmp_path):
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-4",
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(tmp_path / "out.txt"),
                "old_string": "a",
                "new_string": "b",
            },
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode != 2


# ── Read: PreToolUse URL-vs-file translation (special-cased inline in the
#    tool_call handler since the Claude Code tool name depends on input
#    content, not just the OMP tool name) ───────────────────────────────────


class TestReadPreToolUsePayloadShape:
    def test_file_path_translates_to_read_with_file_path_field(self, tmp_path):
        """A non-URL OMP `read` path -> tool_name 'Read', tool_input {file_path}."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-5",
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(tmp_path / "some.txt")},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0

    def test_url_path_translates_to_webfetch_with_url_field(self, tmp_path):
        """A URL-shaped OMP `read` path -> tool_name 'WebFetch', tool_input {url} —
        this is how the bridge routes OMP's URL-capable read tool through the
        same URL/auth-guard checks WebFetch gets under Claude Code."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-6",
            "hook_event_name": "PreToolUse",
            "tool_name": "WebFetch",
            "tool_input": {"url": "https://example.com/page"},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0

    def test_claire_path_rewritten_via_updated_input(self, tmp_path):
        """The bridge forwards the guard's updatedInput correction for Read via
        ToolCallEventResult.input — confirm the guard actually computes it in
        the shape the bridge reads (output.updatedInput.file_path)."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-7",
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/Users/foo/.claire/settings.json"},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        hook = output["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"
        assert hook["updatedInput"]["file_path"] == "/Users/foo/.claude/settings.json"


# ── MCP: server + tool-name re-encoding (reencodeMcpToolName) ───────────────


class TestMcpReencodingPayloadShape:
    def test_context7_resolve_library_id_reencoded_correctly(self, tmp_path):
        """Python-side contract check, NOT a test of the bridge's TypeScript
        reencodeMcpToolName() itself: this payload already carries the
        Claude-Code-shaped name (mcp__context7__resolve-library-id) that the
        bridge is expected to *produce* from OMP's live form
        (mcp__context_resolve_library_id, per the Task 1 spike — server
        portion context -> context7, OMP drops the trailing digit; tool-name
        portion underscore -> hyphen, a context7-specific override) and sends
        it directly to tool-selection-guard.py, bypassing the bridge
        entirely. It verifies mcp_key() matches mcp_constants.py's allowlist
        entry for the correctly re-encoded form — it does not exercise or
        verify the TypeScript re-encoding logic, which has no test coverage
        of its own (a known, accepted trade-off for this branch)."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-8",
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__context7__resolve-library-id",
            "tool_input": {},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert '"allow"' in result.stdout

    def test_context7_query_docs_reencoded_correctly(self, tmp_path):
        """Same Python-side contract check as
        test_context7_resolve_library_id_reencoded_correctly above, for the
        query-docs tool: sends the already-correctly-re-encoded
        Claude-Code-shaped name directly to the guard, not through the
        bridge's re-encoding logic."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-9",
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__context7__query-docs",
            "tool_input": {},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert '"allow"' in result.stdout

    def test_github_server_reencoded_correctly(self, tmp_path):
        """Python-side contract check, not a TypeScript re-encoding test (see
        test_context7_resolve_library_id_reencoded_correctly above for the
        full caveat): sends the already-correctly-re-encoded Claude-Code
        form (mcp__plugin_github-mcp_github__get_me) directly to the guard.
        This is the target of the general "plugin_ prefix restored, hyphens
        preserved" re-encoding OMP's github_mcp_github form is expected to
        produce, with no tool-name hyphen quirk (github's tool names are
        already underscore-native)."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-10",
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__plugin_github-mcp_github__get_me",
            "tool_input": {},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert '"allow"' in result.stdout

    def test_unknown_server_passthrough_no_crash(self, tmp_path):
        """Python-side contract check, not a TypeScript re-encoding test (see
        test_context7_resolve_library_id_reencoded_correctly above for the
        full caveat): sends an already double-underscore Claude-Code-shaped
        name directly to the guard — representing the output of the
        bridge's best-effort single->double underscore split for a server
        not in OMP_TO_CLAUDE_CODE_MCP_SERVER, not the split logic itself.
        Verifies the guard must not crash on it, and must NOT return
        permissionDecision: allow (matches the existing evil-server
        anti-spoofing guarantee in test_tool_selection_guard.py)."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-11",
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__totally_unknown_server__some_tool",
            "tool_input": {},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert '"allow"' not in result.stdout


# ── web_search: PreToolUse translation (field names best-effort, unverified
#    against a live install per the Task 1 spike — this test only pins that
#    the shape the bridge sends doesn't crash the guard, not that the field
#    names are definitely correct) ───────────────────────────────────────────


class TestWebSearchPreToolUsePayloadShape:
    def test_websearch_payload_shape_does_not_crash(self, tmp_path):
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-12",
            "hook_event_name": "PreToolUse",
            "tool_name": "WebSearch",
            "tool_input": {"query": "test", "allowed_domains": [], "blocked_domains": []},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0


# ── PostToolUse: Bash sequencing (validate-commit-message.sh then guard,
#    first-block-wins, one handler — matches hooks.json's array order) ──────


class TestBashPostToolUseSequencing:
    def test_commit_message_convention_violation_blocked(self, tmp_path):
        """The bridge's exact PostToolUse Bash payload to validate-commit-message.sh:
        {tool_input: {command}}. A non-conventional-commit message must exit 2
        BEFORE the guard's own PostToolUse call ever runs (first-block-wins)."""
        repo = git_repo(tmp_path)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "not a conventional commit message"], cwd=repo, check=True
        )

        payload = {"tool_input": {"command": "git commit -m 'not a conventional commit message'"}}
        result = run_validate_commit_message(payload, cwd=repo)
        assert result.returncode == 2

    def test_conventional_commit_passes(self, tmp_path):
        repo = git_repo(tmp_path)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: adds a test file"], cwd=repo, check=True)

        payload = {"tool_input": {"command": "git commit -m 'feat: adds a test file'"}}
        result = run_validate_commit_message(payload, cwd=repo)
        assert result.returncode == 0

    def test_non_commit_bash_passthrough(self, tmp_path):
        """A non-commit Bash command must exit 0 immediately (hook input filtering)."""
        repo = git_repo(tmp_path)
        payload = {"tool_input": {"command": "ls -la"}}
        result = run_validate_commit_message(payload, cwd=repo)
        assert result.returncode == 0

    def test_guard_post_bash_payload_shape(self, tmp_path):
        """The bridge's exact guard PostToolUse Bash payload shape (tool_response
        = {stdout, stderr} built from the tool_result event's text content)."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-13",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "curl https://example.com"},
            "tool_response": {"stdout": "HTTP/1.1 200 OK\n", "stderr": ""},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 0


# ── PostToolUse: `read` branch URL-vs-file dispatch (tool_result handler,
#    omp-extension.ts ~lines 560-576 — both Read and WebFetch route through
#    this one branch, using readToolResponseForGuard()'s {content} shape for
#    tool_response) ────────────────────────────────────────────────────────


class TestReadPostToolUseDispatch:
    def test_file_path_read_posttooluse_accepted(self, tmp_path):
        """The bridge's exact Read PostToolUse payload: tool_name 'Read',
        tool_input {file_path}, tool_response built by readToolResponseForGuard()
        as {content: <joined text content>}. Points file_path at the RTK tee
        dir and asserts an rtk_events row was actually created — mirrors
        TestRTKEventLogging.test_rtk_full_read_event_logged in
        test_tool_selection_guard.py — rather than only asserting exit 0,
        which also passes for a deliberately wrong-shaped payload."""
        if sys.platform == "darwin":
            tee_dir = Path.home() / "Library" / "Application Support" / "rtk" / "tee"
        else:
            xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            tee_dir = Path(xdg) / "rtk" / "tee"
        tee_path = str(tee_dir / "somefile.txt")

        db_path = tmp_path / "test-guard.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
        env.pop("RTK_DISABLED", None)

        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-16",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": tee_path},
            "tool_response": {"content": "file contents here"},
        }
        result = run_guard(payload, cwd=tmp_path, env=env)
        assert result.returncode == 0

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT event_type FROM rtk_events").fetchall()
        conn.close()
        assert len(rows) == 1, f"expected 1 rtk_events row, got {rows}"
        assert rows[0][0] == "full_read"

    def test_url_webfetch_posttooluse_accepted(self, tmp_path):
        """The bridge's exact WebFetch PostToolUse payload: tool_name 'WebFetch',
        tool_input {url}, tool_response {content: <joined text content>} —
        same readToolResponseForGuard() shape as the file-path case above,
        since both branch through the same handler. Gives the response body
        an auth-failure phrase and asserts a url_events row with
        auth_failed: True was created — mirrors
        TestPostToolUseResponseLogging.test_webfetch_auth_failed in
        test_tool_selection_guard.py — rather than only asserting exit 0."""
        db_path = tmp_path / "test.db"
        env = {**os.environ, "GUARD_DB_PATH": str(db_path), "GUARD_LOG_LEVEL": "all"}

        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-17",
            "hook_event_name": "PostToolUse",
            "tool_name": "WebFetch",
            "tool_input": {"url": "https://example.com/page"},
            "tool_response": {"content": "Login Required - Please sign in to continue"},
        }
        result = run_guard(payload, cwd=tmp_path, env=env)
        assert result.returncode == 0

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM events WHERE category='url' ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 1, f"expected 1 url event, got {rows}"
        detail = json.loads(rows[0]["detail"])
        assert detail["auth_failed"] is True


# ── Session lifecycle: --validate and --session-end payload shapes ─────────


class TestSessionLifecyclePayloadShape:
    def test_validate_payload_shape(self, tmp_path):
        """The bridge's exact session_start payload: {session_id, cwd}."""
        payload = {"session_id": str(uuid.uuid4()), "cwd": str(tmp_path)}
        result = run_guard_validate(payload, cwd=tmp_path)
        assert result.returncode == 0

    def test_session_end_payload_shape(self, tmp_path):
        """The bridge's exact session_shutdown payload: {session_id}."""
        payload = {"session_id": str(uuid.uuid4())}
        result = run_guard_session_end(payload, cwd=tmp_path)
        assert result.returncode == 0


# ── AskUserQuestion: decision-persistence.py Pre/PostToolUse payload shapes ─


class TestAskUserQuestionPayloadShape:
    def test_pretooluse_no_prior_decision_passthrough(self, tmp_path):
        """The bridge's exact PreToolUse payload: {session_id, hook_event_name,
        tool_name: 'AskUserQuestion' (hardcoded literal, per the AskUserQuestion
        tool-name-fidelity security constraint), tool_input: {questions}}."""
        payload = {
            "session_id": str(uuid.uuid4()),
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [make_dp_question("Fix the null check?")]},
        }
        result = run_decision_persistence(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""  # no stored decision -> passthrough

    def test_posttooluse_captures_answers_from_tool_response(self, tmp_path):
        """The bridge's exact PostToolUse payload: answers live in tool_response.answers
        (not tool_input.answers — that field is reserved for PreToolUse auto-applied
        decisions, per decision-persistence.py's own comment)."""
        (tmp_path / "hack").mkdir()
        (tmp_path / "hack" / "PROJECT.md").write_text("# Project\n")
        (tmp_path / "hack" / "TODO.md").write_text("# TODO\n")
        (tmp_path / ".git").mkdir()

        question = make_dp_question("Fix the null check?")
        payload = {
            "session_id": str(uuid.uuid4()),
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [question]},
            "tool_response": {"answers": {question["question"]: "Fix"}},
        }
        result = run_decision_persistence(payload, cwd=tmp_path)
        assert result.returncode == 0

        decisions_file = tmp_path / "hack" / "review-decisions.json"
        assert decisions_file.exists()
        data = json.loads(decisions_file.read_text())
        assert len(data["decisions"]) == 1
        assert data["decisions"][0]["decision"] == "Fix"

    def test_two_question_batch_reverse_order_matches_by_text_not_position(self, tmp_path):
        """Matches by question TEXT, not array position — the bridge's own
        collectAnswers() docstring guarantee. Feed two questions, then a
        PostToolUse payload with answers keyed by text in REVERSE order, and
        confirm each decision is captured against the correct fingerprint."""
        (tmp_path / "hack").mkdir()
        (tmp_path / "hack" / "PROJECT.md").write_text("# Project\n")
        (tmp_path / "hack" / "TODO.md").write_text("# TODO\n")
        (tmp_path / ".git").mkdir()

        q1 = make_dp_question("Fix issue A?", file="a.py", line="1", cat="Security")
        q2 = make_dp_question("Fix issue B?", file="b.py", line="2", cat="Correctness")
        payload = {
            "session_id": str(uuid.uuid4()),
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [q1, q2]},
            # Reverse order relative to [q1, q2] above.
            "tool_response": {"answers": {q2["question"]: "Defer", q1["question"]: "Fix"}},
        }
        result = run_decision_persistence(payload, cwd=tmp_path)
        assert result.returncode == 0

        data = json.loads((tmp_path / "hack" / "review-decisions.json").read_text())
        by_file = {d["file"]: d["decision"] for d in data["decisions"]}
        assert by_file["a.py"] == "Fix"
        assert by_file["b.py"] == "Defer"


# ── Stop / SubagentStop: agent_end-derived payload shapes ───────────────────


class TestStopHookPayloadShape:
    def test_first_fire_payload_shape_initializes_state(self, tmp_path):
        """The bridge's exact agent_end -> stop-hook.py payload: {session_id,
        transcript_path, cwd, last_assistant_message, stop_hook_active: false}.
        First fire for a session always fast-exits (state init), regardless of
        content — confirms the shape parses without error."""
        repo = git_repo(tmp_path)
        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        payload = {
            "session_id": str(uuid.uuid4()),
            "transcript_path": str(transcript),
            "cwd": str(repo),
            "last_assistant_message": "Done.",
            "stop_hook_active": False,
        }
        result = run_stop_hook(payload, cwd=repo, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"

    def test_stop_hook_active_true_fast_exits(self, tmp_path):
        """Loop-guard fast-exit: stop_hook_active True must always pass regardless
        of any other field — confirms the bridge's literal `false` default
        doesn't accidentally suppress this guard on a real re-fire."""
        repo = git_repo(tmp_path)
        payload = {
            "session_id": str(uuid.uuid4()),
            "transcript_path": "",
            "cwd": str(repo),
            "last_assistant_message": "",
            "stop_hook_active": True,
        }
        result = run_stop_hook(payload, cwd=repo, state_path=tmp_path / "state.json")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_llm_fail_blocks_via_stdout_json_not_exit_code(self, tmp_path):
        """Regression contract for the bug fixed in 0e81cdc: omp-extension.ts's
        agent_end handler used to check `code === 2` for a Stop block, but
        stop-hook.py always exits 0 and signals a block only via stdout JSON
        {"decision": "block", "reason": "..."} — the exact shape
        parseStopDecision() now reads. Seeds state as a second fire (skipping
        the first-fire init fast-exit) with a transcript containing a write
        tool call and a completion claim, so trigger_reasons is non-empty and
        the LLM path (mocked deterministically, no live call) is actually
        invoked rather than fast-exited."""
        repo = git_repo(tmp_path)
        session_id = str(uuid.uuid4())
        transcript = tmp_path / "session.jsonl"
        completion_msg = "I've completed the fix. The changes are ready."
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": completion_msg},
        ]
        transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        seed_stop_hook_state(tmp_path / "state.json", session_id)
        write_mock_stop_hook_llm(
            tmp_path / "plugin", decision="fail", findings=["Tests were not run."]
        )

        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(repo),
            "last_assistant_message": completion_msg,
            "stop_hook_active": False,
        }
        result = run_stop_hook(
            payload,
            cwd=repo,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert output["reason"]
        assert "Tests were not run" in output["reason"]


class TestSubagentStopHookPayloadShape:
    def test_empty_transcript_path_approves(self, tmp_path):
        """The bridge's exact agent_end -> subagent-stop-hook.py payload:
        {session_id, transcript_path}. ctx.sessionManager.getSessionFile()
        may resolve to an empty string in some modes — confirm this degrades
        safely (approve), matching the plan's documented best-effort
        SubagentStop trade-off."""
        payload = {"session_id": str(uuid.uuid4()), "transcript_path": ""}
        result = run_subagent_stop_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_valid_fix_summary_transcript_approves(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": ["finding-001"],
            "needs_input_items": [],
            "user_deferred": [],
            "fixes": [{"id": "finding-001", "description": "Fixed it"}],
            "files_modified": ["src/x.py"],
        }
        entry = {
            "role": "assistant",
            "content": [{"type": "text", "text": json.dumps(fix_summary)}],
        }
        transcript.write_text(json.dumps(entry) + "\n")

        payload = {"session_id": str(uuid.uuid4()), "transcript_path": str(transcript)}
        result = run_subagent_stop_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_invalid_fix_summary_blocks_via_stdout_json_not_exit_code(self, tmp_path):
        """Regression contract for the bug fixed in 0e81cdc (see
        TestStopHookPayloadShape.test_llm_fail_blocks_via_stdout_json_not_exit_code
        above for the full bug description — the same fix applies to both
        Stop and SubagentStop). subagent-stop-hook.py always exits 0 and
        signals a block only via stdout JSON {"decision": "block", "reason":
        "..."}. Unlike stop-hook.py's LLM-gated block, this one is fully
        deterministic: a FixSummary with all three arrays empty fails
        _validate_fix_summary's structural check ("all arrays empty — no
        findings accounted for") with no LLM call involved."""
        transcript = tmp_path / "transcript.jsonl"
        fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": [],
            "needs_input_items": [],
            "user_deferred": [],
        }
        entry = {
            "role": "assistant",
            "content": [{"type": "text", "text": json.dumps(fix_summary)}],
        }
        transcript.write_text(json.dumps(entry) + "\n")

        payload = {"session_id": str(uuid.uuid4()), "transcript_path": str(transcript)}
        result = run_subagent_stop_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert output["reason"]


# ── Regression: dual block-signal handling (security constraint) ───────────


class TestDualBlockSignalRegression:
    """tool-selection-guard.py signals a hard block via BOTH a stdout JSON shape
    and a separate process exit code 2. A bridge that only reads stdout JSON
    silently turns every hard git-deny block into passthrough allow — this is
    the specific regression the security review flagged (plan Task 3, Step 2).
    """

    def test_git_deny_rule_signals_exit_2_not_just_stdout(self, tmp_path):
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-14",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin main"},
        }
        result = run_guard(payload, cwd=tmp_path)
        # The critical assertion: exit code IS the signal. A bridge reading
        # only stdout could see empty/non-JSON stdout here and default to
        # "no opinion" (allow) — exit code 2 must be checked independently.
        assert result.returncode == 2
        # The block reason is on stderr, per _exit_with_decision's "block" branch —
        # NOT on stdout as JSON (there is deliberately no hookSpecificOutput to
        # parse for a hard block).
        assert result.stdout.strip() == ""
        assert result.stderr.strip() != ""

    def test_reset_hard_signals_exit_2(self, tmp_path):
        payload = {
            "session_id": str(uuid.uuid4()),
            "tool_use_id": "tc-15",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git reset --hard HEAD~1"},
        }
        result = run_guard(payload, cwd=tmp_path)
        assert result.returncode == 2
        assert result.stdout.strip() == ""


# ── Regression: MCP server table sync between mcp_constants.py and
#    omp-extension.ts's OMP_TO_CLAUDE_CODE_MCP_SERVER (no shared schema
#    between the Python allowlist and the hand-written TS re-encoding table,
#    so a server added to one side alone goes undetected until a live OMP
#    session silently stops auto-approving it) ──────────────────────────────


class TestMcpServerSyncContract:
    """Tripwire: every server backing an mcp_constants.py MCP_READ_ONLY entry
    must have a corresponding value in omp-extension.ts's
    OMP_TO_CLAUDE_CODE_MCP_SERVER table (the Claude-Code-shaped server
    identity reencodeMcpToolName() re-encodes into, which mcp_key() then
    matches against MCP_READ_ONLY). If a new server is added to
    mcp_constants.py without a matching bridge-table entry, that server's
    tools silently stop auto-approving under OMP — they fall through to
    reencodeMcpToolName()'s best-effort unknown-server branch and then to
    settings.json passthrough (not a security hole, since unknown servers
    were already excluded from auto-approval) — a silent UX regression
    with no test failure and no visible cause today.

    EXPECTED_BRIDGE_SERVERS is transcribed verbatim from the *values* of
    OMP_TO_CLAUDE_CODE_MCP_SERVER in dev-guard/omp-extension.ts (~lines
    104-115) — re-read the actual .ts file when this needs updating, don't
    reconstruct it from memory or from this comment.
    """

    EXPECTED_BRIDGE_SERVERS = frozenset(
        {
            "plugin_github-mcp_github",
            "plugin_claude-mem_mcp-search",
            "serena",
            "sequential-thinking",
            "plugin_fetchaller-mcp_fetchaller",
            "context7",
            "playwright",
            "plugin_jira_mcp-atlassian-prod",
            "metadata-service",
        }
    )

    def test_mcp_read_only_servers_are_all_mapped_in_omp_bridge_table(self):
        from mcp_constants import MCP_READ_ONLY

        # Server-qualified keys are "server__func" (mcp_constants.py's
        # _qualify()); no server name in the allowlist contains "__" itself,
        # so splitting on the first "__" cleanly isolates the server ID.
        servers_in_read_only = {key.split("__", 1)[0] for key in MCP_READ_ONLY}
        unmapped = servers_in_read_only - self.EXPECTED_BRIDGE_SERVERS
        assert not unmapped, (
            f"mcp_constants.py's MCP_READ_ONLY now covers server(s) {sorted(unmapped)} "
            "with no entry in omp-extension.ts's OMP_TO_CLAUDE_CODE_MCP_SERVER "
            "table. Add a mapping there (and update "
            "TestMcpServerSyncContract.EXPECTED_BRIDGE_SERVERS above) so these "
            "tools keep auto-approving under OMP instead of silently falling "
            "back to settings.json passthrough."
        )
