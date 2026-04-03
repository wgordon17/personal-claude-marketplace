"""Tests for subagent-stop-hook.py

Black-box subprocess tests. Each test feeds JSON on stdin and asserts exit codes
and stdout content. State file isolation via SUBAGENT_STOP_HOOK_STATE_PATH env var.

Approve: exit 0 + empty stdout.
Block:   exit 0 + {"decision": "block", "reason": "..."} JSON on stdout.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "hooks" / "subagent-stop-hook.py"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_payload(
    *,
    session_id: str | None = None,
    transcript_path: str = "",
    cwd: str = ".",
    permission_mode: str = "default",
) -> dict:
    return {
        "hook_event_name": "SubagentStop",
        "session_id": session_id or str(uuid.uuid4()),
        "transcript_path": transcript_path,
        "cwd": cwd,
        "permission_mode": permission_mode,
    }


def run_hook(
    payload: dict,
    *,
    state_path: Path | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if state_path is not None:
        env["SUBAGENT_STOP_HOOK_STATE_PATH"] = str(state_path)
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


def make_fix_summary_entry(fix_summary: dict) -> dict:
    """Create a transcript entry containing a FixSummary in assistant message content."""
    return {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": json.dumps(fix_summary),
            }
        ],
    }


# Reusable valid FixSummary dict with all required fields.
VALID_FIX_SUMMARY: dict = {
    "schema": "FixSummary",
    "findings_fixed": ["finding-001", "finding-002"],
    "needs_input_items": [],
    "user_deferred": [],
    "fixes": [
        {"id": "finding-001", "description": "Fixed null pointer"},
        {"id": "finding-002", "description": "Added input validation"},
    ],
    "files_modified": ["src/auth.py"],
}


# ── TestFastExit ──────────────────────────────────────────────────────────────


class TestFastExit:
    def test_no_transcript_path_approves(self, tmp_path):
        """Payload with empty transcript_path -> approve (exit 0, empty stdout)."""
        payload = make_payload(transcript_path="")
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"

    def test_missing_transcript_file_approves(self, tmp_path):
        """Payload with nonexistent transcript_path -> approve (file missing, fail-open)."""
        payload = make_payload(transcript_path="/nonexistent/path/transcript.jsonl")
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"


# ── TestFixSummaryDetection ───────────────────────────────────────────────────


class TestFixSummaryDetection:
    def test_transcript_with_valid_fix_summary_approves(self, tmp_path):
        """Transcript containing VALID_FIX_SUMMARY -> structural validation passes -> approve."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"role": "user", "content": "Fix the findings."},
            make_fix_summary_entry(VALID_FIX_SUMMARY),
        ]
        write_transcript(transcript, entries)
        payload = make_payload(transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"

    def test_transcript_without_fix_summary_approves(self, tmp_path):
        """Transcript with only regular messages (no FixSummary) -> not a Fixer -> approve."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"role": "user", "content": "Summarize the repo."},
            {"role": "assistant", "content": "The repo contains 9 plugins."},
        ]
        write_transcript(transcript, entries)
        payload = make_payload(transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"

    def test_fix_summary_with_needs_input_items_approves(self, tmp_path):
        """FixSummary with findings_fixed + needs_input_items (total > 0) -> approve."""
        transcript = tmp_path / "transcript.jsonl"
        fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": ["finding-001"],
            "needs_input_items": ["finding-002"],
            "user_deferred": [],
            "fixes": [{"id": "finding-001", "description": "Patched injection vector"}],
            "files_modified": ["src/db.py"],
        }
        entries = [
            {"role": "user", "content": "Fix findings."},
            make_fix_summary_entry(fix_summary),
        ]
        write_transcript(transcript, entries)
        payload = make_payload(transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"

    def test_fix_summary_embedded_in_prose_approves(self, tmp_path):
        """FixSummary JSON surrounded by prose text in assistant message -> detected -> approve."""
        transcript = tmp_path / "transcript.jsonl"
        fix_summary_json = json.dumps(VALID_FIX_SUMMARY)
        entries = [
            {"role": "user", "content": "Fix findings."},
            {
                "role": "assistant",
                "content": (
                    f"I have completed all fixes. Here is the summary:\n\n"
                    f"{fix_summary_json}\n\n"
                    f"All findings have been addressed."
                ),
            },
        ]
        write_transcript(transcript, entries)
        payload = make_payload(transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"

    def test_fix_summary_in_tool_use_content_block_approves(self, tmp_path):
        """FixSummary in SendMessage tool_use input block -> detected -> approve."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"role": "user", "content": "Fix findings."},
            {
                "type": "tool_use",
                "name": "SendMessage",
                "id": "t1",
                "input": {
                    "to": "team-lead",
                    "message": json.dumps(VALID_FIX_SUMMARY),
                    "summary": "FixSummary handoff",
                },
            },
        ]
        write_transcript(transcript, entries)
        payload = make_payload(transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"


# ── TestValidationFailures ────────────────────────────────────────────────────


class TestValidationFailures:
    def test_empty_fix_summary_blocks(self, tmp_path):
        """FixSummary with all empty arrays (total_accounted=0) -> block with reason."""
        transcript = tmp_path / "transcript.jsonl"
        fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": [],
            "needs_input_items": [],
            "user_deferred": [],
            "fixes": [],
            "files_modified": [],
        }
        entries = [
            {"role": "user", "content": "Fix findings."},
            make_fix_summary_entry(fix_summary),
        ]
        write_transcript(transcript, entries)
        session_id = str(uuid.uuid4())
        payload = make_payload(session_id=session_id, transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() != "", "expected block JSON output, got empty stdout"
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert output["reason"]  # non-empty reason

    def test_whitespace_only_ids_blocks(self, tmp_path):
        """FixSummary with whitespace-only string in findings_fixed -> block."""
        transcript = tmp_path / "transcript.jsonl"
        fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": ["   ", "\t"],
            "needs_input_items": [],
            "user_deferred": [],
            "fixes": [],
            "files_modified": [],
        }
        entries = [
            {"role": "user", "content": "Fix findings."},
            make_fix_summary_entry(fix_summary),
        ]
        write_transcript(transcript, entries)
        session_id = str(uuid.uuid4())
        payload = make_payload(session_id=session_id, transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() != "", "expected block JSON output, got empty stdout"
        output = json.loads(result.stdout)
        assert output["decision"] == "block"

    def test_malformed_transcript_approves(self, tmp_path):
        """Transcript file with invalid JSONL content -> fail-open -> approve."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("NOT JSON AT ALL\n{broken: json\n")
        payload = make_payload(transcript_path=str(transcript))
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        # Fail-open: malformed transcript is treated as no FixSummary found
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"


# ── TestLoopGuard ─────────────────────────────────────────────────────────────


class TestLoopGuard:
    def _make_empty_fix_summary_transcript(self, tmp_path: Path) -> Path:
        transcript = tmp_path / "transcript.jsonl"
        fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": [],
            "needs_input_items": [],
            "user_deferred": [],
            "fixes": [],
            "files_modified": [],
        }
        entries = [
            {"role": "user", "content": "Fix findings."},
            make_fix_summary_entry(fix_summary),
        ]
        write_transcript(transcript, entries)
        return transcript

    def test_first_three_blocks_produce_block_json(self, tmp_path):
        """Run 3 times with empty FixSummary, same session_id, same state_path -> all 3 block."""
        transcript = self._make_empty_fix_summary_transcript(tmp_path)
        session_id = str(uuid.uuid4())
        state_path = tmp_path / "state.json"

        for attempt in range(1, 4):
            payload = make_payload(session_id=session_id, transcript_path=str(transcript))
            result = run_hook(payload, state_path=state_path)
            assert result.returncode == 0, f"attempt {attempt} stderr: {result.stderr!r}"
            assert result.stdout.strip() != "", (
                f"attempt {attempt}: expected block JSON, got empty stdout"
            )
            output = json.loads(result.stdout)
            assert output["decision"] == "block", (
                f"attempt {attempt}: expected 'block', got {output['decision']!r}"
            )

    def test_fourth_block_approves(self, tmp_path):
        """After 3 consecutive blocks, 4th attempt fails open -> approve (empty stdout)."""
        transcript = self._make_empty_fix_summary_transcript(tmp_path)
        session_id = str(uuid.uuid4())
        state_path = tmp_path / "state.json"

        # First 3 attempts -> block
        for _ in range(3):
            payload = make_payload(session_id=session_id, transcript_path=str(transcript))
            run_hook(payload, state_path=state_path)

        # 4th attempt -> fail-open -> approve
        payload = make_payload(session_id=session_id, transcript_path=str(transcript))
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert result.stdout.strip() == "", (
            f"expected approve (empty stdout) on 4th block, got: {result.stdout!r}"
        )

    def test_pass_through_resets_counter(self, tmp_path):
        """Block twice, pass (valid FixSummary), block again -> normal block."""
        state_path = tmp_path / "state.json"
        session_id = str(uuid.uuid4())

        # Empty FixSummary transcript -> produces blocks
        bad_transcript = tmp_path / "bad.jsonl"
        bad_fix_summary = {
            "schema": "FixSummary",
            "findings_fixed": [],
            "needs_input_items": [],
            "user_deferred": [],
            "fixes": [],
            "files_modified": [],
        }
        write_transcript(
            bad_transcript,
            [
                {"role": "user", "content": "Fix findings."},
                make_fix_summary_entry(bad_fix_summary),
            ],
        )

        # Valid FixSummary transcript -> produces approve
        good_transcript = tmp_path / "good.jsonl"
        write_transcript(
            good_transcript,
            [
                {"role": "user", "content": "Fix findings."},
                make_fix_summary_entry(VALID_FIX_SUMMARY),
            ],
        )

        # Block twice (counter = 2)
        for _ in range(2):
            payload = make_payload(session_id=session_id, transcript_path=str(bad_transcript))
            result = run_hook(payload, state_path=state_path)
            output = json.loads(result.stdout)
            assert output["decision"] == "block"

        # Pass (valid FixSummary) -> counter reset for bad_transcript state key
        payload = make_payload(session_id=session_id, transcript_path=str(good_transcript))
        result = run_hook(payload, state_path=state_path)
        assert result.stdout.strip() == "", "expected approve after valid FixSummary"

        # Block again with bad_transcript -> normal block (counter at 1)
        payload = make_payload(session_id=session_id, transcript_path=str(bad_transcript))
        result = run_hook(payload, state_path=state_path)
        assert result.returncode == 0
        assert result.stdout.strip() != "", "expected block after counter reset"
        output = json.loads(result.stdout)
        assert output["decision"] == "block"


# ── TestEdgeCases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_different_transcript_paths_independent(self, tmp_path):
        """Block 3 times for transcript A (same session_id), then block for transcript B.

        Transcript B's loop guard counter is independent -> normal block (not approve).
        """
        state_path = tmp_path / "state.json"
        session_id = str(uuid.uuid4())

        def make_bad_transcript(name: str) -> Path:
            path = tmp_path / name
            fix_summary = {
                "schema": "FixSummary",
                "findings_fixed": [],
                "needs_input_items": [],
                "user_deferred": [],
                "fixes": [],
                "files_modified": [],
            }
            write_transcript(
                path,
                [
                    {"role": "user", "content": "Fix findings."},
                    make_fix_summary_entry(fix_summary),
                ],
            )
            return path

        transcript_a = make_bad_transcript("transcript_a.jsonl")
        transcript_b = make_bad_transcript("transcript_b.jsonl")

        # Block 3 times for transcript A -> loop guard exhausted for A
        for _ in range(3):
            payload = make_payload(session_id=session_id, transcript_path=str(transcript_a))
            run_hook(payload, state_path=state_path)

        # 4th attempt for transcript A -> approve (fail-open)
        payload = make_payload(session_id=session_id, transcript_path=str(transcript_a))
        result_a4 = run_hook(payload, state_path=state_path)
        assert result_a4.stdout.strip() == "", "transcript A 4th attempt should approve"

        # Transcript B (same session) -> normal block (independent counter)
        payload = make_payload(session_id=session_id, transcript_path=str(transcript_b))
        result_b1 = run_hook(payload, state_path=state_path)
        assert result_b1.returncode == 0, f"stderr: {result_b1.stderr!r}"
        assert result_b1.stdout.strip() != "", (
            "transcript B first block should produce block JSON (independent counter)"
        )
        output = json.loads(result_b1.stdout)
        assert output["decision"] == "block"

    def test_stdin_payload_parsing(self, tmp_path):
        """Verify hook correctly extracts session_id and transcript_path from stdin JSON.

        Uses a valid FixSummary scenario to confirm end-to-end parsing works.
        """
        transcript = tmp_path / "transcript.jsonl"
        session_id = str(uuid.uuid4())
        entries = [
            {"role": "user", "content": "Fix findings."},
            make_fix_summary_entry(VALID_FIX_SUMMARY),
        ]
        write_transcript(transcript, entries)

        payload = make_payload(
            session_id=session_id,
            transcript_path=str(transcript),
            cwd=str(tmp_path),
            permission_mode="acceptEdits",
        )
        result = run_hook(payload, state_path=tmp_path / "state.json")
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        # Valid FixSummary -> approve -> empty stdout
        assert result.stdout.strip() == "", f"unexpected stdout: {result.stdout!r}"
