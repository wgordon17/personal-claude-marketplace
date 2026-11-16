"""Shared MCP constants for dev-guard hooks.

Keys are server-qualified: "server_id__func_name" where server_id is the middle
segment from tool_name.split("__", 2). This prevents a malicious MCP server from
getting auto-approved by naming its tools to match known read-only function names.
"""


def _qualify(server: str, tools: list[str]) -> list[str]:
    """Build server-qualified keys: 'server__func'."""
    return [f"{server}__{t}" for t in tools]


# MCP tool names that are read-only (auto-approved by guard, no write-signal in stop-hook)
# Format: "server_id__func_name" — matched via mcp_key() helper below
MCP_READ_ONLY: frozenset[str] = frozenset(
    _qualify(
        "serena",
        [
            "activate_project",
            "check_onboarding_performed",
            "find_file",
            "find_referencing_symbols",
            "find_symbol",
            "get_current_config",
            "get_symbols_overview",
            "initial_instructions",
            "list_dir",
            "list_memories",
            "onboarding",
            "read_memory",
            "search_for_pattern",
        ],
    )
    + _qualify(
        "plugin_claude-mem_mcp-search",
        [
            "get_observations",
            "search",
            "smart_outline",
            "smart_search",
            "smart_unfold",
            "timeline",
        ],
    )
    + _qualify("context7", ["resolve-library-id", "query-docs"])
    + _qualify("sequential-thinking", ["sequentialthinking"])
    + _qualify(
        "playwright",
        [
            "browser_snapshot",
            "browser_console_messages",
            "browser_network_requests",
            "browser_tabs",
        ],
    )
    + _qualify(
        "plugin_jira_mcp-atlassian-prod",
        [
            # Jira read
            "atlassianUserInfo",
            "fetch",
            "getAccessibleAtlassianResources",
            "getIssueLinkTypes",
            "getJiraIssue",
            "getJiraIssueRemoteIssueLinks",
            "getJiraIssueTypeMetaWithFields",
            "getJiraProjectIssueTypesMetadata",
            "getTransitionsForJiraIssue",
            "getVisibleJiraProjects",
            "lookupJiraAccountId",
            "search",
            "searchJiraIssuesUsingJql",
            # Confluence read
            "getConfluenceCommentChildren",
            "getConfluencePage",
            "getConfluencePageDescendants",
            "getConfluencePageFooterComments",
            "getConfluencePageInlineComments",
            "getConfluenceSpaces",
            "getPagesInConfluenceSpace",
            "searchConfluenceUsingCql",
        ],
    )
    + _qualify(
        "metadata-service",
        ["get_cluster_info", "get_cluster_cves", "list_clusters"],
    )
    # fetchaller-mcp: unlike the stateless-by-design entries above (Serena,
    # context7, etc.), these tools drive TLS impersonation and browser
    # automation (Patchright/wafer-py) against live third-party sites.
    # Read-only status here is a manual audit finding, not an inherent
    # property — verified at pinned SHA
    # 500078f7f349d080de1fb7f231cd5d11f3fc42d2 (github.com/Averyy/fetchaller-mcp):
    # every tool below has a fixed, query/filter/ID-only MCP schema with
    # additionalProperties=False, no mutation-shaped parameters, hardcoded
    # public origins or strict ATS hostname regexes (*.eightfold.ai,
    # *.wd<N>.myworkdayjobs.com), and hardcoded read-only GraphQL operation
    # IDs (Facebook Marketplace, Meta Careers). See fetchaller-mcp/README.md's
    # "Tool capability audit" section for the full re-verification process
    # required on every SHA bump.
    #
    # Three of fetchaller's 28 tools are deliberately NOT in this list:
    # - `fetch`: generic HTTP client (caller-controlled method/headers/body);
    #   call-time gated in tool-selection-guard.py's _handle_mcp_tool().
    # - `get_unifi_manual`: writes reconstructed PDF/PNG/SVG files to local
    #   disk under ~/.local/share/fetchaller/manuals/.
    # - `search_oracle_jobs`: accepts arbitrary http(s) URLs in `employer` and
    #   fetches them in discover_host() without security.ssrf.check_host().
    + _qualify(
        "plugin_fetchaller-mcp_fetchaller",
        [
            "browse_reddit",
            "get_alibaba_product",
            "get_aliexpress_product",
            "get_gcjobs_job",
            "get_gojobs_job",
            "get_indeed_job",
            "get_linkedin_job",
            "search",
            "search_alibaba",
            "search_aliexpress",
            "search_amazon_jobs",
            "search_apple_jobs",
            "search_eightfold_jobs",
            "search_gcjobs",
            "search_gojobs",
            "search_google_jobs",
            "search_indeed",
            "search_jobbank",
            "search_linkedin_jobs",
            "search_marketplace",
            "search_meta_jobs",
            "search_realtor",
            "search_reddit",
            "search_uber_jobs",
            "search_workday_jobs",
        ],
    )
    + _qualify(
        "plugin_github-mcp_github",
        [
            "actions_get",
            "actions_list",
            "get_code_scanning_alert",
            "get_commit",
            "get_copilot_job_status",
            "get_dependabot_alert",
            "get_discussion",
            "get_discussion_comments",
            "get_file_contents",
            "get_gist",
            "get_global_security_advisory",
            "get_job_logs",
            "get_label",
            "get_latest_release",
            "get_me",
            "get_notification_details",
            "get_release_by_tag",
            "get_secret_scanning_alert",
            "get_tag",
            "get_team_members",
            "get_teams",
            "github_support_docs_search",
            "issue_read",
            "list_branches",
            "list_code_scanning_alerts",
            "list_commits",
            "list_dependabot_alerts",
            "list_discussion_categories",
            "list_discussions",
            "list_gists",
            "list_global_security_advisories",
            "list_issue_types",
            "list_issues",
            "list_label",
            "list_notifications",
            "list_org_repository_security_advisories",
            "list_pull_requests",
            "list_releases",
            "list_repository_security_advisories",
            "list_secret_scanning_alerts",
            "list_tags",
            "projects_get",
            "projects_list",
            "pull_request_read",
            "run_secret_scanning",
            "search_code",
            "search_issues",
            "search_orgs",
            "search_pull_requests",
            "search_repositories",
            "search_users",
        ],
    )
)

# Serena think_about_* prefix — server-qualified
MCP_THINK_PREFIX = "serena__think_about_"


def mcp_key(tool_name: str) -> str:
    """Extract server-qualified key from full MCP tool name.

    'mcp__serena__find_symbol' -> 'serena__find_symbol'
    'mcp__plugin_github-mcp_github__actions_get' -> 'plugin_github-mcp_github__actions_get'
    """
    parts = tool_name.split("__", 2)
    if len(parts) >= 3:
        return f"{parts[1]}__{parts[2]}"
    return ""
