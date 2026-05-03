"""Unit tests for composition eval framework.

Three test groups:
  1. execute_chain — step execution, variable substitution, XML wrapping.
  2. run_composition_eval — fixture loading, worktree lifecycle, scoring.
  3. compare_composition_results — regression detection, ASCII table.

All tests use mocked subprocess.run and resolve_skill_bundle — no claude CLI
or git worktree calls in the test suite.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from skill_eval.runner import (
    ChainStepResult,
    CompositionResult,
    compare_composition_results,
    execute_chain,
    load_composition_config,
    load_composition_fixture,
    run_composition_eval,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with a skill SKILL.md."""
    repo = tmp_path / "repo"
    skill_dir = repo / "code-quality" / "skills" / "quality-gate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# quality-gate\nDoes review.\n")
    return repo


def _make_fixture(repo: Path, key: str, content: str) -> None:
    """Create a fixture file at skill-eval/fixtures/{key}.md."""
    fixture_path = repo / "skill-eval" / "fixtures" / f"{key}.md"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(content)


def _make_composition_config(tmp_path: Path, data: dict) -> Path:
    """Write a composition config JSON and return its path."""
    import json

    config_path = tmp_path / "composition.json"
    config_path.write_text(json.dumps(data))
    return config_path


def _mock_claude_proc(output: str = "step output") -> MagicMock:
    """Return a mock CompletedProcess for claude CLI invocations."""
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = output
    proc.stderr = ""
    return proc


def _step(skill: str = "quality-gate", prompt_template: str = "review this", **kwargs) -> dict:
    """Build a minimal step dict with required fields."""
    return {"skill": skill, "prompt_template": prompt_template, "timeout_seconds": 30, **kwargs}


# ── Group 1: execute_chain ────────────────────────────────────────────────────


class TestExecuteChain:
    """execute_chain executes skill steps and threads outputs."""

    def test_single_step_returns_result(self, tmp_path):
        """A single step returns one ChainStepResult."""
        repo = _make_repo(tmp_path)

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# skill bundle"),
            patch("skill_eval.runner.subprocess.run", return_value=_mock_claude_proc("hello")),
        ):
            results = execute_chain(
                steps=[_step(prompt_template="review this")],
                repo_root=repo,
                context_preamble=None,
            )

        assert len(results) == 1
        assert results[0].skill_name == "quality-gate"
        assert results[0].skill_output == "hello"
        assert results[0].capture_name is None

    def test_capture_as_stored_with_xml_wrapping(self, tmp_path):
        """Captured output is XML-wrapped before storage."""
        repo = _make_repo(tmp_path)

        call_outputs = ["step1 output", "step2 output"]
        call_iter = iter(call_outputs)

        def mock_run(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = next(call_iter)
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            results = execute_chain(
                steps=[
                    _step(prompt_template="first", capture_as="step1"),
                    _step(prompt_template="second: {step1}"),
                ],
                repo_root=repo,
                context_preamble=None,
            )

        assert len(results) == 2
        assert results[0].capture_name == "step1"
        assert results[1].skill_output == "step2 output"

    def test_variable_substitution_from_initial_captures(self, tmp_path):
        """Initial captures are substituted into step prompts."""
        repo = _make_repo(tmp_path)

        captured_prompt: list[str] = []

        def mock_run(*args, **kwargs):
            captured_prompt.append(kwargs.get("input", ""))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "response"
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[_step(prompt_template="Review: {fixture}")],
                repo_root=repo,
                context_preamble=None,
                initial_captures={"fixture": "my fixture content"},
            )

        assert "my fixture content" in captured_prompt[0]

    def test_prior_step_output_xml_wrapped_in_next_prompt(self, tmp_path):
        """Prior step output is XML-wrapped when substituted into later prompts."""
        repo = _make_repo(tmp_path)

        outputs = ["output from step1", "step2 done"]
        call_iter = iter(outputs)
        captured_inputs: list[str] = []

        def mock_run(*args, **kwargs):
            captured_inputs.append(kwargs.get("input", ""))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = next(call_iter)
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[
                    _step(prompt_template="first step", capture_as="result"),
                    _step(prompt_template="use this: {result}"),
                ],
                repo_root=repo,
                context_preamble=None,
            )

        # The second prompt should contain the XML-wrapped output.
        second_input = captured_inputs[1]
        assert '<prior-step-output skill="quality-gate">' in second_input
        assert "output from step1" in second_input

    def test_invalid_skill_name_raises(self, tmp_path):
        """A skill name with invalid characters raises ValueError."""
        repo = _make_repo(tmp_path)

        with pytest.raises(ValueError, match="Invalid skill name"):
            execute_chain(
                steps=[_step(skill="bad skill name!")],
                repo_root=repo,
                context_preamble=None,
            )

    def test_unresolvable_skill_raises(self, tmp_path):
        """A skill that cannot be found raises RuntimeError."""
        repo = _make_repo(tmp_path)

        with pytest.raises(RuntimeError, match="Could not find skill directory"):
            execute_chain(
                steps=[_step(skill="nonexistent-skill")],
                repo_root=repo,
                context_preamble=None,
            )

    def test_stderr_secrets_sanitized(self, tmp_path, capsys):
        """Secrets in stderr are redacted before logging."""
        repo = _make_repo(tmp_path)

        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "auth_key=supersecretvalue123"

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", return_value=proc),
            patch("skill_eval.runner.logger") as mock_logger,
        ):
            execute_chain(
                steps=[_step()],
                repo_root=repo,
                context_preamble=None,
            )

        # Verify the logged message doesn't contain the raw secret.
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        for wc in warning_calls:
            assert "supersecretvalue123" not in wc

    def test_context_preamble_included_in_prompt(self, tmp_path):
        """context_preamble is included in the step's stdin input."""
        repo = _make_repo(tmp_path)

        captured_inputs: list[str] = []

        def mock_run(*args, **kwargs):
            captured_inputs.append(kwargs.get("input", ""))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "done"
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[_step()],
                repo_root=repo,
                context_preamble="MY CLAUDE.MD CONTENT",
            )

        assert "MY CLAUDE.MD CONTENT" in captured_inputs[0]
        assert "BEHAVIORAL CONTEXT" in captured_inputs[0]

    def test_claudecode_env_var_set(self, tmp_path):
        """CLAUDECODE='' is set in the subprocess environment."""
        repo = _make_repo(tmp_path)

        captured_env: list[dict] = []

        def mock_run(*args, **kwargs):
            captured_env.append(kwargs.get("env", {}))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "done"
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[_step()],
                repo_root=repo,
                context_preamble=None,
            )

        assert "CLAUDECODE" in captured_env[0]
        assert captured_env[0]["CLAUDECODE"] == ""

    def test_multiple_steps_in_order(self, tmp_path):
        """Steps execute in order and results accumulate."""
        repo = _make_repo(tmp_path)

        outputs = ["output-1", "output-2", "output-3"]
        call_iter = iter(outputs)

        def mock_run(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = next(call_iter)
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            results = execute_chain(
                steps=[
                    _step(prompt_template="step 1"),
                    _step(prompt_template="step 2"),
                    _step(prompt_template="step 3"),
                ],
                repo_root=repo,
                context_preamble=None,
            )

        assert [r.skill_output for r in results] == ["output-1", "output-2", "output-3"]

    def test_per_step_timeout_used(self, tmp_path):
        """Each step's timeout_seconds is passed to subprocess.run."""
        repo = _make_repo(tmp_path)

        captured_timeouts: list[int] = []

        def mock_run(*args, **kwargs):
            captured_timeouts.append(kwargs.get("timeout"))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "done"
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[
                    {"skill": "quality-gate", "prompt_template": "step 1", "timeout_seconds": 60},
                    {"skill": "quality-gate", "prompt_template": "step 2", "timeout_seconds": 120},
                ],
                repo_root=repo,
                context_preamble=None,
            )

        # subprocess.run is called twice per step (git worktree prune + actual claude).
        # Filter only the claude invocations — those have timeout set.
        assert 60 in captured_timeouts
        assert 120 in captured_timeouts

    def test_missing_timeout_raises_key_error(self, tmp_path):
        """A step without timeout_seconds raises KeyError."""
        repo = _make_repo(tmp_path)

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", return_value=_mock_claude_proc()),
            pytest.raises(KeyError),
        ):
            execute_chain(
                steps=[{"skill": "quality-gate", "prompt_template": "test"}],
                repo_root=repo,
                context_preamble=None,
            )

    def test_bare_flag_passed_when_set(self, tmp_path):
        """step["bare"]=True passes --bare to the claude CLI."""
        repo = _make_repo(tmp_path)

        captured_cmds: list[list] = []

        def mock_run(*args, **kwargs):
            if args:
                captured_cmds.append(args[0])
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "done"
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[_step(bare=True)],
                repo_root=repo,
                context_preamble=None,
            )

        claude_cmds = [c for c in captured_cmds if c and c[0] == "claude"]
        assert any("--bare" in c for c in claude_cmds)

    def test_bundle_passed_via_append_system_prompt(self, tmp_path):
        """The skill bundle is passed via --append-system-prompt, not stdin."""
        repo = _make_repo(tmp_path)

        captured_cmds: list[list] = []
        captured_inputs: list[str] = []

        def mock_run(*args, **kwargs):
            if args:
                captured_cmds.append(list(args[0]))
            captured_inputs.append(kwargs.get("input", ""))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "done"
            proc.stderr = ""
            return proc

        with (
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# MY BUNDLE"),
            patch("skill_eval.runner.subprocess.run", side_effect=mock_run),
        ):
            execute_chain(
                steps=[_step(prompt_template="do the task")],
                repo_root=repo,
                context_preamble=None,
            )

        claude_cmds = [c for c in captured_cmds if c and c[0] == "claude"]
        assert claude_cmds, "No claude invocation found"
        cmd = claude_cmds[0]
        # Bundle must appear as value after --append-system-prompt.
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "# MY BUNDLE"
        # stdin must not contain the bundle marker.
        claude_input = captured_inputs[captured_cmds.index(cmd)]
        assert "# MY BUNDLE" not in claude_input


# ── Group 2: run_composition_eval ────────────────────────────────────────────


class TestRunCompositionEval:
    """run_composition_eval manages worktree lifecycle and scores final output."""

    def _make_composition(self, fixture_key: str = "composition/terse") -> dict:
        return {
            "name": "test-composition",
            "fixture": fixture_key,
            "rubrics": [],
            "configs": [
                {
                    "name": "baseline",
                    "steps": [_step(prompt_template="{fixture}")],
                }
            ],
        }

    def test_returns_dict_of_composition_results(self, tmp_path):
        """run_composition_eval returns a dict of CompositionResult."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\nDo something.\n")

        mock_judge = MagicMock()

        with (
            patch("skill_eval.runner.subprocess.run") as mock_run,
            patch("skill_eval.runner.resolve_skill_bundle", return_value="# bundle"),
            patch("skill_eval.runner.execute_chain") as mock_chain,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_chain.return_value = [ChainStepResult("quality-gate", "the output", None)]

            results = run_composition_eval(
                self._make_composition(),
                repo,
                mock_judge,
                n_trials=1,
            )

        assert isinstance(results, dict)
        assert "baseline" in results
        assert isinstance(results["baseline"], CompositionResult)
        assert results["baseline"].config_name == "baseline"

    def test_infra_error_on_chain_failure(self, tmp_path):
        """Chain execution failure returns CompositionResult with infra_error=True."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\n")

        mock_judge = MagicMock()

        with (
            patch("skill_eval.runner.subprocess.run") as mock_run,
            patch("skill_eval.runner.execute_chain", side_effect=RuntimeError("chain failed")),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            results = run_composition_eval(
                self._make_composition(),
                repo,
                mock_judge,
                n_trials=1,
            )

        assert results["baseline"].infra_error is True

    def test_worktree_cleanup_on_success(self, tmp_path):
        """git worktree remove is called after successful chain execution."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\n")

        mock_judge = MagicMock()
        worktree_remove_calls: list[list] = []

        def mock_subprocess_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "remove" in cmd:
                worktree_remove_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("skill_eval.runner.subprocess.run", side_effect=mock_subprocess_run),
            patch(
                "skill_eval.runner.execute_chain",
                return_value=[ChainStepResult("quality-gate", "output", None)],
            ),
        ):
            run_composition_eval(self._make_composition(), repo, mock_judge, n_trials=1)

        # Verify worktree remove was called.
        assert any("remove" in c for c in worktree_remove_calls)

    def test_worktree_cleanup_on_chain_failure(self, tmp_path):
        """git worktree remove is called even when chain execution fails."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\n")

        mock_judge = MagicMock()
        worktree_remove_calls: list[list] = []

        def mock_subprocess_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "remove" in cmd:
                worktree_remove_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("skill_eval.runner.subprocess.run", side_effect=mock_subprocess_run),
            patch("skill_eval.runner.execute_chain", side_effect=RuntimeError("boom")),
        ):
            results = run_composition_eval(self._make_composition(), repo, mock_judge, n_trials=1)

        assert results["baseline"].infra_error is True
        assert any("remove" in c for c in worktree_remove_calls)

    def test_fixture_not_found_raises(self, tmp_path):
        """Missing fixture raises FileNotFoundError."""
        repo = _make_repo(tmp_path)
        mock_judge = MagicMock()

        with (
            patch("skill_eval.runner.subprocess.run", return_value=MagicMock(returncode=0)),
            pytest.raises(FileNotFoundError),
        ):
            run_composition_eval(
                self._make_composition("composition/nonexistent"),
                repo,
                mock_judge,
                n_trials=1,
            )

    def test_n_trials_runs_multiple_times(self, tmp_path):
        """n_trials=3 runs the chain 3 times."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\n")

        mock_judge = MagicMock()
        chain_call_count = {"n": 0}

        def mock_chain(*args, **kwargs):
            chain_call_count["n"] += 1
            return [ChainStepResult("quality-gate", f"output-{chain_call_count['n']}", None)]

        with (
            patch("skill_eval.runner.subprocess.run") as mock_run,
            patch("skill_eval.runner.execute_chain", side_effect=mock_chain),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            run_composition_eval(self._make_composition(), repo, mock_judge, n_trials=3)

        assert chain_call_count["n"] == 3

    def test_multiple_configs_returned(self, tmp_path):
        """Multiple configs each produce a CompositionResult in the returned dict."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\n")

        mock_judge = MagicMock()
        composition = {
            "name": "multi-config",
            "fixture": "composition/terse",
            "rubrics": [],
            "configs": [
                {"name": "config-a", "steps": [_step()]},
                {"name": "config-b", "steps": [_step()]},
            ],
        }

        with (
            patch("skill_eval.runner.subprocess.run") as mock_run,
            patch(
                "skill_eval.runner.execute_chain",
                return_value=[ChainStepResult("quality-gate", "output", None)],
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            results = run_composition_eval(composition, repo, mock_judge, n_trials=1)

        assert set(results.keys()) == {"config-a", "config-b"}

    def test_fixture_key_is_fixture_not_fixture_key(self, tmp_path):
        """Fixture is read from composition['fixture'], not composition['fixture_key']."""
        repo = _make_repo(tmp_path)
        _make_fixture(repo, "composition/terse", "# Plan\n")

        mock_judge = MagicMock()
        # composition dict uses 'fixture', not 'fixture_key'
        composition = {
            "name": "test",
            "fixture": "composition/terse",
            "rubrics": [],
            "configs": [{"name": "c1", "steps": [_step()]}],
        }

        with (
            patch("skill_eval.runner.subprocess.run") as mock_run,
            patch(
                "skill_eval.runner.execute_chain",
                return_value=[ChainStepResult("quality-gate", "output", None)],
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            results = run_composition_eval(composition, repo, mock_judge, n_trials=1)

        assert "c1" in results


# ── Group 3: compare_composition_results ─────────────────────────────────────


class TestCompareCompositionResults:
    """compare_composition_results detects degradation across configs."""

    def _make_result(
        self,
        config_name: str = "test",
        scores: dict | None = None,
        infra_error: bool = False,
    ) -> CompositionResult:
        return CompositionResult(
            config_name=config_name,
            step_results=[],
            final_scores=scores or {},
            trial_scores=None,
            infra_error=infra_error,
        )

    def _make_results(self, **named_scores) -> dict[str, CompositionResult]:
        """Build a results dict from config_name -> scores pairs."""
        return {
            name: self._make_result(config_name=name, scores=scores)
            for name, scores in named_scores.items()
        }

    def test_no_regression_when_within_tolerance(self):
        """No degradation when score drop is within tolerance."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.1},
            current={"implementer_judgment": 8.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline", tolerance=0.15)

        assert cmp["degradation_detected"] is False
        assert "PASS" in cmp["reports"]["current"]

    def test_degradation_detected_when_score_drops(self):
        """Degradation flagged when score drops more than tolerance."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            current={"implementer_judgment": 5.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline", tolerance=0.15)

        assert cmp["degradation_detected"] is True
        assert "DEGRADATION" in cmp["reports"]["current"]

    def test_infra_error_in_current_is_degradation(self):
        """infra_error=True in a non-baseline config signals degradation."""
        results = {
            "baseline": self._make_result(config_name="baseline", scores={"x": 8.0}),
            "current": self._make_result(config_name="current", infra_error=True),
        }

        cmp = compare_composition_results(results, baseline_config="baseline")

        assert cmp["degradation_detected"] is True
        assert "INFRA ERROR" in cmp["reports"]["current"]

    def test_infra_error_in_baseline_is_pass(self):
        """infra_error=True in baseline treats comparison as pass."""
        results = {
            "baseline": self._make_result(config_name="baseline", infra_error=True),
            "current": self._make_result(config_name="current", scores={"x": 8.0}),
        }

        cmp = compare_composition_results(results, baseline_config="baseline")

        assert cmp["degradation_detected"] is False

    def test_empty_baseline_scores_is_pass(self):
        """No baseline scores available — treating as pass."""
        results = self._make_results(
            baseline={},
            current={"implementer_judgment": 8.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline")

        assert cmp["degradation_detected"] is False

    def test_missing_metric_in_current_flags_degradation(self):
        """A metric present in baseline but absent from current is a regression."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            current={},
        )

        cmp = compare_composition_results(results, baseline_config="baseline")

        assert cmp["degradation_detected"] is True
        assert "missing from current" in cmp["reports"]["current"]

    def test_report_contains_ascii_table(self):
        """The report contains an ASCII table with metric names and scores."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            current={"implementer_judgment": 8.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline")

        report = cmp["reports"]["current"]
        assert "implementer_judgment" in report
        assert "Current" in report
        assert "Baseline" in report
        assert "Delta" in report

    def test_per_rubric_deltas_shown(self):
        """Delta values appear in the ASCII table."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            current={"implementer_judgment": 7.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline", tolerance=0.15)

        report = cmp["reports"]["current"]
        assert "-1.000" in report or "-1.0" in report

    def test_drop_within_tolerance_is_pass(self):
        """A drop smaller than tolerance is not flagged as degradation."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            current={"implementer_judgment": 7.9},
        )

        # Drop = 0.1, tolerance = 0.15 → not > tolerance → pass.
        cmp = compare_composition_results(results, baseline_config="baseline", tolerance=0.15)

        assert cmp["degradation_detected"] is False

    def test_baseline_config_not_in_reports(self):
        """The baseline config itself does not appear in the reports dict."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            current={"implementer_judgment": 8.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline")

        assert "baseline" not in cmp["reports"]

    def test_multiple_non_baseline_configs(self):
        """All non-baseline configs are compared and reported."""
        results = self._make_results(
            baseline={"implementer_judgment": 8.0},
            config_a={"implementer_judgment": 8.0},
            config_b={"implementer_judgment": 5.0},
        )

        cmp = compare_composition_results(results, baseline_config="baseline")

        assert "config_a" in cmp["reports"]
        assert "config_b" in cmp["reports"]
        assert cmp["degradation_detected"] is True

    def test_missing_baseline_config_returns_degradation(self):
        """Missing baseline config name returns degradation_detected=True."""
        results = self._make_results(current={"implementer_judgment": 8.0})

        cmp = compare_composition_results(results, baseline_config="nonexistent")

        assert cmp["degradation_detected"] is True


# ── Group 4: load_composition_fixture and load_composition_config ─────────────


class TestLoadCompositionFixture:
    """load_composition_fixture validates keys and returns full file content."""

    def test_returns_full_content_including_frontmatter(self, tmp_path):
        """Frontmatter is NOT stripped — caller parses it."""
        repo = tmp_path / "repo"
        _make_fixture(
            repo,
            "composition/test-fixture",
            "---\nscaffold:\n  files: []\n---\n\n# Plan\nDo things.\n",
        )

        content = load_composition_fixture("composition/test-fixture", repo)

        assert "---" in content
        assert "scaffold" in content
        assert "# Plan" in content

    def test_invalid_key_raises_value_error(self, tmp_path):
        """Fixture key with invalid characters raises ValueError."""
        with pytest.raises(ValueError, match="Invalid fixture key"):
            load_composition_fixture("../../../etc/passwd", tmp_path)

    def test_missing_fixture_raises_file_not_found(self, tmp_path):
        """Missing fixture raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_composition_fixture("composition/nonexistent", tmp_path)

    def test_key_with_subdirectory_path_is_valid(self, tmp_path):
        """Keys with subdirectory segments (e.g. 'composition/foo') are valid."""
        repo = tmp_path / "repo"
        _make_fixture(repo, "composition/valid-key", "# content\n")

        content = load_composition_fixture("composition/valid-key", repo)

        assert "# content" in content

    def test_path_traversal_key_rejected(self, tmp_path):
        """Keys containing '..' are rejected by validation."""
        with pytest.raises(ValueError, match="Invalid fixture key"):
            load_composition_fixture("composition/../../../secret", tmp_path)


class TestLoadCompositionConfig:
    """load_composition_config reads JSON and raises on missing file."""

    def test_loads_valid_config(self, tmp_path):
        """A valid composition config is loaded as a dict."""
        config = {"compositions": [{"name": "test", "configs": []}]}
        path = _make_composition_config(tmp_path, config)

        result = load_composition_config(path)

        assert result["compositions"][0]["name"] == "test"

    def test_raises_on_missing_file(self, tmp_path):
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_composition_config(tmp_path / "nonexistent.json")

    def test_empty_compositions_list_is_valid(self, tmp_path):
        """An empty compositions list is valid — no error raised."""
        config = {"compositions": []}
        path = _make_composition_config(tmp_path, config)

        result = load_composition_config(path)

        assert result["compositions"] == []
