"""Structural tests for Jira plugin integrity.

Verifies that:
1. jira/skills/jira/SKILL.md contains the URL presentation directive.
2. jira/agents/jira-agent.md contains the URL presentation directive.
3. Both files stay in sync — drift is detected immediately.
4. Version numbers in each plugin's plugin.json match the entry in marketplace.json.

These are grep-based and JSON-parse lint tests — no LLM calls, no subprocess execution.
They guard against accidental deletion of the URL rule and against
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
    """Structural tests that the Jira URL presentation directive is present and consistent."""

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
