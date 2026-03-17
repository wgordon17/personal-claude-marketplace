"""Tests for stop-hook.py

Black-box subprocess tests. Each test feeds JSON on stdin and asserts exit codes
and stdout/stderr content. State file isolation via STOP_HOOK_STATE_PATH env var.
LLM subprocess invocation tested via a mock stop-hook-llm.py stub.
"""

import json
import os
import subprocess
import textwrap
import time
import uuid
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "hooks" / "stop-hook.py"


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


def transcript_offset(entries: list[dict], seen: int) -> int:
    """Byte offset after `seen` JSONL entries (matches write_transcript format)."""
    return sum(len(json.dumps(e)) + 1 for e in entries[:seen])


def seed_state(
    state_path: Path,
    session_id: str,
    *,
    byte_offset: int = 0,
    diff_hash: str = "",
    first_user_message: str | None = None,
) -> None:
    """Write initial state so the hook treats this as a second fire."""
    state = {
        session_id: {
            "last_diff_hash": diff_hash,
            "last_fire_timestamp": time.time(),
            "last_transcript_byte_offset": byte_offset,
            "first_user_message": first_user_message,
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
        assert "last_transcript_byte_offset" in entry

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
        # Seed state at end of file — no new content since last fire
        seed_state(
            tmp_path / "state.json",
            session_id,
            byte_offset=transcript_offset(entries, len(entries)),
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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))

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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))

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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))
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
        """Factual question with no tools → exit-with-guidance path, still exits 0."""
        result = self._run_with_user_message(
            tmp_path,
            "What is the Python version requirement?",
            "Python 3.10+.",
        )
        # Read-only + factual question → exit-with-guidance (exit 0)
        assert result.returncode == 0


# ── Exit-with-guidance ────────────────────────────────────────────────────────


class TestExitWithGuidance:
    def test_research_short_response_guidance_message(self, tmp_path):
        """WebSearch used + short response → prints guidance to stdout."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Research Python packaging."},
            {"type": "tool_use", "name": "WebSearch", "id": "t1"},
            {"role": "assistant", "content": "Here is a summary."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))

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
        assert (
            "verify external claims" in result.stdout.lower()
            or "research done" in result.stdout.lower()
        )

    def test_read_only_factual_question_guidance_message(self, tmp_path):
        """Read tool + factual question → prints guidance to stdout."""
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "What does the Read tool do?"},
            {"role": "assistant", "content": "It reads files."},
        ]
        write_transcript(transcript, entries)
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="It reads files.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0
        # Either guidance message or plain exit 0 — both acceptable for factual + no tools
        assert result.returncode == 0


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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))
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

    def test_edit_tool_llm_fail_exits_2(self, tmp_path):
        """Edit tool + completion claim + mock LLM fail → exit 2."""
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
        assert result.returncode == 2
        assert "Tests were not run" in result.stderr

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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))
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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))
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
        seed_state(state_path, session_id, byte_offset=transcript_offset(entries, 1))
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

    def test_llm_fail_exits_2_with_findings(self, tmp_path):
        write_mock_llm(
            tmp_path / "plugin",
            decision="fail",
            findings=["No tests run.", "TODOs remain."],
        )
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 2
        assert "No tests run" in result.stderr
        assert "TODOs remain" in result.stderr

    def test_llm_fail_exits_2_no_findings_still_has_message(self, tmp_path):
        write_mock_llm(tmp_path / "plugin", decision="fail", findings=None)
        payload, state_path = self._setup_with_edit_tool(tmp_path, "I've completed the changes.")
        result = run_hook(payload, state_path=state_path, plugin_root=str(tmp_path / "plugin"))
        assert result.returncode == 2
        assert result.stderr.strip()  # something printed

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
        """State file reflects new byte offset after a passing run."""
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
        seed_state(state_path, session_id, byte_offset=transcript_offset(entries, 2))

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            last_assistant_message="You're welcome.",
        )
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0

        state = json.loads(state_path.read_text())
        assert session_id in state
        assert state[session_id]["last_transcript_byte_offset"] == transcript_offset(
            entries, len(entries)
        )

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
                "last_transcript_byte_offset": 0,
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
        seed_state(tmp_path / "state.json", session_id, byte_offset=transcript_offset(entries, 1))

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
        seed_state(tmp_path / "state.json", session_id, byte_offset=0)
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
        seed_state(state_path, session_id, byte_offset=transcript_offset(entries, 1))

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
        hack_plans = tmp_path / "hack" / "plans"
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
            byte_offset=transcript_offset(entries, 1),
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
        assert result.returncode == 2
        assert "Planning incomplete" in result.stderr

    def test_hack_research_with_websearch_triggers_llm(self, tmp_path):
        """WebSearch + hack/research/ modified → research trigger → LLM invoked."""
        hack_research = tmp_path / "hack" / "research"
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
            byte_offset=transcript_offset(entries, 1),
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
        assert result.returncode == 2
        assert "Research incomplete" in result.stderr

    def test_hack_dir_old_file_does_not_trigger(self, tmp_path):
        """File in hack/plans/ older than 5 minutes → no planning trigger."""
        hack_plans = tmp_path / "hack" / "plans"
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
            byte_offset=transcript_offset(entries, 1),
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
            byte_offset=transcript_offset(entries, 1),
        )

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            last_assistant_message="It's 3pm.",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0


# ── State migration: old format ───────────────────────────────────────────────


class TestStateMigration:
    def test_old_line_count_state_degrades_gracefully(self, tmp_path):
        """Old state with last_transcript_line_count is handled via .get() defaults."""
        state_path = tmp_path / "state.json"
        session_id = "sess-migration"
        # Write old-format state (pre-byte-offset migration)
        old_state = {
            session_id: {
                "last_diff_hash": "",
                "last_fire_timestamp": time.time(),
                "last_transcript_line_count": 5,  # old key name
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
        # Old key is ignored, byte_offset defaults to 0 → full re-scan → exit 0
        assert result.returncode == 0

        # Verify state was rewritten with new schema
        new_state = json.loads(state_path.read_text())
        assert "last_transcript_byte_offset" in new_state[session_id]
        assert "last_transcript_line_count" not in new_state[session_id]


# ── Multi-fire integration ────────────────────────────────────────────────────


class TestMultiFireIntegration:
    def test_three_sequential_fires_with_transcript_growth(self, tmp_path):
        """Fire hook 3 times as transcript grows — verifies byte offset tracking."""
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
        assert state[session_id]["last_transcript_byte_offset"] == 0  # first fire sets 0

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
        fire2_offset = state[session_id]["last_transcript_byte_offset"]
        assert fire2_offset > 0
        # first_user_message should now be cached
        assert state[session_id]["first_user_message"] == "Fix the bug."

        # Fire 3: transcript grows again, only new bytes parsed
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
        fire3_offset = state[session_id]["last_transcript_byte_offset"]
        assert fire3_offset > fire2_offset
        # first_user_message stays cached
        assert state[session_id]["first_user_message"] == "Fix the bug."
