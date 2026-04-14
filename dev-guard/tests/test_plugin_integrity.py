"""Structural tests for plugin integrity.

Verifies that:
1. jira/skills/jira/SKILL.md contains the URL presentation directive.
2. jira/agents/jira-agent.md contains the URL presentation directive.
3. Both files stay in sync — drift is detected immediately.
4. Version numbers in each plugin's plugin.json match the entry in marketplace.json.
5. Every marketplace.json entry has a corresponding plugin.json on disk.
6. Shared reference files (github-label-definitions.md, tracker-field-spec.md) exist
   on disk and are referenced by their consumer SKILL.md files.
7. Jira self-assignment rules: JIRA_LOGIN capture, -a flag, never-unassigned rule,
   halt-on-empty guard, and post-create verification are present in both files.

These are grep-based and JSON-parse lint tests — no LLM calls, no subprocess execution.
They guard against accidental deletion of rules and references, and against
version drift between plugin manifests.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
JIRA_SKILL = REPO_ROOT / "jira" / "skills" / "jira" / "SKILL.md"
JIRA_AGENT = REPO_ROOT / "jira" / "agents" / "jira-agent.md"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"

URL_DIRECTIVE = "redhat.atlassian.net/browse"
OLD_PHRASE = "After every create or update operation"


class TestJiraPluginIntegrity:
    """Structural tests for Jira plugin rules: URL directive, self-assignment, data safety."""

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

    def test_skill_contains_jira_login_capture(self):
        """jira/skills/jira/SKILL.md must capture JIRA_LOGIN during bootstrap."""
        content = JIRA_SKILL.read_text()
        assert "JIRA_LOGIN=$(jira me)" in content, (
            f"{JIRA_SKILL} does not contain 'JIRA_LOGIN=$(jira me)'. "
            "The bootstrap self-assignment capture step was deleted or altered."
        )

    def test_skill_contains_self_assignment_flag(self):
        """jira/skills/jira/SKILL.md must use -a '$JIRA_LOGIN' in issue create commands."""
        content = JIRA_SKILL.read_text()
        assert '-a "$JIRA_LOGIN"' in content, (
            f"{JIRA_SKILL} does not contain '-a \"$JIRA_LOGIN\"'. "
            "The self-assignment flag was removed from issue create commands."
        )

    def test_skill_contains_never_create_unassigned(self):
        """jira/skills/jira/SKILL.md must contain the 'Never create unassigned cards' rule."""
        content = JIRA_SKILL.read_text()
        assert "Never create unassigned cards" in content, (
            f"{JIRA_SKILL} does not contain 'Never create unassigned cards'. "
            "The unassigned-card prohibition was deleted or altered."
        )

    def test_skill_contains_halt_on_empty_jira_login(self):
        """jira/skills/jira/SKILL.md must contain the halt-on-empty JIRA_LOGIN instruction."""
        content = JIRA_SKILL.read_text()
        assert "JIRA_LOGIN` is empty after capture, halt and report the error" in content, (
            f"{JIRA_SKILL} does not contain the halt-on-empty JIRA_LOGIN instruction. "
            "The guard against missing assignee was deleted or altered."
        )

    def test_agent_contains_jira_login_capture(self):
        """jira/agents/jira-agent.md must capture JIRA_LOGIN during prerequisites."""
        content = JIRA_AGENT.read_text()
        assert "JIRA_LOGIN=$(jira me)" in content, (
            f"{JIRA_AGENT} does not contain 'JIRA_LOGIN=$(jira me)'. "
            "The prerequisites self-assignment capture step was deleted or altered."
        )

    def test_agent_contains_self_assignment_flag(self):
        """jira/agents/jira-agent.md must use -a '$JIRA_LOGIN' in issue create commands."""
        content = JIRA_AGENT.read_text()
        assert '-a "$JIRA_LOGIN"' in content, (
            f"{JIRA_AGENT} does not contain '-a \"$JIRA_LOGIN\"'. "
            "The self-assignment flag was removed from issue create commands."
        )

    def test_agent_contains_never_create_unassigned(self):
        """jira/agents/jira-agent.md must contain the 'Never create unassigned cards' rule."""
        content = JIRA_AGENT.read_text()
        assert "Never create unassigned cards" in content, (
            f"{JIRA_AGENT} does not contain 'Never create unassigned cards'. "
            "The unassigned-card prohibition was deleted or altered."
        )

    def test_agent_contains_halt_on_empty_jira_login(self):
        """jira/agents/jira-agent.md must contain the halt-on-empty JIRA_LOGIN instruction."""
        content = JIRA_AGENT.read_text()
        assert "JIRA_LOGIN` is empty after capture, halt and report the error" in content, (
            f"{JIRA_AGENT} does not contain the halt-on-empty JIRA_LOGIN instruction. "
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


INCREMENTAL_PLANNING_SKILL = (
    REPO_ROOT / "code-quality" / "skills" / "incremental-planning" / "SKILL.md"
)
SWARM_SKILL = REPO_ROOT / "code-quality" / "skills" / "swarm" / "SKILL.md"
LABEL_DEFINITIONS = REPO_ROOT / "code-quality" / "references" / "github-label-definitions.md"
TRACKER_FIELD_SPEC = REPO_ROOT / "code-quality" / "references" / "tracker-field-spec.md"


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
