"""Tests for decision-persistence.py

Tests are organized in two groups:
- Unit tests (importlib): pure functions _fingerprint, _sanitize_path_field,
  _parse_metadata, _save_decisions (OSError path).
- Integration tests (subprocess black-box): full hook invocations via stdin JSON,
  using tmp_path for filesystem isolation. CWD is controlled via subprocess cwd= to
  exercise _find_memory_dir git-root walking and fallback behavior.

Exit semantics for this hook:
  exit 0 + empty stdout  → passthrough (no stored decision matched)
  exit 0 + JSON stdout   → PreToolUse auto-answer (permissionDecision: allow + updatedInput)
  PostToolUse: always exit 0, side effect is decision written to decisions file.
"""

import importlib.util
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).parent.parent / "hooks" / "decision-persistence.py"

METADATA_PREFIX = "▸dp:"


# ── Module loader ─────────────────────────────────────────────────────────────


def _load_module():
    """Import decision-persistence.py as a module for unit testing internal functions."""
    spec = importlib.util.spec_from_file_location("decision_persistence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Subprocess helpers ────────────────────────────────────────────────────────


def run_hook(
    payload: dict | None = None,
    *,
    cwd: Path | None = None,
    raw_input: str | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the hook with the given payload on stdin.

    Use payload= for normal JSON payloads. Use raw_input= for non-JSON tests.
    """
    stdin = raw_input if raw_input is not None else json.dumps(payload)
    return subprocess.run(
        ["uv", "run", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        timeout=30,
    )


def make_pre_payload(questions: list[dict]) -> dict:
    """Build a PreToolUse AskUserQuestion payload."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions},
    }


def make_post_payload(
    questions: list[dict],
    answers: dict,
    *,
    source: str = "tool_input",
) -> dict:
    """Build a PostToolUse AskUserQuestion payload.

    source='tool_input': answers in tool_input (fallback path).
    source='tool_response': answers in tool_response (primary path).
    """
    if source == "tool_response":
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": questions},
            "tool_response": {"answers": answers},
        }
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions, "answers": answers},
        "tool_response": {},
    }


def make_fix_defer_question(q_text: str) -> dict:
    """Return an AskUserQuestion question dict with Fix/Defer options."""
    return {"question": q_text, "options": [{"label": "Fix"}, {"label": "Defer"}]}


def make_dp_question(
    description: str,
    *,
    file: str = "src/auth.py",
    line: str = "42",
    cat: str = "Security",
    skill: str = "pr-review",
) -> dict:
    """Return a Fix/Defer question with a valid ▸dp: metadata suffix."""
    q_text = f"{description} {METADATA_PREFIX}file={file},line={line},cat={cat},skill={skill}"
    return make_fix_defer_question(q_text)


def seed_decisions_file(path: Path, decisions: list[dict]) -> None:
    """Write a decisions JSON file with the given records."""
    data = {"version": 1, "decisions": decisions}
    path.write_text(json.dumps(data, indent=2) + "\n")


_MOD = _load_module()


def make_decision_record(
    *,
    file: str = "src/auth.py",
    line: str = "42",
    cat: str = "Security",
    skill: str = "pr-review",
    decision: str = "Fix",
    decided_at: str | None = None,
) -> dict:
    """Build a stored decision record with a matching fingerprint."""
    fp = _MOD._fingerprint(file, cat, line, skill)
    return {
        "fingerprint": fp,
        "file": file,
        "line": line,
        "category": cat,
        "skill": skill,
        "decision": decision,
        "description_snippet": "Test finding",
        "decided_at": decided_at or datetime.now(UTC).isoformat(),
    }


def setup_project_with_hack(tmp_path: Path, *, with_git: bool = True) -> Path:
    """Create a minimal project directory with hack/ + core memory files."""
    hack = tmp_path / "hack"
    hack.mkdir()
    # core_count >= 2 required for validated memory dir
    (hack / "PROJECT.md").write_text("# Project\n")
    (hack / "TODO.md").write_text("# TODO\n")
    if with_git:
        (tmp_path / ".git").mkdir()
    return tmp_path


# ── pr-test-8: _find_memory_dir git-root walking ──────────────────────────────


class TestFindMemoryDir:
    """Integration tests for _find_memory_dir via subprocess cwd control."""

    def test_hack_at_git_root_found(self, tmp_path):
        """hack/ at git root is detected when CWD is a subdirectory."""
        project = setup_project_with_hack(tmp_path, with_git=True)
        sub = project / "src" / "app"
        sub.mkdir(parents=True)
        hack = project / "hack"

        q = make_dp_question("SQL injection in login handler")
        record = make_decision_record()
        seed_decisions_file(hack / "review-decisions.json", [record])

        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=sub)
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            "expected auto-answer JSON when hack/ is found via git root walk"
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_no_git_root_falls_back_to_cwd(self, tmp_path):
        """Without .git, _find_memory_dir falls back to CWD and still finds hack/ there."""
        project = setup_project_with_hack(tmp_path, with_git=False)
        hack = project / "hack"

        q = make_dp_question("Unvalidated redirect")
        record = make_decision_record(cat="Security")
        seed_decisions_file(hack / "review-decisions.json", [record])

        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            "expected auto-answer JSON when hack/ found via CWD fallback (no .git)"
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_no_hack_dir_passthroughs(self, tmp_path):
        """No hack/ (or .local/, scratch/, .dev/) → passthrough."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()

        q = make_dp_question("Missing input validation")
        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "expected passthrough (empty stdout) when no memory dir exists"
        )

    def test_hack_without_core_files_rejected(self, tmp_path):
        """hack/ with decisions file but <2 core files → rejected (not used)."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        hack = project / "hack"
        hack.mkdir()
        # Only 1 core file (need >= 2)
        (hack / "PROJECT.md").write_text("# Project\n")
        # Seed decisions file — if core_count guard fails, hook would auto-answer
        q = make_dp_question("SQL injection", file="src/auth.py", line="42", cat="Security")
        record = make_decision_record()
        seed_decisions_file(hack / "review-decisions.json", [record])

        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "expected passthrough when hack/ has <2 core files, even with decisions seeded"
        )


# ── pr-test-7: _save_decisions OSError cleanup ───────────────────────────────


class TestSaveDecisionsOSError:
    """Unit tests for _save_decisions OSError cleanup path."""

    def setup_method(self):
        self.mod = _load_module()

    def test_oserror_during_write_cleans_up_tmp(self, tmp_path):
        """If write_text raises OSError, the .tmp file is removed."""
        decisions_path = tmp_path / "review-decisions.json"
        data = {"version": 1, "decisions": []}

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            self.mod._save_decisions(decisions_path, data)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"unexpected .tmp files left: {tmp_files}"
        assert not decisions_path.exists()

    def test_oserror_during_replace_cleans_up_tmp(self, tmp_path):
        """If os.replace raises, the tmp file written by write_text is cleaned up."""
        decisions_path = tmp_path / "review-decisions.json"
        data = {"version": 1, "decisions": []}

        with patch("os.replace", side_effect=OSError("cross-device link")):
            self.mod._save_decisions(decisions_path, data)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"orphaned .tmp files: {tmp_files}"
        assert not decisions_path.exists()

    def test_successful_write_produces_valid_json(self, tmp_path):
        """Happy path: _save_decisions writes valid JSON atomically."""
        decisions_path = tmp_path / "review-decisions.json"
        data = {"version": 1, "decisions": [{"fingerprint": "abc123", "decision": "Fix"}]}
        self.mod._save_decisions(decisions_path, data)

        assert decisions_path.exists()
        loaded = json.loads(decisions_path.read_text())
        assert loaded == data
        assert list(tmp_path.glob("*.tmp")) == []


# ── pr-test-2: _fingerprint line windowing ────────────────────────────────────


class TestFingerprint:
    """Unit tests for _fingerprint windowing behavior."""

    def setup_method(self):
        self.mod = _load_module()

    @pytest.mark.parametrize(
        "line_a,line_b,same_window",
        [
            ("42", "47", True),  # same window: 40
            ("40", "49", True),  # boundary edges, same window: 40
            ("39", "40", False),  # cross-window: 30 vs 40
            ("49", "50", False),  # cross-window: 40 vs 50
            ("0", "9", True),  # window 0
            ("10", "19", True),  # window 10
        ],
    )
    def test_line_windowing(self, line_a, line_b, same_window):
        fp_a = self.mod._fingerprint("src/auth.py", "Security", line_a)
        fp_b = self.mod._fingerprint("src/auth.py", "Security", line_b)
        if same_window:
            assert fp_a == fp_b, f"lines {line_a} and {line_b} should map to the same window"
        else:
            assert fp_a != fp_b, f"lines {line_a} and {line_b} should map to different windows"

    def test_non_digit_line_uses_zero_window(self):
        """Non-digit line string falls back to window 0."""
        fp_non_digit = self.mod._fingerprint("src/auth.py", "Security", "N/A")
        fp_zero = self.mod._fingerprint("src/auth.py", "Security", "0")
        assert fp_non_digit == fp_zero

    def test_empty_string_line_uses_zero_window(self):
        """Empty string line is non-digit → window 0."""
        fp_empty = self.mod._fingerprint("src/auth.py", "Security", "")
        fp_zero = self.mod._fingerprint("src/auth.py", "Security", "0")
        assert fp_empty == fp_zero

    def test_different_categories_different_fingerprints(self):
        fp_a = self.mod._fingerprint("src/auth.py", "Security", "42")
        fp_b = self.mod._fingerprint("src/auth.py", "Performance", "42")
        assert fp_a != fp_b

    def test_different_files_different_fingerprints(self):
        fp_a = self.mod._fingerprint("src/auth.py", "Security", "42")
        fp_b = self.mod._fingerprint("src/other.py", "Security", "42")
        assert fp_a != fp_b

    def test_fingerprint_length_is_16(self):
        fp = self.mod._fingerprint("src/auth.py", "Security", "42")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_default_line_argument(self):
        fp_default = self.mod._fingerprint("src/auth.py", "Security")
        fp_explicit = self.mod._fingerprint("src/auth.py", "Security", "0")
        assert fp_default == fp_explicit

    def test_different_skills_different_fingerprints(self):
        """Same file/cat/line but different skill → different fingerprint."""
        fp_a = self.mod._fingerprint("src/auth.py", "Security", "42", "pr-review")
        fp_b = self.mod._fingerprint("src/auth.py", "Security", "42", "quality-gate")
        assert fp_a != fp_b


# ── pr-test-3: _sanitize_path_field security boundaries ──────────────────────


class TestSanitizePathField:
    """Unit tests for _sanitize_path_field path traversal and injection rejection."""

    def setup_method(self):
        self.mod = _load_module()

    def test_path_traversal_rejected(self):
        assert self.mod._sanitize_path_field("../../etc/passwd") == "<invalid>"

    def test_path_traversal_in_middle_rejected(self):
        assert self.mod._sanitize_path_field("src/../../../etc/shadow") == "<invalid>"

    def test_absolute_path_rejected(self):
        assert self.mod._sanitize_path_field("/absolute/path") == "<invalid>"

    def test_absolute_path_root_rejected(self):
        assert self.mod._sanitize_path_field("/") == "<invalid>"

    def test_valid_relative_path_accepted(self):
        assert self.mod._sanitize_path_field("src/auth.py") == "src/auth.py"

    def test_valid_nested_path_accepted(self):
        result = self.mod._sanitize_path_field("deep/nested/path/file.py")
        assert result == "deep/nested/path/file.py"

    def test_non_path_characters_normalized(self):
        result = self.mod._sanitize_path_field("src/file with spaces.py")
        assert result == "src/file_with_spaces.py"

    def test_long_string_truncated_at_512(self):
        long_path = "a/b/" + "x" * 600
        result = self.mod._sanitize_path_field(long_path)
        assert len(result) <= 512

    def test_empty_string_accepted(self):
        assert self.mod._sanitize_path_field("") == ""


# ── pr-test-4: _parse_metadata edge cases ────────────────────────────────────


class TestParseMetadata:
    """Unit tests for _parse_metadata format edge cases."""

    def setup_method(self):
        self.mod = _load_module()

    def test_valid_full_metadata(self):
        text = f"Question {METADATA_PREFIX}file=src/a.py,line=42,cat=Sec,skill=pr-review"
        result = self.mod._parse_metadata(text)
        assert result is not None
        assert result["file"] == "src/a.py"
        assert result["line"] == "42"
        assert result["cat"] == "Sec"
        assert result["skill"] == "pr-review"

    def test_missing_file_returns_none(self):
        text = f"Question {METADATA_PREFIX}line=42,cat=Security,skill=pr-review"
        assert self.mod._parse_metadata(text) is None

    def test_missing_cat_returns_none(self):
        text = f"Question {METADATA_PREFIX}file=src/auth.py,line=42,skill=pr-review"
        assert self.mod._parse_metadata(text) is None

    def test_no_prefix_returns_none(self):
        text = "A normal question without any metadata suffix"
        assert self.mod._parse_metadata(text) is None

    def test_file_with_comma_causes_parse_failure(self):
        """File path with comma breaks the parser (known limitation)."""
        pfx = METADATA_PREFIX
        text = f"Q {pfx}file=src/file,name.py,line=42,cat=Sec,skill=pr-review"
        result = self.mod._parse_metadata(text)
        if result is not None:
            assert result.get("file") != "src/file,name.py"

    def test_file_with_path_traversal_returns_none(self):
        pfx = METADATA_PREFIX
        text = f"Question {pfx}file=../../etc/passwd,line=1,cat=Sec,skill=pr"
        assert self.mod._parse_metadata(text) is None

    def test_whitespace_around_values_stripped(self):
        pfx = METADATA_PREFIX
        text = f"Q {pfx}file= src/auth.py ,line= 42 ,cat= Security ,skill= pr "
        result = self.mod._parse_metadata(text)
        assert result is not None
        assert result["file"] == "src/auth.py"
        assert result["cat"] == "Security"

    def test_optional_line_field_absent(self):
        text = f"Question {METADATA_PREFIX}file=src/auth.py,cat=Security,skill=pr-review"
        result = self.mod._parse_metadata(text)
        assert result is not None
        assert result["file"] == "src/auth.py"
        assert result["cat"] == "Security"

    def test_rpartition_handles_double_prefix(self):
        """If ▸dp: appears in description body, rpartition finds the last (real) suffix."""
        pfx = METADATA_PREFIX
        text = f"Describes {pfx} injection {pfx}file=src/a.py,line=1,cat=Sec,skill=pr"
        result = self.mod._parse_metadata(text)
        assert result is not None
        assert result["file"] == "src/a.py"
        assert result["cat"] == "Sec"


class TestExtractDescriptionSnippet:
    """Unit tests for _extract_description_snippet."""

    def setup_method(self):
        self.mod = _load_module()

    def test_double_prefix_preserves_full_description(self):
        """rsplit on last ▸dp: preserves description containing the prefix."""
        pfx = METADATA_PREFIX
        text = f"Describes {pfx} injection {pfx}file=src/a.py,line=1,cat=Sec,skill=pr"
        snippet = self.mod._extract_description_snippet(text)
        assert "injection" in snippet


# ── pr-test-5: PreToolUse partial-batch passthrough ──────────────────────────


class TestPreToolUsePartialBatch:
    """Integration tests for partial-batch passthrough in PreToolUse."""

    def test_mixed_type_batch_passthroughs(self, tmp_path):
        """Batch with Fix/Defer + non-Fix/Defer questions → passthrough."""
        project = setup_project_with_hack(tmp_path)
        hack = project / "hack"

        record = make_decision_record()
        seed_decisions_file(hack / "review-decisions.json", [record])

        q_fixdefer = make_dp_question("SQL injection")
        q_other = {
            "question": "Which approach?",
            "options": [{"label": "Option A"}, {"label": "Option B"}],
        }

        payload = make_pre_payload([q_fixdefer, q_other])
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "expected passthrough for mixed-type batch (Fix/Defer + non-Fix/Defer)"
        )

    def test_one_matched_one_unmatched_passthroughs(self, tmp_path):
        """2-question batch where only 1 has a stored decision → passthrough."""
        project = setup_project_with_hack(tmp_path)
        hack = project / "hack"

        record = make_decision_record(file="src/auth.py", line="42", cat="Security")
        seed_decisions_file(hack / "review-decisions.json", [record])

        q1 = make_dp_question("SQL injection", file="src/auth.py", line="42", cat="Security")
        q2 = make_dp_question("XSS in template", file="src/templates.py", line="88", cat="Quality")

        payload = make_pre_payload([q1, q2])
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"expected passthrough for partial batch: got {result.stdout!r}"
        )

    def test_all_matched_produces_auto_answer(self, tmp_path):
        """2-question batch where both have stored decisions → auto-answer."""
        project = setup_project_with_hack(tmp_path)
        hack = project / "hack"

        record1 = make_decision_record(file="src/auth.py", line="42", cat="Security")
        record2 = make_decision_record(
            file="src/templates.py", line="88", cat="Quality", decision="Defer"
        )
        seed_decisions_file(hack / "review-decisions.json", [record1, record2])

        q1 = make_dp_question("SQL injection", file="src/auth.py", line="42", cat="Security")
        q2 = make_dp_question("XSS in template", file="src/templates.py", line="88", cat="Quality")

        payload = make_pre_payload([q1, q2])
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        assert result.stdout.strip() != ""
        output = json.loads(result.stdout)
        hook_out = output["hookSpecificOutput"]
        assert hook_out["permissionDecision"] == "allow"
        assert len(hook_out["updatedInput"]["answers"]) == 2

    def test_no_stored_decisions_passthroughs(self, tmp_path):
        """No stored decisions at all → passthrough."""
        project = setup_project_with_hack(tmp_path)
        seed_decisions_file(project / "hack" / "review-decisions.json", [])

        q = make_dp_question("Missing validation", file="src/api.py", line="10", cat="Security")
        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_expired_decision_not_auto_applied(self, tmp_path):
        """PreToolUse ignores decisions older than 30 days."""
        project = setup_project_with_hack(tmp_path)
        hack = project / "hack"
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        record = make_decision_record(decided_at=old_date)
        seed_decisions_file(hack / "review-decisions.json", [record])

        q = make_dp_question("SQL injection")
        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "expected passthrough — expired decision should not auto-apply"
        )


# ── pr-test-6: PostToolUse answer-source fallback ────────────────────────────


class TestPostToolUseAnswerSource:
    """Integration tests for answer-source fallback in PostToolUse."""

    def test_answers_in_tool_input_captured(self, tmp_path):
        """Answers in tool_input.answers (fallback) are captured when tool_response is empty."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        seed_decisions_file(decisions_file, [])

        q = make_dp_question("SQL injection", file="src/auth.py", line="42", cat="Security")
        q_text = q["question"]

        payload = make_post_payload([q], {q_text: "Fix"})
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        stored = json.loads(decisions_file.read_text())
        assert len(stored["decisions"]) == 1
        assert stored["decisions"][0]["decision"] == "Fix"
        assert stored["decisions"][0]["file"] == "src/auth.py"

    def test_answers_in_tool_response_captured(self, tmp_path):
        """Answers in tool_response.answers are captured and persisted."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        seed_decisions_file(decisions_file, [])

        q = make_dp_question("XSS vulnerability", file="src/views.py", line="15", cat="Security")
        q_text = q["question"]

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [q]},
            "tool_response": {"answers": {q_text: "Defer"}},
        }
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        stored = json.loads(decisions_file.read_text())
        assert len(stored["decisions"]) == 1
        assert stored["decisions"][0]["decision"] == "Defer"
        assert stored["decisions"][0]["file"] == "src/views.py"

    def test_answers_in_tool_response_takes_precedence(self, tmp_path):
        """When answers in both tool_response and tool_input, tool_response wins."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        seed_decisions_file(decisions_file, [])

        q = make_dp_question("CSRF token missing", file="src/forms.py", line="30", cat="Security")
        q_text = q["question"]

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [q], "answers": {q_text: "Fix"}},
            "tool_response": {"answers": {q_text: "Defer"}},
        }
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0

        stored = json.loads(decisions_file.read_text())
        assert len(stored["decisions"]) == 1
        # tool_response wins over tool_input
        assert stored["decisions"][0]["decision"] == "Defer"

    def test_upsert_overwrites_existing_decision(self, tmp_path):
        """Changing from Defer to Fix updates the stored decision."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        # Seed with an existing "Defer" decision
        record = make_decision_record(decision="Defer")
        seed_decisions_file(decisions_file, [record])

        q = make_dp_question("SQL injection")
        q_text = q["question"]

        # User now picks Fix
        payload = make_post_payload([q], {q_text: "Fix"}, source="tool_response")
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0

        stored = json.loads(decisions_file.read_text())
        assert len(stored["decisions"]) == 1
        assert stored["decisions"][0]["decision"] == "Fix"

    def test_invalid_answer_not_captured(self, tmp_path):
        """Answer values not in VALID_DECISIONS are discarded."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        seed_decisions_file(decisions_file, [])

        q = make_dp_question("Unrelated finding", file="src/misc.py", line="5", cat="Quality")
        q_text = q["question"]

        payload = make_post_payload([q], {q_text: "Skip"})
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0

        stored = json.loads(decisions_file.read_text())
        assert stored["decisions"] == []

    def test_expired_decisions_pruned_on_write(self, tmp_path):
        """Decisions older than 30 days are pruned when PostToolUse writes."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        old_record = make_decision_record(
            file="src/old.py", line="10", cat="QA", decided_at=old_date
        )
        seed_decisions_file(decisions_file, [old_record])

        # New decision triggers a write, which prunes the old one
        q = make_dp_question("New finding", file="src/new.py", line="5", cat="Security")
        q_text = q["question"]
        payload = make_post_payload([q], {q_text: "Fix"}, source="tool_response")
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0

        stored = json.loads(decisions_file.read_text())
        assert len(stored["decisions"]) == 1
        assert stored["decisions"][0]["file"] == "src/new.py"

    def test_recapture_skip_preserves_timestamp(self, tmp_path):
        """Auto-applied answers via tool_input fallback skip re-capture."""
        project = setup_project_with_hack(tmp_path)
        decisions_file = project / "hack" / "review-decisions.json"
        original_time = datetime.now(UTC).isoformat()
        record = make_decision_record(decision="Fix", decided_at=original_time)
        seed_decisions_file(decisions_file, [record])

        # Send same decision via tool_input (fallback — simulates auto-applied)
        q = make_dp_question("SQL injection")
        q_text = q["question"]
        payload = make_post_payload([q], {q_text: "Fix"})  # tool_input default
        result = run_hook(payload, cwd=project)
        assert result.returncode == 0

        stored = json.loads(decisions_file.read_text())
        assert len(stored["decisions"]) == 1
        # Timestamp should NOT be refreshed — re-capture was skipped
        assert stored["decisions"][0]["decided_at"] == original_time


# ── pr-test-1: General hook passthrough cases ────────────────────────────────


class TestHookPassthrough:
    """Integration tests for basic hook passthrough behavior."""

    def test_non_ask_user_question_tool_passthroughs(self, tmp_path):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
        }
        result = run_hook(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_invalid_json_stdin_passthroughs(self, tmp_path):
        result = run_hook(cwd=tmp_path, raw_input="not json at all")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_empty_stdin_passthroughs(self, tmp_path):
        result = run_hook(cwd=tmp_path, raw_input="")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_non_fix_defer_question_passthroughs(self, tmp_path):
        payload = make_pre_payload(
            [
                {
                    "question": "Which approach do you prefer?",
                    "options": [{"label": "Option A"}, {"label": "Option B"}],
                }
            ]
        )
        result = run_hook(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_question_without_dp_metadata_passthroughs(self, tmp_path):
        q = make_fix_defer_question("This question has no metadata suffix")
        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_pre_tool_use_auto_answer_format(self, tmp_path):
        """PreToolUse auto-answer includes required hookSpecificOutput fields."""
        project = setup_project_with_hack(tmp_path, with_git=True)
        hack = project / "hack"
        record = make_decision_record(
            file="src/auth.py", line="20", cat="Security", decision="Defer"
        )
        seed_decisions_file(hack / "review-decisions.json", [record])

        q = make_dp_question("Unvalidated input", file="src/auth.py", line="20", cat="Security")
        payload = make_pre_payload([q])
        result = run_hook(payload, cwd=project)

        assert result.returncode == 0
        output = json.loads(result.stdout)
        hook_out = output["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "PreToolUse"
        assert hook_out["permissionDecision"] == "allow"
        assert "updatedInput" in hook_out
        assert "answers" in hook_out["updatedInput"]
        assert "additionalContext" in hook_out

    def test_post_tool_use_no_questions_passthroughs(self, tmp_path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [], "answers": {}},
            "tool_response": {},
        }
        result = run_hook(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unknown_hook_event_passthroughs(self, tmp_path):
        payload = {
            "hook_event_name": "SubagentStop",
            "tool_name": "AskUserQuestion",
            "tool_input": {},
        }
        result = run_hook(payload, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
