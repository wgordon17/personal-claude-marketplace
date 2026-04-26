"""Eval runner module for skill-eval framework (Approach B — behavioral evaluation).

Evaluates skills by EXECUTING them: the skill bundle is given to an LLM as
instructions, a test scenario is provided as input, and the LLM's behavioral
output is evaluated for quality. This tests what the skill DOES, not what
it SAYS.

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
import threading
from concurrent.futures import ThreadPoolExecutor
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


# Skill-goal rubrics that use EXPECTED_OUTPUT and require expected_behaviors.
# Shared between load_eval_config() validation and tests.
SKILL_GOAL_RUBRICS: frozenset[str] = frozenset(
    {
        "anti_evasion",
        "finding_completeness",
        "manipulation_resistance",
        "phase_completion",
        "severity_accuracy",
        "false_positive_resistance",
        "fix_correctness",
        "cleanup_thoroughness",
        "classification_precision",
        "review_comprehensiveness",
        "plan_analysis_depth",
        "orchestration_design",
        "plan_construction",
        "judgment_fidelity",
        "summary_accuracy",
        "root_cause_analysis",
        "multi_pass_execution",
        "pr_depth",
        "convention_adherence",
        "follow_through",
    }
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


def load_eval_config(skill_name: str, tc_dir: Path | None = None) -> dict:
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
              "rubrics": ["anti_deferral"],
              "assertions": [
                "contains: <substring that must appear in the response>",
                "not-contains: <substring that must NOT appear>"
              ]
            }
          ]
        }

    Fields:
        skill_name: Must match the JSON filename (without .json extension).
        rubrics: List of rubric names from RUBRIC_REGISTRY to apply via GEval.
            Registered rubrics: anti_deferral, anti_evasion,
            fabrication_avoidance, phase_completion, instruction_adherence,
            finding_completeness, manipulation_resistance, severity_accuracy.
        test_cases: List of test case objects. Each test case:
            id: Integer identifier, unique within the file.
            prompt: The scenario context string. Passed as LLMTestCase.input;
                the LLM's response is passed as LLMTestCase.actual_output.
            expected_behaviors: Answer key for skill-goal rubrics -- joined
                and passed as LLMTestCase.expected_output. Required (non-empty)
                when rubrics include any skill-goal rubric (e.g.,
                review_comprehensiveness, fix_correctness, plan_analysis_depth,
                orchestration_design, etc.).
            rubrics: Optional per-case list of rubric names. When present,
                overrides config-level rubrics for this test case only.
            assertions: List of assertion strings. Each must start with
                ``contains: `` or ``not-contains: `` followed by the substring
                to check. Assertions are case-insensitive and checked against
                the LLM's response (actual_output). Evaluated by ContainsMetric.

    Args:
        skill_name: The skill identifier (e.g. "quality-gate").

    Returns:
        Full config dict with keys "skill_name", "rubrics", and "test_cases".
        Returns a shell dict with empty rubrics and test_cases if no file found.

    Raises:
        ValueError: If ``rubrics`` is an empty list ``[]``, or if any test case
            uses a competency rubric but has no ``expected_behaviors``.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", skill_name):
        return {"skill_name": skill_name, "rubrics": [], "test_cases": []}
    base = tc_dir if tc_dir is not None else Path(__file__).parent.parent / "test_cases"
    config_path = base / f"{skill_name}.json"
    if not config_path.exists():
        return {"skill_name": skill_name, "rubrics": [], "test_cases": []}
    config = json.loads(config_path.read_text(encoding="utf-8"))

    # Validate: rubrics must not be empty list.
    rubrics = config.get("rubrics")
    if isinstance(rubrics, list) and len(rubrics) == 0:
        raise ValueError(
            f"Eval config for {skill_name!r} has empty rubrics list []."
            " Remove the 'rubrics' key or provide at least one rubric."
        )

    # Validate: skill-goal rubrics require expected_behaviors.
    config_rubrics = set(config.get("rubrics", []))
    for tc in config.get("test_cases", []):
        tc_rubrics = set(tc.get("rubrics", [])) or config_rubrics
        if tc_rubrics & SKILL_GOAL_RUBRICS:
            behaviors = tc.get("expected_behaviors", [])
            if not behaviors:
                tc_id = tc.get("id", "?")
                used = tc_rubrics & SKILL_GOAL_RUBRICS
                raise ValueError(
                    f"Test case {tc_id} in {skill_name!r} uses competency rubric(s)"
                    f" {sorted(used)} but has no expected_behaviors."
                    " Competency rubrics require an answer key."
                )

    return config


# ── Fixture loading ──────────────────────────────────────────────────────────

# Allowlist regex for fixture path components.
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Regex for YAML frontmatter marker: exactly "---" with optional trailing whitespace.
_FRONTMATTER_RE = re.compile(r"^---\s*$")

# Prompt delimiters used by execute_skill — must be stripped from fixture content
# to prevent prompt injection.
_PROMPT_DELIMITERS = [
    "=== SKILL INSTRUCTIONS ===",
    "=== END SKILL INSTRUCTIONS ===",
    "=== BEHAVIORAL CONTEXT (CLAUDE.md) ===",
    "=== END BEHAVIORAL CONTEXT ===",
]

# Regex for fixture placeholders in prompt templates.
_FIXTURE_PLACEHOLDER_RE = re.compile(r"\{fixture:([^}]+)\}")

# Regex for codebase placeholders: {codebase:REPO/PATH}
# REPO is validated with _SAFE_NAME_RE. PATH components validated individually.
_CODEBASE_PLACEHOLDER_RE = re.compile(r"\{codebase:([^}]+)\}")

# Path component regex for codebase files (extends _SAFE_NAME_RE with dots).
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


def load_fixture(
    skill_name: str,
    fixture_key: str,
    repo_root: Path | None = None,
) -> str | None:
    """Load a fixture file for a skill test case.

    Fixture files live at ``skill-eval/fixtures/{skill_name}/{fixture_key}.md``.
    Optional YAML frontmatter (delimited by ``---`` lines) is stripped before
    returning. Lines matching ``execute_skill`` prompt delimiters are also
    removed to prevent prompt injection.

    Args:
        skill_name: The skill identifier (e.g. "quality-gate").
        fixture_key: The fixture filename stem (e.g. "1-clean-plan").
        repo_root: Repository root for path resolution. Defaults to
            ``git rev-parse --show-toplevel``.

    Returns:
        Fixture content as a string (frontmatter stripped), or None if the
        file does not exist or inputs are invalid.
    """
    # Input validation: both must match safe name pattern.
    if not _SAFE_NAME_RE.match(skill_name) or not _SAFE_NAME_RE.match(fixture_key):
        return None

    if repo_root is None:
        repo_root = _get_repo_root()

    candidate = repo_root / "skill-eval" / "fixtures" / skill_name / f"{fixture_key}.md"

    # Path boundary check BEFORE reading.
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(repo_root.resolve()):
        return None

    if not candidate.is_file():
        return None

    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read fixture %s: %s", candidate, exc)
        return None

    lines = content.split("\n")

    # Strip YAML frontmatter if present.
    if lines and _FRONTMATTER_RE.match(lines[0]):
        for i in range(1, len(lines)):
            if _FRONTMATTER_RE.match(lines[i]):
                lines = lines[i + 1 :]
                break

    # Strip prompt delimiter lines.
    delimiter_set = set(_PROMPT_DELIMITERS)
    lines = [line for line in lines if line.rstrip() not in delimiter_set]

    return "\n".join(lines)


def build_fixture_prompt(
    skill_name: str,
    template: str,
    repo_root: Path | None = None,
) -> tuple[str, bool]:
    """Substitute ``{fixture:KEY}`` and ``{codebase:REPO/PATH}`` placeholders.

    Two-pass resolution: first ``{fixture:KEY}`` placeholders are resolved
    via ``load_fixture``, then ``{codebase:REPO/PATH}`` placeholders are
    resolved via ``load_codebase_file`` in the post-substitution result.

    Args:
        skill_name: The skill identifier for fixture directory lookup.
        template: The prompt string with ``{fixture:KEY}`` and/or
            ``{codebase:REPO/PATH}`` placeholders.
        repo_root: Repository root for path resolution.

    Returns:
        Tuple of (substituted_string, has_missing). The bool is True
        if any placeholder could not be resolved (left as-is in output).
    """
    has_missing = False
    fixture_matches = list(_FIXTURE_PLACEHOLDER_RE.finditer(template))

    if not fixture_matches and "{codebase:" not in template:
        return template, False

    # Resolve repo_root once for all placeholder types.
    if repo_root is None:
        repo_root = _get_repo_root()

    # First pass: resolve {fixture:KEY} placeholders.
    replacements: list[tuple[int, int, str]] = []
    for m in fixture_matches:
        key = m.group(1)
        content = load_fixture(skill_name, key, repo_root=repo_root)
        if content is None:
            logger.warning(
                "Missing fixture %r for skill %r — leaving placeholder",
                key,
                skill_name,
            )
            has_missing = True
        else:
            replacements.append((m.start(), m.end(), content))

    # Apply replacements in reverse order.
    result = template
    for start, end, content in reversed(replacements):
        result = result[:start] + content + result[end:]

    # Second pass: resolve {codebase:REPO/PATH} placeholders in the result.
    codebase_matches = list(_CODEBASE_PLACEHOLDER_RE.finditer(result))
    if codebase_matches:
        codebase_replacements: list[tuple[int, int, str]] = []
        for m in codebase_matches:
            ref = m.group(1)
            content = load_codebase_file(ref, repo_root=repo_root)
            if content is None:
                logger.warning(
                    "Missing codebase file %r — leaving placeholder",
                    ref,
                )
                has_missing = True
            else:
                codebase_replacements.append((m.start(), m.end(), content))
        for start, end, content in reversed(codebase_replacements):
            result = result[:start] + content + result[end:]

    return result, has_missing


# ── Codebase file loading ─────────────────────────────────────────────────────

# Max file size for codebase files (64KB).
_MAX_CODEBASE_FILE_BYTES = 65536


def load_codebase_file(
    codebase_ref: str,
    repo_root: Path | None = None,
) -> str | None:
    """Load a source file from a codebase directory.

    Codebase files live at ``skill-eval/codebases/{REPO}/{PATH}``.
    Content is used as LLM prompt text only — never executed as code.
    Unlike fixtures, YAML frontmatter is NOT stripped (source code files).
    Prompt delimiter lines ARE stripped to prevent prompt boundary escape.

    Args:
        codebase_ref: Reference in REPO/PATH format (e.g. "dirty-flask-app/src/auth/login.py").
        repo_root: Repository root for path resolution.

    Returns:
        File content as string, or None if invalid/missing/too large.
    """
    # Split into REPO and PATH.
    slash_idx = codebase_ref.find("/")
    if slash_idx < 1:
        return None
    repo_name = codebase_ref[:slash_idx]
    file_path = codebase_ref[slash_idx + 1 :]

    # Validate REPO with existing safe name pattern.
    if not _SAFE_NAME_RE.match(repo_name):
        return None

    # Validate PATH components.
    if not file_path or file_path.startswith("/"):
        return None
    path_parts = file_path.split("/")
    for part in path_parts:
        if not part:  # empty segment (double slash)
            return None
        if part in (".", ".."):
            return None
        if not _SAFE_PATH_COMPONENT_RE.match(part):
            return None

    if repo_root is None:
        repo_root = _get_repo_root()

    candidate = repo_root / "skill-eval" / "codebases" / repo_name / file_path

    # Path boundary check BEFORE reading.
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(repo_root.resolve()):
        return None

    if not candidate.is_file():
        return None

    # File size guard.
    try:
        if candidate.stat().st_size > _MAX_CODEBASE_FILE_BYTES:
            logger.warning(
                "Codebase file %r exceeds %d bytes — skipping",
                codebase_ref,
                _MAX_CODEBASE_FILE_BYTES,
            )
            return None
    except OSError:
        return None

    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read codebase file %r: %s", codebase_ref, exc)
        return None

    # Strip prompt delimiter lines (same as fixtures — prevents prompt boundary escape).
    lines = content.split("\n")
    delimiter_set = set(_PROMPT_DELIMITERS)
    lines = [line for line in lines if line.rstrip() not in delimiter_set]

    return "\n".join(lines)


def load_baselines(baselines_path: Path | None = None) -> dict[str, dict[str, float]]:
    """Load stored baseline scores from baselines.json.

    Handles three formats:
      - New nested: ``{"skill": {"scores": {...}, "per_case_scores": {...}}}``
      - Old flat: ``{"skill": {"metric": 0.5, ...}}``
      - Wrapped: ``{"baselines": {...}}`` (either nested or flat inside)

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
    raw = data.get("baselines", data)  # unwrap if wrapped

    # Normalize: new nested format has {"scores": {...}} per skill,
    # old flat format has {"metric": float} directly.
    result: dict[str, dict[str, float]] = {}
    for skill, value in raw.items():
        if isinstance(value, dict) and "scores" in value:
            result[skill] = value["scores"]
        else:
            result[skill] = value
    return result


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

    # Log scoring path: custom DeepEvalBaseLLM subclasses lack generate_raw_response(),
    # so GEval silently falls back to integer-only scoring via ReasonScore schema.
    # This is expected for Claude (no logprobs API) — log once for debuggability.
    has_logprobs = hasattr(judge, "generate_raw_response")
    if not has_logprobs:
        logger.info(
            "Judge %s lacks generate_raw_response() — GEval will use integer fallback scoring"
            " (no token probability weighting)",
            judge.get_model_name(),
        )

    metrics = []
    for name in rubric_names:
        if name not in RUBRIC_REGISTRY:
            raise ValueError(f"Unknown rubric: {name!r}. Known: {list(RUBRIC_REGISTRY)}")
        rubric = RUBRIC_REGISTRY[name]
        geval_kwargs = {
            "name": rubric["name"],
            "evaluation_steps": rubric["evaluation_steps"],
            "evaluation_params": rubric["evaluation_params"],
            "model": judge,
            "async_mode": False,
        }
        if "rubric" in rubric:
            geval_kwargs["rubric"] = rubric["rubric"]
        metrics.append(GEval(**geval_kwargs))
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


def load_context_layers(repo_root: Path | None = None) -> dict[str, str]:
    """Discover and load CLAUDE.md context layers.

    Returns a dict of layer_name -> content for each CLAUDE.md found:
      - "global": ~/.claude/CLAUDE.md (user's private global instructions)
      - "project": {repo_root}/CLAUDE.md (project-level instructions)

    Missing files are silently skipped — the returned dict only contains
    layers that exist and are readable.
    """
    layers: dict[str, str] = {}

    # Global CLAUDE.md — user's private instructions.
    global_claude = Path.home() / ".claude" / "CLAUDE.md"
    if global_claude.is_file():
        try:
            layers["global"] = global_claude.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read global CLAUDE.md: %s", exc)

    # Project CLAUDE.md — repo-level instructions.
    if repo_root is not None:
        project_claude = repo_root / "CLAUDE.md"
        if project_claude.is_file():
            try:
                layers["project"] = project_claude.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Cannot read project CLAUDE.md: %s", exc)

    return layers


def execute_skill(
    skill_prompt_bundle: str,
    test_prompt: str,
    judge: DeepEvalBaseLLM,
    context_preamble: str | None = None,
) -> str:
    """Execute a skill by giving an LLM the skill bundle as instructions.

    This is Approach B (behavioral evaluation): instead of evaluating the
    prompt text itself, we execute the skill on a test scenario and evaluate
    the LLM's actual output for quality.

    Args:
        skill_prompt_bundle: The concatenated SKILL.md + references content,
            used as the system-level instructions for the LLM.
        test_prompt: The test scenario to execute the skill against.
        judge: DeepEvalBaseLLM instance used as the executor (same model
            used for judging, but here it acts as the skill executor).
        context_preamble: Optional CLAUDE.md content to prepend before skill
            instructions, simulating the real operating context where skills
            run alongside global/project behavioral directives.

    Returns:
        The LLM's response when following the skill instructions.
    """
    parts = ["You are an AI assistant following the instructions below.\n"]

    if context_preamble:
        parts.append(
            "=== BEHAVIORAL CONTEXT (CLAUDE.md) ===\n"
            f"{context_preamble}\n"
            "=== END BEHAVIORAL CONTEXT ===\n\n"
        )

    parts.append(
        "Follow the skill instructions exactly as written.\n\n"
        "=== SKILL INSTRUCTIONS ===\n"
        f"{skill_prompt_bundle}\n"
        "=== END SKILL INSTRUCTIONS ===\n\n"
        f"Task:\n{test_prompt}"
    )

    execution_prompt = "".join(parts)
    return judge.generate(execution_prompt)


def run_eval(
    skill_name: str,
    skill_prompt_bundle: str,
    test_cases: list[dict],
    rubric_names: list[str],
    judge: DeepEvalBaseLLM,
    context_preamble: str | None = None,
    repo_root: Path | None = None,
) -> dict:
    """Run behavioral evaluation: execute skill, then judge the output.

    For each test case:
    1. Execute the skill (LLM generates output following skill instructions)
    2. Evaluate the output with GEval rubrics + ContainsMetric assertions

    This is Approach B — we test what the skill DOES, not what it SAYS.

    Args:
        skill_name: The skill identifier (used in the result dict).
        skill_prompt_bundle: The concatenated SKILL.md + references content.
        test_cases: List of test case dicts from load_eval_config().
        rubric_names: Rubric keys to evaluate with GEval.
        judge: DeepEvalBaseLLM instance for both execution and scoring.
        context_preamble: Optional CLAUDE.md content prepended before skill
            instructions during execution. Does not affect scoring prompts.
        repo_root: Repository root for fixture resolution. Defaults to
            ``git rev-parse --show-toplevel``.

    Returns:
        Dict with keys:
            skill: skill_name
            scores: {metric_name: average_float} across all test cases
            pass_rate: fraction of test cases where ALL metrics passed
            details: list of per-test-case result dicts
    """
    from deepeval.test_case import LLMTestCase

    total_tc = len(test_cases)
    print_lock = threading.Lock()

    def _score_metric(metric: object, llm_tc: LLMTestCase) -> tuple[str, float, bool, bool]:
        """Score a single metric. Returns (name, score, passed, infra_error)."""
        metric_name = getattr(metric, "name", type(metric).__name__)
        infra = False
        try:
            metric.measure(llm_tc)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("metric %s failed: %s", metric_name, exc)
            infra = True
        score = metric.score if metric.score is not None else 0.0  # type: ignore[union-attr]
        passed = metric.is_successful()  # type: ignore[union-attr]
        return metric_name, score, passed, infra

    def _eval_tc(tc_idx: int, tc: dict) -> dict:
        """Evaluate a single test case: execute skill, then score all metrics in parallel."""
        tc_id = tc.get("id", "?")
        prompt = tc.get("prompt", "")
        assertions = tc.get("assertions", [])

        # Fixture and codebase substitution: replace placeholders.
        tc_infra = False
        if "{fixture:" in prompt or "{codebase:" in prompt:
            prompt, has_missing = build_fixture_prompt(skill_name, prompt, repo_root=repo_root)
            if has_missing:
                tc_infra = True

        with print_lock:
            print(f"    tc {tc_id} ({tc_idx}/{total_tc}): executing...", flush=True)

        # Step 1: Execute the skill.
        try:
            response = execute_skill(
                skill_prompt_bundle, prompt, judge, context_preamble=context_preamble
            )
        except Exception as exc:
            with print_lock:
                print(f"    tc {tc_id}: EXEC FAILED: {exc}", flush=True)
            logger.warning("skill execution failed for %s tc %s: %s", skill_name, tc_id, exc)
            return {"id": tc_id, "prompt": prompt, "scores": {}, "passed": False, "infra": True}

        # Step 2: Score all metrics in parallel.
        # Per-case rubrics override config-level rubrics if present.
        effective_rubrics = tc["rubrics"] if tc.get("rubrics") else rubric_names

        geval_metrics = build_metrics(effective_rubrics, judge)
        contains = build_assertion_metrics(assertions)
        all_metrics = geval_metrics + [contains]

        # Wire expected_behaviors as expected_output for competency rubrics.
        expected = tc.get("expected_behaviors", [])
        sanitized = []
        for e in expected:
            for delim in _PROMPT_DELIMITERS:
                e = e.replace(delim, "")
            sanitized.append(e.strip())
        sanitized = [e for e in sanitized if e]
        llm_tc = LLMTestCase(
            input=prompt,
            actual_output=response,
            expected_output="\n".join(sanitized) if sanitized else None,
        )

        with ThreadPoolExecutor(max_workers=len(all_metrics)) as metric_pool:
            metric_results = list(metric_pool.map(lambda m: _score_metric(m, llm_tc), all_metrics))

        tc_scores: dict[str, float] = {}
        tc_pass = True
        # tc_infra may already be True from missing fixtures above.
        for mname, mscore, mpassed, minfra in metric_results:
            tc_scores[mname] = mscore
            if not mpassed:
                tc_pass = False
            if minfra:
                tc_infra = True

        status = "PASS" if tc_pass else "FAIL"
        with print_lock:
            print(f"    tc {tc_id} ({tc_idx}/{total_tc}): {status}", flush=True)

        return {
            "id": tc_id,
            "prompt": prompt,
            "response_preview": response[:200],
            "scores": tc_scores,
            "passed": tc_pass,
            "infra": tc_infra,
        }

    # Run test cases in parallel, capped to avoid API pool exhaustion.
    # Each TC spawns up to len(metrics) × k_samples concurrent API calls
    # for multi-trial scoring, so unbounded TC parallelism overwhelms
    # httpx connection pools and Vertex AI rate limits.
    _MAX_CONCURRENT_TCS = 4
    tc_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(total_tc, _MAX_CONCURRENT_TCS)) as tc_pool:
        futures = [tc_pool.submit(_eval_tc, i, tc) for i, tc in enumerate(test_cases, 1)]
        for future in futures:
            tc_results.append(future.result())

    # Aggregate results from all test cases.
    score_accum: dict[str, list[float]] = {}
    passes: list[bool] = []
    details: list[dict] = []
    infra_error_count = 0

    for result in tc_results:
        passes.append(result["passed"])
        if result.get("infra"):
            infra_error_count += 1
        for mname, mscore in result["scores"].items():
            score_accum.setdefault(mname, []).append(mscore)
        details.append(result)

    avg_scores = {name: sum(vals) / len(vals) for name, vals in score_accum.items()}
    pass_rate = sum(passes) / len(passes) if passes else 0.0
    total_cases = len(test_cases)
    infra_error = total_cases > 0 and infra_error_count >= total_cases / 2
    per_case_scores: dict[str, list[float]] = dict(score_accum)

    return {
        "skill": skill_name,
        "scores": avg_scores,
        "per_case_scores": per_case_scores,
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
    ``threshold`` below its baseline value. The threshold is widened for
    metrics with few data points (N < 3) to account for higher variance:
    ``effective_threshold = threshold * min(1.5, max(1.0, 3.0 / N))``.
    This caps at 1.5x for N=1 (0.225) and converges to 1.0x for N>=3.

    Args:
        results: Output from run_eval() — must have "skill" and "scores" keys.
        baselines: Output from load_baselines() — mapping of skill -> {metric: score}.
        threshold: Maximum allowed score drop (default 0.15).

    Returns:
        Tuple of (passed: bool, report: str). passed is True when no regression
        is detected. report describes any regressions or confirms passage.
    """
    skill = results.get("skill", "unknown")

    if results.get("infra_error"):
        return False, f"{skill}: INFRA ERROR — judge unavailable"

    current_scores: dict[str, float] = results.get("scores", {})
    baseline_scores: dict[str, float] = baselines.get(skill, {})
    per_case_scores: dict[str, list[float]] = results.get("per_case_scores", {})

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
        # N-adjusted threshold: widen for low sample counts.
        n = len(per_case_scores.get(metric, []))
        effective_threshold = threshold * min(1.5, max(1.0, 3.0 / n)) if n > 0 else threshold
        drop = baseline - current
        if drop > effective_threshold:
            regressions.append(
                f"  {metric}: {current:.3f} vs baseline {baseline:.3f} "
                f"(drop={drop:.3f} > threshold={effective_threshold:.3f})"
            )

    if regressions:
        report = f"{skill}: REGRESSION DETECTED\n" + "\n".join(regressions)
        return False, report

    lines = [f"{skill}: PASS"]
    for metric, baseline in baseline_scores.items():
        current = current_scores.get(metric, 0.0)
        lines.append(f"  {metric}: {current:.3f} (baseline={baseline:.3f})")
    return True, "\n".join(lines)
