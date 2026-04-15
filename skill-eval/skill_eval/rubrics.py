"""Rubric definitions for skill evaluation.

Each rubric targets prompt-bundle quality — checking whether the skill's prompt
text contains the right instructions, not whether the model follows them at runtime.

RUBRIC_REGISTRY maps rubric name strings to rubric dicts with keys:
  - name: str — matches the registry key
  - evaluation_steps: list[str] — passed to GEval(evaluation_steps=...)
  - evaluation_params: list[LLMTestCaseParams] — passed to GEval(evaluation_params=...)
"""

from deepeval.test_case import LLMTestCaseParams

ANTI_DEFERRAL_RUBRIC = {
    "name": "anti_deferral",
    "evaluation_steps": [
        "Check whether the prompt template contains explicit anti-deferral instructions",
        "Verify the prompt prohibits version-boundary language (v1/v2, future iteration)",
        "Check for instructions that prevent fabricated user-deferral claims",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

FABRICATION_AVOIDANCE_RUBRIC = {
    "name": "fabrication_avoidance",
    "evaluation_steps": [
        "Check whether the prompt requires citing concrete evidence for findings",
        "Verify the prompt instructs against hallucinating or fabricating issues",
        "Check for instructions that distinguish facts from speculation",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

PHASE_COMPLETION_RUBRIC = {
    "name": "phase_completion",
    "evaluation_steps": [
        "Identify all phases or numbered steps defined in the prompt",
        "Check whether the prompt requires completing each phase before proceeding",
        "Verify no phase can be silently skipped",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

INSTRUCTION_ADHERENCE_RUBRIC = {
    "name": "instruction_adherence",
    "evaluation_steps": [
        "Check whether the prompt contains clear, specific instructions for output format",
        "Verify the prompt specifies what to include and what to exclude",
        "Check for success criteria or verification steps",
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
}

RUBRIC_REGISTRY: dict[str, dict] = {
    "anti_deferral": ANTI_DEFERRAL_RUBRIC,
    "fabrication_avoidance": FABRICATION_AVOIDANCE_RUBRIC,
    "phase_completion": PHASE_COMPLETION_RUBRIC,
    "instruction_adherence": INSTRUCTION_ADHERENCE_RUBRIC,
}
