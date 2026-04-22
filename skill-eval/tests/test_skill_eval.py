"""Tests for skill-eval package.

Two test groups:
  1. In-process CLI tests — patch sys.argv, _repo_root, and mode functions
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
    SKILL_GOAL_RUBRICS,
    build_assertion_metrics,
    build_fixture_prompt,
    compare_baselines,
    load_baselines,
    load_codebase_file,
    load_eval_config,
    load_fixture,
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
    """Patch sys.stdin to appear as a tty, preventing cli.main() from reading it."""
    with patch.object(sys, "stdin", _TtyStdin()):
        yield


class TestHookNoChangedSkills:
    """Pre-push and --all with no skills → exit 0."""

    def test_prepush_no_changed_skills_exits_0(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch("skill_eval.cli._mode_prepush", return_value=True),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        assert exc.value.code == 0

    def test_all_mode_no_test_cases_exits_0(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch("skill_eval.cli._skill_dirs_with_test_cases", return_value=[]),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        assert exc.value.code == 0


class TestHookPassingBaseline:
    """_eval_skills returns True → exit 0."""

    def test_passing_exits_0(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch("skill_eval.cli._eval_skills", return_value=True),
            patch(
                "skill_eval.cli._skill_dirs_with_test_cases",
                return_value=[tmp_path / "skill"],
            ),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        assert exc.value.code == 0


class TestHookRegression:
    """_eval_skills returns False → exit 1."""

    def test_regression_exits_1(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch("skill_eval.cli._eval_skills", return_value=False),
            patch(
                "skill_eval.cli._skill_dirs_with_test_cases",
                return_value=[tmp_path / "skill"],
            ),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        assert exc.value.code == 1


class TestHookAllFlag:
    """--all dispatches to _mode_all, not _mode_prepush."""

    def test_all_flag_calls_mode_all_not_prepush(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--all"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch("skill_eval.cli._mode_all", return_value=True) as mock_all,
            patch("skill_eval.cli._mode_prepush") as mock_prepush,
            pytest.raises(SystemExit),
        ):
            cli.main()

        mock_all.assert_called_once_with(tmp_path)
        mock_prepush.assert_not_called()


class TestHookPrePushDispatch:
    """No flags → dispatches to _mode_prepush."""

    def test_no_flags_calls_mode_prepush(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch("skill_eval.cli._mode_prepush", return_value=True) as mock_pp,
            pytest.raises(SystemExit),
        ):
            cli.main()

        mock_pp.assert_called_once_with(tmp_path)


# ── Group 2: load_eval_config ─────────────────────────────────────────────────


class TestLoadEvalConfig:
    """load_eval_config(skill_name) reads from test_cases/<skill_name>.json."""

    def test_missing_skill_returns_shell_dict(self):
        result = load_eval_config("nonexistent-skill-xyz")
        assert result["skill_name"] == "nonexistent-skill-xyz"
        assert result["rubrics"] == []
        assert result["test_cases"] == []

    def test_path_traversal_returns_shell_dict(self):
        result = load_eval_config("../../../etc/passwd")
        assert result["rubrics"] == []
        assert result["test_cases"] == []

    def test_backslash_traversal_returns_shell_dict(self):
        result = load_eval_config("..\\..\\etc\\passwd")
        assert result["rubrics"] == []
        assert result["test_cases"] == []


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

    def test_infra_error_returns_false(self):
        results = {**_run_result("quality-gate", {}), "infra_error": True}
        passed, report = compare_baselines(results, {"quality-gate": {"anti_deferral": 0.9}})
        assert passed is False
        assert "INFRA ERROR" in report


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


# ── Group 8: Score anchoring in rubrics ─────────────────────────────────────


class TestNewCompetencyRubrics:
    """Verify skill-goal rubrics exist with the right structure."""

    def test_new_rubrics_registered(self):
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name in SKILL_GOAL_RUBRICS:
            assert name in RUBRIC_REGISTRY, f"Rubric {name!r} not in RUBRIC_REGISTRY"
        assert len(RUBRIC_REGISTRY) == 21

    def test_new_rubrics_have_evaluation_steps(self):
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name in SKILL_GOAL_RUBRICS:
            rubric = RUBRIC_REGISTRY[name]
            assert "evaluation_steps" in rubric, f"Rubric {name!r} missing evaluation_steps"
            assert len(rubric["evaluation_steps"]) >= 3, (
                f"Rubric {name!r} has {len(rubric['evaluation_steps'])} steps, expected >= 3"
            )

    def test_new_rubrics_have_score_anchors(self):
        from deepeval.metrics.g_eval import Rubric
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name in SKILL_GOAL_RUBRICS:
            rubric = RUBRIC_REGISTRY[name]
            anchoring = rubric["rubric"]
            assert len(anchoring) == 5, f"Rubric {name!r} has {len(anchoring)} anchors, expected 5"
            for entry in anchoring:
                assert isinstance(entry, Rubric), (
                    f"Rubric {name!r} anchor is {type(entry)}, expected Rubric"
                )
            ranges = [r.score_range for r in anchoring]
            assert ranges == [(0, 0), (3, 3), (5, 5), (8, 8), (10, 10)], (
                f"Rubric {name!r} score ranges: {ranges}"
            )

    def test_new_rubrics_evaluation_params(self):
        from deepeval.test_case import LLMTestCaseParams
        from skill_eval.rubrics import RUBRIC_REGISTRY

        expected_params = [
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ]
        for name in SKILL_GOAL_RUBRICS:
            rubric = RUBRIC_REGISTRY[name]
            assert rubric["evaluation_params"] == expected_params, (
                f"Rubric {name!r} evaluation_params: {rubric['evaluation_params']}"
            )


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
            assert first_outcome not in seen_outcomes, (
                f"Rubric {name!r} shares score anchoring with {seen_outcomes[first_outcome]!r}"
            )
            seen_outcomes[first_outcome] = name

    def test_evaluation_steps_are_criteria_only(self):
        """Evaluation steps should NOT contain score anchoring (that's in rubric now)."""
        from skill_eval.rubrics import RUBRIC_REGISTRY

        for name, rubric in RUBRIC_REGISTRY.items():
            for step in rubric["evaluation_steps"]:
                assert "Score anchoring" not in step, (
                    f"Rubric {name!r} still has score anchoring in evaluation_steps"
                )


# ── Group 9: load_context_layers ─────────────────────────────────────────


class TestLoadContextLayers:
    """load_context_layers discovers and loads CLAUDE.md files."""

    def test_project_claude_md_loaded(self, tmp_path):
        from skill_eval.runner import load_context_layers

        (tmp_path / "CLAUDE.md").write_text("# Project rules\n")
        layers = load_context_layers(repo_root=tmp_path)
        assert "project" in layers
        assert "Project rules" in layers["project"]

    def test_missing_repo_root_skips_project(self, tmp_path):
        from skill_eval.runner import load_context_layers

        layers = load_context_layers(repo_root=tmp_path)
        assert "project" not in layers

    def test_no_repo_root_returns_global_only(self):
        from skill_eval.runner import load_context_layers

        layers = load_context_layers(repo_root=None)
        # May or may not have "global" depending on ~/.claude/CLAUDE.md existing.
        assert "project" not in layers


# ── Group 11: build_metrics ──────────────────────────────────────────────


class TestBuildMetrics:
    """build_metrics raises ValueError for unknown rubric names."""

    def test_unknown_rubric_raises(self):
        from unittest.mock import MagicMock

        from skill_eval.runner import build_metrics

        mock_judge = MagicMock()
        mock_judge.get_model_name.return_value = "mock-judge"
        with pytest.raises(ValueError, match="Unknown rubric"):
            build_metrics(["nonexistent_rubric"], judge=mock_judge)


# ── Group 12: Nested baselines format ──────────────────────────────────────


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

    def test_constructor_default_region_is_global(self):
        """When CLOUD_ML_REGION is absent, AnthropicVertex is called with region='global'."""
        import os

        env = {k: v for k, v in os.environ.items() if k != "CLOUD_ML_REGION"}
        env["ANTHROPIC_VERTEX_PROJECT_ID"] = "test-project"
        with (
            patch("skill_eval.judge.anthropic.AnthropicVertex") as mock_vertex,
            patch("skill_eval.judge.instructor.from_anthropic"),
            patch.dict("os.environ", env, clear=True),
        ):
            from skill_eval.judge import VertexSonnetJudge

            VertexSonnetJudge()
            mock_vertex.assert_called_once_with(
                project_id="test-project",
                region="global",
                timeout=600.0,
            )

    def test_constructor_cloud_ml_region_override(self):
        """When CLOUD_ML_REGION is set, AnthropicVertex receives that region."""
        import os

        env = {k: v for k, v in os.environ.items()}
        env["CLOUD_ML_REGION"] = "us-central1"
        env["ANTHROPIC_VERTEX_PROJECT_ID"] = "test-project"
        with (
            patch("skill_eval.judge.anthropic.AnthropicVertex") as mock_vertex,
            patch("skill_eval.judge.instructor.from_anthropic"),
            patch.dict("os.environ", env, clear=True),
        ):
            from skill_eval.judge import VertexSonnetJudge

            VertexSonnetJudge()
            mock_vertex.assert_called_once_with(
                project_id="test-project",
                region="us-central1",
                timeout=600.0,
            )


# ── Group 10: Eval integrity guard (--locked) ─────────────────────────────


class TestVerifyEvalIntegrity:
    """_verify_eval_integrity checks for uncommitted changes in skill-eval/."""

    def test_clean_repo_returns_true(self):
        from skill_eval.cli import _verify_eval_integrity

        # Use real repo root so relative_to() resolves correctly.
        real_repo = Path(__file__).parent.parent.parent.resolve()
        clean_result = type("R", (), {"stdout": "", "returncode": 0})()
        with patch("skill_eval.cli.subprocess.run", return_value=clean_result):
            clean, modified = _verify_eval_integrity(real_repo)

        assert clean is True
        assert modified == []

    def test_modified_files_returns_false(self):
        from skill_eval.cli import _verify_eval_integrity

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

        with patch("skill_eval.cli.subprocess.run", side_effect=mock_run):
            clean, modified = _verify_eval_integrity(real_repo)

        assert clean is False
        assert len(modified) == 2


class TestLockedMode:
    """--locked flag blocks update-baselines and verifies integrity."""

    def test_locked_blocks_update_baselines(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--update-baselines", "--locked"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        assert exc.value.code == 1

    def test_locked_fails_on_dirty_eval(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--all", "--locked"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch(
                "skill_eval.cli._verify_eval_integrity",
                return_value=(False, ["skill-eval/test_cases/fix.json"]),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        assert exc.value.code == 1

    def test_locked_passes_on_clean_eval(self, tmp_path, tty_stdin):
        from skill_eval import cli

        with (
            patch.object(sys, "argv", ["hook", "--all", "--locked"]),
            patch("skill_eval.cli._repo_root", return_value=tmp_path),
            patch(
                "skill_eval.cli._verify_eval_integrity",
                return_value=(True, []),
            ),
            patch("skill_eval.cli._mode_all", return_value=True) as mock_all,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()

        mock_all.assert_called_once_with(tmp_path)
        assert exc.value.code == 0


# ── Group 13: load_fixture ─────────────────────────────────────────────────


def _make_fixture(tmp_path: Path, skill: str, key: str, content: str) -> Path:
    """Create a fixture file at skill-eval/fixtures/{skill}/{key}.md. Returns repo root."""
    fixture_dir = tmp_path / "skill-eval" / "fixtures" / skill
    fixture_dir.mkdir(parents=True, exist_ok=True)
    p = fixture_dir / f"{key}.md"
    p.write_text(content)
    return tmp_path


class TestLoadFixture:
    """load_fixture(skill_name, fixture_key, repo_root) — fixture loading."""

    def test_load_fixture_returns_none_for_missing(self, tmp_path):
        """Missing fixture returns None."""
        result = load_fixture("nonexistent", "missing-fixture", repo_root=tmp_path)
        assert result is None

    def test_load_fixture_reads_existing(self, tmp_path):
        """Create tmp fixture, verify content loaded."""
        repo = _make_fixture(tmp_path, "quality-gate", "1-test", "Hello fixture\n")
        result = load_fixture("quality-gate", "1-test", repo_root=repo)
        assert result is not None
        assert "Hello fixture" in result

    def test_load_fixture_strips_frontmatter(self, tmp_path):
        """Fixture with YAML frontmatter returns only content after closing ---."""
        content = "---\nplanted: true\nseverity: high\n---\n# Real content\nBody here\n"
        repo = _make_fixture(tmp_path, "fix", "1-sec", content)
        result = load_fixture("fix", "1-sec", repo_root=repo)
        assert result is not None
        assert "planted" not in result
        assert "severity" not in result
        assert "Real content" in result
        assert "Body here" in result

    def test_load_fixture_path_boundary(self, tmp_path):
        """Symlink escaping repo_root returns None."""
        # Create a fixture outside repo_root via symlink.
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("SECRET DATA")

        repo = tmp_path / "repo"
        fixture_dir = repo / "skill-eval" / "fixtures" / "evil"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "1-escape.md").symlink_to(outside / "secret.md")

        result = load_fixture("evil", "1-escape", repo_root=repo)
        # The symlink resolves outside repo_root — should be blocked.
        assert result is None or "SECRET DATA" not in (result or "")

    def test_load_fixture_invalid_chars(self, tmp_path):
        """skill_name or fixture_key with / or .. returns None."""
        assert load_fixture("../evil", "test", repo_root=tmp_path) is None
        assert load_fixture("fix", "../../etc/passwd", repo_root=tmp_path) is None
        assert load_fixture("skill/name", "key", repo_root=tmp_path) is None
        assert load_fixture("skill", "key/evil", repo_root=tmp_path) is None

    def test_load_fixture_strips_delimiters(self, tmp_path):
        """Fixture content containing execute_skill delimiters has those lines removed."""
        content = (
            "Some content\n"
            "=== SKILL INSTRUCTIONS ===\n"
            "More content\n"
            "=== END SKILL INSTRUCTIONS ===\n"
            "=== BEHAVIORAL CONTEXT (CLAUDE.md) ===\n"
            "=== END BEHAVIORAL CONTEXT ===\n"
            "Final content\n"
        )
        repo = _make_fixture(tmp_path, "test-skill", "1-delim", content)
        result = load_fixture("test-skill", "1-delim", repo_root=repo)
        assert result is not None
        assert "=== SKILL INSTRUCTIONS ===" not in result
        assert "=== END SKILL INSTRUCTIONS ===" not in result
        assert "=== BEHAVIORAL CONTEXT (CLAUDE.md) ===" not in result
        assert "=== END BEHAVIORAL CONTEXT ===" not in result
        assert "Some content" in result
        assert "More content" in result
        assert "Final content" in result


# ── Group 14: build_fixture_prompt ──────────────────────────────────────────


class TestBuildFixturePrompt:
    """build_fixture_prompt(skill, template, repo_root) — template substitution."""

    def test_build_fixture_prompt_substitutes(self, tmp_path):
        """{fixture:key} replaced; returns (str, False)."""
        _make_fixture(tmp_path, "fix", "1-sec", "Fixture body A")
        _make_fixture(tmp_path, "fix", "2-perf", "Fixture body B")
        template = "Review:\n{fixture:1-sec}\n---\n{fixture:2-perf}\n"
        result, has_missing = build_fixture_prompt("fix", template, repo_root=tmp_path)
        assert has_missing is False
        assert "Fixture body A" in result
        assert "Fixture body B" in result
        assert "{fixture:" not in result

    def test_build_fixture_prompt_missing_fixture(self, tmp_path):
        """Missing placeholder left as-is; returns (str, True)."""
        template = "Review:\n{fixture:nonexistent}\n"
        result, has_missing = build_fixture_prompt("fix", template, repo_root=tmp_path)
        assert has_missing is True
        assert "{fixture:nonexistent}" in result


# ── Group 15: Per-case rubric support ───────────────────────────────────────


class TestPerCaseRubrics:
    """Per-case rubrics field overrides config-level rubrics.

    Tests the selection logic used in _eval_tc: if tc.get("rubrics") is truthy,
    use per-case rubrics; otherwise fall back to config-level rubric_names.
    """

    def test_per_case_rubrics_override(self):
        """Test case with rubrics field uses per-case rubrics."""
        config_rubrics = ["anti_deferral", "phase_completion"]
        tc = {"id": 1, "prompt": "test", "rubrics": ["anti_deferral"]}

        # Mirror the selection logic from _eval_tc.
        effective_rubrics = tc["rubrics"] if tc.get("rubrics") else config_rubrics

        assert effective_rubrics == ["anti_deferral"]
        assert "phase_completion" not in effective_rubrics

    def test_per_case_rubrics_fallback(self):
        """Test case without rubrics field uses config-level."""
        config_rubrics = ["anti_deferral", "phase_completion"]
        tc = {"id": 1, "prompt": "test"}

        # Mirror the selection logic from _eval_tc.
        effective_rubrics = tc["rubrics"] if tc.get("rubrics") else config_rubrics

        assert effective_rubrics == ["anti_deferral", "phase_completion"]
        assert len(effective_rubrics) == 2


# ── Group 16: expected_behaviors wiring ─────────────────────────────────────


class TestExpectedBehaviorsWiring:
    """expected_behaviors list is joined and passed as LLMTestCase.expected_output."""

    def test_expected_behaviors_wired_as_expected_output(self):
        """When non-empty, LLMTestCase.expected_output equals joined string."""
        from skill_eval.runner import _PROMPT_DELIMITERS

        behaviors = ["Finds SQL injection", "Reports path traversal"]
        sanitized = []
        for e in behaviors:
            for delim in _PROMPT_DELIMITERS:
                e = e.replace(delim, "")
            sanitized.append(e.strip())
        sanitized = [e for e in sanitized if e]

        llm_tc = LLMTestCase(
            input="test",
            actual_output="response",
            expected_output="\n".join(sanitized) if sanitized else None,
        )
        assert llm_tc.expected_output == "Finds SQL injection\nReports path traversal"

    def test_expected_output_none_when_empty(self):
        """When absent/empty, expected_output is None."""
        from skill_eval.runner import _PROMPT_DELIMITERS

        behaviors: list[str] = []
        sanitized = []
        for e in behaviors:
            for delim in _PROMPT_DELIMITERS:
                e = e.replace(delim, "")
            sanitized.append(e.strip())
        sanitized = [e for e in sanitized if e]

        llm_tc = LLMTestCase(
            input="test",
            actual_output="response",
            expected_output="\n".join(sanitized) if sanitized else None,
        )
        assert llm_tc.expected_output is None


# ── Group 17: load_eval_config validation ───────────────────────────────────


class TestLoadEvalConfigValidation:
    """Validation rules for load_eval_config."""

    def test_load_eval_config_raises_for_competency_rubric_without_expected_behaviors(
        self, tmp_path
    ):
        """ValueError raised when competency rubric used without expected_behaviors."""
        config = {
            "skill_name": "test-skill",
            "rubrics": ["review_comprehensiveness"],
            "test_cases": [
                {"id": 1, "prompt": "test", "assertions": []},
            ],
        }
        tc_file = tmp_path / "test-competency-validation.json"
        tc_file.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="competency rubric"):
            load_eval_config("test-competency-validation", tc_dir=tmp_path)

    def test_load_eval_config_raises_for_empty_rubrics(self, tmp_path):
        """ValueError when rubrics is []."""
        config = {
            "skill_name": "test-empty-rubrics",
            "rubrics": [],
            "test_cases": [],
        }
        tc_file = tmp_path / "test-empty-rubrics.json"
        tc_file.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="empty rubrics"):
            load_eval_config("test-empty-rubrics", tc_dir=tmp_path)


# ── Group 18: N-adjusted regression threshold ──────────────────────────────


class TestNAdjustedThreshold:
    """compare_baselines uses widened threshold for low N."""

    def test_compare_baselines_widened_threshold_for_low_n(self):
        """N=1 metric uses 1.5x threshold (0.225 instead of 0.15)."""
        # A drop of 0.20 should PASS with N=1 (threshold widened to 0.225)
        # but would FAIL with N>=3 (threshold stays 0.15).
        results = {
            "skill": "test-skill",
            "scores": {"anti_deferral": 0.70},
            "per_case_scores": {"anti_deferral": [0.70]},  # N=1
        }
        baselines = {"test-skill": {"anti_deferral": 0.90}}

        # Drop = 0.20, base threshold = 0.15, N=1 effective = 0.225
        # 0.20 < 0.225 -> pass
        passed, report = compare_baselines(results, baselines)
        assert passed is True, f"N=1 drop of 0.20 should pass with widened threshold: {report}"

        # Same drop with N=3 should fail (threshold stays at 0.15).
        results_n3 = {
            "skill": "test-skill",
            "scores": {"anti_deferral": 0.70},
            "per_case_scores": {"anti_deferral": [0.60, 0.70, 0.80]},  # N=3
        }
        passed_n3, report_n3 = compare_baselines(results_n3, baselines)
        assert passed_n3 is False, f"N=3 drop of 0.20 should fail: {report_n3}"


# ── Group 19: load_codebase_file ─────────────────────────────────────────────


class TestLoadCodebaseFile:
    """Tests for load_codebase_file()."""

    def test_valid_codebase_ref(self, tmp_path):
        """Valid ref resolves correctly."""
        codebases = tmp_path / "skill-eval" / "codebases" / "test-repo" / "src"
        codebases.mkdir(parents=True)
        (codebases / "app.py").write_text("print('hello')")
        result = load_codebase_file("test-repo/src/app.py", repo_root=tmp_path)
        assert result == "print('hello')"

    def test_path_traversal_rejected(self, tmp_path):
        """.. in path is rejected."""
        result = load_codebase_file("../evil/file.py", repo_root=tmp_path)
        assert result is None

    def test_dotdot_in_path_rejected(self, tmp_path):
        """.. in nested path is rejected."""
        result = load_codebase_file("repo/../evil.py", repo_root=tmp_path)
        assert result is None

    def test_absolute_path_rejected(self, tmp_path):
        """Leading / is rejected."""
        result = load_codebase_file("repo//etc/passwd", repo_root=tmp_path)
        assert result is None

    def test_double_slash_rejected(self, tmp_path):
        """Empty path segment is rejected."""
        result = load_codebase_file("repo//file.py", repo_root=tmp_path)
        assert result is None

    def test_dot_segment_rejected(self, tmp_path):
        """Single . in path is rejected."""
        result = load_codebase_file("repo/./file.py", repo_root=tmp_path)
        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        """Non-existent file returns None."""
        result = load_codebase_file("repo/nonexistent.py", repo_root=tmp_path)
        assert result is None

    def test_no_slash_rejected(self, tmp_path):
        """Ref without slash is rejected (must be REPO/PATH)."""
        result = load_codebase_file("justfile.py", repo_root=tmp_path)
        assert result is None

    def test_symlink_escape_blocked(self, tmp_path):
        """Symlink pointing outside repo_root is blocked."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"evil content")
            evil_path = f.name
        try:
            codebases = tmp_path / "skill-eval" / "codebases" / "evil-repo"
            codebases.mkdir(parents=True)
            link = codebases / "escape.py"
            link.symlink_to(evil_path)
            result = load_codebase_file("evil-repo/escape.py", repo_root=tmp_path)
            assert result is None
        finally:
            Path(evil_path).unlink(missing_ok=True)

    def test_size_limit_enforced(self, tmp_path):
        """Files exceeding MAX size return None."""
        codebases = tmp_path / "skill-eval" / "codebases" / "big-repo"
        codebases.mkdir(parents=True)
        big_file = codebases / "huge.py"
        big_file.write_text("x" * 70000)  # > 65536
        result = load_codebase_file("big-repo/huge.py", repo_root=tmp_path)
        assert result is None

    def test_delimiter_stripping(self, tmp_path):
        """Prompt delimiter lines are stripped from codebase content."""
        codebases = tmp_path / "skill-eval" / "codebases" / "test-repo"
        codebases.mkdir(parents=True)
        content = "line1\n=== SKILL INSTRUCTIONS ===\nline3"
        (codebases / "file.py").write_text(content)
        result = load_codebase_file("test-repo/file.py", repo_root=tmp_path)
        assert result == "line1\nline3"

    def test_frontmatter_not_stripped(self, tmp_path):
        """YAML frontmatter is preserved (not stripped like fixtures)."""
        codebases = tmp_path / "skill-eval" / "codebases" / "test-repo"
        codebases.mkdir(parents=True)
        content = "---\nkey: value\n---\ncode here"
        (codebases / "file.py").write_text(content)
        result = load_codebase_file("test-repo/file.py", repo_root=tmp_path)
        assert "---" in result
        assert "key: value" in result


# ── Group 20: build_fixture_prompt codebase support ──────────────────────────


class TestBuildFixturePromptCodebase:
    """Tests for {codebase:} placeholder resolution in build_fixture_prompt."""

    def test_codebase_placeholder_substituted(self, tmp_path):
        """Codebase placeholder is resolved."""
        codebases = tmp_path / "skill-eval" / "codebases" / "test-repo" / "src"
        codebases.mkdir(parents=True)
        (codebases / "app.py").write_text("from flask import Flask")
        template = "Review this: {codebase:test-repo/src/app.py}"
        result, has_missing = build_fixture_prompt("any-skill", template, repo_root=tmp_path)
        assert "from flask import Flask" in result
        assert not has_missing

    def test_missing_codebase_leaves_placeholder(self, tmp_path):
        """Missing codebase file leaves placeholder and sets has_missing."""
        template = "Review: {codebase:nonexistent/file.py}"
        result, has_missing = build_fixture_prompt("any-skill", template, repo_root=tmp_path)
        assert "{codebase:nonexistent/file.py}" in result
        assert has_missing

    def test_mixed_fixture_and_codebase(self, tmp_path):
        """Both fixture and codebase placeholders resolve."""
        # Create fixture
        fixtures = tmp_path / "skill-eval" / "fixtures" / "test-skill"
        fixtures.mkdir(parents=True)
        (fixtures / "1-test.md").write_text("fixture content here")
        # Create codebase file
        codebases = tmp_path / "skill-eval" / "codebases" / "test-repo"
        codebases.mkdir(parents=True)
        (codebases / "app.py").write_text("codebase content here")
        template = "{fixture:1-test} and {codebase:test-repo/app.py}"
        result, has_missing = build_fixture_prompt("test-skill", template, repo_root=tmp_path)
        assert "fixture content here" in result
        assert "codebase content here" in result
        assert not has_missing

    def test_codebase_only_prompt_resolved(self, tmp_path):
        """Prompt with only codebase placeholders (no fixtures) is resolved."""
        codebases = tmp_path / "skill-eval" / "codebases" / "repo" / "src"
        codebases.mkdir(parents=True)
        (codebases / "main.py").write_text("def main(): pass")
        template = "Analyze: {codebase:repo/src/main.py}"
        result, has_missing = build_fixture_prompt("any-skill", template, repo_root=tmp_path)
        assert "def main(): pass" in result
        assert not has_missing


# ── Group 21: Codebase Python syntax regression ───────────────────────────────


class TestCodebasePythonSyntax:
    """Regression test: all .py files under skill-eval/codebases/ must parse."""

    def test_codebase_python_syntax(self):
        import ast

        codebases_dir = Path(__file__).parent.parent / "codebases"
        if not codebases_dir.exists():
            pytest.skip("No codebases directory yet")
        py_files = list(codebases_dir.rglob("*.py"))
        if not py_files:
            pytest.skip("No .py files found under skill-eval/codebases/")
        for path in py_files:
            try:
                ast.parse(path.read_text())
            except SyntaxError as e:
                raise AssertionError(f"Syntax error in {path}: {e}") from e
