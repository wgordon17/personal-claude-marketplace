---
name: roadmap
description: >-
  Stateful multi-plan phase sequencing, dependency analysis, and roadmap lifecycle
  management. Detects existing roadmaps and routes to update, cleanup, status/drift,
  or fresh creation. Use when coordinating multiple implementation plans into
  parallel/sequential workstreams, or when managing an existing roadmap.
  Trigger phrases: "roadmap", "sequence plans", "coordinate multiple plans",
  "plan execution order", "roadmap status", "roadmap cleanup", "update roadmap",
  "roadmap drift", or when 2+ plans need ordering.
allowed-tools: Read Write Edit Agent AskUserQuestion Bash ToolSearch
---

# Roadmap

> **Note:** `{skill_dir}` refers to the absolute "Skill directory" path provided in your system instructions.

Stateful multi-plan phase sequencing with roadmap lifecycle management. Takes existing plan
files as input, produces a structured roadmap document consumable by `/swarm` and other
orchestrators. Detects existing roadmaps and routes to update, cleanup, status/drift, or
fresh creation.

## Activation

This skill activates when:

- User asks to "roadmap", "sequence plans", "coordinate these plans"
- User has 2+ plan files and asks about execution order
- After `/incremental-planning` generates a plan that references other planned work

**Announce at start:** "Using roadmap to sequence [N] plans into phases."

---

## Phase 0: Detect & Route

Before ingesting any plans, check whether an existing roadmap document already exists.

### Step 1: Locate plan directory

Detect the project memory directory using the convention in
`[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)` (Directory Detection section).

- **If found:** Plan directory is `{memory_dir}/plans/`
- **If none found:** **Skip Phase 0** — proceed to Phase 1. Stateful roadmap management
  requires a project-local memory directory. `~/.t3/hack/plans/` is shared across all projects
  without a repo-local memory directory and globbing it would surface cross-project roadmaps,
  leading to false positives and unintended mutations.

  **Exception:** If the user's request matches a lifecycle trigger phrase (`roadmap status`,
  `update roadmap`, `roadmap cleanup`, `roadmap drift`, or similar), do NOT silently fall
  through to Phase 1. Instead, surface via `AskUserQuestion`:
  > "Roadmap lifecycle management (update, cleanup, status/drift) requires a project-local
  > memory directory (hack/, .local/, scratch/, or .dev/). This project doesn't have one.
  > Options:
  > 1. Create a new roadmap via Phase 1 (fresh creation only — no lifecycle features)
  > 2. Abort"

### Step 2: Glob and verify

**Scope the glob strictly to the determined plan directory** — do not glob the project root
or any other location. This prevents false positives from unrelated files.

Glob for `*roadmap*.md` within the plan directory only.

For each match, verify it is an actual roadmap document by checking for **both**:
- A `# Roadmap:` header (first-level heading starting with "Roadmap:")
- A `**Source plans:**` field in the document header block

Discard any matches that lack either marker (e.g., a plan file about improving the roadmap
skill). If no verified roadmaps remain → **skip to Phase 1** (existing flow, unchanged).

### Step 3: Handle multiple roadmaps

If more than one verified roadmap is found, first ask which one to operate on:

```
AskUserQuestion: "I found [N] roadmap files in [plan-dir]:
  1. [filename-1] — [title from # Roadmap: header]
  2. [filename-2] — [title from # Roadmap: header]
Which roadmap do you want to work with?"
```

Proceed with the selected roadmap for the remainder of Phase 0.

### Step 4: Parse roadmap header

From the selected roadmap, extract:
- **Title** — from the `# Roadmap:` heading
- **Source plans** — from the `**Source plans:**` field (list of plan file paths)
- **Total phases** — from the `**Total phases:**` field

### Step 5: HITL mode menu

Present a `AskUserQuestion` with four options:

```
AskUserQuestion: "Found existing roadmap: [title]
  Source plans: [N] plans | Total phases: [M]

  What would you like to do?
  1. Update existing roadmap — re-ingest all plans (including any new ones) and regenerate
  2. Clean up completed work — archive done plans and remove merged branches
  3. Show status / drift report — see per-phase completion and drift detection (read-only)
  4. Create new roadmap (ignore existing) — start fresh with Phase 1"
```

### Routing

Based on the user's selection, route to the corresponding lifecycle mode in `[{skill_dir}/references/lifecycle-modes.md](./references/lifecycle-modes.md)` or proceed to Phase 1:

| Choice | Routes to |
|--------|-----------|
| Update existing roadmap | **Phase 0.U** in `[{skill_dir}/references/lifecycle-modes.md](./references/lifecycle-modes.md)` — re-ingest with completion awareness (preserves completed phases, backs up existing roadmap to `{plan-dir}/done/*.pre-update`, sets `**Last updated:**`) |
| Clean up completed work | **Phase 0.C** in `[{skill_dir}/references/lifecycle-modes.md](./references/lifecycle-modes.md)` — archive completed plans to `{plan-dir}/done/`, remove merged `roadmap/phase-N/` branches, and mark phases `**Status:** Completed` |
| Show status / drift report | **Phase 0.S** in `[{skill_dir}/references/lifecycle-modes.md](./references/lifecycle-modes.md)` — read-only per-phase status and drift detection (modified, new untracked, deleted plans, orphaned branches) |
| Create new roadmap | **Phase 1** — existing flow, existing roadmap is left untouched |

---

## Phase 1: Ingest Plans

Read all provided plan files and extract semi-structured data. If plan paths are not
provided, use `AskUserQuestion` to ask for them before proceeding.

### What to extract

For each plan, extract:

- **Goal** — always present as `**Goal:**` line in the plan header
- **Cynefin Domain** — always present in the plan header
- **Architecture Summary** — always present in the plan header
- **File Structure section** — tables or lists of files to create/modify

For task-level extraction:

- Use `## Task N:` heading pattern to identify tasks
- Use `**Files:**` block within each task to identify which files that task touches
- Do NOT parse intra-task dependencies from free-form markdown — cross-plan dependencies
  are inferred from file-path overlap in Phase 2

### Non-conforming plans

If a plan doesn't follow incremental-planning's format (missing `**Goal:**`, no `## Task N:`
headings, no `**Files:**` blocks), use `AskUserQuestion` to surface what's missing. Offer
best-effort extraction or skip. Do not silently proceed.

### BUGS.md Cross-Reference

After all plans are ingested and conformance-checked, check if any address open bugs.
Detect or reuse the memory directory (using the convention in
`[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)`). If Phase 0 was skipped (no
project-local memory directory exists), skip this check entirely — proceed to
`### Chat output`.

1. Check if `{memory_dir}/BUGS.md` exists
2. If it exists, read it and extract all entries with `**Status:**` of `Investigating`,
   `Root Cause Found`, or `Fix Ready`
3. Build an aggregated file set: collect all file paths from every ingested plan's
   `## File Structure` section (tables and lists) and `**Files:**` blocks within tasks,
   deduplicate into a single working set. Then, for each open bug entry that has a
   `### Files Involved` section AND whose `**Tracked In:**` is currently `—` (or the
   field is missing entirely — backward compatibility), compare those paths against
   this aggregated file set. Strip `:NN` line number suffixes from BUGS.md paths before
   comparing (e.g., `src/auth/login.ts:42` becomes `src/auth/login.ts`). Match using
   this precedence: exact file match (strongest signal); or common directory prefix of
   at least 3 components (e.g., `code-quality/skills/fix/`) — prefixes
   of 1 or 2 components are too broad and do not count. Skip entries without
   `### Files Involved` (investigation may not be complete) and entries with existing
   Tracked In values.
4. If any overlap is found, hold the matching BUG IDs and their overlapping plan names
   in working memory for Phase 4's Tracked In update step
   (do NOT update `**Tracked In:**` yet — phase grouping is not determined until Phase 3)

If no BUGS.md exists, no untracked entries, or no overlaps are found, skip silently.

If overlaps ARE found, add a separate line after the existing Phase 1 chat output:
> "N untracked BUGS.md entries overlap with ingested plans: BUG-001, BUG-003."

### Chat output

After ingesting all plans:

> "Ingested N plans. Total tasks: M. Key file overlaps: [list files touched by 2+ plans,
> or 'none detected']."

---

## Phase 2: Dependency Analysis

Identify relationships between plans and tasks that constrain execution order.

### File-level analysis

For each pair of plans:

1. Which files does each plan's tasks touch?
2. Do any plans touch the same files? → conflict candidates
3. Can conflicting tasks be reordered to avoid parallel writes? → reorder if yes

### Semantic analysis

Use the `sequentialthinking` MCP tool (or extended thinking if unavailable) to reason:

- Does Plan A's output feed Plan B's input? (output/input dependency)
- Are there shared abstractions being modified that create implicit ordering? (semantic dependency)
- Would executing plans in parallel cause merge conflicts that are hard to resolve?
  (conflict dependency)

### Output

Produce a dependency edge list in your working notes (not necessarily shown to user):

```
Plan A Tasks 1-2 → Plan B (Plan B needs the interface defined by Plan A Tasks 1-2)
Plan C → Plan A (Plan C reads files created by Plan A's tasks)
```

If no dependencies are found: all plans are independent → proceed to single-phase grouping.

---

## Phase 3: Phase Construction

Group plans (or task ranges) into phases that respect the dependency edges.

### Steps

1. Start with the full dependency graph from Phase 2
2. Group plans (or task ranges) with no remaining dependencies into the first phase as parallel tracks
3. Remove those from the graph; remaining plans whose dependencies are now satisfied form the next phase
4. Repeat until all tasks are assigned
5. If a single plan has tasks split across phases (some tasks depend on another plan's output,
   later tasks don't), split it into multiple track entries with non-overlapping task ranges

### Per-phase decisions

For each phase:

- **Assign worktree branches** — follow convention from `[{skill_dir}/references/phase-schema.md](./references/phase-schema.md)`:
  `roadmap/phase-N/plan-name`
- **Determine merge order** — order tracks to minimize conflict risk. Tracks that touch
  foundational files (schemas, interfaces, shared utilities) merge first.
- **Identify critical path** — the longest chain of dependent phases

**Intra-plan PR boundaries and mixed phases:** When a source plan has `**Workflow:** incremental`, each PR boundary becomes a sequential track (`PR 1`, `PR 2`) within a single phase per `[{skill_dir}/references/phase-schema.md](./references/phase-schema.md)`. The `PR 1`, `PR 2` labels in the table are internal plumbing — each PR created from a boundary is standalone work described by what it accomplishes, never "Part X of Y".

### User checkpoint

After constructing phase groupings but before writing the document, use `AskUserQuestion`:

> "I've organized [N] plans into [M] phases. Phase 1 runs [tracks] in parallel.
> Phase 2 requires Phase 1 to complete first. Does this grouping make sense, or should
> I adjust?"

Options:
- "Looks correct, write the roadmap"
- "Adjust the grouping"
- "Let me reconsider one of the dependency edges"

---

## Phase 4: Document Generation

Write the roadmap file following the schema in `[{skill_dir}/references/phase-schema.md](./references/phase-schema.md)`.

### Determine roadmap file location

Use the same logic as `/incremental-planning`:

1. Detect the project memory directory using the convention in
   `[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)` (Directory Detection section).
2. **If found:** Generate a run ID per the Run-ID Naming Convention in that reference, then
   write to `{memory_dir}/plans/{run-id}-roadmap-<name>.md`
   (create the `plans/` subdirectory if it doesn't exist)
3. **If none found:** Fall back to `~/.t3/hack/plans/{run-id}-roadmap-<name>.md`

**Announce location:** "Roadmap file: `hack/plans/feat-auth-1711388400-roadmap-auth-launch.md`"

Do NOT create a `hack/` directory if one doesn't exist.

### Writing sequence

Write incrementally, one phase block at a time:

1. Write the document header (title, source plans, total phases, critical path)
2. For each phase, write the complete phase block (table + sync point + merge order)
3. After all phases are written, if Phase 1's BUGS.md cross-reference identified
   overlapping entries, update those BUGS.md entries now: for each matching BUG ID,
   determine which phase its overlapping plan was assigned to, then set `**Tracked In:**`
   to `Roadmap: plans/{roadmap-filename}.md (Phase N)` — where `{roadmap-filename}` is
   the filename determined by the "Determine roadmap file location" step at the start of
   Phase 4 (the filename of the single roadmap output file being generated by this task,
   e.g., `feat-auth-1711388400-roadmap-auth-launch.md`), the path is relative to the
   memory directory, and N is the assigned phase number. If an entry's
   `**Tracked In:**` already has a non-dash value, skip it. If the entry lacks a
   `**Tracked In:**` field entirely (backward compatibility), insert the field after
   `**Impact:**` or `**Severity:**`.

   **Chat output after step 3:** If any entries were updated:
   > "Updated Tracked In for N BUGS.md entries: BUG-001 → Phase 2, BUG-003 → Phase 1."

**Chat output per phase written:**
> "Phase N written: [one sentence summary]. [M] parallel tracks."

Do not write all phases at once.

---

## Phase 5: Validation and Completion

After all phases are written:

1. Re-read the complete roadmap file
2. Verify all phase transitions are logically sound
3. Cross-reference against original plans — every task must be accounted for:
   - No task appears in two phases (unless it was intentionally split)
   - No task is missing from all phases
4. Verify worktree branches don't collide across tracks or phases
5. Verify merge ordering is conflict-safe (foundational files always merge first)
6. Surface any flags or assumptions via `AskUserQuestion`

**Complete with:**

> "Roadmap complete. N phases, M total tracks. Critical path: [description].
> Roadmap file: [absolute path]."

---

## Completion Tracking & Edge Cases

Follow `[{skill_dir}/references/lifecycle-modes.md](./references/lifecycle-modes.md)` for:
- **Completion Tracking:** Track status determination (`gh pr list --state merged` primary, `git branch --merged main` / `git log` fallback), parallel subagent validation of merged tracks (without echoing raw diff content), phase status derivation (`Completed`, `In Progress`, `Not Started`, `Incomplete`), PR boundary completion via `**PRs:**`, and handling `partial`/`deferred` validation gaps via `AskUserQuestion`.
- **Edge Case Guidance:** Single plan input, all plans independent, circular dependency resolution, plans with no tasks, and non-conforming plan formats.

---

## Quick Reference

### Flow

```
Phase 0: Detect & Route (HITL, requires project memory dir) →
  [New]     Phase 1 → Phase 2 → Phase 3 (checkpoint) → Phase 4 (incremental) → Phase 5
  [Update]  Completion Check → Phase 1-5 (re-ingest, completed phases preserved)
  [Status]  Completion Check → Drift Detection → Report → offer Update/Cleanup/Exit
  [Cleanup] Completion Check → Archive plans → Delete branches → Update roadmap doc
  [No memory dir] → skip Phase 0, go to Phase 1 (lifecycle requests get AskUserQuestion first)
```

### What Goes Where

```
CHAT: Mode routing, ingest summary, dependency edges, checkpoint questions,
      per-phase write confirmations, status/drift report, completion validation verdicts
FILE: Full roadmap document (header + phase blocks, optional Status/Last updated fields)
NEVER IN CHAT: Full roadmap content, raw table data, raw diff content from subagent validators
```

### Roadmap File Location (Phase 4 — document generation)

```
1. {memory_dir}/plans/{run-id}-roadmap-<name>.md → if memory dir exists (detect per [{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md))
2. ~/.t3/hack/plans/{run-id}-roadmap-<name>.md → fallback for all other cases
```

**Phase 0 detection** only runs when a project memory dir exists (option 1 above).
Projects using `~/.t3/hack/plans/` skip Phase 0 and go straight to Phase 1.

### Schema Reference

`[{skill_dir}/references/phase-schema.md](./references/phase-schema.md)` — canonical schema for all roadmap document fields,
including per-track table columns and worktree branch naming.
