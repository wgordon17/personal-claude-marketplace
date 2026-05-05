# OSAC Jira Conventions

Point-in-time snapshot of OSAC project conventions (verified 2026-05-05). These are
observed patterns from live cards, not enforced schema — Jira does not prevent deviation.

## Issue Type Hierarchy

OSAC uses a two-level flat hierarchy in practice:

```
Epic
└── Story / Task / Bug
    └── Sub-task (rarely used)
```

**Issue types in OSAC:**
- **Epic** — Feature-level work items with structured description template, tracked across sprints
- **Feature** — Capability or well-defined set of functionality spanning multiple teams/releases (hierarchy level 2, above Epic)
- **Outcome** — Organizational objective focused on a measurable outcome (hierarchy level 3, above Feature)
- **Story** — Scoped implementation work under an Epic — terse prose description
- **Task** — Engineering/operational work without a user story framing
- **Bug** — Defects
- **Sub-task** — Rarely used; child of Story/Task
- **Weakness** — Product Security: known security weaknesses for remediation tracking
- **Vulnerability** — Product Security: CVE-based vulnerabilities from manifest data

**Epic Link:** Stories, Tasks, and Bugs are linked to their parent Epic via `customfield_10014` (Epic Link) set to the parent epic key in `createJiraIssue` `additional_fields`.

## Status Workflows

All issue types in OSAC share a single simplified workflow (verified 2026-05-05 via
`getTransitionsForJiraIssue` on OSAC-354, OSAC-2, OSAC-75):

```
New → Refinement → In Progress → Closed
```

| Status | statusCategory | Transition ID |
|--------|----------------|---------------|
| New | To Do | 51 |
| Refinement | To Do | 61 |
| In Progress | In Progress | 31 |
| Closed | Done | 41 |

All transitions are global — any status can transition to any other status. This is a
significant simplification from the old MGMT project which had separate workflows for
Epics (7 states), Tasks/Stories (5 states), and Bugs (Bugzilla-legacy, 8 states).

**Cross-project tip:** Use `statusCategory` in JQL for cross-project queries to avoid workflow-specific status names:
```jql
statusCategory = "To Do"
statusCategory = "In Progress"
statusCategory = Done
```

Use `getTransitionsForJiraIssue` to discover valid transitions before calling `transitionJiraIssue`.

## Description Templates

### Epic Template

Structured multi-section format. Sections in order:

| Section | Required? | Description |
|---------|-----------|-------------|
| Summary | Yes | 1-3 sentences: what it does, for whom, why. State scope boundary |
| Use Cases | Yes | Persona-prefixed bullets: "As a [Role], I want [action] so that [benefit]" |
| Capabilities | Yes | Bold sub-headers with colons grouping related bullet lists |
| Implementation Notes | Recommended | Technical design: architecture, constraints, integration points |
| Scope | Optional | In Scope / Out of Scope lists (prevents scope creep) |
| Deliverables | Optional | Concrete outputs (scripts, docs, configs) |

**Acceptable section variants observed in practice:** "Motivation" as alternative to expanded
Summary, "User Stories" as variant of "Use Cases", "Goals/Non-Goals" as variant of
"Scope/Out of Scope", "Current Pain Points" for problem statements.

```markdown
## Summary

[1-3 sentences: what the feature does, for whom, and why. Reference industry
analogues if applicable (AWS, Azure, GCP patterns). State scope boundary.]

## Use Cases

- As a [Role], I want [action] so that [benefit]
- As a [Role], I want [action]
[4-8 bullets, most common use cases first, admin/edge cases last]

## Capabilities

**[Sub-Feature A]:**

- Capability 1
- Capability 2
- Capability 3

**[Sub-Feature B]:**

- Capability 4
- Capability 5
[2-5 groups of 3-8 capabilities each]

## Implementation Notes

**[Aspect A]:**

- Technical detail 1
- Technical detail 2

**[Aspect B]:**

- Technical detail 3
[Architecture, technology choices, data model, integration points]

## Scope
*(optional — include when scope boundaries need to be explicit)*

**In Scope:**

- Item 1
- Item 2

**Out of Scope:**

- Item 1 (brief reason)
- Item 2 (brief reason)

## Deliverables
*(optional — include when concrete outputs are expected)*

- Deliverable 1
- Deliverable 2
```

**Formatting rules:**
- Bold sub-headers with colons for Capabilities and Implementation Notes groupings
- Persona-prefixed bullets for Use Cases ("As a [Role], I want...")
- 4-8 use cases per Epic, most common first
- 2-5 capability sub-feature groups, 3-8 bullets each

### Task Template

Plain prose with contextual background. Keep it concise:

```markdown
Brief description of what needs to be done and why.

Background context (1-2 sentences if needed).

Any relevant links or references.
```

### Story Template

Stories under OSAC Epics are scoped implementation units described in prose —
functionally identical to the Task template:

```markdown
[What this story covers in the context of its parent Epic].
[Scope: which repo, which components, which files].
```

### Bug Template

```markdown
## How to reproduce

1. Step 1
2. Step 2
3. Observe the issue

## Expectations

What should happen instead.

## Root cause
*(optional but encouraged)*

[Technical analysis of why the bug occurs. Name specific functions, patterns.]

## Fix
*(optional but encouraged)*

[Proposed solution approach. Reference existing patterns or PRs.]

## Component versions

- Component A: version
- Component B: version
```

Always pass `contentFormat: "markdown"` for description fields in MCP write operations.
See `jira/reference/jira-formatting.md` for markdown guidance.

## Title Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Epic | Noun phrase (feature area) | "VM Instance Types" |
| Task | Action + subject | "Fix unsafe CEL interpolation in describe command" |
| Story | Action + scope | "Rename fulfillment-cli to osac in fulfillment-service repo" |
| Bug | Problem statement | "Public API Update without FieldMask silently clears optional fields" |

## Sprint, Labels, and Defaults

### Sprint

- **Naming pattern:** `OSAC Sprint <N>` (sequential integers, e.g., `OSAC Sprint 42`)
- **Board ID:** 4269
- **Custom field:** `customfield_10020` (alias `sprint` may work in `fields` arrays; use the custom field ID in JQL for reliability)
- **Assignment:** Sprint is not available at create time. Assign post-creation via
  `editJiraIssue` with `fields: {"customfield_10020": <sprint-id>}` (raw integer).
  Discover sprint IDs by querying `sprint in openSprints()` and reading `customfield_10020`
  from the results. Omit for backlog items.

### Labels

| Label | Usage |
|-------|-------|
| `OSAC` | Standard — apply to all OSAC issues |
| `gori-ga` | GORI GA feature track |
| `vmaas` | VmaaS feature track |
| `vmaas-gori` | Combined VmaaS+GORI feature track |
| `mvp` | MVP milestone tracking |
| `backlog` | Backlog items (lower-priority) |
| `demo-summit` | Summit demo milestone |

Always include the `OSAC` label on creation.

### Field Defaults

Default field usage for OSAC issues. Most fields below are not set unless specifically
requested; exceptions noted in the table.

| Field | Status |
|-------|--------|
| Priority | Not set — all issues show "Undefined" |
| fixVersions | Not used |
| Story Points | Not used (`customfield_10028` present but empty) |
| Security Level | Not set |
| Due Date | Sometimes set — lightweight, not enforced |

### Self-Assignment Rule

Always assign newly created OSAC issues to the current user via `assignee_account_id`
(account ID captured from `atlassianUserInfo` at session start). Never create unassigned cards.

| Scenario | Risk |
|----------|------|
| Unassigned card in backlog | Another developer picks it up, duplicating in-progress work |
| Unassigned card in sprint | Team sees unclaimed work, starts working on it independently |
| Assigned to Project Lead (Jira default) | Wrong owner — lead gets noise, actual worker has no card |

The `assignee` field is the ownership signal in Jira — it tells the team who is actively
responsible for the work. The `reporter` field (set automatically to the API caller) only
indicates who created the card, not who is working on it.

## Custom Field IDs

These IDs are point-in-time snapshots (verified 2026-05-05). Use `getJiraIssueTypeMetaWithFields`
to discover additional custom fields or verify IDs at runtime — Jira admins can remap custom
fields server-side.

| Field | Custom Field ID | Notes |
|-------|-----------------|-------|
| Epic Link | `customfield_10014` | Set in `createJiraIssue` `additional_fields` |
| Epic Name (Epic type only) | `customfield_10011` | Set in `createJiraIssue` `additional_fields` |
| Sprint | `customfield_10020` | Sprint assignment |
| Story Points | `customfield_10028` | Present but unused in OSAC |
| Blocked | `customfield_10517` | Select: True/False (default: False) |
| Blocked Reason | `customfield_10483` | Text field, has default |
| Severity | `customfield_10840` | Select: Critical/Important/Moderate/Low/Informational |
| Release Blocker | `customfield_10847` | Select: Approved/Proposed/Rejected |

## OSAC Project Coordinates

| Coordinate | Value |
|-----------|-------|
| Project key | `OSAC` |
| Project name | Open Sovereign AI Cloud Platform |
| Board ID | `4269` |
| Instance | `redhat.atlassian.net` |
