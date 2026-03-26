# Project Memory Reference

> **Cross-reference note:** This file is referenced by multiple skills and commands. When editing, check all consumers.
> Consumers — Skills: swarm (+ references), speculative, unfuck, map-reduce, incremental-planning, deep-research, index-repo,
> roadmap, pr-review, quality-gate, bug-investigation, file-audit. Commands: session-start, session-end, review-project.

Canonical definitions for project memory conventions. All memory-aware skills and commands in this plugin reference
this file. Do not maintain ad-hoc memory conventions elsewhere — point here.

---

## Directory Detection

Skills detect the memory directory by checking for existence in priority order. Use the first match found.

| Priority | Directory | Notes |
|----------|-----------|-------|
| 1 | `hack/` | Primary; most projects use this |
| 2 | `.local/` | Alternative for projects that prefer hidden dirs |
| 3 | `scratch/` | Alternative naming convention |
| 4 | `.dev/` | Alternative for dev-only scratch space |

**Detection rule:** Check each directory for existence in order; use the first one found. If none exist, skip memory
operations — do not create the directory. Skills that only read memory must never create the memory directory.

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

### Non-Scope

Date stamps in content (lesson dates, report headers) remain `YYYY-MM-DD`. Run IDs are for filenames and paths only.

### Backward Compatibility

Existing directories using old naming formats are left as-is. Run IDs apply to new skill invocations only.

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

1. **Symlink check:** If the memory dir exists as a symlink in the current worktree, follow it. Use the resolved path.
2. **Main worktree fallback:** If no memory dir in current worktree, locate the main worktree:
   ```bash
   git worktree list --porcelain | head -1 | sed 's/^worktree //'
   ```
   Use `{main_worktree_path}/{memory_dir}/` (e.g., `{main_worktree_path}/hack/`).
3. **No memory dir anywhere:** Skip memory operations entirely. Do not create the directory.

### Read vs. Write Behavior

| Operation | Behavior |
|-----------|---------|
| **Write** (audit trails, run outputs) | Write to resolved memory dir; run-ID subdirectory prevents contention across worktrees |
| **Read** (shared memory files) | Read from resolved main memory dir (PROJECT.md, TODO.md, etc. are shared) |
