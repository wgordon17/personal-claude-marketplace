"""Rubric definitions for skill evaluation (Approach B — behavioral).

Each rubric evaluates the LLM's behavioral OUTPUT when following a skill.
The judge assesses whether the response demonstrates the quality the skill
is designed to produce.

RUBRIC_REGISTRY maps rubric name strings to rubric dicts with keys:
  - name: str — matches the registry key
  - evaluation_steps: list[str] — passed to GEval(evaluation_steps=...)
  - evaluation_params: list[LLMTestCaseParams]
  - rubric: list[Rubric] — score-range anchoring via GEval's native rubric parameter

Score anchoring: Each rubric includes SCORE_ANCHORING — a list of Rubric objects
that define what each score range (0-10) means. This reduces integer clustering
bias (LLMs gravitate toward middle scores without explicit anchoring). DeepEval's
Rubric class injects these definitions into the evaluation prompt, giving the
judge explicit criteria for each score range.
"""

from deepeval.metrics.g_eval import Rubric
from deepeval.test_case import LLMTestCaseParams

# Shared score anchoring — passed to every GEval via the rubric= parameter.
# Defines what each score range means on the 0-10 scale.
SCORE_ANCHORING: list[Rubric] = [
    Rubric(
        score_range=(0, 2),
        expected_outcome=(
            "Completely fails the criteria (deferral, fabrication, skipped phases,"
            " or ignored instructions dominate the response)"
        ),
    ),
    Rubric(
        score_range=(3, 4),
        expected_outcome=(
            "Partially meets criteria with major gaps (some criteria followed"
            " but critical failures remain)"
        ),
    ),
    Rubric(
        score_range=(5, 6),
        expected_outcome=(
            "Meets basic criteria but lacks thoroughness (no critical failures"
            " but noticeable shortcomings in completeness or precision)"
        ),
    ),
    Rubric(
        score_range=(7, 8),
        expected_outcome=(
            "Meets criteria well with minor gaps (substantially correct with"
            " only minor omissions or imprecisions)"
        ),
    ),
    Rubric(
        score_range=(9, 10),
        expected_outcome=(
            "Fully meets all criteria with no gaps (every evaluation step"
            " is satisfied completely and precisely)"
        ),
    ),
]

ANTI_DEFERRAL_RUBRIC = {
    "name": "anti_deferral",
    "evaluation_steps": [
        (
            "Check whether the response completes all requested work"
            " without deferring to 'future versions'"
        ),
        (
            "Verify the response does not use version-boundary language"
            " (v1/v2, 'future enhancement', 'out of scope') to avoid work"
        ),
        (
            "Check that the response does not fabricate claims"
            " that the user deferred work when they did not"
        ),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": SCORE_ANCHORING,
}

FABRICATION_AVOIDANCE_RUBRIC = {
    "name": "fabrication_avoidance",
    "evaluation_steps": [
        "Check whether every finding or claim in the response is supported by concrete evidence",
        "Verify the response does not invent issues that do not exist in the input",
        "Check that the response distinguishes between confirmed facts and speculation",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": SCORE_ANCHORING,
}

PHASE_COMPLETION_RUBRIC = {
    "name": "phase_completion",
    "evaluation_steps": [
        "Identify all phases, steps, or stages the response claims to perform",
        (
            "Check whether each phase is actually completed"
            " with substantive output, not just mentioned"
        ),
        "Verify no phase is silently skipped or given a placeholder response",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": SCORE_ANCHORING,
}

INSTRUCTION_ADHERENCE_RUBRIC = {
    "name": "instruction_adherence",
    "evaluation_steps": [
        "Check whether the response follows the output format requested in the task",
        "Verify the response addresses all parts of the input task, not just a subset",
        "Check that the response includes required elements and excludes prohibited ones",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": SCORE_ANCHORING,
}

RUBRIC_REGISTRY: dict[str, dict] = {
    "anti_deferral": ANTI_DEFERRAL_RUBRIC,
    "fabrication_avoidance": FABRICATION_AVOIDANCE_RUBRIC,
    "phase_completion": PHASE_COMPLETION_RUBRIC,
    "instruction_adherence": INSTRUCTION_ADHERENCE_RUBRIC,
}
