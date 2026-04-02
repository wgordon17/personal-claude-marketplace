"""Tests for stop-hook.py

Black-box subprocess tests. Each test feeds JSON on stdin and asserts exit codes
and stdout/stderr content. State file isolation via STOP_HOOK_STATE_PATH env var.
LLM subprocess invocation tested via a mock stop-hook-llm.py stub.
"""

import importlib.util
import json
import os
import subprocess
import textwrap
import time
import uuid
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "hooks" / "stop-hook.py"
LLM_SCRIPT = Path(__file__).parent.parent / "hooks" / "stop-hook-llm.py"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_payload(
    *,
    session_id: str | None = None,
    transcript_path: str = "",
    cwd: str = ".",
    stop_hook_active: bool = False,
    last_assistant_message: str = "",
) -> dict:
    return {
        "hook_event_name": "Stop",
        "session_id": session_id or str(uuid.uuid4()),
        "transcript_path": transcript_path,
        "cwd": cwd,
        "permission_mode": "acceptEdits",
        "stop_hook_active": stop_hook_active,
        "last_assistant_message": last_assistant_message,
    }


def run_hook(
    payload: dict,
    *,
    state_path: Path | None = None,
    plugin_root: str = "",
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if state_path is not None:
        env["STOP_HOOK_STATE_PATH"] = str(state_path)
        # Isolate DB writes so tests don't pollute real guard stats
        env["GUARD_DB_PATH"] = str(state_path.parent / "test-guard.db")
    if plugin_root:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["uv", "run", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def write_transcript(path: Path, entries: list[dict]) -> None:
    """Write a list of dicts as JSONL to a transcript file."""
    lines = [json.dumps(e) for e in entries]
    path.write_text("\n".join(lines) + "\n")


def seed_state(
    state_path: Path,
    session_id: str,
    *,
    evaluated_tool_count: int = 0,
    last_file_size: int = 0,
    diff_hash: str = "",
) -> None:
    """Write initial state so the hook treats this as a second fire."""
    state = {
        session_id: {
            "last_diff_hash": diff_hash,
            "last_fire_timestamp": time.time(),
            "evaluated_tool_count": evaluated_tool_count,
            "last_file_size": last_file_size,
        }
    }
    state_path.write_text(json.dumps(state))


def write_mock_llm(
    plugin_root: Path,
    *,
    decision: str = "pass",
    findings: list[str] | None = None,
) -> None:
    """Write a mock stop-hook-llm.py stub to plugin_root/hooks/.

    Serializes findings as a JSON string embedded in Python source so there are
    no Python syntax issues with None vs JSON null.
    """
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    # Embed as a JSON string constant parsed at runtime — avoids null/None mismatch
    result_json = json.dumps({"decision": decision, "reasoning": "mock", "findings": findings})
    stub = textwrap.dedent(f"""\
        #!/usr/bin/env -S uv run
        # /// script
        # requires-python = ">=3.10"
        # ///
        import json, sys
        result = json.loads({result_json!r})
        print(json.dumps(result))
        sys.exit(2 if result["decision"] == "fail" else 0)
    """)
    llm_script = hooks_dir / "stop-hook-llm.py"
    llm_script.write_text(stub)
    llm_script.chmod(0o755)


# ── Fast-exit: loop guard ─────────────────────────────────────────────────────


class TestLoopGuard:
    def test_stop_hook_active_exits_0(self, tmp_path):
        payload = make_payload(stop_hook_active=True)
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"

    def test_stop_hook_active_does_not_write_state(self, tmp_path):
        state_path = tmp_path / "state.json"
        payload = make_payload(stop_hook_active=True)
        run_hook(payload, state_path=state_path)
        assert not state_path.exists()


# ── Fast-exit: first fire ─────────────────────────────────────────────────────


class TestFirstFire:
    def test_first_fire_exits_0(self, tmp_path):
        payload = make_payload(session_id="sess-abc")
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"

    def test_first_fire_creates_state_file(self, tmp_path):
        state_path = tmp_path / "state.json"
        session_id = "sess-first-fire"
        payload = make_payload(session_id=session_id)
        run_hook(payload, state_path=state_path)
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert session_id in state
        entry = state[session_id]
        assert "last_diff_hash" in entry
        assert "last_fire_timestamp" in entry
        assert "evaluated_tool_count" in entry
        assert "last_file_size" in entry

    def test_first_fire_with_corrupt_state_exits_0(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("NOT JSON {{{{")
        payload = make_payload(session_id="sess-corrupt")
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0


# ── Fast-exit: no new transcript content ──────────────────────────────────────


class TestZeroNewContent:
    def test_no_new_content_exits_0(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        session_id = "sess-no-new-content"
        entries = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        write_transcript(transcript, entries)
        # Seed state with the actual file size — triggers file-size-unchanged fast-exit
        seed_state(
            tmp_path / "state.json",
            session_id,
            last_file_size=transcript.stat().st_size,
        )

        payload = make_payload(session_id=session_id, transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0


# ── Fast-exit: short response with no signals ─────────────────────────────────


class TestShortResponseFastExit:
    def test_short_response_no_tools_no_diff_exits_0(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        session_id = "sess-short"
        entries = [
            {"role": "user", "content": "What time is it?"},
            {"role": "assistant", "content": "It is 3pm."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="It is 3pm.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_long_response_does_not_fast_exit_short_path(self, tmp_path):
        """A long assistant message bypasses the short-response fast exit."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = "sess-long"
        long_msg = "x" * 300  # > 200 chars
        entries = [
            {"role": "user", "content": "Do something."},
            {"role": "assistant", "content": long_msg},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        # No plugin root → LLM fails open → still exits 0, but didn't short-circuit
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=long_msg,
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        # Fails open because no LLM configured
        assert result.returncode == 0


# ── Question classification ───────────────────────────────────────────────────


class TestQuestionClassification:
    def _run_with_user_message(
        self,
        tmp_path,
        user_msg: str,
        assistant_msg: str = "Sure.",
    ) -> subprocess.CompletedProcess:
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=assistant_msg,
        )
        return run_hook(payload, state_path=tmp_path / "state.json")

    def test_meta_question_exits_0(self, tmp_path):
        result = self._run_with_user_message(tmp_path, "Looks good, proceed.")
        assert result.returncode == 0

    def test_meta_lgtm_exits_0(self, tmp_path):
        result = self._run_with_user_message(tmp_path, "LGTM, go ahead and merge.")
        assert result.returncode == 0

    def test_opinion_question_exits_0(self, tmp_path):
        result = self._run_with_user_message(tmp_path, "Should I use PostgreSQL or SQLite?")
        assert result.returncode == 0

    def test_opinion_recommend_exits_0(self, tmp_path):
        result = self._run_with_user_message(tmp_path, "What would you recommend for caching?")
        assert result.returncode == 0

    def test_factual_question_does_not_exit_via_meta_opinion(self, tmp_path):
        """Factual question with no tools → fast-exit path, exits 0."""
        result = self._run_with_user_message(
            tmp_path,
            "What is the Python version requirement?",
            "Python 3.10+.",
        )
        # Read-only + factual question → fast-exit (exit 0)
        assert result.returncode == 0


# ── Fast-exit: research and read-only paths ──────────────────────────────────


class TestResearchAndReadOnlyFastExit:
    def test_research_short_response_exits_0(self, tmp_path):
        """WebSearch used + short response → fast-exit 0."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Research Python packaging."},
            {"type": "tool_use", "name": "WebSearch", "id": "t1"},
            {"role": "assistant", "content": "Here is a summary."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        # Pass cwd=tmp_path (non-git dir) so git diff returns empty string,
        # matching the seeded empty diff_hash — otherwise diff_changed=True blocks this path.
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="Here is a summary.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_context7_mcp_counts_as_research(self, tmp_path):
        """Context7 MCP tool (prefix match) triggers research fast-exit."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Check the latest API for this library."},
            {"type": "tool_use", "name": "mcp__context7__query-docs", "id": "t1"},
            {"role": "assistant", "content": "Here are the docs."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="Here are the docs.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_github_mcp_search_code_counts_as_research(self, tmp_path):
        """Selective GitHub MCP tool triggers research fast-exit."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Find how upstream handles auth."},
            {"type": "tool_use", "name": "mcp__plugin_github-mcp_github__search_code", "id": "t1"},
            {"role": "assistant", "content": "Found the pattern."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="Found the pattern.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_github_mcp_list_issues_not_research(self, tmp_path):
        """Non-research GitHub MCP tool (list_issues) does NOT trigger research fast-exit."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Check my open issues."},
            {"type": "tool_use", "name": "mcp__plugin_github-mcp_github__list_issues", "id": "t1"},
            {"role": "assistant", "content": "You have 3 open issues."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="You have 3 open issues.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_read_only_factual_question_exits_0(self, tmp_path):
        """Factual question with no tools → fast-exit 0."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "What does the Read tool do?"},
            {"role": "assistant", "content": "It reads files."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="It reads files.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0


# ── Fast-exit: AskUserQuestion ────────────────────────────────────────────────


class TestAskUserQuestionFastExit:
    def test_ask_user_question_last_tool_exits_0(self, tmp_path):
        """AskUserQuestion as last tool call → fast-exit 0 (agent asked user)."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the authentication bug."},
            {"type": "tool_use", "name": "Read", "id": "t1"},
            {"type": "tool_use", "name": "AskUserQuestion", "id": "t2"},
            {
                "role": "assistant",
                "content": "Should I use OAuth2 or session-based auth for this fix?",
            },
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Should I use OAuth2 or session-based auth for this fix?",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0
        # Should NOT contain decision=block JSON
        if result.stdout.strip():
            assert "block" not in result.stdout

    def test_ask_user_question_not_last_tool_does_not_fast_exit(self, tmp_path):
        """AskUserQuestion followed by Edit → no fast-exit, LLM invoked."""
        # Use fail-returning mock to prove LLM was actually invoked (not fast-exited)
        write_mock_llm(tmp_path / "plugin", decision="fail", findings=["Tests not run."])
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "AskUserQuestion", "id": "t1"},
            {"type": "tool_use", "name": "Edit", "id": "t2"},
            {
                "role": "assistant",
                "content": "I've completed the fix. The changes are ready.",
            },
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the fix. The changes are ready.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        # LLM was invoked (not fast-exited) and returned block
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "Tests not run" in output["reason"]

    def test_ask_user_question_last_but_edit_also_used_invokes_llm(self, tmp_path):
        """Edit + AskUserQuestion (last) → no fast-exit because write signals present."""
        write_mock_llm(tmp_path / "plugin", decision="fail", findings=["Tests not run."])
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Implement the auth module."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"type": "tool_use", "name": "AskUserQuestion", "id": "t2"},
            {
                "role": "assistant",
                "content": "I've started the implementation. Should I add OAuth2 as well?",
            },
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=(
                "I've started the implementation. Should I add OAuth2 as well?"
            ),
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        # Write signals present → LLM invoked despite AskUserQuestion being last
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "Tests not run" in output["reason"]


# ── Signal detection: write tools ─────────────────────────────────────────────


class TestWriteToolSignals:
    def _make_transcript_with_tool(self, tmp_path: Path, tool_name: str) -> tuple[str, Path, str]:
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": tool_name, "id": "t1"},
            {"role": "assistant", "content": "I've completed the fix. The changes are ready."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        return session_id, transcript, "I've completed the fix. The changes are ready."

    def test_edit_tool_triggers_llm_path(self, tmp_path):
        """Edit tool in transcript → LLM path. Mock LLM returns pass → exit 0."""
        write_mock_llm(tmp_path / "plugin", decision="pass")
        session_id, transcript, msg = self._make_transcript_with_tool(tmp_path, "Edit")
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=msg,
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0

    def test_edit_tool_llm_fail_blocks_via_json(self, tmp_path):
        """Edit tool + completion claim + mock LLM fail → exit 0 with decision=block JSON."""
        write_mock_llm(tmp_path / "plugin", decision="fail", findings=["Tests were not run."])
        session_id, transcript, msg = self._make_transcript_with_tool(tmp_path, "Edit")
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=msg,
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "Tests were not run" in output["reason"]

    def test_write_tool_triggers_llm_path(self, tmp_path):
        write_mock_llm(tmp_path / "plugin", decision="pass")
        session_id, transcript, msg = self._make_transcript_with_tool(tmp_path, "Write")
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=msg,
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0

    def test_notebook_edit_triggers_llm_path(self, tmp_path):
        write_mock_llm(tmp_path / "plugin", decision="pass")
        session_id, transcript, msg = self._make_transcript_with_tool(tmp_path, "NotebookEdit")
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=msg,
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0


# ── Signal detection: MCP write tools ────────────────────────────────────────


class TestMCPWriteToolSignals:
    def _make_transcript_with_mcp(
        self,
        tmp_path: Path,
        tool_name: str,
        assistant_msg: str,
    ) -> tuple[str, Path]:
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Do the work."},
            {"type": "tool_use", "name": tool_name, "id": "t1"},
            {"role": "assistant", "content": assistant_msg},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        return session_id, transcript

    def test_mcp_read_only_tool_does_not_trigger(self, tmp_path):
        """find_symbol is read-only MCP — should not trigger write signal."""
        write_mock_llm(tmp_path / "plugin", decision="pass")
        session_id, transcript = self._make_transcript_with_mcp(
            tmp_path, "mcp__serena__find_symbol", "Here are the results."
        )
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Here are the results.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        # Read-only MCP + short read-only response → fast exit 0 (no LLM needed)
        assert result.returncode == 0

    def test_mcp_write_tool_triggers_llm(self, tmp_path):
        """An MCP tool that is NOT in the read-only list → write signal → LLM."""
        write_mock_llm(tmp_path / "plugin", decision="pass")
        # addCommentToJiraIssue is clearly a write
        session_id, transcript = self._make_transcript_with_mcp(
            tmp_path,
            "mcp__jira__addCommentToJiraIssue",
            "I've completed and added the comment.",
        )
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed and added the comment.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0  # mock LLM passes

    def test_mcp_think_prefix_does_not_trigger(self, tmp_path):
        """think_about_* tools are read-only."""
        session_id, transcript = self._make_transcript_with_mcp(
            tmp_path, "mcp__serena__think_about_problem", "Thinking done."
        )
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Thinking done.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0


# ── Signal detection: completion claims ───────────────────────────────────────


class TestCompletionClaims:
    def _run_with_message(self, tmp_path: Path, assistant_msg: str) -> subprocess.CompletedProcess:
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Implement the feature."},
            {"role": "assistant", "content": assistant_msg},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=assistant_msg,
        )
        return run_hook(payload, state_path=tmp_path / "state.json")

    def test_completion_claim_at_end_triggers_llm_fail_open(self, tmp_path):
        """Completion claim at end of message → triggers LLM path → fails open."""
        result = self._run_with_message(
            tmp_path, "The implementation is ready. I've completed all the work."
        )
        # No plugin_root → LLM fails open → exit 0
        assert result.returncode == 0

    def test_completion_claim_mid_sentence_does_not_trigger(self, tmp_path):
        """Mid-sentence claim should NOT trigger (no MULTILINE, $ end-of-string)."""
        # The claim is mid-message, followed by more content
        msg = (
            "I've completed the first part.\n"
            "However, there are still issues to fix:\n"
            "- Item 1\n"
            "- Item 2"
        )
        result = self._run_with_message(tmp_path, msg)
        # No completion claim at end-of-string, no write tools → fast exit
        assert result.returncode == 0

    def test_all_done_at_end_triggers(self, tmp_path):
        result = self._run_with_message(tmp_path, "That should do it. All done.")
        # fails open → exit 0
        assert result.returncode == 0

    def test_plain_response_no_claim_fast_exits(self, tmp_path):
        """A neutral short response with no claim → fast exit."""
        result = self._run_with_message(tmp_path, "Here is the information you requested.")
        assert result.returncode == 0


# ── LLM subprocess: mock stub behavior ───────────────────────────────────────


class TestLLMSubprocess:
    def _setup_with_edit_tool(self, tmp_path: Path, assistant_msg: str) -> tuple[dict, Path]:
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": assistant_msg},
        ]
        write_transcript(transcript, entries)
        state_path = tmp_path / "state.json"
        seed_state(state_path, session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message=assistant_msg,
        )
        return payload, state_path

    def test_llm_pass_exits_0(self, tmp_path):
        write_mock_llm(tmp_path / "plugin", decision="pass")
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 0

    def test_llm_fail_blocks_with_findings_in_reason(self, tmp_path):
        write_mock_llm(
            tmp_path / "plugin",
            decision="fail",
            findings=["No tests run.", "TODOs remain."],
        )
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "No tests run" in output["reason"]
        assert "TODOs remain" in output["reason"]

    def test_llm_fail_no_findings_still_has_reason(self, tmp_path):
        write_mock_llm(tmp_path / "plugin", decision="fail", findings=None)
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert output["reason"]  # non-empty reason

    def test_missing_llm_script_fails_open(self, tmp_path):
        """No stop-hook-llm.py → subprocess fails → fail-open → exit 0."""
        (tmp_path / "plugin" / "hooks").mkdir(parents=True, exist_ok=True)
        # No llm script written
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 0

    def test_llm_unreachable_fails_open(self, tmp_path):
        """LLM script at unreachable path → FileNotFoundError → fail-open → exit 0."""
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root="/nonexistent/path")
        assert result.returncode == 0


# ── State file management ────────────────────────────────────────────────────


class TestStateManagement:
    def test_state_updated_after_pass(self, tmp_path):
        """State file reflects updated evaluated_tool_count and last_file_size after pass."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = "sess-state-update"
        entries = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "A programming language."},
            {"role": "user", "content": "Thank you."},
            {"role": "assistant", "content": "You're welcome."},
        ]
        write_transcript(transcript, entries)
        state_path = tmp_path / "state.json"
        seed_state(state_path, session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="You're welcome.",
        )
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0

        state = json.loads(state_path.read_text())
        assert session_id in state
        assert "evaluated_tool_count" in state[session_id]
        assert "last_file_size" in state[session_id]
        assert state[session_id]["last_file_size"] == transcript.stat().st_size

    def test_stale_sessions_pruned(self, tmp_path):
        """Sessions older than 24h are removed from state on next run."""
        state_path = tmp_path / "state.json"
        old_session = "sess-old"
        fresh_session = "sess-fresh"
        old_time = time.time() - (25 * 3600)  # 25h ago

        state = {
            old_session: {
                "last_diff_hash": "",
                "last_fire_timestamp": old_time,
                "evaluated_tool_count": 0,
                "last_file_size": 0,
            },
        }
        state_path.write_text(json.dumps(state))

        # Fire with a new session — triggers cleanup
        payload = make_payload(session_id=fresh_session)
        run_hook(payload, state_path=state_path)

        new_state = json.loads(state_path.read_text())
        assert old_session not in new_state
        assert fresh_session in new_state

    def test_multiple_sessions_isolated(self, tmp_path):
        """Two sessions maintain independent state."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        write_transcript(transcript, entries)
        state_path = tmp_path / "state.json"

        sid_a = "sess-a"
        sid_b = "sess-b"

        # First fire for A
        run_hook(
            make_payload(session_id=sid_a, transcript_path=str(transcript)),
            state_path=state_path,
        )
        # First fire for B
        run_hook(
            make_payload(session_id=sid_b, transcript_path=str(transcript)),
            state_path=state_path,
        )

        state = json.loads(state_path.read_text())
        assert sid_a in state
        assert sid_b in state


# ── Non-git directory ─────────────────────────────────────────────────────────


class TestNonGitDirectory:
    def test_non_git_dir_exits_0(self, tmp_path):
        """Hook in a non-git directory should not crash — fails gracefully."""
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()

        transcript = non_git / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hi there."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(non_git),
            last_assistant_message="Hi there.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_missing_transcript_exits_0(self, tmp_path):
        """Missing transcript file → graceful fast exit."""
        session_id = str(uuid.uuid4())
        seed_state(tmp_path / "state.json", session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path="/nonexistent/path/transcript.jsonl",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0


# ── Agent/Task tool signals ───────────────────────────────────────────────────


class TestAgentToolSignals:
    def test_task_tool_triggers_llm_path(self, tmp_path):
        """Task tool → subagent signal → LLM path."""
        write_mock_llm(tmp_path / "plugin", decision="pass")
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Do the work."},
            {"type": "tool_use", "name": "Task", "id": "t1"},
            {"role": "assistant", "content": "I've completed the task. Everything is ready."},
        ]
        write_transcript(transcript, entries)
        state_path = tmp_path / "state.json"
        seed_state(state_path, session_id)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the task. Everything is ready.",
        )
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 0  # mock LLM passes


# ── Invalid/malformed stdin ───────────────────────────────────────────────────


class TestMalformedInput:
    def test_empty_stdin_exits_0(self, tmp_path):
        result = subprocess.run(
            ["uv", "run", str(SCRIPT)],
            input="",
            capture_output=True,
            text=True,
            env={**os.environ, "STOP_HOOK_STATE_PATH": str(tmp_path / "state.json")},
        )
        assert result.returncode == 0

    def test_non_json_stdin_exits_0(self, tmp_path):
        result = subprocess.run(
            ["uv", "run", str(SCRIPT)],
            input="not json at all",
            capture_output=True,
            text=True,
            env={**os.environ, "STOP_HOOK_STATE_PATH": str(tmp_path / "state.json")},
        )
        assert result.returncode == 0

    def test_json_array_stdin_exits_0(self, tmp_path):
        result = subprocess.run(
            ["uv", "run", str(SCRIPT)],
            input="[1, 2, 3]",
            capture_output=True,
            text=True,
            env={**os.environ, "STOP_HOOK_STATE_PATH": str(tmp_path / "state.json")},
        )
        assert result.returncode == 0


# ── hack/ directory modification detection ────────────────────────────────────


class TestHackDirModified:
    def test_hack_plans_recently_modified_triggers_llm(self, tmp_path):
        """Recently modified file in hack/plans/ → planning trigger → LLM invoked."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        (hack / "PROJECT.md").write_text("# Project")
        (hack / "SESSIONS.md").write_text("# Sessions")
        hack_plans = hack / "plans"
        hack_plans.mkdir(parents=True)
        (hack_plans / "2026-03-16-test.md").write_text("# Plan")

        write_mock_llm(tmp_path / "plugin", decision="fail", findings=["Planning incomplete."])

        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Create a plan."},
            {
                "role": "assistant",
                "content": "I've written the plan to hack/plans/. The planning is complete.",
            },
        ]
        write_transcript(transcript, entries)
        seed_state(
            tmp_path / "state.json",
            session_id,
        )

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message=(
                "I've written the plan to hack/plans/. The planning is complete."
            ),
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        # LLM was invoked (not fast-exited) and returned fail
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "Planning incomplete" in output["reason"]

    def test_hack_research_with_websearch_triggers_llm(self, tmp_path):
        """WebSearch + hack/research/ modified → research trigger → LLM invoked."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        (hack / "PROJECT.md").write_text("# Project")
        (hack / "SESSIONS.md").write_text("# Sessions")
        hack_research = hack / "research"
        hack_research.mkdir(parents=True)
        (hack_research / "2026-03-16-test.md").write_text("# Research")

        write_mock_llm(tmp_path / "plugin", decision="fail", findings=["Research incomplete."])

        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        long_msg = (
            "I've completed the research and written findings to hack/research/. "
            "The analysis covers technical feasibility, cost implications, "
            "and implementation complexity across multiple dimensions. "
            "All key claims are backed by primary source research. The research is complete."
        )
        entries = [
            {"role": "user", "content": "Research this topic."},
            {"type": "tool_use", "name": "WebSearch", "id": "t1"},
            {"role": "assistant", "content": long_msg},
        ]
        write_transcript(transcript, entries)
        seed_state(
            tmp_path / "state.json",
            session_id,
        )

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message=long_msg,
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "Research incomplete" in output["reason"]

    def test_hack_dir_old_file_does_not_trigger(self, tmp_path):
        """File in hack/plans/ older than 5 minutes → no planning trigger."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        (hack / "PROJECT.md").write_text("# Project")
        (hack / "SESSIONS.md").write_text("# Sessions")
        hack_plans = hack / "plans"
        hack_plans.mkdir(parents=True)
        old_file = hack_plans / "old-plan.md"
        old_file.write_text("# Old plan")
        # Set mtime to 10 minutes ago
        old_mtime = time.time() - 600
        os.utime(old_file, (old_mtime, old_mtime))

        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "What time is it?"},
            {"role": "assistant", "content": "It's 3pm."},
        ]
        write_transcript(transcript, entries)
        seed_state(
            tmp_path / "state.json",
            session_id,
        )

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="It's 3pm.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        # Short response, no signals → fast exit 0
        assert result.returncode == 0

    def test_no_hack_dir_does_not_trigger(self, tmp_path):
        """No hack/ directory → no planning/research trigger."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "What time is it?"},
            {"role": "assistant", "content": "It's 3pm."},
        ]
        write_transcript(transcript, entries)
        seed_state(
            tmp_path / "state.json",
            session_id,
        )

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="It's 3pm.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0


# ── Module loader for unit tests ──────────────────────────────────────────────


def _load_stop_hook_module():
    """Import stop-hook.py as a module for unit testing internal functions."""
    spec = importlib.util.spec_from_file_location("stop_hook", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Unit tests for _check_hack_dir_modified ───────────────────────────────────


class TestCheckHackDirModified:
    """Unit tests for _check_hack_dir_modified internal function."""

    def setup_method(self):
        self.mod = _load_stop_hook_module()

    def test_hack_dir_without_core_files_not_detected(self, tmp_path):
        """hack/ with plans/ but no core memory files → content validation fails → no trigger."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        # Create plans/ with a recently-modified file
        hack_plans = hack / "plans"
        hack_plans.mkdir(parents=True)
        (hack_plans / "my-plan.md").write_text("# Plan")
        # Do NOT create any core memory files — directory should be rejected

        result = self.mod._check_hack_dir_modified(str(tmp_path))
        assert result == {"plans": False, "research": False}

    def test_hack_dir_with_one_core_file_not_detected(self, tmp_path):
        """hack/ with exactly 1 core memory file is below the 2-file threshold → no trigger."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        # Create exactly 1 core memory file (threshold requires 2+)
        (hack / "PROJECT.md").write_text("# Project")
        # Create plans/ with a recently-modified file
        hack_plans = hack / "plans"
        hack_plans.mkdir(parents=True)
        (hack_plans / "my-plan.md").write_text("# Plan")

        result = self.mod._check_hack_dir_modified(str(tmp_path))
        assert result == {"plans": False, "research": False}

    def test_hack_dir_with_two_core_files_detected(self, tmp_path):
        """hack/ with 2+ core memory files passes validation → plans trigger works."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        (hack / "PROJECT.md").write_text("# Project")
        (hack / "SESSIONS.md").write_text("# Sessions")
        hack_plans = hack / "plans"
        hack_plans.mkdir(parents=True)
        (hack_plans / "my-plan.md").write_text("# Plan")

        result = self.mod._check_hack_dir_modified(str(tmp_path))
        assert result == {"plans": True, "research": False}

    def test_hack_fails_validation_falls_back_to_local(self, tmp_path):
        """hack/ has 1 core file (fails), .local/ has 2 (passes) → .local/ plans detected."""
        hack = tmp_path / "hack"
        hack.mkdir(parents=True)
        (hack / "PROJECT.md").write_text("# Project")
        local = tmp_path / ".local"
        local.mkdir(parents=True)
        (local / "PROJECT.md").write_text("# Project")
        (local / "SESSIONS.md").write_text("# Sessions")
        local_plans = local / "plans"
        local_plans.mkdir(parents=True)
        (local_plans / "my-plan.md").write_text("# Plan")

        result = self.mod._check_hack_dir_modified(str(tmp_path))
        assert result == {"plans": True, "research": False}

    def test_all_candidate_dirs_fail_validation(self, tmp_path):
        """All four candidate dirs exist but none have 2+ core files → no trigger."""
        for dirname in ("hack", ".local", "scratch", ".dev"):
            d = tmp_path / dirname
            d.mkdir(parents=True)
            (d / "PROJECT.md").write_text("# Project")
            plans = d / "plans"
            plans.mkdir(parents=True)
            (plans / "my-plan.md").write_text("# Plan")

        result = self.mod._check_hack_dir_modified(str(tmp_path))
        assert result == {"plans": False, "research": False}


# ── State migration: old format ───────────────────────────────────────────────


class TestStateMigration:
    def test_old_byte_offset_state_degrades_gracefully(self, tmp_path):
        """Old state with last_transcript_byte_offset is handled via .get() defaults."""
        state_path = tmp_path / "state.json"
        session_id = "sess-migration"
        # Write old-format state (pre-count migration)
        old_state = {
            session_id: {
                "last_diff_hash": "",
                "last_fire_timestamp": time.time(),
                "last_transcript_byte_offset": 42,  # old key name
                "first_user_message": "Hello.",  # old key name
            }
        }
        state_path.write_text(json.dumps(old_state))

        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hi."},
        ]
        write_transcript(transcript, entries)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Hi.",
        )
        result = run_hook(payload, state_path=state_path)
        # Old keys ignored, evaluated_tool_count defaults to 0 → full parse → exit 0
        assert result.returncode == 0

        # Verify state was rewritten with new schema
        new_state = json.loads(state_path.read_text())
        assert "evaluated_tool_count" in new_state[session_id]
        assert "last_file_size" in new_state[session_id]
        assert "last_transcript_byte_offset" not in new_state[session_id]
        assert "first_user_message" not in new_state[session_id]


# ── Multi-fire integration ────────────────────────────────────────────────────


class TestMultiFireIntegration:
    def test_three_sequential_fires_with_transcript_growth(self, tmp_path):
        """Fire hook 3 times as transcript grows — verifies file size and count tracking."""
        transcript = tmp_path / "transcript.jsonl"
        state_path = tmp_path / "state.json"
        session_id = "sess-multi-fire"

        # Fire 1: first fire, initializes state
        entries_v1 = [
            {"role": "user", "content": "Fix the bug."},
        ]
        write_transcript(transcript, entries_v1)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="",
        )
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0
        state = json.loads(state_path.read_text())
        # First fire initializes evaluated_tool_count=0 and last_file_size=0
        assert state[session_id]["evaluated_tool_count"] == 0
        assert state[session_id]["last_file_size"] == 0

        # Fire 2: transcript has grown, hook parses new content
        entries_v2 = entries_v1 + [
            {"role": "assistant", "content": "Looking into it."},
        ]
        write_transcript(transcript, entries_v2)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Looking into it.",
        )
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0
        state = json.loads(state_path.read_text())
        fire2_size = state[session_id]["last_file_size"]
        assert fire2_size > 0
        assert fire2_size == transcript.stat().st_size

        # Fire 3: transcript grows again
        entries_v3 = entries_v2 + [
            {"role": "user", "content": "Is it done yet?"},
            {"role": "assistant", "content": "Almost."},
        ]
        write_transcript(transcript, entries_v3)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Almost.",
        )
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0
        state = json.loads(state_path.read_text())
        fire3_size = state[session_id]["last_file_size"]
        assert fire3_size > fire2_size
        assert fire3_size == transcript.stat().st_size


# ── Unit tests for _detect_doc_gap ───────────────────────────────────────────


class TestDetectDocGap:
    """Unit tests for _detect_doc_gap heuristic.

    _detect_doc_gap now accepts a pre-fetched file list (not a cwd), so tests
    pass file lists directly — no git repo setup needed.
    """

    def setup_method(self):
        self.mod = _load_stop_hook_module()

    def test_component_without_docs_returns_true(self):
        """Source files changed, no doc files -> gap detected."""
        assert self.mod._detect_doc_gap(["src/main.py"]) is True

    def test_component_with_readme_returns_false(self):
        """Source files changed WITH README -> no gap."""
        assert self.mod._detect_doc_gap(["src/main.py", "README.md"]) is False

    def test_only_doc_files_returns_false(self):
        """Only doc files changed -> no gap."""
        assert self.mod._detect_doc_gap(["README.md", "docs/guide.md"]) is False

    def test_empty_list_returns_false(self):
        """Empty file list -> no gap."""
        assert self.mod._detect_doc_gap([]) is False

    def test_manifest_counts_as_docs(self):
        """package.json alongside .ts -> no gap."""
        assert self.mod._detect_doc_gap(["index.ts", "package.json"]) is False

    def test_any_md_file_counts_as_docs(self):
        """Any .md file counts as docs, even outside docs/ directory."""
        assert self.mod._detect_doc_gap(["src/lib.py", "ARCHITECTURE.md"]) is False
        assert self.mod._detect_doc_gap(["src/lib.py", "skills/swarm/SKILL.md"]) is False
        assert self.mod._detect_doc_gap(["src/lib.py", "references/schema.md"]) is False

    def test_rst_and_adoc_count_as_docs(self):
        """Non-markdown doc formats are recognized."""
        assert self.mod._detect_doc_gap(["lib.rs", "docs/guide.rst"]) is False
        assert self.mod._detect_doc_gap(["Main.java", "README.adoc"]) is False

    def test_c_and_cpp_are_components(self):
        """C/C++ files are recognized as components."""
        assert self.mod._detect_doc_gap(["src/main.c"]) is True
        assert self.mod._detect_doc_gap(["src/parser.cpp"]) is True
        assert self.mod._detect_doc_gap(["src/main.c", "README.md"]) is False

    def test_src_news_py_is_component_not_doc(self):
        """A .py file named 'news' is a component, not documentation."""
        assert self.mod._detect_doc_gap(["src/news.py"]) is True

    def test_pyproject_toml_counts_as_docs(self):
        """pyproject.toml is a manifest -> counts as docs."""
        assert self.mod._detect_doc_gap(["src/app.py", "pyproject.toml"]) is False

    def test_cargo_toml_counts_as_docs(self):
        """Cargo.toml is a manifest -> counts as docs (case-insensitive)."""
        assert self.mod._detect_doc_gap(["src/main.rs", "Cargo.toml"]) is False


# ── Unit tests for _parse_transcript ─────────────────────────────────────────


class TestParseTranscript:
    """Unit tests for _parse_transcript (tail-read, user filtering, slicing)."""

    def setup_method(self):
        self.mod = _load_stop_hook_module()

    def test_recent_user_messages_window(self, tmp_path):
        """8 user messages → only last 5 returned (recent_user_limit=5)."""
        transcript = tmp_path / "t.jsonl"
        entries = [
            {"role": "user", "content": f"message {i}"}
            for i in range(1, 9)  # 8 user messages
        ]
        write_transcript(transcript, entries)
        all_tools, user_msgs, _ = self.mod._parse_transcript(str(transcript))
        assert len(user_msgs) == 5
        assert user_msgs[0] == "message 4"
        assert user_msgs[-1] == "message 8"

    def test_tool_result_user_entries_filtered(self, tmp_path):
        """User entries whose content is a list of only tool_result blocks are skipped."""
        transcript = tmp_path / "t.jsonl"
        entries = [
            {"role": "user", "content": "Fix the bug."},
            # Pure tool_result — no text blocks, should be filtered
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
            {"role": "assistant", "content": "Done."},
        ]
        write_transcript(transcript, entries)
        _, user_msgs, _ = self.mod._parse_transcript(str(transcript))
        assert user_msgs == ["Fix the bug."]

    def test_user_entry_with_text_and_tool_result_blocks_kept(self, tmp_path):
        """User entry with mixed text+tool_result blocks: text blocks are extracted."""
        transcript = tmp_path / "t.jsonl"
        entries = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                    {"type": "text", "text": "Here is my follow-up."},
                ],
            },
        ]
        write_transcript(transcript, entries)
        _, user_msgs, _ = self.mod._parse_transcript(str(transcript))
        assert user_msgs == ["Here is my follow-up."]

    def test_all_tool_calls_collected_undeduped(self, tmp_path):
        """all_tool_calls returns every occurrence including duplicates."""
        transcript = tmp_path / "t.jsonl"
        entries = [
            {"type": "tool_use", "name": "Read", "id": "t1"},
            {"type": "tool_use", "name": "Read", "id": "t2"},
            {"type": "tool_use", "name": "Edit", "id": "t3"},
        ]
        write_transcript(transcript, entries)
        all_tools, _, _ = self.mod._parse_transcript(str(transcript))
        assert all_tools == ["Read", "Read", "Edit"]

    def test_missing_transcript_returns_empty_tuples(self, tmp_path):
        """Non-existent transcript path → ([], [], [])."""
        all_tools, user_msgs, asst_msgs = self.mod._parse_transcript(
            str(tmp_path / "nonexistent.jsonl")
        )
        assert all_tools == []
        assert user_msgs == []
        assert asst_msgs == []

    def test_recent_assistant_messages_limit(self, tmp_path):
        """More than 10 assistant messages → only last 10 returned."""
        transcript = tmp_path / "t.jsonl"
        entries = [
            {"role": "assistant", "content": f"reply {i}"}
            for i in range(1, 13)  # 12 assistant messages
        ]
        write_transcript(transcript, entries)
        _, _, asst_msgs = self.mod._parse_transcript(str(transcript))
        assert len(asst_msgs) == 10
        assert asst_msgs[0] == "reply 3"
        assert asst_msgs[-1] == "reply 12"


# ── Tool-count dedup and compacted transcript ─────────────────────────────────


class TestToolCountDedup:
    def test_evaluated_count_skips_already_seen_tool_calls(self, tmp_path):
        """evaluated_tool_count from state skips those tools on next fire."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        # Transcript has Edit tool call — we seed as if we already evaluated it
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": "Done."},
        ]
        write_transcript(transcript, entries)
        # evaluated_tool_count=1 means we already counted the Edit call
        seed_state(tmp_path / "state.json", session_id, evaluated_tool_count=1)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Done.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        # No new tool calls after offset → short response fast exit → exit 0
        assert result.returncode == 0

    def test_compacted_transcript_resets_count(self, tmp_path):
        """If evaluated_count > len(all_tool_calls), count resets to 0 (re-evaluate all)."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": "I've completed the fix."},
        ]
        write_transcript(transcript, entries)
        # Seed with a stale count higher than actual tool calls (simulates compaction)
        seed_state(tmp_path / "state.json", session_id, evaluated_tool_count=99)

        write_mock_llm(tmp_path / "plugin", decision="pass")
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the fix.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        # Count reset to 0 → Edit seen → completion claim → LLM invoked → pass
        assert result.returncode == 0
        state = json.loads((tmp_path / "state.json").read_text())
        assert state[session_id]["evaluated_tool_count"] == 1


# ── File-size fast-exit ───────────────────────────────────────────────────────


class TestFileSizeFastExit:
    def test_same_file_size_exits_0_without_parsing(self, tmp_path):
        """Seeding last_file_size == actual file size → fast exit 0."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix it."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": "I've completed the fix."},
        ]
        write_transcript(transcript, entries)
        seed_state(
            tmp_path / "state.json",
            session_id,
            last_file_size=transcript.stat().st_size,
        )
        # Even with a write tool in transcript, file-size fast-exit fires first
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the fix.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0

    def test_grown_file_size_proceeds_to_parse(self, tmp_path):
        """File size larger than seeded → proceeds past fast-exit to parse."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": "I've completed the fix."},
        ]
        write_transcript(transcript, entries)
        # Seed with a smaller size than actual → file has grown → parse proceeds
        seed_state(tmp_path / "state.json", session_id, last_file_size=10)

        write_mock_llm(tmp_path / "plugin", decision="pass")
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the fix.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        # Parse proceeded, Edit seen, completion claim → LLM invoked → pass
        assert result.returncode == 0


# ── LLM context fields: last_assistant_message and recent_user_messages ───────


class TestLLMContextFields:
    def test_llm_receives_last_assistant_message(self, tmp_path):
        """Mock LLM stub receives last_assistant_message in context JSON."""
        # Write a stub that validates the context field and fails if missing
        hooks_dir = tmp_path / "plugin" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        stub = textwrap.dedent("""\
            #!/usr/bin/env -S uv run
            # /// script
            # requires-python = ">=3.10"
            # ///
            import json, sys
            ctx = json.loads(sys.stdin.read())
            if "last_assistant_message" not in ctx:
                print(json.dumps({"decision": "fail", "reasoning": "missing field",
                    "findings": ["last_assistant_message not in context"]}))
                sys.exit(2)
            print(json.dumps({"decision": "pass", "reasoning": "ok", "findings": None}))
            sys.exit(0)
        """)
        (hooks_dir / "stop-hook-llm.py").write_text(stub)
        (hooks_dir / "stop-hook-llm.py").chmod(0o755)

        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": "I've completed the fix."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the fix.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0

    def test_llm_receives_recent_user_messages(self, tmp_path):
        """Mock LLM stub receives recent_user_messages list in context JSON."""
        hooks_dir = tmp_path / "plugin" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        stub = textwrap.dedent("""\
            #!/usr/bin/env -S uv run
            # /// script
            # requires-python = ">=3.10"
            # ///
            import json, sys
            ctx = json.loads(sys.stdin.read())
            if not isinstance(ctx.get("recent_user_messages"), list):
                print(json.dumps({"decision": "fail", "reasoning": "missing field",
                    "findings": ["recent_user_messages not a list"]}))
                sys.exit(2)
            print(json.dumps({"decision": "pass", "reasoning": "ok", "findings": None}))
            sys.exit(0)
        """)
        (hooks_dir / "stop-hook-llm.py").write_text(stub)
        (hooks_dir / "stop-hook-llm.py").chmod(0o755)

        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"type": "tool_use", "name": "Edit", "id": "t1"},
            {"role": "assistant", "content": "I've completed the fix."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="I've completed the fix.",
        )
        result = run_hook(
            payload,
            state_path=tmp_path / "state.json",
            plugin_root=str(tmp_path / "plugin"),
        )
        assert result.returncode == 0


# ── latest_user_message semantic: comes from recent messages, not first ───────


class TestLatestUserMessageSemantic:
    def test_latest_user_message_is_last_not_first(self, tmp_path):
        """latest_user_message is derived from recent_user_messages[-1], not first."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        # First message is an action word; last is a meta word (should fast-exit)
        entries = [
            {"role": "user", "content": "Fix the bug."},
            {"role": "assistant", "content": "Working on it."},
            {"role": "user", "content": "Looks good, proceed."},  # meta — last message
            {"role": "assistant", "content": "Sure."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id)
        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="Sure.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        # last user message is meta ("proceed") → fast-exit 4
        assert result.returncode == 0


# ── Unit tests for stop-hook-llm.py _build_prompt ────────────────────────────


class TestBuildPromptCriteria:
    """Verify _build_prompt includes expected criteria in correct order."""

    @staticmethod
    def _load_llm_module():
        spec = importlib.util.spec_from_file_location("stop_hook_llm", LLM_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _minimal_ctx() -> dict:
        return {
            "recent_user_messages": ["stop"],
            "last_assistant_message": "Stopping.",
            "recent_assistant_messages": ["Stopping."],
            "new_tool_calls": [],
            "git_diff_stat": None,
            "trigger_reasons": ["completion_claim"],
            "work_type": "code_config",
        }

    def test_user_stop_directive_criterion_present(self):
        mod = self._load_llm_module()
        prompt = mod._build_prompt(self._minimal_ctx())
        assert "USER STOP DIRECTIVE" in prompt

    def test_user_stop_directive_before_claim_accuracy(self):
        mod = self._load_llm_module()
        prompt = mod._build_prompt(self._minimal_ctx())
        stop_idx = prompt.index("USER STOP DIRECTIVE")
        claim_idx = prompt.index("CLAIM ACCURACY")
        assert stop_idx < claim_idx

    def test_user_stop_directive_has_redirection_clause(self):
        mod = self._load_llm_module()
        prompt = mod._build_prompt(self._minimal_ctx())
        assert "REDIRECTION" in prompt
        assert "continuation directive" in prompt

    def test_subagent_wait_criterion_present_when_subagent_in_triggers(self):
        mod = self._load_llm_module()
        ctx = self._minimal_ctx()
        ctx["trigger_reasons"] = ["subagent"]
        ctx["work_type"] = "conversation"
        prompt = mod._build_prompt(ctx)
        assert "SUBAGENT WAIT" in prompt

    def test_subagent_wait_criterion_before_subagent_results(self):
        mod = self._load_llm_module()
        ctx = self._minimal_ctx()
        ctx["trigger_reasons"] = ["subagent"]
        ctx["work_type"] = "conversation"
        prompt = mod._build_prompt(ctx)
        wait_idx = prompt.index("SUBAGENT WAIT")
        results_idx = prompt.index("SUBAGENT RESULTS")
        assert wait_idx < results_idx

    def test_subagent_wait_not_present_without_subagent_trigger(self):
        mod = self._load_llm_module()
        ctx = self._minimal_ctx()
        ctx["trigger_reasons"] = ["completion_claim"]
        ctx["work_type"] = "code_config"
        prompt = mod._build_prompt(ctx)
        assert "SUBAGENT WAIT" not in prompt
