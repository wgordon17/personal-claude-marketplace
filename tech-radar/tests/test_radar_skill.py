"""Tests for SKILL.md and references/*.md contracts, examples, and bundle resolution."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from tech_radar import bootstrap, brief, cli, verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFS_DIR = SKILL_DIR / "references"

if str(REPO_ROOT / "skill-eval") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "skill-eval"))

from skill_eval.runner import resolve_skill_bundle  # noqa: E402

EXPECTED_REF_FILES: tuple[str, ...] = (
    "run-procedure.md",
    "bootstrap-interview.md",
    "decisions-and-feedback.md",
    "brief-and-verdict-schema.md",
    "evaluation-rubric.md",
    "digest-format.md",
)

EXPECTED_BUNDLED_FILES: tuple[str, ...] = (
    "scripts/radar.py",
    "scripts/radar.py.lock",
    "scripts/reddit_pull.py",
    "scripts/reddit_pull.py.lock",
    "scripts/verify_app.py",
    "scripts/tech_radar/schema.sql",
    "scripts/tech_radar/cache_schema.sql",
    "scripts/tech_radar/templates/hourly.plist.tmpl",
    "scripts/tech_radar/templates/radar.html.tmpl",
    "scripts/tech_radar/templates/run-project-settings.json",
)

REPORTED_ONLY_CRITERIA_IDS: tuple[str, ...] = (
    "GATE-01",
    "GATE-02",
    "GATE-11",
    "GATE-12",
    "GATE-13",
    "GATE-15",
    "GATE-16",
    "SCORE-01",
    "SCORE-04",
    "SCORE-05",
    "SCORE-06",
    "SCORE-07",
    "SCORE-08",
    "SCORE-09",
    "SCORE-10",
    "SCORE-13",
    "SCORE-15",
    "REDF-01",
    "REDF-04",
    "EVID-01",
    "EVID-02",
    "EVID-04",
    "EVID-05",
)

ENFORCED_CRITERIA_IDS: tuple[str, ...] = (
    "GATE-03",
    "GATE-04",
    "GATE-05",
    "GATE-06",
    "GATE-07",
    "GATE-08",
    "GATE-09",
    "GATE-10",
    "GATE-14",
    "REDF-02",
    "REDF-03",
    "REDF-05",
    "REDF-06",
    "REDF-07",
    "REDF-08",
    "REDF-09",
    "REDF-10",
    "T10",
)

EXPECTED_ALLOWED_TOOLS: tuple[str, ...] = (
    "Bash(*radar.py *)",
    "Read",
    "Write",
    "WebSearch",
    "WebFetch",
    "ToolSearch",
    "mcp__plugin_fetchaller-mcp_fetchaller__browse_reddit",
    "mcp__plugin_fetchaller-mcp_fetchaller__search_reddit",
    "mcp__t3-code__html_render",
    "mcp__t3-code__html_preview",
    "mcp__t3-code__t3_project_create",
    "mcp__t3-code__t3_project_update",
    "mcp__t3-code__t3_thread_launch",
    "mcp__t3-code__t3_thread_wait",
    "mcp__t3-code__t3_thread_read",
    "mcp__t3-code__schedule_task",
    "mcp__t3-code__run_scheduled_task_now",
    "AskUserQuestion",
)


def _extract_frontmatter_field(text: str, key: str) -> str:
    lines = text.splitlines()
    assert lines[0].strip() == "---"
    end_idx = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    for line in lines[1:end_idx]:
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"Frontmatter key {key!r} not found")


def _extract_json_blocks(md_text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for match in re.finditer(r"```json\s*\n(.*?)\n```", md_text, flags=re.DOTALL):
        blocks.append(json.loads(match.group(1)))
    return blocks


def _top_level_subcommands() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    raise AssertionError("No subcommands found on parser")


def test_skill_frontmatter_size_radar_cmd_and_bundle_resolution() -> None:
    """SKILL.md allowed-tools matches Spec 08 set, size <= 10,000 chars, references RADAR_CMD,
    and resolve_skill_bundle includes all 6 reference files without pulling external plugin refs.
    """
    assert SKILL_MD.is_file()
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    assert len(skill_text) <= 10_000, f"SKILL.md too long: {len(skill_text)} chars"
    assert "RADAR_CMD" in skill_text

    raw_allowed = _extract_frontmatter_field(skill_text, "allowed-tools")
    parsed_allowed = tuple(part.strip() for part in raw_allowed.split(",") if part.strip())
    assert parsed_allowed == EXPECTED_ALLOWED_TOOLS
    assert "Edit" not in parsed_allowed

    bundle = resolve_skill_bundle(SKILL_DIR, repo_root=REPO_ROOT)
    assert bundle is not None
    for ref_name in EXPECTED_REF_FILES:
        assert (REFS_DIR / ref_name).is_file(), f"Missing {ref_name}"
        assert f"## {ref_name}" in bundle, f"{ref_name} missing from resolved skill bundle"
        assert f"](references/{ref_name})" in skill_text, (
            f"SKILL.md must directly link references/{ref_name}"
        )
    assert "## dependency-evaluation.md" not in bundle

    # All 8 hard rules (a)–(h) and explicit bundled file links without globs
    for rule_letter in ("a", "b", "c", "d", "e", "f", "g", "h"):
        assert f"**({rule_letter})" in skill_text, f"Missing hard rule ({rule_letter}) in SKILL.md"

    for bundled_rel in EXPECTED_BUNDLED_FILES:
        assert f"]({bundled_rel})" in skill_text, f"Missing bundled file link {bundled_rel}"
        assert (SKILL_DIR / bundled_rel).is_file(), f"Bundled file missing on disk: {bundled_rel}"

    md_link_targets = re.findall(r"\]\(([^)]+)\)", skill_text)
    for target in md_link_targets:
        assert "*" not in target, f"Glob link target forbidden in SKILL.md: {target}"


def test_references_security_rule_schema_examples_and_forbidden_commands() -> None:
    """Every reference states the untrusted-data rule; schema examples pass validators;
    forbidden install strings and terminal-only $R invocations are absent; every $R subcommand
    exists in cli.build_parser().
    """
    for ref_name in EXPECTED_REF_FILES:
        ref_text = (REFS_DIR / ref_name).read_text(encoding="utf-8")
        low = ref_text.lower()
        assert "data, never instructions" in low, f"Missing data rule in {ref_name}"
        assert "untrusted_excerpt" in ref_text, f"Missing untrusted_excerpt note in {ref_name}"
        # Cross-references inside references/ must use ./<sibling>.md, never references/<sibling>.md
        assert "](references/" not in ref_text, (
            f"{ref_name} must use ./<sibling>.md links, not references/<sibling>.md"
        )
        for sib_match in re.finditer(r"\]\((\./[^)]+\.md)\)", ref_text):
            sib_target = sib_match.group(1)
            assert (REFS_DIR / sib_target).is_file(), (
                f"Broken sibling link {sib_target} in {ref_name}"
            )

    # Check schema examples in brief-and-verdict-schema.md pass validators
    schema_text = (REFS_DIR / "brief-and-verdict-schema.md").read_text(encoding="utf-8")
    json_blocks = _extract_json_blocks(schema_text)
    assert len(json_blocks) == 6

    index_examples = [b for b in json_blocks if "sections" in b and "suppressed" in b]
    context_examples = [b for b in json_blocks if "profile" in b and "in_use" in b]
    brief_item_examples = [b for b in json_blocks if "gates" in b and "evidence" in b]
    shortlist_examples = [b for b in json_blocks if "item_ids" in b and "proposals" in b]
    verdict_examples = [b for b in json_blocks if "run_id" in b and "items" in b]
    answers_examples = [b for b in json_blocks if "initial_rings" in b and "taste_makers" in b]

    assert len(index_examples) == 1
    assert len(context_examples) == 1
    assert len(brief_item_examples) == 1
    assert len(shortlist_examples) == 1
    assert len(verdict_examples) == 1
    assert len(answers_examples) == 1

    idx_ex = index_examples[0]
    assert isinstance(idx_ex["suppressed"]["deferred"], int | float)
    assert isinstance(idx_ex["usage_last_run"], dict | None)

    ctx_ex = context_examples[0]
    assert isinstance(ctx_ex["in_use"]["private_counts"], dict)
    assert any(p["alias"] == "private-project-1" for p in ctx_ex["projects"])

    item_ex = brief_item_examples[0]
    assert item_ex["visibility"] == "public"
    assert isinstance(item_ex["momentum"], int | float)
    assert isinstance(item_ex["cap"], str | None)

    assert brief.validate_shortlist(shortlist_examples[0]) == []
    assert verdict.validate(verdict_examples[0]) == []
    assert bootstrap.validate_answers(answers_examples[0]) == []

    # Check evaluation-rubric.md says "pipe-to-shell installer", notes provenance in plain text,
    # and inlines all enforced and reported-only criteria IDs
    rubric_text = (REFS_DIR / "evaluation-rubric.md").read_text(encoding="utf-8")
    assert "pipe-to-shell installer" in rubric_text
    assert "code-quality/references/dependency-evaluation.md" in rubric_text
    assert "`code-quality/references/dependency-evaluation.md`" not in rubric_text
    assert "](code-quality/references/dependency-evaluation.md)" not in rubric_text
    for crit_id in (*REPORTED_ONLY_CRITERIA_IDS, *ENFORCED_CRITERIA_IDS):
        assert crit_id in rubric_text, f"Missing criterion {crit_id} in evaluation-rubric.md"

    # Check bootstrap-interview.md places schedule creation (Step 7) before lockdown (Step 8, A43)
    boot_text = (REFS_DIR / "bootstrap-interview.md").read_text(encoding="utf-8")
    assert boot_text.index("schedule_task") < boot_text.index("bootstrap lockdown")
    assert "profile private-scopes add" in boot_text

    # Forbidden install strings absent from SKILL.md and the 4 specified reference files
    files_for_install_check = (
        SKILL_MD,
        REFS_DIR / "run-procedure.md",
        REFS_DIR / "decisions-and-feedback.md",
        REFS_DIR / "evaluation-rubric.md",
        REFS_DIR / "digest-format.md",
    )
    forbidden_install_strings = ("curl", "brew install", "npm i", "git clone")
    for fpath in files_for_install_check:
        content = fpath.read_text(encoding="utf-8")
        for forbidden in forbidden_install_strings:
            assert forbidden not in content, f"Forbidden string {forbidden!r} found in {fpath.name}"

    # Terminal-only commands are never invoked via $R; every $R <subcommand> is in argparse
    all_skill_docs = (SKILL_MD, *[REFS_DIR / name for name in EXPECTED_REF_FILES])
    forbidden_agent_r_prefixes = (
        "$R decide ",
        "$R taste apply",
        "$R alert ",
        "$R launchd",
        "$R bootstrap",
        "$R decisions apply",
        "$R pending list",
        "$R profile private-scopes",
        "$R app sync",
    )
    valid_subcmds = _top_level_subcommands()
    r_cmd_re = re.compile(r"\$R\s+([a-z][a-z0-9-]*)")

    for doc_path in all_skill_docs:
        doc_text = doc_path.read_text(encoding="utf-8")
        for bad_prefix in forbidden_agent_r_prefixes:
            assert bad_prefix not in doc_text, (
                f"Terminal-only command {bad_prefix!r} instructed via $R in {doc_path.name}"
            )
        for match in r_cmd_re.finditer(doc_text):
            subcmd = match.group(1)
            assert subcmd in valid_subcmds, f"Unknown $R subcommand {subcmd!r} in {doc_path.name}"
