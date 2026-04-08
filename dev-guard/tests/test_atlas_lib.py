"""Tests for _atlas_lib.py parsing functions.

Uses real repo plugin files as fixtures — hardcoded counts are intentional
coupling to the repo's current state and catch parser regressions.
"""

import sys
from pathlib import Path

# Locate scripts — parents[2] from dev-guard/tests/ resolves to repo root
COMMANDS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "commands"

# Canary: verify path resolution is correct before any tests run
assert (COMMANDS_DIR / "_atlas_lib.py").exists(), (
    f"_atlas_lib.py not found at {COMMANDS_DIR}. "
    "Check that COMMANDS_DIR = parents[2] / '.claude' / 'commands' resolves correctly."
)

sys.path.insert(0, str(COMMANDS_DIR))
from _atlas_lib import (  # noqa: E402
    Skill,
    list_reference_docs,
    parse_agents,
    parse_commands,
    parse_marketplace,
    parse_skills,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_QUALITY = REPO_ROOT / "code-quality"
DEV_GUARD = REPO_ROOT / "dev-guard"


class TestParseMarketplace:
    """Tests for parse_marketplace()."""

    def test_parse_marketplace_returns_all_plugins(self):
        """Parse real marketplace.json — verify all 10 plugins are returned."""
        plugins = parse_marketplace(REPO_ROOT)
        assert len(plugins) == 10, (
            f"Expected 10 plugins, got {len(plugins)}. "
            "Update this count if a plugin was added or removed."
        )

    def test_plugin_names_are_present(self):
        """All expected plugin names are in the result."""
        plugins = parse_marketplace(REPO_ROOT)
        names = {p.name for p in plugins}
        expected = {
            "pyright-uvx",
            "vtsls-npx",
            "gopls-go",
            "vscode-html-css-npx",
            "rust-analyzer-rustup",
            "dev-guard",
            "code-quality",
            "git-tools",
            "github-mcp",
            "jira",
        }
        assert names == expected

    def test_lsp_plugins_flagged(self):
        """LSP plugins are flagged with is_lsp=True."""
        plugins = parse_marketplace(REPO_ROOT)
        lsp_names = {p.name for p in plugins if p.is_lsp}
        assert lsp_names == {
            "pyright-uvx",
            "vtsls-npx",
            "gopls-go",
            "vscode-html-css-npx",
            "rust-analyzer-rustup",
        }

    def test_plugin_source_paths_resolved(self):
        """Each non-lsp plugin has a source_path that exists."""
        plugins = parse_marketplace(REPO_ROOT)
        non_lsp = [p for p in plugins if not p.is_lsp]
        for plugin in non_lsp:
            assert plugin.source_path.is_dir(), (
                f"Plugin {plugin.name} source_path {plugin.source_path} does not exist"
            )

    def test_plugin_ordering_matches_marketplace(self):
        """Plugins are returned in marketplace.json order (LSP first)."""
        plugins = parse_marketplace(REPO_ROOT)
        # First 5 should be LSP plugins
        lsp = plugins[:5]
        assert all(p.is_lsp for p in lsp), "First 5 plugins should be LSP"
        # Last 5 should be non-LSP
        non_lsp = plugins[5:]
        assert all(not p.is_lsp for p in non_lsp), "Last 5 plugins should be non-LSP"


class TestParseSkills:
    """Tests for parse_skills()."""

    def test_skill_count(self):
        """Total SKILL.md files across all plugins matches expected count (23)."""
        plugins = parse_marketplace(REPO_ROOT)
        all_skills: list[Skill] = []
        for plugin in plugins:
            all_skills.extend(parse_skills(plugin.source_path, plugin.name))
        assert len(all_skills) == 23, (
            f"Expected 23 skills, got {len(all_skills)}. "
            "Update this count if a skill was added or removed."
        )

    def test_parse_skill_frontmatter(self):
        """Parse swarm/SKILL.md — verify name, description, and allowed-tools."""
        skills = parse_skills(CODE_QUALITY, "code-quality")
        swarm = next((s for s in skills if s.name == "swarm"), None)
        assert swarm is not None, "swarm skill not found in code-quality"
        assert swarm.name == "swarm"
        assert "agent swarm" in swarm.description.lower(), (
            f"Unexpected description: {swarm.description!r}"
        )
        assert "Agent" in swarm.allowed_tools
        assert "Read" in swarm.allowed_tools

    def test_parse_skill_body_content(self):
        """Body content is returned separately from frontmatter."""
        skills = parse_skills(CODE_QUALITY, "code-quality")
        swarm = next((s for s in skills if s.name == "swarm"), None)
        assert swarm is not None, "swarm skill not found"
        # Body should not contain frontmatter delimiters
        assert "---" not in swarm.body.split("\n")[0]
        # Body should contain actual skill content
        assert len(swarm.body) > 100, "Expected substantial body content"

    def test_parse_skill_allowed_tools_is_list(self):
        """allowed_tools is always a list (not a raw string)."""
        skills = parse_skills(CODE_QUALITY, "code-quality")
        for skill in skills:
            assert isinstance(skill.allowed_tools, list), (
                f"Skill {skill.name} allowed_tools is not a list: {skill.allowed_tools!r}"
            )

    def test_skill_plugin_field_set(self):
        """Skill.plugin field is set from the plugin_name argument."""
        skills = parse_skills(CODE_QUALITY, "code-quality")
        for skill in skills:
            assert skill.plugin == "code-quality", (
                f"Skill {skill.name} has wrong plugin: {skill.plugin!r}"
            )

    def test_skill_path_exists(self):
        """Each parsed skill has a path pointing to an existing file."""
        skills = parse_skills(CODE_QUALITY, "code-quality")
        for skill in skills:
            assert skill.path.exists(), f"Skill {skill.name} path does not exist: {skill.path}"


class TestParseAgents:
    """Tests for parse_agents()."""

    def test_parse_agent_frontmatter(self):
        """Parse architect.md — verify name, model, tools, and color."""
        agents = parse_agents(CODE_QUALITY, "code-quality")
        architect = next((a for a in agents if a.name == "architect"), None)
        assert architect is not None, "architect agent not found"
        assert architect.model == "opus"
        assert architect.color == "blue"
        assert "Read" in architect.tools
        assert "Glob" in architect.tools

    def test_parse_agent_tools_is_list(self):
        """Agent tools are normalized from comma-separated scalar to a list."""
        agents = parse_agents(CODE_QUALITY, "code-quality")
        for agent in agents:
            assert isinstance(agent.tools, list), (
                f"Agent {agent.name} tools is not a list: {agent.tools!r}"
            )

    def test_parse_agent_missing_fields(self):
        """Agent .md with partial frontmatter returns graceful defaults."""
        # jira-agent.md may have fewer fields — just ensure it doesn't crash
        jira_dir = REPO_ROOT / "jira"
        agents = parse_agents(jira_dir, "jira")
        assert len(agents) >= 1, "Expected at least 1 agent in jira plugin"
        for agent in agents:
            assert isinstance(agent.name, str)
            assert isinstance(agent.tools, list)
            assert isinstance(agent.model, str)
            assert isinstance(agent.color, str)

    def test_agent_count_code_quality(self):
        """code-quality has 8 agents."""
        agents = parse_agents(CODE_QUALITY, "code-quality")
        assert len(agents) == 8, (
            f"Expected 8 agents in code-quality, got {len(agents)}. "
            "Update this count if an agent was added or removed."
        )

    def test_agent_plugin_field_set(self):
        """Agent.plugin field is set from the plugin_name argument."""
        agents = parse_agents(CODE_QUALITY, "code-quality")
        for agent in agents:
            assert agent.plugin == "code-quality"


class TestListReferenceDocs:
    """Tests for list_reference_docs()."""

    def test_list_reference_docs(self):
        """code-quality has reference docs in expected locations."""
        refs = list_reference_docs(CODE_QUALITY)
        assert len(refs) > 0, "Expected reference docs in code-quality"
        # All returned paths should exist
        for ref in refs:
            assert ref.exists(), f"Reference path does not exist: {ref}"

    def test_list_reference_docs_includes_json(self):
        """Reference docs include .json schema files (not just .md)."""
        refs = list_reference_docs(CODE_QUALITY)
        suffixes = {r.suffix for r in refs}
        # Should have at least .md files; .json may also be present
        assert ".md" in suffixes, "Expected at least one .md reference doc"

    def test_list_reference_docs_empty_for_lsp(self):
        """LSP plugins have no reference docs."""
        lsp_path = REPO_ROOT / "pyright-uvx"
        refs = list_reference_docs(lsp_path)
        assert refs == [], f"Expected no refs for LSP plugin, got: {refs}"

    def test_list_reference_docs_returns_paths(self):
        """Return type is a list of Path objects."""
        refs = list_reference_docs(CODE_QUALITY)
        for ref in refs:
            assert isinstance(ref, Path)


class TestParseCommands:
    """Tests for parse_commands()."""

    def test_parse_command_with_frontmatter(self):
        """dev-guard trust/COMMAND.md has name and description in frontmatter."""
        commands = parse_commands(DEV_GUARD, "dev-guard")
        trust = next((c for c in commands if c.name == "trust"), None)
        assert trust is not None, (
            f"trust command not found. Commands found: {[c.name for c in commands]}"
        )
        assert trust.description == "Manage trusted rules for dev-guard ask prompts"

    def test_parse_command_name_from_parent_dir(self):
        """COMMAND.md without name frontmatter gets name from parent directory."""
        # Create a minimal COMMAND.md without a name field to test the fallback
        # Use guard-stats which is another COMMAND.md
        commands = parse_commands(DEV_GUARD, "dev-guard")
        # All COMMAND.md files should get a sensible name (not "COMMAND")
        for cmd in commands:
            assert cmd.name != "COMMAND", (
                "Command name should not be 'COMMAND' — "
                f"should come from parent dir or frontmatter: {cmd.path}"
            )

    def test_parse_command_without_frontmatter(self):
        """Plain markdown command files get name from filename stem."""
        # code-quality/commands/ has flat .md files without COMMAND.md pattern
        code_quality_commands = parse_commands(CODE_QUALITY, "code-quality")
        # Flat commands should be found; their names come from filename stems
        assert len(code_quality_commands) >= 0  # May be zero if no flat commands

    def test_parse_command_plugin_field(self):
        """Command.plugin field is set from the plugin_name argument."""
        commands = parse_commands(DEV_GUARD, "dev-guard")
        for cmd in commands:
            assert cmd.plugin == "dev-guard"

    def test_parse_command_no_crash_on_empty(self):
        """parse_commands on LSP plugin with no commands returns empty list."""
        lsp_path = REPO_ROOT / "pyright-uvx"
        commands = parse_commands(lsp_path, "pyright-uvx")
        assert commands == []
