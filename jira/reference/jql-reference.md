# JQL Reference

JQL (Jira Query Language) syntax reference for redhat.atlassian.net. Covers operators,
functions, MGMT custom fields, and OSAC-specific query patterns.

**Official Atlassian documentation:**
- [JQL operators](https://support.atlassian.com/jira-software-cloud/docs/jql-operators/)
- [JQL functions](https://support.atlassian.com/jira-software-cloud/docs/jql-functions/)
- [JQL fields](https://support.atlassian.com/jira-software-cloud/docs/jql-fields/)
- [Advanced search overview](https://support.atlassian.com/jira-software-cloud/docs/what-is-advanced-search-in-jira-cloud/)

When a specific JQL syntax isn't covered here, consult the official docs above.

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
| `WAS NOT` | Was never in state | `status WAS NOT Closed` |
| `WAS IN` | Was in any of list | `status WAS IN ('In Progress', 'To Do')` |
| `WAS NOT IN` | Was never in list | `status WAS NOT IN (Closed, Done)` |
| `CHANGED` | Field changed | `status CHANGED` |

**Note:** JQL does not support `NOT` as a standalone logical operator. To negate, use `!=`, `NOT IN`, `IS NOT EMPTY`, etc.

### History Operator Predicates

The `WAS`, `WAS NOT`, `WAS IN`, `WAS NOT IN`, and `CHANGED` operators support optional predicates to narrow history searches. These only work with: Assignee, Fix Version, Priority, Reporter, Resolution, and Status fields.

| Predicate | Purpose | Example |
|-----------|---------|---------|
| `AFTER` | Changes after a date | `status CHANGED AFTER -7d` |
| `BEFORE` | Changes before a date | `status CHANGED BEFORE "2026-01-01"` |
| `BY` | Changes by a user | `status CHANGED BY currentUser()` |
| `ON` | Changes on a date | `status CHANGED ON "2026-04-01"` |
| `FROM` | Changed from value | `status CHANGED FROM 'To Do' TO 'In Progress'` |
| `TO` | Changed to value | `status WAS 'In Progress' BEFORE "2026-01-01"` |

Predicates can be combined: `status CHANGED FROM 'To Do' TO 'In Progress' BY currentUser() AFTER -30d`

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
| Story Points | `customfield_10028` | `customfield_10028 IS EMPTY` (unused in OSAC) |

**Note:** The `sprint` alias may work in `fields` parameter arrays; use the custom field ID or the `sprint` function in JQL filters for reliability.

## JQL Functions (Atlassian Cloud)

Built-in functions on Atlassian Cloud (redhat.atlassian.net). For the complete list with
syntax details, see the [official JQL functions reference](https://support.atlassian.com/jira-software-cloud/docs/jql-functions/).

### User Functions

- `currentUser()` — Current logged-in user (prefer over literal usernames)
- `membersOf("group-name")` — Members of a group
- `currentLogin()` — Time the current user's session began
- `lastLogin()` — Time of the user's previous login

### Date Functions

- `now()` — Current date/time
- `startOfDay([offset])`, `endOfDay([offset])` — Day boundaries (offset: `startOfDay(-1d)`)
- `startOfWeek([offset])`, `endOfWeek([offset])` — Week boundaries
- `startOfMonth([offset])`, `endOfMonth([offset])` — Month boundaries
- `startOfYear([offset])`, `endOfYear([offset])` — Year boundaries

**Date literals:** `-1d` (1 day ago), `-2w` (2 weeks ago), `-3m` (3 months ago), `+1d` (1 day from now)

### Sprint Functions

- `openSprints()` — Currently active sprints
- `closedSprints()` — Completed sprints
- `futureSprints()` — Sprints not yet started

### Issue Functions

- `linkedIssues(issueKey[, linkType])` — Issues linked to a specific issue
- `watchedIssues()` — Issues watched by the current user
- `votedIssues()` — Issues voted for by the current user
- `issueHistory()` — Issues from the user's recent history
- `standardIssueTypes()` — All non-sub-task issue types
- `subtaskIssueTypes()` — All sub-task issue types

### Version Functions

- `earliestUnreleasedVersion(project)` — Earliest unreleased version
- `latestReleasedVersion(project)` — Most recently released version
- `releasedVersions([project])` — All released versions
- `unreleasedVersions([project])` — All unreleased versions

### Project Functions

- `projectsLeadByUser([user])` — Projects led by a user (defaults to current user)
- `projectsWhereUserHasPermission("permission")` — Projects where user has a permission
- `projectsWhereUserHasRole("role")` — Projects where user has a role

### Other Functions

- `cascadeOption(parentValue[, childValue])` — Cascading select custom field matching

## Discovering Custom JQL Functions

Marketplace apps installed on redhat.atlassian.net may provide additional JQL functions.
To discover all available functions (built-in + app-provided), use the JQL autocomplete
endpoint via the `fetch` tool:

```
fetch with ARI: ari:cloud:jira::site/<cloudId>
GET /rest/api/3/jql/autocompletedata
```

The response includes a `jqlReservedWords` array and `visibleFunctionNames` listing all
available JQL functions including any from installed apps.

## ScriptRunner Functions — NOT Available on Cloud

The following functions existed on Jira Server but are **absent on Atlassian Cloud**. Do not use them — they return zero results or errors:

- `issueFieldMatch()` — field comparison
- `subtasksOf()` — subtask traversal
- `linkedIssuesOf()` / `linkedIssuesOfRecursive()` — link traversal
- `hasComments()` — comment presence
- `dateCompare()` — date field comparison
- `epicsOf()` / `issuesInEpics()` — epic hierarchy
- `portfolioChildrenOf()` / `portfolioParentsOf()` — Advanced Roadmaps hierarchy

For link traversal on Cloud, use `linkedIssues()` function in JQL, or `getJiraIssueRemoteIssueLinks` and `getIssueLinkTypes` MCP tools for programmatic access.

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
8. **Prefer typed MCP tools** — use `searchJiraIssuesUsingJql` over `search` for Jira-only queries

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
