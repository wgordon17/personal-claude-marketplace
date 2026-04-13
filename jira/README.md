# jira

Jira integration plugin for Claude Code via the `jira` CLI. MCP server access (Atlassian Rovo)
is temporarily disabled — revert this commit to restore it.

## Prerequisites

1. Install jira CLI: `brew install jira-cli`
2. Run `jira init` to configure (server: `https://redhat.atlassian.net`, project: `MGMT`)
3. Set up API token:
   - Generate at https://id.atlassian.com/manage-profile/security/api-tokens
   - Export `JIRA_API_TOKEN` in your shell environment
   - Export `JIRA_AUTH_TYPE=basic` (for Personal Access Tokens) or `JIRA_AUTH_TYPE=bearer` (for OAuth tokens)
4. Verify: `jira issue list -q "project = MGMT" --plain --paginate 0:1`

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

## Write Command Approval

CLI write commands prompt for permission on each use unless you add them to
`~/.claude/settings.local.json`. To auto-approve, add to your existing `permissions.allow` array:

```json
{
  "permissions": {
    "allow": [
      "Bash(jira issue create:*)",
      "Bash(jira issue edit:*)",
      "Bash(jira issue move:*)",
      "Bash(jira issue comment add:*)",
      "Bash(jira issue worklog add:*)",
      "Bash(jira issue link:*)",
      "Bash(jira epic create:*)",
      "Bash(jira epic add:*)",
      "Bash(jira sprint add:*)"
    ]
  }
}
```

For read operations, also consider adding:

```json
"Bash(jira issue list:*)",
"Bash(jira issue view:*)",
"Bash(jira project:*)"
```

## Restoring MCP Access

To restore Atlassian Rovo MCP access, revert this commit:

```bash
git revert <commit-hash>
```

Then re-authenticate via `/mcp` in Claude Code.

## Installation

```bash
claude plugin install jira@personal-claude-marketplace
```
