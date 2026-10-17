"""Structural tests for plugin integrity.

Verifies that:
1. jira/skills/jira/SKILL.md contains the URL presentation directive.
2. jira/agents/jira-agent.md contains the URL presentation directive.
3. Both files stay in sync — drift is detected immediately.
4. Version numbers in each plugin's plugin.json match the entry in marketplace.json.
5. Every marketplace.json entry has a corresponding plugin.json on disk.
6. Shared reference files (github-label-definitions.md, tracker-field-spec.md) exist
   on disk and are referenced by their consumer SKILL.md files.
7. Jira self-assignment rules: account ID capture via atlassianUserInfo,
   assignee_account_id param, never-unassigned rule, halt-on-empty guard,
   and post-create verification are present in both files.
8. MGMT→OSAC migration regression: neither SKILL.md nor jira-agent.md may
   reference 'project = MGMT' or 'component = OSAC'.

These are grep-based and JSON-parse lint tests — no LLM calls, no subprocess execution.
They guard against accidental deletion of rules and references, and against
version drift between plugin manifests.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
JIRA_SKILL = REPO_ROOT / "jira" / "skills" / "jira" / "SKILL.md"
JIRA_AGENT = REPO_ROOT / "jira" / "agents" / "jira-agent.md"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
DEV_GUARD_HOOKS_JSON = REPO_ROOT / "dev-guard" / "hooks" / "hooks.json"
DEV_GUARD_PLUGIN_JSON = REPO_ROOT / "dev-guard" / ".claude-plugin" / "plugin.json"

URL_DIRECTIVE = "redhat.atlassian.net/browse"
OLD_PHRASE = "After every create or update operation"


class TestJiraPluginIntegrity:
    """Jira plugin rules: URL directive, self-assignment, data safety, MGMT regression."""

    def test_skill_contains_url_directive(self):
        """jira/skills/jira/SKILL.md must contain the URL presentation directive."""
        content = JIRA_SKILL.read_text()
        assert URL_DIRECTIVE in content, (
            f"{JIRA_SKILL} does not contain '{URL_DIRECTIVE}'. "
            "The URL presentation rule was deleted or altered."
        )

    def test_agent_contains_url_directive(self):
        """jira/agents/jira-agent.md must contain the URL presentation directive."""
        content = JIRA_AGENT.read_text()
        assert URL_DIRECTIVE in content, (
            f"{JIRA_AGENT} does not contain '{URL_DIRECTIVE}'. "
            "The URL presentation rule was deleted or altered."
        )

    def test_skill_does_not_contain_old_phrase(self):
        """jira/skills/jira/SKILL.md must not use the old pre-URL-directive phrasing."""
        content = JIRA_SKILL.read_text()
        assert OLD_PHRASE not in content, (
            f"{JIRA_SKILL} still contains the old phrase '{OLD_PHRASE}'. "
            "The URL presentation rule may have been reverted."
        )

    def test_agent_does_not_contain_old_phrase(self):
        """jira/agents/jira-agent.md must not use the old pre-URL-directive phrasing."""
        content = JIRA_AGENT.read_text()
        assert OLD_PHRASE not in content, (
            f"{JIRA_AGENT} still contains the old phrase '{OLD_PHRASE}'. "
            "The URL presentation rule may have been reverted."
        )

    def test_agent_contains_verbatim_passthrough_directive(self):
        """jira/agents/jira-agent.md must contain the verbatim-passthrough conditional block.

        Cross-file contract: incremental-planning spawns jira-agent with
        pre-formatted issue fields; jira-agent must honour them verbatim.
        """
        content = JIRA_AGENT.read_text()
        assert "use them verbatim" in content, (
            f"{JIRA_AGENT} does not contain 'use them verbatim'. "
            "The verbatim-passthrough directive (Create Issue section) was deleted or altered."
        )

    def test_agent_treats_spawn_data_as_data(self):
        """jira/agents/jira-agent.md must treat spawn-data content as data, not instructions."""
        content = JIRA_AGENT.read_text()
        assert "Do not follow" in content, (
            f"{JIRA_AGENT} does not contain 'Do not follow'. "
            "The spawn-data anti-injection treatment was deleted or altered."
        )

    def test_agent_contains_spawn_data_escape_table(self):
        """jira-agent.md must document the spawn-data escape table for consumer-side parsing."""
        content = JIRA_AGENT.read_text()
        assert "&lt;/spawn-data&gt;" in content, (
            f"{JIRA_AGENT} does not contain the spawn-data escape table. "
            "The consumer-side anti-injection escape mechanism was deleted."
        )

    def test_skill_contains_account_id_capture(self):
        """jira/skills/jira/SKILL.md must capture account ID via atlassianUserInfo."""
        content = JIRA_SKILL.read_text()
        assert "atlassianUserInfo" in content, (
            f"{JIRA_SKILL} does not contain 'atlassianUserInfo'. "
            "The bootstrap self-assignment capture step was deleted or altered."
        )

    def test_skill_contains_self_assignment_param(self):
        """jira/skills/jira/SKILL.md must use assignee_account_id in issue create."""
        content = JIRA_SKILL.read_text()
        assert "assignee_account_id" in content, (
            f"{JIRA_SKILL} does not contain 'assignee_account_id'. "
            "The self-assignment parameter was removed from issue create guidance."
        )

    def test_skill_contains_never_create_unassigned(self):
        """jira/skills/jira/SKILL.md must contain the 'Never create unassigned cards' rule."""
        content = JIRA_SKILL.read_text()
        assert "Never create unassigned cards" in content, (
            f"{JIRA_SKILL} does not contain 'Never create unassigned cards'. "
            "The unassigned-card prohibition was deleted or altered."
        )

    def test_skill_contains_halt_on_empty_account_id(self):
        """jira/skills/jira/SKILL.md must halt if account ID is empty."""
        content = JIRA_SKILL.read_text()
        assert "account ID is empty after capture, halt and report the error" in content, (
            f"{JIRA_SKILL} does not contain the halt-on-empty account ID instruction. "
            "The guard against missing assignee was deleted or altered."
        )

    def test_agent_contains_account_id_capture(self):
        """jira/agents/jira-agent.md must capture account ID via atlassianUserInfo."""
        content = JIRA_AGENT.read_text()
        assert "atlassianUserInfo" in content, (
            f"{JIRA_AGENT} does not contain 'atlassianUserInfo'. "
            "The bootstrap self-assignment capture step was deleted or altered."
        )

    def test_agent_contains_self_assignment_param(self):
        """jira/agents/jira-agent.md must use assignee_account_id in issue create."""
        content = JIRA_AGENT.read_text()
        assert "assignee_account_id" in content, (
            f"{JIRA_AGENT} does not contain 'assignee_account_id'. "
            "The self-assignment parameter was removed from issue create guidance."
        )

    def test_agent_contains_never_create_unassigned(self):
        """jira/agents/jira-agent.md must contain the 'Never create unassigned cards' rule."""
        content = JIRA_AGENT.read_text()
        assert "Never create unassigned cards" in content, (
            f"{JIRA_AGENT} does not contain 'Never create unassigned cards'. "
            "The unassigned-card prohibition was deleted or altered."
        )

    def test_agent_contains_halt_on_empty_account_id(self):
        """jira/agents/jira-agent.md must halt if account ID is empty."""
        content = JIRA_AGENT.read_text()
        assert "account ID is empty after capture, halt and report the error" in content, (
            f"{JIRA_AGENT} does not contain the halt-on-empty account ID instruction. "
            "The guard against missing assignee was deleted or altered."
        )

    def test_skill_contains_post_create_verification(self):
        """jira/skills/jira/SKILL.md must contain the post-create assignee verification step."""
        content = JIRA_SKILL.read_text()
        assert "Post-create assignee verification" in content, (
            f"{JIRA_SKILL} does not contain 'Post-create assignee verification'. "
            "The post-create assignee verification step was deleted or altered."
        )

    def test_agent_contains_post_create_verification(self):
        """jira/agents/jira-agent.md must contain the post-create assignee verification step."""
        content = JIRA_AGENT.read_text()
        assert "Post-create assignee verification" in content, (
            f"{JIRA_AGENT} does not contain 'Post-create assignee verification'. "
            "The post-create assignee verification step was deleted or altered."
        )

    def test_skill_does_not_contain_mgmt_project_key(self):
        """jira/skills/jira/SKILL.md must not reference the old MGMT project key in JQL."""
        content = JIRA_SKILL.read_text()
        assert "project = MGMT" not in content, (
            f"{JIRA_SKILL} still contains 'project = MGMT'. "
            "The MGMT→OSAC migration was partially reverted."
        )

    def test_skill_does_not_contain_old_compound_jql(self):
        """jira/skills/jira/SKILL.md must not use MGMT-era 'component = OSAC' JQL."""
        content = JIRA_SKILL.read_text()
        assert "component = OSAC" not in content, (
            f"{JIRA_SKILL} still contains 'component = OSAC'. "
            "The old MGMT-era compound JQL filter was restored."
        )

    def test_agent_does_not_contain_mgmt_project_key(self):
        """jira/agents/jira-agent.md must not reference the old MGMT project key in JQL."""
        content = JIRA_AGENT.read_text()
        assert "project = MGMT" not in content, (
            f"{JIRA_AGENT} still contains 'project = MGMT'. "
            "The MGMT→OSAC migration was partially reverted."
        )

    def test_agent_does_not_contain_old_compound_jql(self):
        """jira/agents/jira-agent.md must not use MGMT-era 'component = OSAC' JQL."""
        content = JIRA_AGENT.read_text()
        assert "component = OSAC" not in content, (
            f"{JIRA_AGENT} still contains 'component = OSAC'. "
            "The old MGMT-era compound JQL filter was restored."
        )


INCREMENTAL_PLANNING_SKILL = (
    REPO_ROOT / "code-quality" / "skills" / "incremental-planning" / "SKILL.md"
)
SWARM_SKILL = REPO_ROOT / "code-quality" / "skills" / "swarm" / "SKILL.md"
LABEL_DEFINITIONS = REPO_ROOT / "code-quality" / "references" / "github-label-definitions.md"
TRACKER_FIELD_SPEC = REPO_ROOT / "code-quality" / "references" / "tracker-field-spec.md"
PROJECT_MEMORY_REFERENCE = REPO_ROOT / "code-quality" / "references" / "project-memory-reference.md"
SHARED_FEEDBACK = REPO_ROOT / "dev-guard" / "references" / "shared-feedback.md"
ROADMAP_SKILL = REPO_ROOT / "code-quality" / "skills" / "roadmap" / "SKILL.md"
PHASE_SCHEMA = REPO_ROOT / "code-quality" / "skills" / "roadmap" / "references" / "phase-schema.md"


class TestCodeQualityReferenceIntegrity:
    """Verify that shared reference files exist and are referenced by their consumers."""

    def test_label_definitions_exists(self):
        assert LABEL_DEFINITIONS.exists(), (
            f"{LABEL_DEFINITIONS} does not exist on disk. "
            "Both incremental-planning and swarm SKILL.md reference this file."
        )

    def test_label_definitions_contains_categorization_rule(self):
        content = LABEL_DEFINITIONS.read_text()
        assert "Labels categorize, titles describe" in content, (
            f"{LABEL_DEFINITIONS} does not contain 'Labels categorize, titles describe'. "
            "The normative Title Rules directive was deleted or altered."
        )

    def test_label_definitions_referenced_by_incremental_planning(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "github-label-definitions.md" in content, (
            "incremental-planning/SKILL.md does not reference github-label-definitions.md"
        )

    def test_label_definitions_referenced_by_swarm(self):
        content = SWARM_SKILL.read_text()
        assert "github-label-definitions.md" in content, (
            "swarm/SKILL.md does not reference github-label-definitions.md"
        )

    def test_tracker_field_spec_exists(self):
        assert TRACKER_FIELD_SPEC.exists(), (
            f"{TRACKER_FIELD_SPEC} does not exist on disk. "
            "incremental-planning, swarm, and git-instructions reference this file."
        )

    def test_tracker_field_spec_referenced_by_incremental_planning(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "tracker-field-spec.md" in content, (
            "incremental-planning/SKILL.md does not reference tracker-field-spec.md"
        )

    def test_tracker_field_spec_referenced_by_swarm(self):
        content = SWARM_SKILL.read_text()
        assert "tracker-field-spec.md" in content, (
            "swarm/SKILL.md does not reference tracker-field-spec.md"
        )

    def test_tracker_field_spec_referenced_by_git_instructions(self):
        git_instructions = REPO_ROOT / "git-tools" / "scripts" / "git-instructions.sh"
        content = git_instructions.read_text()
        assert "tracker-field-spec.md" in content, (
            "git-instructions.sh does not reference tracker-field-spec.md"
        )

    def test_incremental_planning_contains_issue_format_section(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "### Issue Format" in content, (
            "incremental-planning/SKILL.md does not contain '### Issue Format'. "
            "The section was renamed or deleted."
        )

    def test_incremental_planning_contains_issue_sanitization_subsection(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "#### Issue Sanitization" in content, (
            "incremental-planning/SKILL.md does not contain '#### Issue Sanitization'. "
            "The subsection was renamed or deleted."
        )

    def test_incremental_planning_contains_mainline_branch_guard(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "Mainline branch guard" in content, (
            "incremental-planning/SKILL.md does not contain 'Mainline branch guard'. "
            "The mainline branch protection was deleted or altered."
        )

    def test_incremental_planning_contains_forbidden_term_check(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "Post-generation forbidden-term check" in content, (
            "incremental-planning/SKILL.md does not contain the forbidden-term check. "
            "The post-generation scan was deleted or altered."
        )

    def test_incremental_planning_contains_spawn_data_protocol(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "<spawn-data>" in content, (
            "incremental-planning/SKILL.md does not contain '<spawn-data>'. "
            "The Jira spawn-data boundary protocol was deleted or altered."
        )
        assert "&lt;/spawn-data&gt;" in content, (
            "incremental-planning/SKILL.md does not contain the spawn-data escape table. "
            "The anti-injection escape mechanism was deleted or altered."
        )

    def test_incremental_planning_contains_workflow_field(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "**Workflow:**" in content, (
            "incremental-planning/SKILL.md does not contain '**Workflow:**'. "
            "The Workflow plan header field definition was deleted or altered."
        )

    def test_incremental_planning_contains_pr_boundaries_field(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "**PR Boundaries:**" in content, (
            "incremental-planning/SKILL.md does not contain '**PR Boundaries:**'. "
            "The PR Boundaries plan header field definition was deleted or altered."
        )

    def test_incremental_planning_contains_prs_field(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "**PRs:**" in content, (
            "incremental-planning/SKILL.md does not contain '**PRs:**'. "
            "The PRs tracking plan header field definition was deleted or altered."
        )

    def test_swarm_detects_workflow_field(self):
        content = SWARM_SKILL.read_text()
        assert "**Workflow:**" in content, (
            "swarm/SKILL.md does not contain '**Workflow:**'. "
            "The Workflow field detection for incremental mode was deleted or altered."
        )

    def test_swarm_detects_pr_boundaries_field(self):
        content = SWARM_SKILL.read_text()
        assert "**PR Boundaries:**" in content, (
            "swarm/SKILL.md does not contain '**PR Boundaries:**'. "
            "The PR Boundaries extraction for incremental mode was deleted or altered."
        )

    def test_swarm_detects_prs_field(self):
        content = SWARM_SKILL.read_text()
        assert "**PRs:**" in content, (
            "swarm/SKILL.md does not contain '**PRs:**'. "
            "The PRs field extraction for incremental mode was deleted or altered."
        )

    def test_swarm_pr_template_no_serial_numbering(self):
        content = SWARM_SKILL.read_text()
        assert "Part {current_pr} of {total_prs}" not in content, (
            "swarm/SKILL.md still contains 'Part {current_pr} of {total_prs}'. "
            "PR body template must not use serial numbering — each PR is standalone work."
        )
        assert "PR framing rules" in content, (
            "swarm/SKILL.md does not contain 'PR framing rules'. "
            "The standalone PR framing rules section was deleted or altered."
        )

    def test_shared_feedback_standalone_pr_rule(self):
        content = SHARED_FEEDBACK.read_text()
        assert "PRs are standalone work" in content, (
            "shared-feedback.md does not contain 'PRs are standalone work'. "
            "The standalone PR framing rule was deleted or altered."
        )

    def test_roadmap_standalone_pr_framing(self):
        content = ROADMAP_SKILL.read_text()
        assert 'never "Part X of Y"' in content, (
            "roadmap/SKILL.md does not contain the standalone PR framing rule. "
            "The rule was deleted or altered."
        )

    def test_incremental_planning_standalone_pr_framing(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "must NOT appear in PR titles, PR bodies, or user-facing messages" in content, (
            "incremental-planning/SKILL.md does not contain the standalone PR framing rule. "
            "The rule was deleted or altered."
        )

    def test_phase_schema_internal_plumbing_rule(self):
        content = PHASE_SCHEMA.read_text()
        assert "internal plumbing" in content, (
            "phase-schema.md does not contain 'internal plumbing'. "
            "The PR column internal plumbing caveat was deleted or altered."
        )

    def test_project_memory_reference_contains_checkpoint_schema(self):
        content = PROJECT_MEMORY_REFERENCE.read_text()
        assert "checkpoint.json" in content, (
            f"{PROJECT_MEMORY_REFERENCE} does not contain 'checkpoint.json'. "
            "The checkpoint schema section was deleted or altered."
        )

    def test_project_memory_reference_checkpoint_has_plan_file_field(self):
        content = PROJECT_MEMORY_REFERENCE.read_text()
        assert '"plan_file"' in content, (
            f"{PROJECT_MEMORY_REFERENCE} does not contain '\"plan_file\"'. "
            "The checkpoint.json plan_file field was removed from the schema."
        )

    def test_project_memory_reference_checkpoint_has_tasks_remaining_field(self):
        content = PROJECT_MEMORY_REFERENCE.read_text()
        assert '"tasks_remaining"' in content, (
            f"{PROJECT_MEMORY_REFERENCE} does not contain '\"tasks_remaining\"'. "
            "The checkpoint.json tasks_remaining field was removed from the schema."
        )

    def test_project_memory_reference_checkpoint_has_context_summary_field(self):
        content = PROJECT_MEMORY_REFERENCE.read_text()
        assert '"context_summary"' in content, (
            f"{PROJECT_MEMORY_REFERENCE} does not contain '\"context_summary\"'. "
            "The checkpoint.json context_summary field was removed from the schema."
        )


class TestPluginVersionParity:
    """Structural tests that plugin.json and marketplace.json versions agree for all plugins."""

    def _load_marketplace_versions(self):
        """Return a dict mapping plugin name -> version from marketplace.json."""
        marketplace = json.loads(MARKETPLACE_JSON.read_text())
        return {entry["name"]: entry["version"] for entry in marketplace["plugins"]}

    def test_all_plugin_versions_match_marketplace(self):
        """Every plugin's plugin.json version must match its marketplace.json entry."""
        marketplace_versions = self._load_marketplace_versions()
        mismatches = []

        for plugin_json_path in sorted(REPO_ROOT.glob("*/.claude-plugin/plugin.json")):
            plugin_data = json.loads(plugin_json_path.read_text())
            plugin_name = plugin_data["name"]
            plugin_version = plugin_data["version"]

            if plugin_name not in marketplace_versions:
                mismatches.append(f"{plugin_name}: plugin.json name not found in marketplace.json")
                continue

            marketplace_version = marketplace_versions[plugin_name]
            if plugin_version != marketplace_version:
                mismatches.append(
                    f"{plugin_name}: plugin.json={plugin_version!r} "
                    f"vs marketplace.json={marketplace_version!r}"
                )

        assert not mismatches, (
            "Plugin version mismatch between plugin.json and marketplace.json.\n"
            "CLAUDE.md rule: 'Always bump plugin versions in both files.'\n"
            + "\n".join(f"  - {m}" for m in mismatches)
        )

    def test_marketplace_entries_have_plugin_on_disk(self):
        """Every plugin in marketplace.json must have a plugin.json on disk."""
        marketplace_versions = self._load_marketplace_versions()
        missing = []

        for name in sorted(marketplace_versions):
            plugin_json = REPO_ROOT / name / ".claude-plugin" / "plugin.json"
            if not plugin_json.exists():
                missing.append(f"{name}: listed in marketplace.json but {plugin_json} not found")

        assert not missing, "Phantom marketplace entries (no plugin.json on disk):\n" + "\n".join(
            f"  - {m}" for m in missing
        )

    def test_all_plugin_descriptions_and_authors_match_marketplace(self):
        """Every plugin's .claude-plugin/plugin.json and marketplace.json entry must have
        identical description strings and author={'name': 'wgordon17'}."""
        marketplace = json.loads(MARKETPLACE_JSON.read_text())
        entries_by_name = {entry["name"]: entry for entry in marketplace["plugins"]}
        mismatches = []

        for name, entry in sorted(entries_by_name.items()):
            plugin_json_path = REPO_ROOT / name / ".claude-plugin" / "plugin.json"
            plugin_data = json.loads(plugin_json_path.read_text())

            if plugin_data.get("description") != entry.get("description"):
                mismatches.append(
                    f"{name}: description mismatch between .claude-plugin/plugin.json "
                    f"({plugin_data.get('description')!r}) and marketplace.json "
                    f"({entry.get('description')!r})"
                )
            if plugin_data.get("author") != {"name": "wgordon17"}:
                mismatches.append(
                    f"{name}: .claude-plugin/plugin.json author={plugin_data.get('author')!r}"
                )
            if entry.get("author") != {"name": "wgordon17"}:
                mismatches.append(f"{name}: marketplace.json author={entry.get('author')!r}")

        assert not mismatches, "Plugin description/author drift:\n" + "\n".join(
            f"  - {m}" for m in mismatches
        )

    def test_agent_plugins_v1_root_manifests_in_sync(self):
        """Every plugin must have a root <plugin>/plugin.json (Agent Plugins v1.0.0)
        matching <plugin>/.claude-plugin/plugin.json."""
        marketplace = json.loads(MARKETPLACE_JSON.read_text())
        mismatches = []

        for entry in marketplace["plugins"]:
            name = entry["name"]
            claude_manifest = REPO_ROOT / name / ".claude-plugin" / "plugin.json"
            agent_manifest = REPO_ROOT / name / "plugin.json"

            if not agent_manifest.exists():
                mismatches.append(f"{name}: missing root {agent_manifest}")
                continue

            claude_data = json.loads(claude_manifest.read_text())
            agent_data = json.loads(agent_manifest.read_text())

            expected_schema = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
            if agent_data.get("$schema") != expected_schema:
                mismatches.append(
                    f"{name}/plugin.json: $schema={agent_data.get('$schema')!r} "
                    f"(expected {expected_schema!r})"
                )
            for key in ("name", "version", "description", "author"):
                if agent_data.get(key) != claude_data.get(key):
                    mismatches.append(
                        f"{name}: {key} mismatch between root plugin.json "
                        f"({agent_data.get(key)!r}) and .claude-plugin/plugin.json "
                        f"({claude_data.get(key)!r})"
                    )

        assert not mismatches, "Agent Plugins v1 root manifest drift:\n" + "\n".join(
            f"  - {m}" for m in mismatches
        )

    def test_codex_marketplace_entries_match_claude_marketplace(self):
        """Both .claude-plugin/marketplace.json and .agents/plugins/marketplace.json must
        register the exact same 13 plugins."""
        codex_marketplace_path = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
        assert codex_marketplace_path.exists(), f"Missing {codex_marketplace_path}"

        claude_names = [
            entry["name"] for entry in json.loads(MARKETPLACE_JSON.read_text())["plugins"]
        ]
        codex_names = [
            entry["name"] for entry in json.loads(codex_marketplace_path.read_text())["plugins"]
        ]
        assert len(claude_names) == 13, f"Expected 13 plugins, got {len(claude_names)}"
        assert set(claude_names) == set(codex_names), (
            f"Marketplace registration mismatch: claude={claude_names} vs codex={codex_names}"
        )

    def test_all_plugins_have_readme(self):
        """Every plugin in marketplace.json must have a non-empty README.md on disk."""
        marketplace = json.loads(MARKETPLACE_JSON.read_text())
        missing = []

        for entry in marketplace["plugins"]:
            name = entry["name"]
            readme = REPO_ROOT / name / "README.md"
            if not readme.exists() or not readme.read_text().strip():
                missing.append(f"{name}: missing or empty {readme}")

        assert not missing, "Plugins missing README.md:\n" + "\n".join(f"  - {m}" for m in missing)

    def test_no_nested_command_md_files(self):
        """No plugin may use nested commands/<name>/COMMAND.md (causes :COMMAND slug bug)."""
        nested = [
            str(p.relative_to(REPO_ROOT)) for p in sorted(REPO_ROOT.glob("*/commands/*/COMMAND.md"))
        ]
        assert not nested, (
            "Nested COMMAND.md files found (use flat commands/<name>.md instead):\n"
            + "\n".join(f"  - {p}" for p in nested)
        )

    def test_agent_plugins_v1_mcp_configs_in_sync(self):
        """For all 4 MCP plugins, root <plugin>/mcp.json must match <plugin>/.mcp.json."""
        mcp_plugins = ("jira", "fetchaller-mcp", "github-mcp", "chai-bot")
        mismatches = []

        for name in mcp_plugins:
            dot_mcp_path = REPO_ROOT / name / ".mcp.json"
            root_mcp_path = REPO_ROOT / name / "mcp.json"
            if not dot_mcp_path.exists() or not root_mcp_path.exists():
                mismatches.append(f"{name}: missing .mcp.json or mcp.json")
                continue

            dot_data = json.loads(dot_mcp_path.read_text())
            root_data = json.loads(root_mcp_path.read_text())

            expected_schema = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
            if root_data.get("$schema") != expected_schema:
                mismatches.append(
                    f"{name}/mcp.json: $schema={root_data.get('$schema')!r} "
                    f"(expected {expected_schema!r})"
                )

            dot_servers = dot_data.get("mcpServers", {})
            root_servers = root_data.get("mcpServers", {})
            if set(dot_servers.keys()) != set(root_servers.keys()):
                mismatches.append(
                    f"{name}: server keys mismatch (.mcp.json={list(dot_servers)} "
                    f"vs mcp.json={list(root_servers)})"
                )
                continue

            for srv_name, dot_srv in dot_servers.items():
                root_srv = root_servers[srv_name]
                expected_type = "streamable-http" if dot_srv.get("type") == "http" else "stdio"
                if root_srv.get("type") != expected_type:
                    mismatches.append(
                        f"{name} ({srv_name}): expected type={expected_type!r}, "
                        f"got {root_srv.get('type')!r}"
                    )
                for field in ("command", "args", "env", "url", "headers"):
                    if dot_srv.get(field) != root_srv.get(field):
                        mismatches.append(
                            f"{name} ({srv_name}): {field} mismatch between .mcp.json and mcp.json"
                        )

        assert not mismatches, "Agent Plugins v1 mcp.json drift:\n" + "\n".join(
            f"  - {m}" for m in mismatches
        )


class TestDevGuardDescriptionSync:
    """Structural test that dev-guard's description stays in sync across its three
    surfaces: hooks.json, plugin.json, and marketplace.json. All three must share the
    same trailing capability phrase, per CLAUDE.md's 'bump plugin versions in both
    files' rule extended to descriptions — a substring-presence grep only confirms a
    phrase exists somewhere, not that the surfaces agree with each other."""

    def _trailing_phrase(self, description: str) -> str:
        return description.rsplit(",", 1)[-1].strip()

    def test_description_suffix_matches_across_surfaces(self):
        hooks_description = json.loads(DEV_GUARD_HOOKS_JSON.read_text())["description"]
        plugin_description = json.loads(DEV_GUARD_PLUGIN_JSON.read_text())["description"]

        marketplace = json.loads(MARKETPLACE_JSON.read_text())
        dev_guard_entry = next(
            entry for entry in marketplace["plugins"] if entry["name"] == "dev-guard"
        )
        marketplace_description = dev_guard_entry["description"]

        hooks_suffix = self._trailing_phrase(hooks_description)
        plugin_suffix = self._trailing_phrase(plugin_description)
        marketplace_suffix = self._trailing_phrase(marketplace_description)

        assert hooks_suffix == plugin_suffix == marketplace_suffix, (
            "dev-guard's description trailing phrase must match across "
            "hooks.json, plugin.json, and marketplace.json.\n"
            f"  hooks.json:       {hooks_suffix!r}\n"
            f"  plugin.json:      {plugin_suffix!r}\n"
            f"  marketplace.json: {marketplace_suffix!r}"
        )


FETCHALLER_MCP_JSON = REPO_ROOT / "fetchaller-mcp" / ".mcp.json"
FETCHALLER_AGENT_MCP_JSON = REPO_ROOT / "fetchaller-mcp" / "mcp.json"


class TestFetchallerMcpConfigSecurity:
    """Structural guardrails on fetchaller-mcp/.mcp.json and fetchaller-mcp/mcp.json."""

    def _load_args(self, path: Path = FETCHALLER_MCP_JSON) -> list[str]:
        data = json.loads(path.read_text())
        return data["mcpServers"]["fetchaller"]["args"]

    @pytest.mark.parametrize("mcp_path", [FETCHALLER_MCP_JSON, FETCHALLER_AGENT_MCP_JSON])
    def test_pinned_to_full_commit_sha(self, mcp_path: Path):
        """The git ref must be a full 40-char commit SHA, never a branch/tag/main."""
        args = self._load_args(mcp_path)
        from_arg = args[args.index("--from") + 1]
        ref = from_arg.rsplit("@", 1)[-1]
        assert re.match(r"^[0-9a-f]{40}$", ref), (
            f"{mcp_path.relative_to(REPO_ROOT)} must pin to a full 40-char commit SHA, got: {ref!r}"
        )

    @pytest.mark.parametrize("mcp_path", [FETCHALLER_MCP_JSON, FETCHALLER_AGENT_MCP_JSON])
    def test_no_http_mode_flag(self, mcp_path: Path):
        """--http must never appear in args (stdio-only, per Security Flags)."""
        args = self._load_args(mcp_path)
        assert "--http" not in args, (
            f"{mcp_path.relative_to(REPO_ROOT)} must not enable --http hosted mode"
        )

    @pytest.mark.parametrize("mcp_path", [FETCHALLER_MCP_JSON, FETCHALLER_AGENT_MCP_JSON])
    def test_wafer_py_version_pinned(self, mcp_path: Path):
        """The wafer-py[browser] pin must stay present and exact -- guards
        against it being accidentally dropped during a future SHA bump."""
        args = self._load_args(mcp_path)
        with_index = args.index("--with")
        assert args[with_index + 1] == "wafer-py[browser]==0.4.4", (
            f"{mcp_path.relative_to(REPO_ROOT)} must pin 'wafer-py[browser]==0.4.4' via --with, "
            f"got: {args[with_index + 1]!r}"
        )

    @pytest.mark.slow
    @pytest.mark.skipif(
        not os.environ.get("RUN_LIVE_TESTS"),
        reason="network-dependent live smoke test, opt-in only (set RUN_LIVE_TESTS=1)",
    )
    def test_live_uvx_invocation_resolves_and_starts(self):
        """Live smoke test: the exact uvx args from .mcp.json must resolve the
        pinned commit + wafer-py[browser] extra and successfully invoke the
        fetchaller-mcp entry point.

        Network-dependent and slow (package resolution + install) -- not run
        by default. Opt in with `make test-live` (RUN_LIVE_TESTS=1, -m slow).

        Uses --help rather than a full stdio MCP handshake: fetchaller-mcp's
        argparse only defines -h/--help and --http, so --help is a fast,
        deterministic way to prove the pinned commit + dependency pin combo
        installs cleanly and the entry point runs, without driving the
        JSON-RPC stdio protocol.
        """
        args = self._load_args()
        try:
            result = subprocess.run(
                ["uvx", *args, "--help"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "fetchaller-mcp did not resolve/start within 60s via the exact "
                f".mcp.json args. Pinned commit or wafer-py[browser] extra may be "
                f"broken or unreachable.\npartial stdout:\n{exc.stdout}"
            ) from exc

        assert result.returncode == 0, (
            "fetchaller-mcp failed to resolve/start via the exact .mcp.json args. "
            f"Pinned commit or wafer-py[browser] extra may be broken.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "fetchaller-mcp" in result.stdout, (
            "fetchaller-mcp --help did not print expected usage text -- "
            f"entry point may have changed.\nstdout:\n{result.stdout}"
        )


FETCHALLER_DOMAIN_RULES_DOC = REPO_ROOT / "dev-guard" / "references" / "fetchaller-domain-rules.md"
TOOL_SELECTION_GUARD = REPO_ROOT / "dev-guard" / "hooks" / "tool-selection-guard.py"


class TestFetchallerDomainRulesDocSync:
    """fetchaller-domain-rules.md documents a subset of BLOCKED_URL_RULES --
    verify the domain table's Rule name column stays in sync with the code,
    in both directions. Catches the exact drift the doc itself warns about:
    'a domain added there without a matching _WEBSEARCH_TOOL_HINTS entry
    silently falls back to the generic _DEFAULT_WEBSEARCH_HINT' starts with a
    BLOCKED_URL_RULES addition that isn't documented here at all.

    Fetchaller-related rules are identified structurally by their
    `-blocked`/`-gated` name suffix (matching every current entry) rather
    than a hardcoded exclusion list of pre-existing auth-only rule names --
    a hardcoded list would need updating every time an unrelated auth rule
    is added, coupling this file to changes that have nothing to do with
    fetchaller-mcp.
    """

    def _doc_rule_names(self) -> set[str]:
        content = FETCHALLER_DOMAIN_RULES_DOC.read_text()
        return set(re.findall(r"`([a-z0-9-]+-(?:blocked|gated))`", content))

    def _guard_rule_names(self) -> set[str]:
        content = TOOL_SELECTION_GUARD.read_text()
        names = re.findall(r'URLRule\(\s*\n\s*"([a-z0-9-]+)"', content)
        return {name for name in names if name.endswith(("-blocked", "-gated"))}

    def test_doc_rule_names_exist_in_blocked_url_rules(self):
        """Every rule name documented in fetchaller-domain-rules.md must
        exist in BLOCKED_URL_RULES -- catches stale/renamed doc entries."""
        doc_names = self._doc_rule_names()
        guard_names = self._guard_rule_names()
        stale = doc_names - guard_names
        assert not stale, (
            f"fetchaller-domain-rules.md documents rule name(s) not present in "
            f"BLOCKED_URL_RULES: {sorted(stale)}. Update the doc or the rule name."
        )

    def test_blocked_url_rules_fetchaller_subset_documented(self):
        """Every fetchaller-related BLOCKED_URL_RULES entry must be documented
        in fetchaller-domain-rules.md's domain table -- catches undocumented
        new rules (the drift the doc itself warns about)."""
        doc_names = self._doc_rule_names()
        guard_names = self._guard_rule_names()
        undocumented = guard_names - doc_names
        assert not undocumented, (
            f"BLOCKED_URL_RULES contains fetchaller-related rule name(s) missing from "
            f"fetchaller-domain-rules.md's domain table: {sorted(undocumented)}. "
            "Add a row to the table and check whether _WEBSEARCH_TOOL_HINTS needs an entry too."
        )


TOKEN_EFFICIENCY_DOC = REPO_ROOT / "dev-guard" / "references" / "token-efficiency.md"


class TestTokenEfficiencyDocSafety:
    """token-efficiency.md's Rules 1-3 examples are locally patched (by
    .github/scripts/patch-token-efficiency.sh) on every upstream sync to avoid
    recommending shell tools this repo's own tool-selection-guard.py blocks
    (head, tail, cat, sed, awk, bare python3) in favor of the Read tool / uv
    run. Those three safety assertions only run in the weekly
    sync-token-efficiency.yml workflow, so a manual edit to this doc that
    reintroduces a blocked-tool recommendation would pass PR-gating CI. Re-run
    the same three invariants here so `make test` catches it too.
    """

    def test_no_blocked_tool_words_outside_attribution_line(self):
        """Line 1 is the attribution comment and legitimately names the
        blocked tools in prose; every other line must not mention them."""
        blocked_pattern = re.compile(r"\b(head|tail|cat|sed|awk)\b")
        lines = TOKEN_EFFICIENCY_DOC.read_text().splitlines()
        hits = [
            f"{i}: {line}"
            for i, line in enumerate(lines[1:], start=2)
            if blocked_pattern.search(line)
        ]
        assert not hits, (
            "blocked-tool mention(s) found outside line 1 attribution comment "
            f"in token-efficiency.md: {hits}"
        )

    def test_python3_mentions_are_all_uv_run_prefixed(self):
        """Every 'python3' mention must be prefixed by 'uv run ' -- a bare
        python3 reference slipping through would fail this parity check."""
        content = TOKEN_EFFICIENCY_DOC.read_text()
        python3_count = len(re.findall(r"python3", content))
        uv_python3_count = len(re.findall(r"uv run python3", content))
        assert python3_count == uv_python3_count, (
            f"'python3' mention count ({python3_count}) does not match "
            f"'uv run python3' count ({uv_python3_count}) in token-efficiency.md "
            "-- a bare python3 reference may have slipped through unpatched"
        )

    def test_read_tool_replacement_text_present(self):
        """Confirms Rule 2's 'Read tool with' replacement text actually
        landed, rather than the substitution being a silent no-op."""
        content = TOKEN_EFFICIENCY_DOC.read_text()
        assert "Read tool with" in content, (
            "expected replacement text 'Read tool with' not found in "
            "token-efficiency.md -- Rule 2 patch may not have applied"
        )


BUG_INVESTIGATION_SKILL = REPO_ROOT / "code-quality" / "skills" / "bug-investigation" / "SKILL.md"
QUALITY_GATE_SKILL = REPO_ROOT / "code-quality" / "skills" / "quality-gate" / "SKILL.md"
ARTIFACT_FORMATS = (
    REPO_ROOT / "code-quality" / "skills" / "summarize" / "references" / "artifact-formats.md"
)

_EM_DASH = "\u2014"
_DEPTH_3_PHRASE = "at least 3 components"
_ROADMAP_HOLD_PHRASE = "do NOT update"
_ROADMAP_WRITE_PHRASE = "update those BUGS.md entries now"


class TestBugsTrackingIntegrity:
    """Guard the cross-skill Tracked In field contract."""

    def test_bug_investigation_contains_tracked_in_field(self):
        content = BUG_INVESTIGATION_SKILL.read_text()
        assert "**Tracked In:**" in content, (
            f"{BUG_INVESTIGATION_SKILL} does not contain '**Tracked In:**'. "
            "The Tracked In field was deleted or renamed."
        )

    def test_incremental_planning_contains_tracked_in_field(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert "**Tracked In:**" in content, (
            f"{INCREMENTAL_PLANNING_SKILL} does not contain '**Tracked In:**'. "
            "The Tracked In field was deleted or renamed."
        )

    def test_quality_gate_contains_tracked_in_field(self):
        content = QUALITY_GATE_SKILL.read_text()
        assert "**Tracked In:**" in content, (
            f"{QUALITY_GATE_SKILL} does not contain '**Tracked In:**'. "
            "The Tracked In field was deleted or renamed."
        )

    def test_roadmap_contains_tracked_in_field(self):
        content = ROADMAP_SKILL.read_text()
        assert "**Tracked In:**" in content, (
            f"{ROADMAP_SKILL} does not contain '**Tracked In:**'. "
            "The Tracked In field was deleted or renamed."
        )

    def test_artifact_formats_contains_tracked_in(self):
        content = ARTIFACT_FORMATS.read_text()
        assert "**Tracked In:**" in content, (
            f"{ARTIFACT_FORMATS} does not contain '**Tracked In:**'. "
            "The Tracked In field was removed from artifact format documentation."
        )

    def test_bug_investigation_contains_em_dash_sentinel(self):
        content = BUG_INVESTIGATION_SKILL.read_text()
        assert f"**Tracked In:** {_EM_DASH}" in content, (
            f"{BUG_INVESTIGATION_SKILL} does not contain '**Tracked In:** {_EM_DASH}'. "
            "The em-dash untracked sentinel was changed or removed."
        )

    def test_quality_gate_contains_em_dash_sentinel(self):
        content = QUALITY_GATE_SKILL.read_text()
        assert f"**Tracked In:** {_EM_DASH}" in content, (
            f"{QUALITY_GATE_SKILL} does not contain '**Tracked In:** {_EM_DASH}'. "
            "The em-dash untracked sentinel was changed or removed."
        )

    def test_quality_gate_uses_correct_mcp_tool_name(self):
        gate_specs = QUALITY_GATE_SKILL.parent / "references" / "gate-specifications.md"
        content = QUALITY_GATE_SKILL.read_text() + gate_specs.read_text()
        assert "pull_request_read" in content, (
            f"{QUALITY_GATE_SKILL} and {gate_specs} do not reference 'pull_request_read'."
        )
        assert "mcp__github__pull_request_read" not in content, (
            f"{QUALITY_GATE_SKILL} or {gate_specs} contains stale "
            "'mcp__github__pull_request_read' reference(s)."
        )


class TestBugsTrackingDepthConsistency:
    """Guard the path-comparison depth-3 rule shared across 3 skills."""

    def test_incremental_planning_requires_depth_3_prefix(self):
        content = INCREMENTAL_PLANNING_SKILL.read_text()
        assert _DEPTH_3_PHRASE in content, (
            f"{INCREMENTAL_PLANNING_SKILL} does not contain '{_DEPTH_3_PHRASE}'. "
            "The path-comparison depth rule was changed or removed."
        )

    def test_quality_gate_requires_depth_3_prefix(self):
        content = QUALITY_GATE_SKILL.read_text()
        assert _DEPTH_3_PHRASE in content, (
            f"{QUALITY_GATE_SKILL} does not contain '{_DEPTH_3_PHRASE}'. "
            "The path-comparison depth rule was changed or removed."
        )

    def test_roadmap_requires_depth_3_prefix(self):
        content = ROADMAP_SKILL.read_text()
        assert _DEPTH_3_PHRASE in content, (
            f"{ROADMAP_SKILL} does not contain '{_DEPTH_3_PHRASE}'. "
            "The path-comparison depth rule was changed or removed."
        )


class TestRoadmapBugsContractIntegrity:
    """Guard the cross-phase deferred-write contract in roadmap/SKILL.md."""

    def test_roadmap_phase1_holds_tracked_in_update(self):
        content = ROADMAP_SKILL.read_text()
        assert _ROADMAP_HOLD_PHRASE in content, (
            f"{ROADMAP_SKILL} does not contain '{_ROADMAP_HOLD_PHRASE}'. "
            "The Phase 1 deferred-write instruction was deleted or altered."
        )

    def test_roadmap_phase4_writes_tracked_in(self):
        content = ROADMAP_SKILL.read_text()
        assert _ROADMAP_WRITE_PHRASE in content, (
            f"{ROADMAP_SKILL} does not contain '{_ROADMAP_WRITE_PHRASE}'. "
            "The Phase 4 deferred-write step was deleted or altered."
        )

    def test_roadmap_hold_precedes_write(self):
        content = ROADMAP_SKILL.read_text()
        hold_pos = content.find(_ROADMAP_HOLD_PHRASE)
        write_pos = content.find(_ROADMAP_WRITE_PHRASE)
        assert hold_pos != -1, f"'{_ROADMAP_HOLD_PHRASE}' not found in {ROADMAP_SKILL}"
        assert write_pos != -1, f"'{_ROADMAP_WRITE_PHRASE}' not found in {ROADMAP_SKILL}"
        assert hold_pos < write_pos, (
            f"{ROADMAP_SKILL}: Phase 1 hold instruction appears AFTER Phase 4 write instruction. "
            "The cross-phase contract ordering is broken."
        )
