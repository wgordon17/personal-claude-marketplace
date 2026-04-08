# JQL Reference

JQL (Jira Query Language) syntax reference for redhat.atlassian.net. Adapted from HCM JQL documentation with OSAC-specific patterns and Cloud-only function notes.

## Syntax Overview

```
field OPERATOR value [AND/OR field OPERATOR value] [ORDER BY field [ASC|DESC]]
```

Example:
```jql
assignee = currentUser() AND status != Closed ORDER BY priority DESC
```

## Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `=` | Equals | `status = 'In Progress'` |
| `!=` | Not equals | `status != Closed` |
| `>` | Greater than | `created > -7d` |
| `<` | Less than | `duedate < now()` |
| `>=` | Greater or equal | `priority >= Major` |
| `<=` | Less or equal | `updated <= -1d` |
| `~` | Contains text | `summary ~ 'authentication'` |
| `!~` | Does not contain | `summary !~ 'test'` |
| `IN` | In list | `status IN (Backlog, 'To Do')` |
| `NOT IN` | Not in list | `priority NOT IN (Minor, Trivial)` |
| `IS EMPTY` | Field is empty | `assignee IS EMPTY` |
| `IS NOT EMPTY` | Field has value | `duedate IS NOT EMPTY` |
| `WAS` | Historical state | `status WAS 'In Progress'` |
| `CHANGED` | Field changed | `status CHANGED` |

**Note:** JQL does not support `NOT` as a standalone logical operator. To negate, use `!=`, `NOT IN`, `IS NOT EMPTY`, etc.

## Logical Operators

- `AND` — both conditions must be true
- `OR` — either condition must be true
- Parentheses `()` for grouping

```jql
(assignee = currentUser() OR reporter = currentUser()) AND status != Closed
```

## ORDER BY

```jql
ORDER BY field [ASC|DESC]
ORDER BY field1 ASC, field2 DESC
```

Common orderings:
- `ORDER BY created DESC` — newest first
- `ORDER BY priority DESC, updated DESC` — by priority, then recent
- `ORDER BY updated DESC` — most recently changed first

## Common Fields

### Issue Identification

- `key` — Issue key (e.g., `MGMT-12345`)
- `id` — Internal issue ID
- `issuetype` (or `type`) — Epic, Story, Task, Bug, Sub-task
- `project` — Project key (e.g., `MGMT`, `OCM`, `ROSA`)

### People

- `assignee` — Assigned user
- `reporter` — User who created the issue
- `creator` — User who created the issue
- `watcher` — Users watching the issue

### Status and Workflow

- `status` — Current workflow status (e.g., `To Do`, `In Progress`, `Closed`, `ASSIGNED`)
- `statusCategory` — `"To Do"`, `"In Progress"`, `Done` (cross-project safe)
- `resolution` — Done, Won't Do, etc.

### Dates

- `created` — When issue was created
- `updated` — Last update time
- `resolved` — When issue was resolved
- `duedate` — Due date (not used in OSAC)

### Content

- `summary` — Issue title
- `description` — Issue description body
- `comment` — Issue comments
- `labels` — Issue labels (e.g., `OSAC`, `gori-ga`)
- `component` — Components (e.g., `OSAC`)
- `text` — Searches both summary and description

### Links and Hierarchy

- `parent` — Parent issue key (for sub-tasks)
- `"Epic Link"` — Epic the issue belongs to (requires quotes)
- `issueLink` — Linked issues

## MGMT Custom Fields

These IDs are point-in-time snapshots (verified 2026-04-08). Use `getJiraIssueTypeMetaWithFields` to discover additional custom fields or verify IDs at runtime — Jira admins can remap custom fields server-side.

| Field | Custom Field ID | JQL Usage |
|-------|-----------------|-----------|
| Epic Link | `customfield_10014` | `"Epic Link" = MGMT-12345` |
| Sprint | `customfield_10020` | `sprint in openSprints()` or `customfield_10020 = "OSAC Sprint 42"` |
| Story Points | `customfield_10016` | `customfield_10016 IS EMPTY` (unused in OSAC) |

**Note:** The `sprint` alias may work in `fields` parameter arrays; use the custom field ID or the `sprint` function in JQL filters for reliability.

## JQL Functions (Atlassian Cloud)

These functions are available on Atlassian Cloud (redhat.atlassian.net):

### User Functions

- `currentUser()` — Current logged-in user (prefer this over literal usernames)
- `membersOf("group-name")` — Members of a group

### Date Functions

- `now()` — Current date/time
- `startOfDay()`, `endOfDay()` — Start/end of today
- `startOfWeek()`, `endOfWeek()` — Start/end of current week
- `startOfMonth()`, `endOfMonth()` — Start/end of current month
- `startOfYear()`, `endOfYear()` — Start/end of current year

**Date literals:** `-1d` (1 day ago), `-2w` (2 weeks ago), `-3m` (3 months ago), `+1d` (1 day from now)

### Sprint Functions

- `openSprints()` — All currently active sprints
- `closedSprints()` — All completed sprints

### Project Functions

- `currentProject()` — Current project context

## ScriptRunner Functions — NOT Available on Cloud

The following functions existed on Jira Server but are **absent on Atlassian Cloud**. Do not use them — they return zero results or errors:

- `issueFieldMatch()` — field comparison
- `subtasksOf()` — subtask traversal
- `linkedIssuesOf()` / `linkedIssuesOfRecursive()` — link traversal
- `hasComments()` — comment presence
- `dateCompare()` — date field comparison
- `epicsOf()` / `issuesInEpics()` — epic hierarchy
- `portfolioChildrenOf()` / `portfolioParentsOf()` — Advanced Roadmaps hierarchy

For link traversal on Cloud, use `getJiraIssueRemoteIssueLinks`, `createIssueLink`, and `getIssueLinkTypes` MCP tools instead.

## OSAC Query Patterns

Pre-built JQL scoped to the OSAC component in MGMT project:

### My Open OSAC Work

```jql
project = MGMT AND component = OSAC AND assignee = currentUser() AND statusCategory != Done
```

### Current Sprint

```jql
project = MGMT AND component = OSAC AND sprint in openSprints()
```

### OSAC Backlog

```jql
project = MGMT AND component = OSAC AND statusCategory = "To Do"
```

### OSAC Epics

```jql
project = MGMT AND component = OSAC AND type = Epic AND statusCategory != Done ORDER BY status ASC
```

### Recently Updated

```jql
project = MGMT AND component = OSAC AND updated >= -7d ORDER BY updated DESC
```

### Issues Under an Epic

```jql
"Epic Link" = MGMT-12345 AND statusCategory != Done
```

### By Label Track

```jql
project = MGMT AND component = OSAC AND labels = "gori-ga" AND statusCategory != Done
```

## Query Construction Tips

1. **Start simple, then refine** — add filters incrementally
2. **Use `currentUser()`** — makes queries portable across users
3. **Quote field names with spaces** — `"Epic Link"`, `"Story Points"`
4. **Quote status values with spaces** — `status = 'In Progress'`
5. **Use parentheses for OR groups** — `(A OR B) AND C`
6. **Use `statusCategory` for cross-project queries** — avoid workflow-specific status names
7. **Test in Jira UI** — validate at https://redhat.atlassian.net/jira before coding into plans
8. **Prefer typed MCP tools** — use `searchJiraIssuesUsingJql` over `searchAtlassian` for Jira-only queries

## Troubleshooting

**No results:**
- Verify JQL at https://redhat.atlassian.net/jira
- Check project access permissions
- Ensure quotes around multi-word values: `status = 'In Progress'`
- Confirm the query does not use ScriptRunner functions (they return 0 on Cloud)

**Invalid syntax:**
- Quote field names with spaces: `"Epic Link"`, `"activity-type"`
- Check operator (e.g., `IS EMPTY` not `= EMPTY`)
- Avoid `NOT` as standalone logical operator

**Performance:**
- Always scope with `project =` filter
- Avoid broad `text ~` searches on large projects
- Use specific fields instead of `text ~`
