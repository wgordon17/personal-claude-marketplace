---
name: roadmap
description: >-
  Multi-plan phase sequencing and dependency analysis. Use when coordinating
  multiple implementation plans into parallel/sequential workstreams. Takes
  existing plan files as input, analyzes cross-plan dependencies, re-organizes
  tasks across phases, and produces a structured roadmap document optimized
  for AI orchestrator consumption. Use when asked to "roadmap", "sequence plans",
  "coordinate multiple plans", "plan execution order", or when 2+ plans need
  ordering.
allowed-tools: [Read, Write, Edit, Glob, Grep, Agent, AskUserQuestion, Bash, ToolSearch]
---

# Roadmap

Multi-plan phase sequencing and dependency analysis. Takes existing plan files as input,
produces a structured roadmap document consumable by `/swarm` and other orchestrators.

## Activation

This skill activates when:

- User asks to "roadmap", "sequence plans", "coordinate these plans"
- User has 2+ plan files and asks about execution order
- After `/incremental-planning` generates a plan that references other planned work

**Announce at start:** "Using roadmap to sequence [N] plans into phases."

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

Use `mcp__sequential-thinking__sequentialthinking` (or extended thinking if unavailable) to reason:

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

- **Assign worktree branches** — follow convention from `references/phase-schema.md`:
  `roadmap/phase-N/plan-name`
- **Determine merge order** — order tracks to minimize conflict risk. Tracks that touch
  foundational files (schemas, interfaces, shared utilities) merge first.
- **Identify critical path** — the longest chain of dependent phases

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

Write the roadmap file following the schema in `references/phase-schema.md`.

### Determine roadmap file location

Use the same logic as `/incremental-planning`:

1. Check for `hack/`, `.local/`, `scratch/`, `.dev/` in the project root (in this order)
2. **If found:** Write to `{memory-dir}/plans/YYYY-MM-DD-roadmap-<name>.md`
   (create the `plans/` subdirectory if it doesn't exist)
3. **If none found:** Fall back to `~/.claude/plans/YYYY-MM-DD-roadmap-<name>.md`

**Announce location:** "Roadmap file: `hack/plans/2026-03-20-roadmap-auth-launch.md`"

Do NOT create a `hack/` directory if one doesn't exist.

### Writing sequence

Write incrementally, one phase block at a time:

1. Write the document header (title, source plans, total phases, critical path)
2. For each phase, write the complete phase block (table + sync point + merge order)

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

## Edge Case Guidance

### Single plan input

Trivial roadmap: one phase, one track. Still useful for documenting worktree isolation and
the execution skill to use. Write it — don't skip.

### All plans independent

Single phase with N parallel tracks. Skip dependency analysis (Phase 2) and go straight to
track assignment. Note in roadmap header: "No dependencies detected — all plans run in parallel."

### Circular dependencies detected

Surface immediately via `AskUserQuestion`:

> "I found a circular dependency: Plan A depends on Plan B, and Plan B depends on Plan A
> (via [specific files/tasks]). I cannot resolve this automatically. Options:
> 1. Break the cycle by extracting the shared work into a new prep task
> 2. Manually specify which plan should run first and accept the merge conflict
> 3. Re-examine whether the dependency is real"

Do not write a roadmap document until the cycle is resolved.

### Plan with no tasks

Skip it and note in the roadmap document header:

> **Skipped plans:** `/path/to/plan.md` — no `## Task N:` headings found.

### Non-conforming plan format

If a plan is missing `**Goal:**`, no `## Task N:` headings, or no `**Files:**` blocks, use
`AskUserQuestion` listing exactly what's missing. Offer:

1. Best-effort extraction (treat entire plan as one task)
2. Skip this plan
3. Let the user fix the plan format and retry

---

## Quick Reference

### Flow

```
Phase 1: Ingest Plans → Phase 2: Dependency Analysis →
Phase 3: Phase Construction (checkpoint) → Phase 4: Document Generation (incremental) →
Phase 5: Validation and Completion
```

### What Goes Where

```
CHAT: Ingest summary, dependency edges found, checkpoint questions, per-phase write confirmations
FILE: Full roadmap document (header + phase blocks)
NEVER IN CHAT: Full roadmap content, raw table data
```

### Roadmap File Location

```
1. hack/plans/ (or .local/plans/, scratch/plans/, .dev/plans/) → if memory dir exists
2. ~/.claude/plans/ → fallback for all other cases
```

### Schema Reference

`code-quality/skills/roadmap/references/phase-schema.md` — canonical schema for all
roadmap document fields, including per-track table columns and worktree branch naming.
