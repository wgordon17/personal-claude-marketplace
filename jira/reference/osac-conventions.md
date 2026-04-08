# OSAC Jira Conventions

Point-in-time snapshot of OSAC/MGMT project conventions (verified 2026-04-08). These are
observed patterns from live cards, not enforced schema — Jira does not prevent deviation.

## Issue Type Hierarchy

OSAC uses a two-level flat hierarchy in practice:

```
Epic
└── Story / Task / Bug
    └── Sub-task (rarely used)
```

**Issue types used in MGMT/OSAC:**
- **Epic** — Feature-level work items, tracked across sprints
- **Story** — User-facing feature work with acceptance criteria
- **Task** — Engineering/operational work without a user story framing
- **Bug** — Defects; uses the Bugzilla-legacy workflow (see Status Workflows)
- **Sub-task** — Rarely used; child of Story/Task

**Epic Link:** Stories, Tasks, and Bugs are linked to their parent Epic via `customfield_10014` (`"Epic Link"`). Set this field when creating issues under an existing epic.

## Status Workflows

Two distinct workflows are observed in MGMT/OSAC:

### Standard Workflow (Epic, Story, Task, Sub-task)

```
New → Planning → To Do → In Progress → Closed
```

| Status | statusCategory |
|--------|----------------|
| New | To Do |
| Planning | To Do |
| To Do | To Do |
| In Progress | In Progress |
| Closed | Done |

### Bug Workflow (Bugzilla-legacy)

```
ASSIGNED → MODIFIED → Closed
```

| Status | statusCategory |
|--------|----------------|
| ASSIGNED | In Progress |
| MODIFIED | In Progress |
| Closed | Done |

**Cross-project tip:** Use `statusCategory` in JQL for cross-project queries to avoid workflow-specific status names:
```jql
statusCategory = "To Do"
statusCategory = "In Progress"
statusCategory = Done
```

Always validate available transitions with `getTransitionsForJiraIssue` before calling `transitionJiraIssue` — not all transitions are available from every state.

## Description Templates

### Task Template

Plain prose with contextual background. Keep it concise:

```markdown
Brief description of what needs to be done and why.

Background context (1-2 sentences if needed).

Any relevant links or references.
```

### Story Template

Structured with acceptance criteria:

```markdown
Brief description of the user-facing feature or capability.

## Acceptance Criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Phase

Phase N: Description

## Requirements

- Requirement 1
- Requirement 2
```

### Bug Template

```markdown
## How to reproduce

1. Step 1
2. Step 2
3. Observe the issue

## Expectations

What should happen instead.

## Component versions

- Component A: version
- Component B: version
```

Always use `contentFormat: "markdown"` when writing descriptions via MCP tools. The server converts markdown to Atlassian Document Format (ADF). See `jira/reference/jira-formatting.md` for markdown guidance.

## Sprint, Labels, and Defaults

### Sprint

- **Naming pattern:** `OSAC Sprint <N>` (sequential integers, e.g., `OSAC Sprint 42`)
- **Board ID:** 4269
- **Custom field:** `customfield_10020` (alias `sprint` may work in `fields` arrays; use the custom field ID in JQL for reliability)
- **Assignment:** Set sprint on creation for in-sprint work; omit for backlog items

### Labels

| Label | Usage |
|-------|-------|
| `OSAC` | Standard — apply to all OSAC issues |
| `gori-ga` | GORI GA feature track |
| `vmaas` | VmaaS feature track |
| `vmaas-gori` | Combined VmaaS+GORI feature track |

Always include the `OSAC` label on creation.

### Fields NOT used in OSAC

| Field | Status |
|-------|--------|
| Priority | Not set — all issues show "Undefined" |
| fixVersions | Not used |
| Story Points | Not used (`customfield_10016` present but empty) |
| Security Level | Not set |
| Due Date | Not used |

Do not set these fields when creating OSAC issues unless specifically requested.

## Active Epics

Point-in-time snapshot may be stale. Run this JQL to get current epics after plugin installation:

```jql
project = MGMT AND component = OSAC AND type = Epic AND statusCategory != Done ORDER BY status ASC
```

| Key | Summary | Status | Owner | Labels |
|-----|---------|--------|-------|--------|
| (run JQL above after installing the jira plugin and authenticating via /mcp) | | | | |

After authenticating (`/mcp` → auth `mcp-atlassian-prod`), run the JQL above via `/jira:jira` to populate this table.

## Custom Field IDs

These IDs are point-in-time snapshots (verified 2026-04-08). Use `getJiraIssueTypeMetaWithFields` to discover additional custom fields or verify IDs at runtime — Jira admins can remap custom fields server-side.

| Field | Custom Field ID | Notes |
|-------|-----------------|-------|
| Epic Link | `customfield_10014` | Links issues to their parent Epic |
| Sprint | `customfield_10020` | Sprint assignment |
| Story Points | `customfield_10016` | Present but unused in OSAC |

## MGMT Project Coordinates

| Coordinate | Value |
|-----------|-------|
| Project key | `MGMT` |
| Component | `OSAC` |
| Board ID | `4269` |
| Instance | `redhat.atlassian.net` |
