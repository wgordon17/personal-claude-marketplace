"""Deterministic contains/not-contains assertion metric for skill evaluation.

ContainsMetric performs case-insensitive substring checks on the actual output
of a test case. Unlike GEval rubrics (which are LLM-judged), this metric is
fully deterministic: it passes only if all expected substrings are present and
all forbidden substrings are absent.

Typical use: verifying that a skill bundle contains specific keywords or
instructions without relying on LLM judgment for factual content checks.
"""

from deepeval.metrics.base_metric import BaseMetric
from deepeval.test_case import LLMTestCase


class ContainsMetric(BaseMetric):
    """Deterministic substring assertion metric.

    Scores 1.0 only when every expected substring is found in the actual output
    AND no forbidden substring is present. Partial credit is awarded proportionally
    to the fraction of checks that pass.
    """

    def __init__(
        self,
        expected: list[str],
        forbidden: list[str],
        threshold: float = 1.0,
    ) -> None:
        self.expected = expected
        self.forbidden = forbidden
        self.threshold = threshold
        self.async_mode = False
        self.score: float | None = None
        self.success: bool | None = None
        self.reason: str | None = None
        self.error: str | None = None

    @property
    def name(self) -> str:
        return "Contains Assertion Metric"

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Contains Assertion Metric"

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        """Run substring checks against test_case.actual_output.

        Score = checks_passed / total_checks. With no checks defined, score is 1.0.
        """
        actual = (test_case.actual_output or "").lower()

        passed: list[str] = []
        failed: list[str] = []

        for substring in self.expected:
            if substring.lower() in actual:
                passed.append(f"found expected: {substring!r}")
            else:
                failed.append(f"missing expected: {substring!r}")

        for substring in self.forbidden:
            if substring.lower() not in actual:
                passed.append(f"absent forbidden: {substring!r}")
            else:
                failed.append(f"found forbidden: {substring!r}")

        total = len(passed) + len(failed)
        self.score = 1.0 if total == 0 else len(passed) / total
        self.success = self.score >= self.threshold

        if failed:
            self.reason = "Failed checks: " + "; ".join(failed)
        else:
            self.reason = "All checks passed"

        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        """Async counterpart — delegates to synchronous measure()."""
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self) -> bool:
        if self.error:
            return False
        return bool(self.score is not None and self.score >= self.threshold)
