"""Tests for generate-atlas.py CLI and inventory generation.

Uses subprocess for CLI-level tests (--check mode, default mode).
generate-atlas.py has a hyphen in the name, so direct import is not used.

All tests use --atlas-path={tmp_path}/ATLAS.md and --repo-root=. to avoid
writing to the live repo ATLAS.md or requiring the live ATLAS.md to exist.
"""

import subprocess
import sys
from pathlib import Path

# Locate scripts — parents[2] from dev-guard/tests/ resolves to repo root
COMMANDS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "commands"
GENERATE_ATLAS = COMMANDS_DIR / "generate-atlas.py"
ATLAS_HEALTH_LLM = COMMANDS_DIR / "atlas-health-llm.py"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Canary: verify path resolution is correct before any tests run
assert (COMMANDS_DIR / "_atlas_lib.py").exists(), (
    f"_atlas_lib.py not found at {COMMANDS_DIR}. "
    "Check that COMMANDS_DIR = parents[2] / '.claude' / 'commands' resolves correctly."
)
assert GENERATE_ATLAS.exists(), f"generate-atlas.py not found at {GENERATE_ATLAS}"


def _run_generate(
    *extra_args: str,
    atlas_path: Path | None = None,
    repo_root: Path | None = None,
    date: str | None = None,
) -> subprocess.CompletedProcess:
    """Run generate-atlas.py with uv run and return the completed process."""
    cmd = [sys.executable, "-m", "uv", "run", str(GENERATE_ATLAS)]
    # Use uv run directly for PEP 723 script
    cmd = ["uv", "run", str(GENERATE_ATLAS)]
    if atlas_path is not None:
        cmd.extend(["--atlas-path", str(atlas_path)])
    if repo_root is not None:
        cmd.extend(["--repo-root", str(repo_root)])
    if date is not None:
        cmd.extend(["--date", date])
    cmd.extend(extra_args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestFullGeneration:
    """Tests for default generation mode."""

    def test_full_generation_produces_valid_markdown(self, tmp_path):
        """Run generation — output has expected section headers."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        assert atlas.exists(), "ATLAS.md was not created"
        content = atlas.read_text()
        assert "## Plugin Inventory" in content
        assert "## Cross-References" in content
        assert "## Health Report" in content

    def test_workflow_md_included_at_top(self, tmp_path):
        """docs/WORKFLOW.md content appears verbatim near the top of ATLAS.md."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        workflow_content = (REPO_ROOT / "docs" / "WORKFLOW.md").read_text()
        # The first non-empty, non-heading line of WORKFLOW.md should appear in the output
        for line in workflow_content.splitlines():
            if line.strip() and not line.startswith("#"):
                assert line in content, f"WORKFLOW.md line not found in ATLAS.md: {line!r}"
                break

    def test_plugin_count_matches_marketplace(self, tmp_path):
        """Count ### plugin_name headers in output vs marketplace.json plugin count."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        # Count level-3 headers in the Plugin Inventory section
        plugin_headers = [line for line in content.splitlines() if line.startswith("### ")]
        # Should have exactly 10 plugin headers (one per plugin)
        # Note: Cross-References section uses ### for sub-sections too, so filter by (v
        inventory_headers = [h for h in plugin_headers if "(v" in h]
        assert len(inventory_headers) == 10, (
            f"Expected 10 plugin inventory headers, got {len(inventory_headers)}: "
            f"{inventory_headers}"
        )

    def test_skill_count_matches_filesystem(self, tmp_path):
        """Skill count in output matches actual SKILL.md files in code-quality."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        # Find the "#### Skills (N)" header in code-quality section
        import re

        skill_section = re.search(r"#### Skills \((\d+)\)", content)
        assert skill_section is not None, "Skills section not found in ATLAS.md"
        declared = int(skill_section.group(1))
        actual = len(list((REPO_ROOT / "code-quality").glob("skills/*/SKILL.md")))
        assert declared == actual, (
            f"Skills declared in ATLAS.md ({declared}) != actual SKILL.md count ({actual})"
        )

    def test_agent_table_has_all_agents(self, tmp_path):
        """All 9 agent names appear in the ATLAS.md output."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        expected_agents = [
            "architect",
            "code-reviewer",
            "code-simplifier",
            "performance",
            "plan-adherence",
            "qa",
            "security",
            "test-runner",
            "jira-agent",
        ]
        for agent_name in expected_agents:
            assert f"| {agent_name} |" in content, (
                f"Agent '{agent_name}' not found in ATLAS.md agent table"
            )

    def test_abbr_tags_in_tool_lists(self, tmp_path):
        """<abbr title="..."> wraps truncated tool lists."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        # Skills with many tools (e.g., swarm) should have abbr tags
        assert '<abbr title="' in content, "No <abbr> tags found in ATLAS.md"
        assert "title=" in content

    def test_cross_reference_section_exists(self, tmp_path):
        """Cross-reference tables are populated."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        assert "### Agent Spawn Graph" in content
        # The spawn graph table must have at least a header row
        assert "| Agent | Spawned By |" in content

    def test_health_report_section_exists(self, tmp_path):
        """Health report section is present."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        assert "## Health Report" in content
        assert "### Structural Findings" in content

    def test_empty_plugin_renders_header_only(self, tmp_path):
        """LSP plugin with no components renders only the header line."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        # pyright-uvx is an LSP plugin — it should appear as a header with no sub-sections
        assert "### pyright-uvx" in content
        # There should be no "#### Skills" immediately following the LSP plugin header
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("### pyright-uvx"):
                # Look ahead: next non-empty line should not be a sub-section
                for j in range(i + 1, min(i + 10, len(lines))):
                    if lines[j].strip():
                        assert not lines[j].startswith("####"), (
                            f"LSP plugin pyright-uvx unexpectedly has a sub-section: {lines[j]!r}"
                        )
                        break
                break

    def test_generation_summary_printed(self, tmp_path):
        """Default mode prints summary with plugin/skill/agent counts."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        assert "plugins" in result.stdout
        assert "skills" in result.stdout
        assert "agents" in result.stdout


class TestCheckMode:
    """Tests for --check mode staleness detection."""

    def test_check_mode_exits_0_when_current(self, tmp_path):
        """Generate to tmp_path, then --check with same flags → exit 0."""
        atlas = tmp_path / "ATLAS.md"
        # Generate
        gen = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert gen.returncode == 0, f"Generation failed:\n{gen.stderr}"
        # Check — must exit 0
        check = _run_generate("--check", atlas_path=atlas, repo_root=REPO_ROOT)
        assert check.returncode == 0, (
            f"--check failed on freshly generated ATLAS.md:\n{check.stderr}"
        )

    def test_check_mode_exits_1_when_stale(self, tmp_path):
        """Generate ATLAS.md, modify one line, --check → exit 1."""
        atlas = tmp_path / "ATLAS.md"
        # Generate fresh ATLAS.md
        gen = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT)
        assert gen.returncode == 0, f"Generation failed:\n{gen.stderr}"
        # Corrupt it
        content = atlas.read_text()
        atlas.write_text(content + "\n<!-- stale marker -->\n")
        # Check must now fail
        check = _run_generate("--check", atlas_path=atlas, repo_root=REPO_ROOT)
        assert check.returncode == 1, (
            f"--check should have exited 1 on stale ATLAS.md, got 0:\n{check.stdout}"
        )

    def test_check_mode_exits_1_when_missing(self, tmp_path):
        """--check on missing ATLAS.md → exit 1 with informative message."""
        atlas = tmp_path / "ATLAS.md"
        assert not atlas.exists(), "Test setup: ATLAS.md should not exist yet"
        check = _run_generate("--check", atlas_path=atlas, repo_root=REPO_ROOT)
        assert check.returncode == 1
        assert "ATLAS.md not found" in check.stderr or "not found" in check.stderr.lower()

    def test_check_mode_ignores_timestamp(self, tmp_path):
        """--check ignores the <!-- Last generated: ... --> line (date changes)."""
        atlas = tmp_path / "ATLAS.md"
        # Generate with a fixed date
        gen = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT, date="2026-01-01")
        assert gen.returncode == 0, f"Generation failed:\n{gen.stderr}"
        # Check with a different date — should still exit 0 (timestamps stripped)
        check = _run_generate("--check", atlas_path=atlas, repo_root=REPO_ROOT, date="2026-01-02")
        assert check.returncode == 0, (
            f"--check should ignore timestamp change, but exited 1:\n{check.stderr}"
        )

    def test_date_flag_overrides_today(self, tmp_path):
        """--date flag is reflected in the generated <!-- Last generated: ... --> comment."""
        atlas = tmp_path / "ATLAS.md"
        result = _run_generate(atlas_path=atlas, repo_root=REPO_ROOT, date="2099-12-31")
        assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
        content = atlas.read_text()
        assert "2099-12-31" in content, "Custom date not reflected in ATLAS.md"
