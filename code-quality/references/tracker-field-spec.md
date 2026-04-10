# Tracker Field Specification

Shared specification for the `**Tracker:**` plan file header field. Referenced by
`/incremental-planning` (Phase 2 question, Phase 4 header, Phase 6 issue creation),
`/swarm` (Phase 0 extraction, Phase 7 completion), and `git-instructions.sh` (PR body
issue linking).

Centralizing here ensures all consumers use consistent parsing, validation, and state
transitions.

## Field Values

| Value | Meaning | Written by | Resolved by |
|-------|---------|------------|-------------|
| `github:pending` | Create new GH issue | incremental-planning Phase 4 | incremental-planning Phase 6 |
| `github:linked#N` | Link existing GH issue (pre-repo-detection) | incremental-planning Phase 4 | incremental-planning Phase 6 |
| `github:owner/repo#N` | Fully resolved GH issue | incremental-planning Phase 6 | — (terminal state) |
| `jira:pending` | Create new Jira card | incremental-planning Phase 4 | incremental-planning Phase 6 |
| `jira:PROJ-N` | Linked existing or created Jira card | incremental-planning Phase 4/6 | — (terminal state) |
| `none` | No external issue tracking | incremental-planning Phase 4 | — (terminal state) |

## Parsing Spec

The GitHub tracker encodes the full `owner/repo#N` so downstream consumers can parse the
repo directly without re-detecting from git remotes.

**Extraction from `github:owner/repo#N`:**
- `owner/repo` = everything between `github:` and `#`
- Issue number `N` = digits after `#`

## Validation Regex

Before interpolating parsed values into `gh` CLI commands, validate:

| Component | Pattern | Action on failure |
|-----------|---------|-------------------|
| `owner/repo` | `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$` | Skip issue operation entirely |
| Issue number `N` | `^[0-9]+$` | Skip issue operation entirely |

## Finalization Constraint

The `**Tracker:**` field must reach a terminal state (`github:owner/repo#N`, `jira:PROJ-N`,
or `none`) before `/swarm` is invoked. No `pending` or `linked#N` states may remain.
Phase 6 issue creation must complete — including error handling resolution — before the
plan is handed off to /swarm.

Do NOT modify the `**Tracker:**` field once a swarm is active, as swarm Phase 5.5's
plan file updater may be concurrently modifying other fields in the same file.

## PR Body Linking

When creating a PR on a branch with a plan file containing a `**Tracker:**` field:

| Tracker value | PR body action |
|---------------|----------------|
| `github:owner/repo#N` | Add `Closes #N` on a new line after the Summary section |
| `jira:PROJ-N` | Add `Jira: PROJ-N` in the PR body; remind to transition the card after merge |
| `none` or absent | Skip issue linking entirely |

Use `--repo <owner/repo>` parsed from the Tracker field value (not from git remotes) for
any `gh issue edit` commands (label add/remove). If the plan file's `**Branch:**` field is
`not yet created`, update it with the current branch name before creating the PR.

## Lifecycle

1. **Plan time (Phase 2):** User selects tracker option via AskUserQuestion
2. **Plan write (Phase 4):** `**Tracker:**` header written with initial state
3. **Plan complete (Phase 6):** Issue created/linked, field resolved to terminal state
4. **Swarm completion (Phase 7):** `in-progress` label added (GH) or card transitioned
   to In Progress (Jira) — both trackers apply their "active" status at completion, not
   at plan time
5. **PR merge:** `Closes #N` auto-closes GH issue; `in-progress` label removed manually;
   Jira card transitioned post-merge
