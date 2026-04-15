"""Eval runner module for skill-eval framework.

Orchestrates skill bundle resolution, test case loading, metric construction,
per-test-case evaluation, and baseline comparison.

Security constraints (enforced throughout):
- All subprocess.run calls use list-form args (never shell=True).
- Path boundary check (resolve().is_relative_to(repo_root)) runs AFTER
  resolution and BEFORE any file read.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepeval.models.base_model import DeepEvalBaseLLM

    from skill_eval.contains_metric import ContainsMetric

logger = logging.getLogger(__name__)

# Plugin directories allowed as path prefixes in reference resolution.
_PLUGIN_ALLOWLIST: frozenset[str] = frozenset(
    {"code-quality", "dev-guard", "git-tools", "github-mcp", "jira"}
)

# Regex for backtick-quoted reference paths.
_RE_BACKTICK = re.compile(r"`((?:[a-zA-Z0-9_.-]+/)*references/[a-zA-Z0-9_.-]+\.md)`")

# Regex for angle-bracket reference paths (DOTALL for multi-line angle brackets).
_RE_ANGLE = re.compile(
    r"<[^>]*?((?:[a-zA-Z0-9_.-]+/)*references/[a-zA-Z0-9_.-]+\.md)[^>]*?>",
    re.DOTALL,
)


def _get_repo_root() -> Path:
    """Return the git repository root as an absolute Path."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve()


def resolve_skill_bundle(
    skill_dir: Path,
    git_ref: str | None = None,
    repo_root: Path | None = None,
) -> str | None:
    """Build a concatenated prompt bundle for a skill.

    Reads SKILL.md (from disk or via git show for --compare mode) and
    resolves all referenced files into a single string suitable for
    LLM-as-judge evaluation.

    Args:
        skill_dir: Absolute path to the skill directory containing SKILL.md.
        git_ref: Optional git ref (e.g. "HEAD~1") for --compare mode.
            When provided, SKILL.md content is read from git history.
        repo_root: Repository root for resolving plugin-prefixed reference
            paths. Defaults to the result of ``git rev-parse --show-toplevel``.

    Returns:
        Concatenated bundle string, or None if SKILL.md cannot be read
        (e.g. renamed directory in git history).
    """
    if repo_root is None:
        repo_root = _get_repo_root()

    skill_md_path = skill_dir / "SKILL.md"

    # ── Read SKILL.md ──────────────────────────────────────────────────────
    if git_ref is None:
        # Boundary check before reading from disk.
        resolved = skill_md_path.resolve()
        if not resolved.is_relative_to(repo_root):
            logger.warning("SKILL.md at %s is outside repo root — skipping", skill_md_path)
            return None
        try:
            skill_content = skill_md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", skill_md_path, exc)
            return None
    else:
        # git show for --compare mode. Compute path relative to repo root.
        try:
            rel = skill_md_path.relative_to(repo_root)
        except ValueError:
            logger.warning("SKILL.md at %s is outside repo root — skipping", skill_md_path)
            return None
        git_path = f"{git_ref}:{rel.as_posix()}"
        result = subprocess.run(
            ["git", "show", git_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "git show %s failed (directory may have been renamed): %s",
                git_path,
                result.stderr.strip(),
            )
            return None
        skill_content = result.stdout

    # ── Extract reference paths (two regex passes, dedup) ─────────────────
    seen: dict[str, None] = {}
    for match in _RE_BACKTICK.finditer(skill_content):
        seen.setdefault(match.group(1), None)
    for match in _RE_ANGLE.finditer(skill_content):
        seen.setdefault(match.group(1), None)
    ref_paths = list(seen.keys())

    # ── Resolve and read each reference file ──────────────────────────────
    ref_sections: list[str] = []
    for ref_path in ref_paths:
        parts = ref_path.split("/")
        if parts[0] == "references":
            # Bare per-skill path: resolve against skill_dir.
            candidate = skill_dir / ref_path
        elif parts[0] in _PLUGIN_ALLOWLIST:
            # Plugin-prefixed path: resolve against repo_root.
            candidate = repo_root / ref_path
        else:
            logger.warning(
                "Reference prefix %r not in allowlist — skipping %s",
                parts[0],
                ref_path,
            )
            continue

        # Security boundary check BEFORE any file read.
        try:
            resolved_ref = candidate.resolve()
        except OSError:
            logger.warning("Cannot resolve path %s — skipping", candidate)
            continue
        if not resolved_ref.is_relative_to(repo_root):
            logger.warning("Reference %s resolves outside repo root — skipping", ref_path)
            continue

        if git_ref is None:
            try:
                ref_content = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Cannot read reference %s: %s", candidate, exc)
                continue
        else:
            try:
                rel_ref = candidate.relative_to(repo_root)
            except ValueError:
                logger.warning("Reference %s is outside repo root — skipping", candidate)
                continue
            git_ref_path = f"{git_ref}:{rel_ref.as_posix()}"
            result = subprocess.run(
                ["git", "show", git_ref_path],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.warning("git show %s failed: %s", git_ref_path, result.stderr.strip())
                continue
            ref_content = result.stdout

        ref_sections.append(f"## {candidate.name}\n{ref_content}")

    # ── Concatenate bundle ─────────────────────────────────────────────────
    bundle = f"# SKILL.MD\n{skill_content}"
    if ref_sections:
        bundle += "\n\n# REFERENCED FILES\n" + "\n\n".join(ref_sections)
    return bundle


def load_eval_config(skill_name: str) -> dict:
    """Load the evaluation config for a skill from the test_cases directory.

    Test case JSON format
    ---------------------
    Each file at ``skill-eval/test_cases/{skill_name}.json`` must conform to:

    .. code-block:: json

        {
          "skill_name": "quality-gate",
          "rubrics": ["anti_deferral", "phase_completion"],
          "test_cases": [
            {
              "id": 1,
              "prompt": "Scenario description used as GEval INPUT",
              "expected_behaviors": ["Human-readable description"],
              "assertions": [
                "contains: <substring that must appear in the bundle>",
                "not-contains: <substring that must NOT appear>"
              ]
            }
          ]
        }

    Fields:
        skill_name: Must match the JSON filename (without .json extension).
        rubrics: List of rubric names from RUBRIC_REGISTRY to apply via GEval.
            All four registered rubrics: anti_deferral, fabrication_avoidance,
            phase_completion, instruction_adherence.
        test_cases: List of test case objects. Each test case:
            id: Integer identifier, unique within the file.
            prompt: The scenario context string. Passed as LLMTestCase.input;
                the skill bundle is passed as LLMTestCase.actual_output.
            expected_behaviors: List of human-readable descriptions (documentation
                only — not used by the evaluator).
            assertions: List of assertion strings. Each must start with
                ``contains: `` or ``not-contains: `` followed by the substring
                to check. Assertions are case-insensitive and checked against
                the skill bundle (actual_output). Evaluated by ContainsMetric.

    Args:
        skill_name: The skill identifier (e.g. "quality-gate").

    Returns:
        Full config dict with keys "skill_name", "rubrics", and "test_cases".
        Returns a shell dict with empty rubrics and test_cases if no file found.
    """
    if "/" in skill_name or "\\" in skill_name:
        return {"skill_name": skill_name, "rubrics": [], "test_cases": []}
    config_path = Path(__file__).parent.parent / "test_cases" / f"{skill_name}.json"
    if not config_path.exists():
        return {"skill_name": skill_name, "rubrics": [], "test_cases": []}
    return json.loads(config_path.read_text(encoding="utf-8"))


def load_baselines(baselines_path: Path | None = None) -> dict[str, dict[str, float]]:
    """Load stored baseline scores from baselines.json.

    Args:
        baselines_path: Path to baselines.json. Defaults to
            ``skill-eval/baselines.json`` (sibling of the test_cases directory).

    Returns:
        Mapping of skill_name -> {metric_name: baseline_score}.
        Returns empty dict if the file does not exist.
    """
    if baselines_path is None:
        baselines_path = Path(__file__).parent.parent / "baselines.json"
    if not baselines_path.exists():
        return {}
    data = json.loads(baselines_path.read_text(encoding="utf-8"))
    return data.get("baselines", data)  # handles both wrapped and flat formats


def build_metrics(rubric_names: list[str], judge: DeepEvalBaseLLM) -> list:
    """Construct GEval metric instances for the given rubric names.

    Args:
        rubric_names: List of rubric keys from RUBRIC_REGISTRY.
        judge: DeepEvalBaseLLM instance to use for scoring. Passed explicitly
            to each GEval so it does not fall back to the OpenAI default.

    Returns:
        List of GEval instances, one per rubric name.
    """
    from deepeval.metrics import GEval

    from skill_eval.rubrics import RUBRIC_REGISTRY

    metrics = []
    for name in rubric_names:
        if name not in RUBRIC_REGISTRY:
            raise ValueError(f"Unknown rubric: {name!r}. Known: {list(RUBRIC_REGISTRY)}")
        rubric = RUBRIC_REGISTRY[name]
        metrics.append(
            GEval(
                name=rubric["name"],
                evaluation_steps=rubric["evaluation_steps"],
                evaluation_params=rubric["evaluation_params"],
                model=judge,
                async_mode=False,
            )
        )
    return metrics


def build_assertion_metrics(assertions: list[str]) -> ContainsMetric:
    """Parse contains:/not-contains: assertion strings into a ContainsMetric.

    Args:
        assertions: List of strings like ``"contains: foo"`` or
            ``"not-contains: bar"``.

    Returns:
        A ContainsMetric configured with the parsed expected/forbidden lists.
    """
    from skill_eval.contains_metric import ContainsMetric

    expected: list[str] = []
    forbidden: list[str] = []
    for assertion in assertions:
        if assertion.startswith("contains: "):
            expected.append(assertion[len("contains: ") :])
        elif assertion.startswith("not-contains: "):
            forbidden.append(assertion[len("not-contains: ") :])
        else:
            logger.warning("Unknown assertion format: %r — skipping", assertion)
    return ContainsMetric(expected=expected, forbidden=forbidden)


def run_eval(
    skill_name: str,
    skill_prompt_bundle: str,
    test_cases: list[dict],
    rubric_names: list[str],
    judge: DeepEvalBaseLLM,
) -> dict:
    """Run per-test-case evaluation and aggregate results.

    Each test case is evaluated independently via direct metric.measure() calls
    to provide assertion isolation (ContainsMetric thresholds are per-case).
    Scores are read directly from each metric object after measurement.

    Args:
        skill_name: The skill identifier (used in the result dict).
        skill_prompt_bundle: The concatenated SKILL.md + references content.
        test_cases: List of test case dicts from load_eval_config().
        rubric_names: Rubric keys to evaluate with GEval.
        judge: DeepEvalBaseLLM instance for GEval scoring.

    Returns:
        Dict with keys:
            skill: skill_name
            scores: {metric_name: average_float} across all test cases
            pass_rate: fraction of test cases where ALL metrics passed
            details: list of per-test-case result dicts
    """
    from deepeval.test_case import LLMTestCase

    score_accum: dict[str, list[float]] = {}
    passes: list[bool] = []
    details: list[dict] = []
    infra_error_count: int = 0

    for tc in test_cases:
        tc_id = tc.get("id", "?")
        prompt = tc.get("prompt", "")
        assertions = tc.get("assertions", [])

        # GEval is stateful (measure() writes score/success onto the instance),
        # so we must build fresh instances per test case. deepeval does not expose
        # a copy/clone mechanism. ContainsMetric also varies per test case
        # (assertions differ), so both must be constructed here.
        geval_metrics = build_metrics(rubric_names, judge)
        contains = build_assertion_metrics(assertions)
        all_metrics = geval_metrics + [contains]

        llm_tc = LLMTestCase(input=prompt, actual_output=skill_prompt_bundle)

        tc_scores: dict[str, float] = {}
        tc_pass = True
        tc_had_infra_error = False

        # Call measure() directly on each metric — avoids deepeval.evaluate()'s
        # internal copy_metrics() which would leave original scores at None.
        for metric in all_metrics:
            metric_name = getattr(metric, "name", type(metric).__name__)
            try:
                metric.measure(llm_tc)
            except Exception as exc:
                logger.warning("metric %s failed: %s", metric_name, exc)
                tc_had_infra_error = True
            score = metric.score if metric.score is not None else 0.0
            tc_scores[metric_name] = score
            score_accum.setdefault(metric_name, []).append(score)
            if not metric.is_successful():
                tc_pass = False

        if tc_had_infra_error:
            infra_error_count += 1

        passes.append(tc_pass)
        details.append(
            {
                "id": tc_id,
                "prompt": prompt,
                "scores": tc_scores,
                "passed": tc_pass,
            }
        )

    # Aggregate averages.
    avg_scores = {name: sum(vals) / len(vals) for name, vals in score_accum.items()}
    pass_rate = sum(passes) / len(passes) if passes else 0.0
    total_cases = len(test_cases)
    infra_error = total_cases > 0 and infra_error_count >= total_cases / 2

    return {
        "skill": skill_name,
        "scores": avg_scores,
        "pass_rate": pass_rate,
        "details": details,
        "infra_error": infra_error,
    }


def compare_baselines(
    results: dict,
    baselines: dict,
    threshold: float = 0.15,
) -> tuple[bool, str]:
    """Compare current eval results against stored baselines.

    Threshold calibrated from 5x variance measurement with temperature=0
    (2026-04-15): max observed range = 0.133 (tr.instruction_adherence).
    Threshold set to 0.15 (max + 0.02 margin). Prior measurement at
    temperature=1.0 (default) showed 0.380 max range — temperature=0
    reduced variance by 65%.

    A regression is detected when any metric score drops by more than
    ``threshold`` below its baseline value.

    Args:
        results: Output from run_eval() — must have "skill" and "scores" keys.
        baselines: Output from load_baselines() — mapping of skill -> {metric: score}.
        threshold: Maximum allowed score drop (default 0.05).

    Returns:
        Tuple of (passed: bool, report: str). passed is True when no regression
        is detected. report describes any regressions or confirms passage.
    """
    skill = results.get("skill", "unknown")

    if results.get("infra_error"):
        return False, f"{skill}: INFRA ERROR — judge unavailable"

    current_scores: dict[str, float] = results.get("scores", {})
    baseline_scores: dict[str, float] = baselines.get(skill, {})

    if not baseline_scores:
        return True, f"{skill}: no baseline — treating as pass"

    regressions: list[str] = []
    for metric, baseline in baseline_scores.items():
        current = current_scores.get(metric)
        if current is None:
            regressions.append(
                f"  {metric}: missing from current results (baseline={baseline:.3f})"
            )
            continue
        drop = baseline - current
        if drop > threshold:
            regressions.append(
                f"  {metric}: {current:.3f} vs baseline {baseline:.3f} "
                f"(drop={drop:.3f} > threshold={threshold:.3f})"
            )

    if regressions:
        report = f"{skill}: REGRESSION DETECTED\n" + "\n".join(regressions)
        return False, report

    lines = [f"{skill}: PASS"]
    for metric, baseline in baseline_scores.items():
        current = current_scores.get(metric, 0.0)
        lines.append(f"  {metric}: {current:.3f} (baseline={baseline:.3f})")
    return True, "\n".join(lines)
