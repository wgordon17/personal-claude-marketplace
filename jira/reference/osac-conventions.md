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

**Epic Link:** Stories, Tasks, and Bugs are linked to their parent Epic via `--parent MGMT-12345`
(automatically sets customfield_10014 from `epic.link` config for classic project non-subtask types).
Do NOT use `--custom customfield_10014=...` as a workaround (would double-set the field).

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

Use `jira issue move KEY STATE` for transitions. If the state name is wrong, the CLI returns
valid transitions in the error output. No separate discovery step needed.

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

The jira CLI accepts markdown natively for `-b` body text. No format parameter needed.
See `jira/reference/jira-formatting.md` for markdown guidance.

## Sprint, Labels, and Defaults

### Sprint

- **Naming pattern:** `OSAC Sprint <N>` (sequential integers, e.g., `OSAC Sprint 42`)
- **Board ID:** 4269
- **Custom field:** `customfield_10020` (alias `sprint` may work in `fields` arrays; use the custom field ID in JQL for reliability)
- **Assignment:** Sprint requires post-creation step: `jira sprint add SPRINT_ID ISSUE-KEY`
  (no `--sprint` flag on `jira issue create`). Omit for backlog items.

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

Leave these fields unset unless specifically requested — because OSAC convention omits them by default.

### Self-Assignment Rule

Always assign newly created OSAC issues to the current user via `-a "$JIRA_LOGIN"` (login
captured from `jira me` at session start). Never create unassigned cards.

| Scenario | Risk |
|----------|------|
| Unassigned card in backlog | Another developer picks it up, duplicating in-progress work |
| Unassigned card in sprint | Team sees unclaimed work, starts working on it independently |
| Assigned to Project Lead (Jira default) | Wrong owner — lead gets noise, actual worker has no card |

The `assignee` field is the ownership signal in Jira — it tells the team who is actively
responsible for the work. The `reporter` field (set automatically to the API caller) only
indicates who created the card, not who is working on it.

## Custom Field IDs

These IDs are point-in-time snapshots (verified 2026-04-08). Use `--parent` for Epic Link on
classic project non-subtask types (automatically sets customfield_10014 via epic.link config).
Use `--custom` flag only for truly custom fields not covered by built-in flags (e.g.,
`--custom customfield_10016=5` for Story Points). No runtime field discovery available via
CLI — use the documented field IDs.

| Field | Custom Field ID | Notes |
|-------|-----------------|-------|
| Epic Link | `customfield_10014` | Use `--parent` flag instead of `--custom` |
| Sprint | `customfield_10020` | Sprint assignment |
| Story Points | `customfield_10016` | Present but unused in OSAC |

## MGMT Project Coordinates

| Coordinate | Value |
|-----------|-------|
| Project key | `MGMT` |
| Component | `OSAC` |
| Board ID | `4269` |
| Instance | `redhat.atlassian.net` |
