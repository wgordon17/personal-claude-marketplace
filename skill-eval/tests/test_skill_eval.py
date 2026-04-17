"""Tests for skill-eval package.

Two test groups:
  1. In-process hook tests — patch sys.argv, _repo_root, and mode functions
     to test main() dispatch and exit codes without real git or LLM calls.
  2. Unit tests for runner/contains_metric — tmp_path fixtures, no LLM calls.

Run with: uv run --project skill-eval pytest tests/test_skill_eval.py -v --tb=short
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from deepeval.test_case import LLMTestCase
from skill_eval.contains_metric import ContainsMetric
from skill_eval.runner import (
    build_assertion_metrics,
    compare_baselines,
    load_baselines,
    load_eval_config,
    resolve_skill_bundle,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal repo structure. Returns (repo_root, skill_dir)."""
    repo = tmp_path / "repo"
    skill_dir = repo / "code-quality" / "skills" / "quality-gate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "references").mkdir()
    (repo / "code-quality" / "references").mkdir(parents=True, exist_ok=True)
    return repo, skill_dir


def _write_skill_md(skill_dir: Path, content: str = "") -> Path:
    p = skill_dir / "SKILL.md"
    p.write_text(content or "# Skill\nThis skill does things.\n")
    return p


def _write_baselines(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _run_result(skill: str, scores: dict) -> dict:
    """Minimal run_eval() result dict."""
    return {"skill": skill, "scores": scores, "pass_rate": 1.0, "details": []}


def _tc(actual: str) -> LLMTestCase:
    return LLMTestCase(input="test", actual_output=actual)


# ── Group 1: Hook dispatch tests ─────────────────────────────────────────────
#
# main() reads sys.argv via argparse and calls sys.stdin.read() if not a tty.
# We patch sys.argv, sys.stdin.isatty (to avoid the read), and mode functions
# to avoid real git invocations and LLM calls.


class _TtyStdin:
    """Minimal stdin replacement that reports itself as a tty."""

    def isatty(self) -> bool:
        return True


@pytest.fixture()
def tty_stdin():
    """Patch sys.stdin to appear as a tty, preventing hook.main() from reading it."""
    with patch.object(sys, "stdin", _TtyStdin()):
        yield


class TestHookNoChangedSkills:
    """Pre-push and --all with no skills → exit 0."""

    def test_prepush_no_changed_skills_exits_0(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._mode_prepush", return_value=True),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code == 0

    def test_all_mode_no_test_cases_exits_0(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._skill_dirs_with_test_cases", return_value=[]),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code == 0


class TestHookPassingBaseline:
    """_eval_skills returns True → exit 0."""

    def test_passing_exits_0(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._eval_skills", return_value=True),
            patch(
                "skill_eval.hook._skill_dirs_with_test_cases",
                return_value=[tmp_path / "skill"],
            ),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code == 0


class TestHookRegression:
    """_eval_skills returns False → exit 1."""

    def test_regression_exits_1(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._eval_skills", return_value=False),
            patch(
                "skill_eval.hook._skill_dirs_with_test_cases",
                return_value=[tmp_path / "skill"],
            ),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code == 1


class TestHookAllFlag:
    """--all dispatches to _mode_all, not _mode_prepush."""

    def test_all_flag_calls_mode_all_not_prepush(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._mode_all", return_value=True) as mock_all,
            patch("skill_eval.hook._mode_prepush") as mock_prepush,
            pytest.raises(SystemExit),
        ):
            hook.main()

        mock_all.assert_called_once_with(tmp_path)
        mock_prepush.assert_not_called()


class TestHookCompareFlag:
    """--compare REF validates ref then dispatches to _mode_compare."""

    def test_valid_ref_calls_mode_compare(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--compare", "main"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._mode_compare", return_value=True) as mock_cmp,
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        mock_cmp.assert_called_once_with(tmp_path, "main")
        assert exc.value.code == 0

    def test_invalid_ref_semicolon_exits_nonzero(self, tmp_path, tty_stdin):
        """Semicolon fails _VALID_REF_RE → sys.exit(1) inside _mode_compare."""
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--compare", "main;evil"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._eval_skills") as mock_eval,
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        mock_eval.assert_not_called()
        assert exc.value.code != 0

    def test_invalid_ref_backtick_exits_nonzero(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--compare", "`evil`"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._eval_skills") as mock_eval,
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        mock_eval.assert_not_called()
        assert exc.value.code != 0


class TestHookPrePushDispatch:
    """No flags → dispatches to _mode_prepush."""

    def test_no_flags_calls_mode_prepush(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._mode_prepush", return_value=True) as mock_pp,
            pytest.raises(SystemExit),
        ):
            hook.main()

        mock_pp.assert_called_once_with(tmp_path)


# ── Group 2: load_eval_config ─────────────────────────────────────────────────


class TestLoadEvalConfig:
    """load_eval_config(skill_name) reads from test_cases/<skill_name>.json."""

    def test_missing_skill_returns_shell_dict(self):
        result = load_eval_config("nonexistent-skill-xyz")
        assert result["skill_name"] == "nonexistent-skill-xyz"
        assert result["rubrics"] == []
        assert result["test_cases"] == []

    def test_existing_skill_returns_full_config(self):
        result = load_eval_config("quality-gate")
        assert result["skill_name"] == "quality-gate"
        assert len(result["rubrics"]) > 0
        assert len(result["test_cases"]) > 0

    def test_rubrics_are_known_registry_names(self):
        from skill_eval.rubrics import RUBRIC_REGISTRY

        result = load_eval_config("quality-gate")
        for rubric in result["rubrics"]:
            assert rubric in RUBRIC_REGISTRY

    def test_test_cases_have_required_fields(self):
        result = load_eval_config("quality-gate")
        for tc in result["test_cases"]:
            assert "id" in tc
            assert "prompt" in tc
            assert "assertions" in tc


# ── Group 3: load_baselines ───────────────────────────────────────────────────


class TestLoadBaselines:
    """load_baselines(Path) — missing file returns {}; both JSON formats work."""

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_baselines(tmp_path / "baselines.json")
        assert result == {}

    def test_flat_format_loaded(self, tmp_path):
        bl = tmp_path / "baselines.json"
        _write_baselines(bl, {"quality-gate": {"anti_deferral": 0.85}})
        result = load_baselines(bl)
        assert result["quality-gate"]["anti_deferral"] == pytest.approx(0.85)

    def test_wrapped_format_unwrapped(self, tmp_path):
        bl = tmp_path / "baselines.json"
        _write_baselines(bl, {"baselines": {"quality-gate": {"anti_deferral": 0.9}}})
        result = load_baselines(bl)
        assert result["quality-gate"]["anti_deferral"] == pytest.approx(0.9)


# ── Group 4: compare_baselines ────────────────────────────────────────────────


class TestCompareBaselines:
    """compare_baselines(results, baselines) → tuple[bool, str]."""

    def test_no_baseline_returns_pass(self):
        passed, report = compare_baselines(_run_result("new-skill", {}), {})
        assert passed is True
        assert "new-skill" in report

    def test_score_above_baseline_passes(self):
        results = _run_result("quality-gate", {"anti_deferral": 0.9})
        passed, _ = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.85}})
        assert passed is True

    def test_large_drop_fails(self):
        results = _run_result("quality-gate", {"anti_deferral": 0.7})
        passed, report = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.9}})
        assert passed is False
        assert "REGRESSION" in report

    def test_drop_within_threshold_passes(self):
        """0.10 drop is below default threshold of 0.15."""
        results = _run_result("quality-gate", {"anti_deferral": 0.80})
        passed, _ = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.90}})
        assert passed is True

    def test_moderate_drop_passes(self):
        """Drop of 0.14 is below threshold of 0.15 → pass."""
        results = _run_result("quality-gate", {"anti_deferral": 0.76})
        passed, _ = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.90}})
        assert passed is True

    def test_drop_just_over_threshold_fails(self):
        results = _run_result("quality-gate", {"anti_deferral": 0.74})
        passed, _ = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.90}})
        assert passed is False

    def test_missing_metric_in_current_fails(self):
        results = _run_result("quality-gate", {})
        passed, report = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.9}})
        assert passed is False
        assert "missing" in report.lower()

    def test_custom_threshold_respected(self):
        results = _run_result("quality-gate", {"anti_deferral": 0.82})
        passed, _ = compare_baselines(
            results, {"quality-gate": {"anti_deferral": 0.90}}, threshold=0.10
        )
        assert passed is True

    def test_report_contains_skill_name(self):
        results = _run_result("fix", {"instruction_adherence": 0.88})
        _, report = compare_baselines(results, {"fix": {"instruction_adherence": 0.85}})
        assert "fix" in report


# ── Group 5: resolve_skill_bundle ─────────────────────────────────────────────


class TestResolveSkillBundle:
    """resolve_skill_bundle(skill_dir, repo_root) — path resolution and boundary check."""

    def test_skill_md_only_returns_string(self, tmp_path):
        repo, skill_dir = _make_repo(tmp_path)
        _write_skill_md(skill_dir, "# Test Skill\nNo references.\n")

        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is not None
        assert "Test Skill" in result

    def test_missing_skill_md_returns_none(self, tmp_path):
        repo, skill_dir = _make_repo(tmp_path)
        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is None

    def test_backtick_reference_included(self, tmp_path):
        repo, skill_dir = _make_repo(tmp_path)
        (skill_dir / "references" / "rubrics.md").write_text("# Rubric content\n")
        _write_skill_md(skill_dir, "# Skill\nSee `references/rubrics.md`.\n")

        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is not None
        assert "Rubric content" in result

    def test_angle_bracket_reference_included(self, tmp_path):
        repo, skill_dir = _make_repo(tmp_path)
        (skill_dir / "references" / "prompts.md").write_text("# Subagent prompt\n")
        _write_skill_md(skill_dir, "# Skill\n<see references/prompts.md, Domain: Sec>\n")

        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is not None
        assert "Subagent prompt" in result

    def test_path_traversal_excluded(self, tmp_path):
        """Reference resolving outside repo root must not be read."""
        repo, skill_dir = _make_repo(tmp_path)
        _write_skill_md(skill_dir, "# Skill\nSee `../../references/evil.md`.\n")
        (tmp_path / "evil.md").write_text("EVIL CONTENT\n")

        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is not None
        assert "EVIL CONTENT" not in result

    def test_plugin_level_reference_resolved(self, tmp_path):
        repo, skill_dir = _make_repo(tmp_path)
        (repo / "code-quality" / "references" / "checklist.md").write_text("# Checklist\n")
        _write_skill_md(skill_dir, "# Skill\nSee `code-quality/references/checklist.md`.\n")

        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is not None
        assert "Checklist" in result

    def test_unknown_plugin_prefix_skipped(self, tmp_path):
        repo, skill_dir = _make_repo(tmp_path)
        _write_skill_md(skill_dir, "# Skill\nSee `unknown-plugin/references/secret.md`.\n")

        result = resolve_skill_bundle(skill_dir, repo_root=repo)
        assert result is not None  # no crash; prefix skipped


# ── Group 6: ContainsMetric ───────────────────────────────────────────────────


class TestContainsMetric:
    """ContainsMetric — deterministic substring scoring via LLMTestCase."""

    def test_all_expected_present_scores_1(self):
        m = ContainsMetric(expected=["Correctness", "Security"], forbidden=[])
        m.measure(_tc("Correctness and Security checked."))
        assert m.score == pytest.approx(1.0)
        assert m.is_successful() is True

    def test_missing_expected_scores_below_1(self):
        m = ContainsMetric(expected=["Correctness", "Missing"], forbidden=[])
        m.measure(_tc("Correctness is here."))
        assert m.score < 1.0

    def test_forbidden_present_scores_0(self):
        m = ContainsMetric(expected=[], forbidden=["deferred"])
        m.measure(_tc("This is deferred to a later phase."))
        assert m.score == pytest.approx(0.0)
        assert m.is_successful() is False

    def test_forbidden_absent_scores_1(self):
        m = ContainsMetric(expected=[], forbidden=["deferred"])
        m.measure(_tc("All requirements implemented."))
        assert m.score == pytest.approx(1.0)

    def test_empty_lists_scores_1(self):
        m = ContainsMetric(expected=[], forbidden=[])
        m.measure(_tc("Anything."))
        assert m.score == pytest.approx(1.0)
        assert m.is_successful() is True

    def test_case_insensitive_expected(self):
        m = ContainsMetric(expected=["correctness"], forbidden=[])
        m.measure(_tc("CORRECTNESS matters."))
        assert m.score == pytest.approx(1.0)

    def test_case_insensitive_forbidden(self):
        m = ContainsMetric(expected=[], forbidden=["DEFERRED"])
        m.measure(_tc("This work is deferred."))
        assert m.score == pytest.approx(0.0)

    def test_partial_expected_gives_partial_score(self):
        m = ContainsMetric(expected=["A", "B", "C"], forbidden=[])
        m.measure(_tc("Only A is here."))
        assert 0.0 < m.score < 1.0

    def test_name_is_string(self):
        m = ContainsMetric(expected=[], forbidden=[])
        assert isinstance(m.name, str)
        assert len(m.name) > 0

    def test_is_successful_false_before_measure(self):
        m = ContainsMetric(expected=["x"], forbidden=[])
        assert m.is_successful() is False

    def test_reason_set_on_failure(self):
        m = ContainsMetric(expected=["missing"], forbidden=[])
        m.measure(_tc("nothing here"))
        assert m.reason is not None
        assert "missing" in m.reason.lower()

    def test_reason_set_on_success(self):
        m = ContainsMetric(expected=["hello"], forbidden=[])
        m.measure(_tc("hello world"))
        assert m.reason is not None


# ── Group 7: build_assertion_metrics ─────────────────────────────────────────


class TestBuildAssertionMetrics:
    """build_assertion_metrics(list[str]) → single ContainsMetric."""

    def test_returns_contains_metric_instance(self):
        result = build_assertion_metrics(["contains: Correctness"])
        assert isinstance(result, ContainsMetric)

    def test_contains_prefix_populates_expected(self):
        result = build_assertion_metrics(["contains: Correctness", "contains: Security"])
        assert "correctness" in result.expected
        assert "security" in result.expected

    def test_not_contains_prefix_populates_forbidden(self):
        result = build_assertion_metrics(["not-contains: deferred"])
        assert "deferred" in result.forbidden

    def test_mixed_populates_both_lists(self):
        result = build_assertion_metrics(
            ["contains: SELF-SCOPING", "not-contains: future iteration"]
        )
        assert "self-scoping" in result.expected
        assert "future iteration" in result.forbidden

    def test_empty_list_produces_empty_metric(self):
        result = build_assertion_metrics([])
        assert result.expected == []
        assert result.forbidden == []

    def test_unknown_prefix_skipped_no_crash(self):
        result = build_assertion_metrics(["invalid: something"])
        assert result.expected == []
        assert result.forbidden == []

    def test_parses_quality_gate_test_case_assertions(self):
        """Assertions from quality-gate.json parse correctly end-to-end."""
        config = load_eval_config("quality-gate")
        tc = config["test_cases"][0]
        metric = build_assertion_metrics(tc["assertions"])
        assert isinstance(metric, ContainsMetric)
        assert "layer 1" in metric.expected


# ── Group 8: Score anchoring in rubrics ─────────────────────────────────────


class TestScoreAnchoring:
    """Every rubric includes per-rubric Rubric objects for score-range guidance."""

    def test_all_rubrics_have_rubric_key(self):
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name, rubric in RUBRIC_REGISTRY.items():
            assert "rubric" in rubric, f"Rubric {name!r} missing 'rubric' key"
            assert isinstance(rubric["rubric"], list), (
                f"Rubric {name!r} 'rubric' should be a list of Rubric objects"
            )
            assert len(rubric["rubric"]) > 0, f"Rubric {name!r} has empty 'rubric' list"

    def test_per_rubric_anchoring_covers_full_range(self):
        """Each rubric's score anchoring should have 5 Rubric objects covering 0-10."""
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name, rubric in RUBRIC_REGISTRY.items():
            anchoring = rubric["rubric"]
            assert len(anchoring) == 5, f"Rubric {name!r} has {len(anchoring)} anchors, expected 5"
            ranges = [r.score_range for r in anchoring]
            assert ranges == [(0, 0), (3, 3), (5, 5), (8, 8), (10, 10)], (
                f"Rubric {name!r} score ranges not 5-point discrete scale: {ranges}"
            )

    def test_per_rubric_anchoring_has_outcomes(self):
        """Each Rubric object should have a non-empty expected_outcome."""
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name, rubric in RUBRIC_REGISTRY.items():
            for r in rubric["rubric"]:
                assert r.expected_outcome, (
                    f"Rubric {name!r}: empty expected_outcome for range {r.score_range}"
                )

    def test_per_rubric_anchoring_is_unique(self):
        """Each rubric should have distinct behavioral anchors, not shared."""
        from skill_eval.rubrics import RUBRIC_REGISTRY

        seen_outcomes: dict[str, str] = {}
        for name, rubric in RUBRIC_REGISTRY.items():
            # Check the first anchor's text is unique to this rubric.
            first_outcome = rubric["rubric"][0].expected_outcome
            assert first_outcome not in seen_outcomes.values() or name == seen_outcomes.get(
                first_outcome
            ), f"Rubric {name!r} shares score anchoring with another rubric"
            seen_outcomes[first_outcome] = name

    def test_evaluation_steps_are_criteria_only(self):
        """Evaluation steps should NOT contain score anchoring (that's in rubric now)."""
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name, rubric in RUBRIC_REGISTRY.items():
            for step in rubric["evaluation_steps"]:
                assert "Score anchoring" not in step, (
                    f"Rubric {name!r} still has score anchoring in evaluation_steps"
                )


# ── Group 9: Nested baselines format ───────────────────────────────────────


class TestNestedBaselinesFormat:
    """load_baselines handles the new nested format with per_case_scores."""

    def test_nested_format_extracts_scores(self, tmp_path):
        bl = tmp_path / "baselines.json"
        _write_baselines(
            bl,
            {
                "quality-gate": {
                    "scores": {"anti_deferral": 0.85, "phase_completion": 0.70},
                    "per_case_scores": {
                        "anti_deferral": [0.8, 0.9],
                        "phase_completion": [0.7, 0.7],
                    },
                }
            },
        )
        result = load_baselines(bl)
        assert result["quality-gate"]["anti_deferral"] == pytest.approx(0.85)
        assert result["quality-gate"]["phase_completion"] == pytest.approx(0.70)

    def test_nested_format_ignores_per_case(self, tmp_path):
        """load_baselines returns only the scores dict, not per_case_scores."""
        bl = tmp_path / "baselines.json"
        _write_baselines(
            bl,
            {
                "fix": {
                    "scores": {"instruction_adherence": 0.5},
                    "per_case_scores": {"instruction_adherence": [0.4, 0.6]},
                }
            },
        )
        result = load_baselines(bl)
        assert "per_case_scores" not in result["fix"]

    def test_mixed_format_both_loaded(self, tmp_path):
        """A baselines file mixing old flat and new nested formats loads correctly."""
        bl = tmp_path / "baselines.json"
        _write_baselines(
            bl,
            {
                "fix": {"anti_deferral": 0.5},  # old flat
                "quality-gate": {
                    "scores": {"anti_deferral": 0.85},
                    "per_case_scores": {"anti_deferral": [0.8, 0.9]},
                },  # new nested
            },
        )
        result = load_baselines(bl)
        assert result["fix"]["anti_deferral"] == pytest.approx(0.5)
        assert result["quality-gate"]["anti_deferral"] == pytest.approx(0.85)


# ── Group 10: Multi-trial averaging (VertexSonnetJudge) ────────────────────


class TestMultiTrialAveraging:
    """Test multi-trial averaging logic in VertexSonnetJudge.generate().

    These tests mock _single_generate to avoid real API calls, testing only
    the averaging and fallback logic.
    """

    def _make_judge(self, k_samples=5, eval_temperature=0.7):
        """Create a VertexSonnetJudge with mocked Vertex AI client."""
        with (
            patch("skill_eval.judge.anthropic.AnthropicVertex"),
            patch("skill_eval.judge.instructor.from_anthropic"),
        ):
            from skill_eval.judge import VertexSonnetJudge

            return VertexSonnetJudge(
                k_samples=k_samples,
                eval_temperature=eval_temperature,
            )

    def test_single_trial_uses_temp_0(self):
        """k_samples=1 should call _single_generate with temperature=0."""
        judge = self._make_judge(k_samples=1)
        mock_result = type("R", (), {"score": 7.0, "reason": "good"})()
        with patch.object(judge, "_single_generate", return_value=mock_result) as mock_sg:
            judge.generate("prompt", schema=type("S", (), {}))
            mock_sg.assert_called_once()
            assert mock_sg.call_args[1]["temperature"] == 0

    def test_multi_trial_averages_scores(self):
        """k_samples=3 should average the scores from 3 calls."""
        judge = self._make_judge(k_samples=3, eval_temperature=0.5)

        class FakeSchema:
            def __init__(self, score=0, reason=""):
                self.score = score
                self.reason = reason

        results = [
            FakeSchema(score=6.0, reason="ok"),
            FakeSchema(score=8.0, reason="great"),
            FakeSchema(score=4.0, reason="meh"),
        ]
        call_count = {"n": 0}

        def mock_single_gen(prompt, schema=None, temperature=0):
            r = results[call_count["n"]]
            call_count["n"] += 1
            return r

        with patch.object(judge, "_single_generate", side_effect=mock_single_gen):
            result = judge.generate("prompt", schema=FakeSchema)
            assert result.score == pytest.approx(6.0)  # (6+8+4)/3
            assert result.reason == "great"  # from highest-scoring trial

    def test_multi_trial_skips_failed_samples(self):
        """If some trials fail, average over the successful ones."""
        judge = self._make_judge(k_samples=3, eval_temperature=0.5)

        class FakeSchema:
            def __init__(self, score=0, reason=""):
                self.score = score
                self.reason = reason

        call_count = {"n": 0}

        def mock_single_gen(prompt, schema=None, temperature=0):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("API error")
            return FakeSchema(score=8.0, reason="good")

        with patch.object(judge, "_single_generate", side_effect=mock_single_gen):
            result = judge.generate("prompt", schema=FakeSchema)
            assert result.score == pytest.approx(8.0)  # 2 successes, both 8.0

    def test_all_trials_fail_falls_back_to_single(self):
        """If all K trials fail, fall back to single deterministic call."""
        judge = self._make_judge(k_samples=3, eval_temperature=0.5)

        class FakeSchema:
            def __init__(self, score=0, reason=""):
                self.score = score
                self.reason = reason

        call_count = {"n": 0}

        def mock_single_gen(prompt, schema=None, temperature=0):
            call_count["n"] += 1
            if temperature > 0:
                raise RuntimeError("API error")
            return FakeSchema(score=5.0, reason="fallback")

        with patch.object(judge, "_single_generate", side_effect=mock_single_gen):
            result = judge.generate("prompt", schema=FakeSchema)
            assert result.score == pytest.approx(5.0)
            assert result.reason == "fallback"

    def test_no_schema_always_single_shot(self):
        """Skill execution (schema=None) should always use single call at temp=0."""
        judge = self._make_judge(k_samples=5)
        with patch.object(judge, "_single_generate", return_value="output") as mock_sg:
            result = judge.generate("prompt", schema=None)
            assert result == "output"
            mock_sg.assert_called_once()
            assert mock_sg.call_args[1]["schema"] is None
            assert mock_sg.call_args[1]["temperature"] == 0

    def test_multi_trial_uses_eval_temperature(self):
        """Multi-trial calls should use eval_temperature, not 0."""
        judge = self._make_judge(k_samples=2, eval_temperature=0.7)

        class FakeSchema:
            def __init__(self, score=0, reason=""):
                self.score = score
                self.reason = reason

        with patch.object(
            judge,
            "_single_generate",
            return_value=FakeSchema(score=5.0, reason="r"),
        ) as mock_sg:
            judge.generate("prompt", schema=FakeSchema)
            for call in mock_sg.call_args_list:
                assert call[1]["temperature"] == 0.7

    def test_multi_trial_produces_continuous_scores(self):
        """With varied integer inputs, averaging should produce non-integer scores."""
        judge = self._make_judge(k_samples=4, eval_temperature=0.5)

        class FakeSchema:
            def __init__(self, score=0, reason=""):
                self.score = score
                self.reason = reason

        scores = [3.0, 5.0, 4.0, 6.0]
        call_count = {"n": 0}

        def mock_single_gen(prompt, schema=None, temperature=0):
            r = FakeSchema(score=scores[call_count["n"]], reason="r")
            call_count["n"] += 1
            return r

        with patch.object(judge, "_single_generate", side_effect=mock_single_gen):
            result = judge.generate("prompt", schema=FakeSchema)
            assert result.score == pytest.approx(4.5)  # (3+5+4+6)/4
            # 4.5 is not one of the 11 discrete values (0.0, 0.1, ..., 1.0)
            # demonstrating the continuous score benefit


# ── Group 11: Hook dispatch for --compare-scoring ──────────────────────────


class TestHookCompareScoringFlag:
    """--compare-scoring dispatches to _mode_compare_scoring."""

    def test_compare_scoring_calls_mode(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--compare-scoring"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch("skill_eval.hook._mode_compare_scoring", return_value=True) as mock_cs,
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        mock_cs.assert_called_once_with(tmp_path)
        assert exc.value.code == 0

    def test_compare_scoring_mutually_exclusive_with_all(self, tty_stdin):
        """--compare-scoring and --all cannot be used together."""
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--compare-scoring", "--all"]),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code != 0


# ── Group 12: Eval integrity guard (--locked) ──────────────────────────────


class TestVerifyEvalIntegrity:
    """_verify_eval_integrity checks for uncommitted changes in skill-eval/."""

    def test_clean_repo_returns_true(self):
        from skill_eval.hook import _verify_eval_integrity

        # Use real repo root so relative_to() resolves correctly.
        real_repo = Path(__file__).parent.parent.parent.resolve()
        clean_result = type("R", (), {"stdout": "", "returncode": 0})()
        with patch("skill_eval.hook.subprocess.run", return_value=clean_result):
            clean, modified = _verify_eval_integrity(real_repo)

        assert clean is True
        assert modified == []

    def test_modified_files_returns_false(self):
        from skill_eval.hook import _verify_eval_integrity

        # Use the real repo root so Path(__file__).parent.parent resolves
        # as relative_to(repo_root) correctly.
        real_repo = Path(__file__).parent.parent.parent.resolve()

        call_count = {"n": 0}

        def mock_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Unstaged changes.
                return type(
                    "R",
                    (),
                    {"stdout": "skill-eval/test_cases/fix.json\n", "returncode": 0},
                )()
            # Staged changes.
            return type(
                "R",
                (),
                {"stdout": "skill-eval/skill_eval/rubrics.py\n", "returncode": 0},
            )()

        with patch("skill_eval.hook.subprocess.run", side_effect=mock_run):
            clean, modified = _verify_eval_integrity(real_repo)

        assert clean is False
        assert len(modified) == 2


class TestLockedMode:
    """--locked flag blocks update-baselines and verifies integrity."""

    def test_locked_blocks_update_baselines(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--update-baselines", "--locked"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code == 1

    def test_locked_fails_on_dirty_eval(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--all", "--locked"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch(
                "skill_eval.hook._verify_eval_integrity",
                return_value=(False, ["skill-eval/test_cases/fix.json"]),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        assert exc.value.code == 1

    def test_locked_passes_on_clean_eval(self, tmp_path, tty_stdin):
        from skill_eval import hook

        with (
            patch.object(sys, "argv", ["hook", "--all", "--locked"]),
            patch("skill_eval.hook._repo_root", return_value=tmp_path),
            patch(
                "skill_eval.hook._verify_eval_integrity",
                return_value=(True, []),
            ),
            patch("skill_eval.hook._mode_all", return_value=True) as mock_all,
            pytest.raises(SystemExit) as exc,
        ):
            hook.main()

        mock_all.assert_called_once_with(tmp_path)
        assert exc.value.code == 0
