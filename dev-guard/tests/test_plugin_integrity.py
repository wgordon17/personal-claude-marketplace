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
        assert "account ID is empty after" in content and "halt and report the error" in content, (
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
        content = QUALITY_GATE_SKILL.read_text()
        assert "mcp__plugin_github-mcp_github__pull_request_read" in content, (
            f"{QUALITY_GATE_SKILL} does not contain the correct MCP tool name. "
            "Stale 'mcp__github__pull_request_read' may have been reintroduced."
        )
        cleaned = content.replace("mcp__plugin_github-mcp_github__pull_request_read", "")
        assert "mcp__github__pull_request_read" not in cleaned, (
            f"{QUALITY_GATE_SKILL} contains stale 'mcp__github__pull_request_read' "
            "reference(s) without the 'plugin_github-mcp_' qualifier."
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
