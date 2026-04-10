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
CODE_QUALITY_SKILLS = CODE_QUALITY / "skills"
FINDING_CLASSIFICATION = CODE_QUALITY / "references" / "finding-classification.md"
TEST_PLAN_SKILL = CODE_QUALITY_SKILLS / "test-plan" / "SKILL.md"

# Downstream consumers that reference finding-classification.md and must
# transitively inherit the PROVENANCE rule.
DOWNSTREAM_CONSUMERS = [
    CODE_QUALITY_SKILLS / "quality-gate" / "SKILL.md",
    CODE_QUALITY_SKILLS / "swarm" / "SKILL.md",
    CODE_QUALITY_SKILLS / "pr-review" / "SKILL.md",
    CODE_QUALITY_SKILLS / "fix" / "SKILL.md",
    CODE_QUALITY_SKILLS / "unfuck" / "SKILL.md",
    CODE_QUALITY_SKILLS / "bug-investigation" / "SKILL.md",
]

# The 5 lifecycle counter names as defined in incremental-planning.
LIFECYCLE_COUNTER_NAMES = [
    "review-cycle",
    "fix-cycle",
    "pr-review-cycle",
    "pr-fix-cycle",
    "quality-gate",
]

# Skills that write one or more counters. Each entry maps a skill path to the
# counter name(s) that skill is expected to write.
COUNTER_WRITERS: dict[Path, list[str]] = {
    CODE_QUALITY_SKILLS / "incremental-planning" / "SKILL.md": LIFECYCLE_COUNTER_NAMES,
    CODE_QUALITY_SKILLS / "plan-review" / "SKILL.md": ["review-cycle"],
    CODE_QUALITY_SKILLS / "pr-review" / "SKILL.md": ["pr-review-cycle"],
    CODE_QUALITY_SKILLS / "fix" / "SKILL.md": ["fix-cycle", "pr-fix-cycle"],
    CODE_QUALITY_SKILLS / "quality-gate" / "SKILL.md": ["quality-gate"],
}

# Skills that read / validate the full set of 5 counters.
FULL_SET_READERS: list[Path] = [
    CODE_QUALITY_SKILLS / "quality-gate" / "SKILL.md",
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


class TestLifecycleCounterIntegrity:
    """Structural tests that lifecycle counter names are consistent across skills.

    Each counter name is defined in incremental-planning and re-stated independently
    by every skill that reads or writes it. A typo produces a silent no-op: the Edit
    call targets a line that does not exist and the plan file is left unchanged.
    """

    def test_incremental_planning_defines_iterations_block_header(self):
        """incremental-planning/SKILL.md must contain the **Iterations:** block header."""
        skill = CODE_QUALITY_SKILLS / "incremental-planning" / "SKILL.md"
        assert skill.exists(), f"Expected skill not found: {skill}"
        content = skill.read_text()
        assert "**Iterations:**" in content, (
            f"{skill} does not contain '**Iterations:**'. "
            "The canonical block header was deleted or renamed."
        )

    def test_incremental_planning_initializes_all_counters(self):
        """incremental-planning/SKILL.md must initialize every lifecycle counter."""
        skill = CODE_QUALITY_SKILLS / "incremental-planning" / "SKILL.md"
        assert skill.exists(), f"Expected skill not found: {skill}"
        content = skill.read_text()
        missing = [name for name in LIFECYCLE_COUNTER_NAMES if f"- {name}:" not in content]
        assert not missing, f"{skill} is missing initialization lines for counters:\n" + "\n".join(
            f"  - {name}" for name in missing
        )

    def test_each_writer_skill_references_its_counters(self):
        """Every writer skill must reference the exact counter name(s) it writes."""
        failures: list[str] = []
        for skill_path, counter_names in COUNTER_WRITERS.items():
            assert skill_path.exists(), f"Expected skill not found: {skill_path}"
            content = skill_path.read_text()
            for name in counter_names:
                if f"- {name}:" not in content:
                    failures.append(f"{skill_path.relative_to(REPO_ROOT)}: missing '- {name}:'")
        assert not failures, (
            "Writer skills do not reference their counter name(s) "
            "in the expected '- <name>:' format:\n" + "\n".join(f"  - {p}" for p in failures)
        )

    def test_quality_gate_references_all_five_counters(self):
        """quality-gate/SKILL.md names all 5 counters in its structural check list."""
        for skill_path in FULL_SET_READERS:
            assert skill_path.exists(), f"Expected skill not found: {skill_path}"
            content = skill_path.read_text()
            missing = [name for name in LIFECYCLE_COUNTER_NAMES if name not in content]
            assert not missing, (
                f"{skill_path.relative_to(REPO_ROOT)} does not reference counters:\n"
                + "\n".join(f"  - {name}" for name in missing)
            )

    def test_lifecycle_counter_names_list_matches_canonical_source(self):
        """LIFECYCLE_COUNTER_NAMES must match the counters defined in incremental-planning."""
        import re

        skill = CODE_QUALITY_SKILLS / "incremental-planning" / "SKILL.md"
        content = skill.read_text()
        # Extract counter names from the Iterations block (lines like "- review-cycle: 0")
        # Only match lines inside a code fence that follows **Iterations:**
        in_block = False
        on_disk: list[str] = []
        for line in content.splitlines():
            if "**Iterations:**" in line:
                in_block = True
                continue
            if in_block:
                m = re.match(r"^  - ([a-z-]+): 0$", line)
                if m:
                    on_disk.append(m.group(1))
                elif on_disk:
                    break  # end of counter block
        assert sorted(on_disk) == sorted(LIFECYCLE_COUNTER_NAMES), (
            f"LIFECYCLE_COUNTER_NAMES in test file does not match counters in "
            f"incremental-planning/SKILL.md.\n"
            f"  On disk: {sorted(on_disk)}\n"
            f"  In test: {sorted(LIFECYCLE_COUNTER_NAMES)}"
        )

    def test_counter_names_use_hyphens_not_underscores(self):
        """No skill uses underscore variants of the counter names."""
        underscore_variants = [name.replace("-", "_") for name in LIFECYCLE_COUNTER_NAMES]
        failures: list[str] = []
        all_skills = list(COUNTER_WRITERS.keys()) + FULL_SET_READERS
        for skill_path in dict.fromkeys(all_skills):
            content = skill_path.read_text()
            for variant in underscore_variants:
                if f"- {variant}:" in content:
                    failures.append(
                        f"{skill_path.relative_to(REPO_ROOT)}: found '- {variant}:' "
                        f"— should be '- {variant.replace('_', '-')}:'"
                    )
        assert not failures, (
            "Skills use underscore variants of counter names "
            "(these would silently fail to match plan file lines):\n"
            + "\n".join(f"  - {p}" for p in failures)
        )
