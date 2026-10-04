# Project Memory Reference

> **Cross-reference note:** This file is referenced by multiple skills and commands. When editing, check all consumers.
> Consumers — Skills: swarm (+ references), speculative, unfuck, map-reduce, incremental-planning, deep-research, index-repo,
> roadmap, pr-review, plan-review, quality-gate, bug-investigation, file-audit, fix, summarize, test-plan. Commands: session-start, session-end, review-project.

Canonical definitions for project memory conventions. All memory-aware skills and commands in this plugin reference
this file. Do not maintain ad-hoc memory conventions elsewhere — point here.

---

## Directory Detection

Skills detect the memory directory using a unified algorithm that is completely **worktree-aware**. Because project memory directories (like `hack/`) are typically git-ignored, they do not automatically clone or link into new git worktrees. To prevent duplicating memory directories across worktrees, all skills MUST resolve the memory directory against the **main** worktree.

### Detection Algorithm (All skills MUST use this)

1. **Locate the main worktree:**
   Run the following command to find the absolute path to the main repository worktree:
   ```bash
   git worktree list --porcelain | head -1 | sed 's/^worktree //'
   ```
   *(If the command fails or you are not in a git repository, fall back to the current directory).*
   Store this as `{main_worktree_path}`.

2. **Check candidate directories:**
   For each candidate in priority order (`hack/`, `.local/`, `scratch/`, `.dev/`), check if it exists within the main worktree (e.g., `{main_worktree_path}/hack/`).

3. **Validate contents:**
   Verify the candidate directory contains at least 2 of the 5 core memory files: `PROJECT.md`, `TODO.md`, `SESSIONS.md`, `NEXT.md`, `LESSONS.md`.
   *(Why? Many projects use `hack/` for build scripts. Content validation prevents false positives).*

**Result:** Use the first directory that passes both existence and content validation. This absolute path becomes your `{memory_dir}` for ALL reads and writes.

**Creation gatekeeper:** If no directory passes validation, skip memory operations entirely. Only the `/session-start` and `/session-end` commands are authorized to initialize a new memory directory. When they do, they MUST create it in the `{main_worktree_path}` so it is shared globally across all worktrees.

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
├── YES → Spawn named teammates via Agent(name="...") — implicit team, no setup call needed
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
| **Named teammates** | Persistent agents with bidirectional handoffs, rejection/retry loops | SendMessage between teammates | Yes (separate context per teammate) |
| **Parallel foreground** | Independent workers that report findings back | Result returns inline | Yes (separate context per agent) |
| **Background** | Fire-and-forget; lead continues other work | Agent writes to files, lead reads later | Yes |
| **Worktree isolation** | Competing implementations that edit the same files | Result returns inline + worktree path | Yes + file isolation |
| **Plain foreground** | Simple one-off tasks (1-2 agents) | Result returns inline | Yes |

### Teammate Naming Convention

As of Claude Code v2.1.178, `TeamCreate`/`TeamDelete` were removed — every session has one
implicit team, and named teammates are spawned directly via `Agent(name="...")`. Teammate names
are scoped to the caller's own session rather than a shared home-directory-scoped registry, so
the collision risk that motivated run-ID-suffixed team names under the old model no longer
applies. Use plain role names for teammates (`"architect"`, `"team-lead"`, `"implementer"`) —
no run-ID suffix needed.

| Skill | Teammate Naming |
|-------|------------------|
| `/swarm` | Plain role names (`architect`, `implementer`, `reviewer`, etc.) |
| `/unfuck` | Plain role names (`dead-code-hunter`, `security-auditor`, etc.) |

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

### Checkpoint Files

Created by the swarm skill at each PR boundary during an incremental workflow run.

**Location:** `{memory_dir}/swarm/{run-id}/checkpoint.json`

**Schema:**
```json
{
  "plan_file": "{memory_dir}/plans/plan.md",
  "workflow": "incremental",
  "run_dir": "{memory_dir}/swarm/{run-id}/",
  "completed_prs": [
    {"pr_number": 42, "branch": "feat/plan-slug-pr1", "tasks": [1, 2, 3], "merged": true}
  ],
  "current_pr": 2,
  "tasks_remaining": [4, 5, 6],
  "branch_base": "main",
  "architect_plan": "{run_dir}/architect-plan.json",
  "context_summary": "brief summary of work completed so far"
}
```

**Field notes:**
- `current_pr` represents the next PR boundary to process on resume, not the one currently being worked on.
- The `branch` field in `completed_prs` entries stores the branch name used for that boundary's PR. The `merged` field is updated by the resume flow when verifying PR merge status.

**Lifecycle:** Created by swarm at each PR boundary stop. Read by swarm on resume to restore state. Deleted at final swarm completion (after Phase 7), not at the start of the final boundary's run.

For `run-id` format, see [Run-ID Naming Convention](#run-id-naming-convention).

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
