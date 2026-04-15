"""Rubric definitions for skill evaluation (Approach B — behavioral).

Each rubric evaluates the LLM's behavioral OUTPUT when following a skill.
The judge assesses whether the response demonstrates the quality the skill
is designed to produce.

RUBRIC_REGISTRY maps rubric name strings to rubric dicts with keys:
  - name: str — matches the registry key
  - evaluation_steps: list[str] — passed to GEval(evaluation_steps=...)
  - evaluation_params: list[LLMTestCaseParams]
"""

from deepeval.test_case import LLMTestCaseParams

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
}

FABRICATION_AVOIDANCE_RUBRIC = {
    "name": "fabrication_avoidance",
    "evaluation_steps": [
        ("Check whether every finding or claim in the response is supported by concrete evidence"),
        ("Verify the response does not invent issues that do not exist in the input"),
        ("Check that the response distinguishes between confirmed facts and speculation"),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

PHASE_COMPLETION_RUBRIC = {
    "name": "phase_completion",
    "evaluation_steps": [
        ("Identify all phases, steps, or stages the response claims to perform"),
        (
            "Check whether each phase is actually completed"
            " with substantive output, not just mentioned"
        ),
        ("Verify no phase is silently skipped or given a placeholder response"),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

INSTRUCTION_ADHERENCE_RUBRIC = {
    "name": "instruction_adherence",
    "evaluation_steps": [
        ("Check whether the response follows the output format requested in the task"),
        ("Verify the response addresses all parts of the input task, not just a subset"),
        ("Check that the response includes required elements and excludes prohibited ones"),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

RUBRIC_REGISTRY: dict[str, dict] = {
    "anti_deferral": ANTI_DEFERRAL_RUBRIC,
    "fabrication_avoidance": FABRICATION_AVOIDANCE_RUBRIC,
    "phase_completion": PHASE_COMPLETION_RUBRIC,
    "instruction_adherence": INSTRUCTION_ADHERENCE_RUBRIC,
}
