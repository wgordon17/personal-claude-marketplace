# jira

Jira integration plugin for Claude Code. Provides issue tracking, querying, and management for redhat.atlassian.net, with OSAC-focused defaults and full cross-project capability.

## Interfaces

### Interactive: `/jira:jira`

Invoke the `/jira:jira` skill for interactive Jira work:
- Query issues, epics, and sprints
- Create and update issues
- Transition workflow status
- Add comments and worklogs

Defaults to OSAC scope (project=MGMT, component=OSAC). Drop the filter when asking about other projects.

### Programmatic: `jira:jira-agent`

Spawn the `jira:jira-agent` agent for background Jira operations from plan tasks, swarm implementers, or quality-gate verifiers:

```
Spawn jira:jira-agent to create a MGMT task for X under epic MGMT-YYYYY
```

The agent carries full OSAC conventions and returns the created issue URL.

## MCP Server Setup

This plugin uses the Atlassian Rovo MCP server (`mcp-atlassian-prod`).

**First-time setup:**
1. Run `/mcp` in Claude Code
2. Authenticate the `mcp-atlassian-prod` server via Atlassian Rovo OAuth
3. Complete the OAuth flow in your browser

**Important:** OAuth credentials are keyed by plugin name. Even if you previously authenticated via `hcm-jira-administrator-agent`, you must re-authenticate for the `jira` plugin — it uses a separate credential entry (`plugin:jira:mcp-atlassian-prod`).

## Capabilities

| Category | Operations |
|----------|-----------|
| **Query** | JQL search, issue fetch, epic/sprint queries, cross-project search |
| **Create** | New issues (Task, Story, Bug, Epic, Sub-task) with OSAC defaults |
| **Update** | Field edits, status transitions, comment, worklog |
| **Link** | Issue linking with configurable link types |
| **Discover** | Project metadata, issue type fields, workflow transitions |

## Write Tool Approval

The dev-guard auto-approves Jira READ-ONLY tools. Write tools prompt for permission on each use unless you add them to `~/.claude/settings.local.json`.

To auto-approve write operations, add to your existing `permissions.allow` array:

```json
{
  "permissions": {
    "allow": [
      "mcp__plugin_jira_mcp-atlassian-prod__editJiraIssue",
      "mcp__plugin_jira_mcp-atlassian-prod__createJiraIssue",
      "mcp__plugin_jira_mcp-atlassian-prod__transitionJiraIssue",
      "mcp__plugin_jira_mcp-atlassian-prod__addCommentToJiraIssue",
      "mcp__plugin_jira_mcp-atlassian-prod__createIssueLink",
      "mcp__plugin_jira_mcp-atlassian-prod__addWorklogToJiraIssue"
    ]
  }
}
```

The most commonly needed are `editJiraIssue`, `createJiraIssue`, `transitionJiraIssue`, and `addCommentToJiraIssue`. Add individual entries as needed — you need not auto-approve all six.

## Installation

```bash
claude plugin install jira@personal-claude-marketplace
```
