"""Structural tests for PROVENANCE rule integrity.

Verifies that:
1. finding-classification.md contains the PROVENANCE override rule.
2. test-plan/SKILL.md generates PROVENANCE markers (the only producer).
3. All downstream consumer skills that reference finding-classification.md
   do so via the shared reference file (not duplicating or overriding the rule).

These are grep-based lint tests — no LLM calls, no subprocess execution.
They guard against accidental deletion of the provenance rule and against
consumers that silently bypass it by not loading the canonical reference.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CODE_QUALITY = REPO_ROOT / "code-quality"
FINDING_CLASSIFICATION = CODE_QUALITY / "references" / "finding-classification.md"
TEST_PLAN_SKILL = CODE_QUALITY / "skills" / "test-plan" / "SKILL.md"

# Downstream consumers that reference finding-classification.md and must
# transitively inherit the PROVENANCE rule.
DOWNSTREAM_CONSUMERS = [
    CODE_QUALITY / "skills" / "quality-gate" / "SKILL.md",
    CODE_QUALITY / "skills" / "swarm" / "SKILL.md",
    CODE_QUALITY / "skills" / "pr-review" / "SKILL.md",
    CODE_QUALITY / "skills" / "fix" / "SKILL.md",
    CODE_QUALITY / "skills" / "unfuck" / "SKILL.md",
    CODE_QUALITY / "skills" / "bug-investigation" / "SKILL.md",
]


class TestProvenanceRuleIntegrity:
    """Structural tests that the PROVENANCE classification rule is correctly anchored."""

    def test_finding_classification_contains_provenance_rule(self):
        """finding-classification.md must contain the PROVENANCE override rule."""
        content = FINDING_CLASSIFICATION.read_text()
        assert "PROVENANCE" in content, (
            f"{FINDING_CLASSIFICATION} does not contain 'PROVENANCE'. "
            "The provenance override rule was deleted or renamed."
        )
        assert "needs-input" in content, (
            f"{FINDING_CLASSIFICATION} does not contain 'needs-input'. "
            "The classification taxonomy appears to have been removed."
        )

    def test_provenance_rule_appears_in_definition_and_example(self):
        """The PROVENANCE keyword must appear in at least the rule and one example."""
        content = FINDING_CLASSIFICATION.read_text()
        lines = content.splitlines()
        provenance_line_indices = [i for i, ln in enumerate(lines) if "PROVENANCE" in ln]
        assert len(provenance_line_indices) >= 2, (
            "Expected PROVENANCE to appear in at least the rule definition and one example. "
            f"Found only {len(provenance_line_indices)} occurrence(s)."
        )

    def test_test_plan_skill_generates_provenance_markers(self):
        """test-plan/SKILL.md must include PROVENANCE marker generation."""
        content = TEST_PLAN_SKILL.read_text()
        assert "PROVENANCE" in content, (
            f"{TEST_PLAN_SKILL} does not contain 'PROVENANCE'. "
            "The test-plan skill must generate <!-- PROVENANCE: ... --> markers "
            "so that downstream consumers can apply the provenance override rule."
        )

    def test_downstream_consumers_reference_finding_classification(self):
        """Each downstream consumer skill must reference finding-classification.md."""
        missing = []
        for skill_path in DOWNSTREAM_CONSUMERS:
            assert skill_path.exists(), f"Expected skill file not found: {skill_path}"
            content = skill_path.read_text()
            if "finding-classification" not in content:
                missing.append(str(skill_path.relative_to(REPO_ROOT)))

        assert not missing, (
            "The following downstream consumer skills do not reference "
            "finding-classification.md and may bypass the PROVENANCE rule:\n"
            + "\n".join(f"  - {p}" for p in missing)
        )
