# Project Memory Reference

> **Cross-reference note:** This file is referenced by multiple skills and commands. When editing, check all consumers.
> Consumers — Skills: swarm (+ references), speculative, unfuck, map-reduce, incremental-planning, deep-research, index-repo,
> roadmap, pr-review, plan-review, quality-gate, bug-investigation, file-audit, fix, summarize, test-plan. Commands: session-start, session-end, review-project.

Canonical definitions for project memory conventions. All memory-aware skills and commands in this plugin reference
this file. Do not maintain ad-hoc memory conventions elsewhere — point here.

---

## Directory Detection

Skills detect the memory directory using a two-stage check. Use the first directory that passes both stages.

| Priority | Directory | Notes |
|----------|-----------|-------|
| 1 | `hack/` | Primary; most projects use this |
| 2 | `.local/` | Alternative for projects that prefer hidden dirs |
| 3 | `scratch/` | Alternative naming convention |
| 4 | `.dev/` | Alternative for dev-only scratch space |

**Detection rule:** For each directory in priority order:
1. Check that the directory exists.
2. Verify it contains at least 2 of the 5 core memory files: `PROJECT.md`, `TODO.md`, `SESSIONS.md`, `NEXT.md`, `LESSONS.md`.

Use the first directory that passes both checks. If none pass, treat as "no memory directory" — skip memory operations.

> **Why bare existence is insufficient:** Many projects use `hack/` for build scripts (Go convention), `scratch/` for
> experiments, etc. Content validation prevents these false positives.

**Creation gatekeeper:** If no directory passes validation, skip memory operations. Only `session-start` and
`session-end` may create and initialize a new memory directory. All other skills must skip memory operations when
no validated directory is found.

**Worktree resolution:** See [Worktree Resolution](#worktree-resolution) for git worktree handling.

---

## Content Placement Rules

Where content belongs within a memory directory. All skills must respect these placement rules.

| Content Type | Correct File | NOT in |
|--------------|--------------|--------|
| Decisions and rationale | `PROJECT.md` | `SESSIONS.md`, `NEXT.md` |
| Architecture details | `PROJECT.md` | `SESSIONS.md` |
| Gotchas and discoveries | `PROJECT.md` | `TODO.md` |
| Future tasks | `TODO.md` | `SESSIONS.md`, `NEXT.md` |
| Session summary | `SESSIONS.md` (3–5 bullets) | — |
| Next focus | `NEXT.md` (pointer only) | — |
| Principle-level lessons | `LESSONS.md` | `PROJECT.md`, `SESSIONS.md` |

**Anti-patterns:**

- **SESSIONS.md is a log, not documentation.** If you are writing paragraphs, it belongs in `PROJECT.md`.
- **NEXT.md is a pointer, not a plan.** Reference TODO items; do not write implementation details.

---

## Run-ID Naming Convention

Run IDs uniquely identify skill invocations for audit trails, report filenames, and branch names.
They encode the current branch and a unix timestamp to be sortable, unique, and traceable.

### Format

```
<branch-slug>-<unix-timestamp>
```

Example: `feat-auth-1711388400`

### Generation (EXACT — all skills MUST use this)

```bash
BRANCH_SLUG=$(git branch --show-current | tr '[:upper:]/' '[:lower:]-' | sed 's/[^a-z0-9-]//g' | sed 's/-\{2,\}/-/g' | cut -c1-40 | sed 's/^-//;s/-$//')
BRANCH_SLUG=${BRANCH_SLUG:-detached}
TIMESTAMP=$(date +%s)
RUN_ID="${BRANCH_SLUG}-${TIMESTAMP}"
```

### Branch Slug Sanitization Rules

| Rule | Detail |
|------|--------|
| Source | `git branch --show-current` |
| Slashes | Convert to hyphens |
| Uppercase | Convert to lowercase |
| Non-alphanumeric | Strip (except hyphens) |
| Length | Truncate to 40 chars after sanitization |
| Detached HEAD / empty | Use `detached` as slug |
| Consecutive hyphens | Collapse to single hyphen |
| Leading/trailing hyphens | Strip |

### Usage Patterns

| Use case | Pattern | Example |
|----------|---------|---------|
| Audit trail directories | `{memory_dir}/{skill}/{run-id}/` | `hack/swarm/feat-auth-1711388400/` |
| Report/plan filenames | `{memory_dir}/{type}/{run-id}-<topic>.md` | `hack/plans/feat-auth-1711388400-scope.md` |
| Git branch names | `{skill}/{run-id}-<task-slug>` or `{skill}/{run-id}` | `swarm/feat-auth-1711388400-api` |
| TeamCreate team names | `{prefix}-{run-id}` | `swarm-feat-auth-1711388400` |
| Test plan documents | `{memory_dir}/test-plans/{run-id}.md` | `hack/test-plans/feat-auth-1711388400.md` |
| Staged feature files | `{memory_dir}/test-plans/{run-id}-features/` | `hack/test-plans/feat-auth-1711388400-features/` |

### Non-Scope

Date stamps in content (lesson dates, report headers) remain `YYYY-MM-DD`. Run IDs are for filenames and paths only.

### Backward Compatibility

Existing directories using old naming formats are left as-is. Run IDs apply to new skill invocations only.

---

## Agent Orchestration Pattern Selection

Skills that spawn agents must choose the right orchestration pattern. The deciding criterion is
**inter-agent communication**: do agents need to talk to each other, or do they only report results?

### Decision Tree

```
Do agents need to send structured messages to EACH OTHER (not just the lead)?
├── YES → TeamCreate with run-ID team name
└── NO
    ├── Do agents edit files that would conflict in parallel?
    │   └── YES → Agent(isolation="worktree")
    ├── Should the lead continue working while agents run?
    │   └── YES → Agent(run_in_background=true) with file-based output
    ├── Are there 3+ independent agents to run in parallel?
    │   └── YES → Multiple Agent() calls in a single message
    └── Otherwise → Single Agent() call (foreground, blocking)
```

### Pattern Reference

| Pattern | When to Use | Communication | Context Isolation |
|---------|------------|---------------|-------------------|
| **TeamCreate** | Persistent agents with bidirectional handoffs, rejection/retry loops | SendMessage between teammates | Yes (separate context per teammate) |
| **Parallel foreground** | Independent workers that report findings back | Result returns inline | Yes (separate context per agent) |
| **Background** | Fire-and-forget; lead continues other work | Agent writes to files, lead reads later | Yes |
| **Worktree isolation** | Competing implementations that edit the same files | Result returns inline + worktree path | Yes + file isolation |
| **Plain foreground** | Simple one-off tasks (1-2 agents) | Result returns inline | Yes |

### Team Naming Convention

Skills using `TeamCreate` MUST include the run ID in the team name to prevent cross-session
collisions. Team files persist at `~/.claude/teams/{team-name}/` and are home-directory-scoped
(not project-scoped), so hardcoded names collide across projects and sessions.

| Skill | Team Name Pattern |
|-------|------------------|
| `/swarm` | `swarm-{run_id}` |
| `/unfuck` | `cleanup-{run_id}` |

New skills using TeamCreate must follow the same `{prefix}-{run_id}` pattern.

---

## Memory Files

Files that may appear in a memory directory. Skills read only what they need; session-start/session-end own the write format.

### Core Files (always present in an initialized memory dir)

| File | Purpose |
|------|---------|
| `PROJECT.md` | Architecture decisions, implementation details, gotchas |
| `TODO.md` | Task list with checkboxes `- [ ] Task` / `- [x] Task (date)` |
| `SESSIONS.md` | Session log (3–5 bullets per session, newest first) |
| `NEXT.md` | Pointer to next task — one line referencing a TODO item |
| `LESSONS.md` | Principle-level lessons: `[Category] Pattern → Action → Why (date)` |

### Optional Files (skill-specific)

| File | Created by | Purpose |
|------|------------|---------|
| `BUGS.md` | bug-investigation | Active and resolved bug tracking |
| `WORK_ETHIC.md` | session-start | Agent behavior rules for this project |

---

## Worktree Resolution

Skills running inside a git worktree must resolve where to find (and write) memory files.

### Resolution Order

1. **Symlink check:** If a validated memory dir (per the [Detection Rule](#directory-detection)) exists as a symlink
   in the current worktree, follow it. Use the resolved path.
2. **Main worktree fallback:** If no validated memory dir in the current worktree, locate the main worktree:
   ```bash
   git worktree list --porcelain | head -1 | sed 's/^worktree //'
   ```
   Apply the two-stage detection rule against `{main_worktree_path}` (e.g., check `{main_worktree_path}/hack/`
   for existence + content). Use the first directory that passes.
3. **No validated memory dir anywhere:** Skip memory operations entirely. Do not create the directory.

### Read vs. Write Behavior

| Operation | Behavior |
|-----------|---------|
| **Write** (audit trails, run outputs) | Write to resolved memory dir; run-ID subdirectory prevents contention across worktrees |
| **Read** (shared memory files) | Read from resolved main memory dir (PROJECT.md, TODO.md, etc. are shared) |

---

## Archive Convention

Skills that archive completed or superseded artifacts move them to a `done/` subdirectory within the
artifact type's directory. This convention keeps active artifact scans clean while preserving history.

### Pattern

```
{memory_dir}/{artifact-type}/done/{filename-or-dirname}
```

Examples:
- `hack/plans/done/feat-auth-1711388400-scope.md`
- `hack/swarm/done/feat-fix-skill-1775231207/`
- `hack/test-plans/done/feat-auth-1711388400.md`
- `hack/unfuck/done/root-20260218/`

### Current State

- `plans/done/` — currently exists, created by `/roadmap` cleanup mode
- All other `done/` subdirectories — created on demand by `/summarize` when archiving

### Rules for Active-Artifact Scanners

Skills scanning for active artifacts **MUST** exclude `done/` subdirectories to avoid matching
archived content. Specifically:

- `/summarize` Phase 0 Path B: include `done/` artifacts but label them "(archived)" — Phase 3
  is skipped for archived artifacts (summary and audit still run); exclude `test-plans/done/` when
  scanning `{memory_dir}/test-plans/` for active test plan artifacts
- `/swarm` Phase 0: reads test plan documents referenced by plan file annotations in
  `{memory_dir}/test-plans/` (excludes `test-plans/done/`)
- `/swarm` Phase 4 Plan Adherence: when searching `{memory_dir}/plans/` for plan files matching a
  branch header, exclude `plans/done/`
- `/swarm` Phase 5.5 Plan Reconciliation: same exclusion as Plan Adherence
- `/roadmap`: already manages `plans/done/` — no changes needed

### Backup Files

`/roadmap` Update Mode creates `.pre-update` backup files in `plans/done/`
(e.g., `plans/done/feat-summarize-*.pre-update`). These are pre-edit snapshots, not summarizable
artifacts. Detection must exclude `.pre-update` files from artifact scanning.
