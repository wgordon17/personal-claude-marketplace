# Roadmap Phase Schema

This reference defines the exact schema every roadmap document must follow. Both the roadmap
skill and consuming orchestrators (e.g., `/swarm`) use this schema as a shared contract.

---

## Document Header Schema

Every roadmap document begins with a header block:

```markdown
# Roadmap: [Name]

**Source plans:**
- /absolute/path/to/plan-a.md
- /absolute/path/to/plan-b.md

**Total phases:** N
**Critical path:** Plan A (Phase 1) → Plan B (Phase 2) — [one sentence explanation]
```

| Field | Required | Description |
|-------|----------|-------------|
| Roadmap title | Yes | Human-readable name for the coordinated work |
| Source plans | Yes | Absolute paths to all plan files ingested |
| Total phases | Yes | Count of sequential phases |
| Critical path | Yes | Which plans/tracks gate the overall schedule, and why |
| Status | No | Overall roadmap lifecycle status: `Active` (default, absence = Active), `Completed` (written by cleanup mode when all phases are complete), or `Archived` (written by cleanup mode before moving the roadmap file to `done/`). |
| Last updated | No | ISO timestamp (e.g., `2026-03-20T14:30:00`) written by update mode when regenerating the roadmap. Provides provenance for distinguishing fresh vs. updated roadmaps and prevents drift detection false negatives after updates. |

---

## Per-Phase Block Schema

Each phase follows this exact structure:

```markdown
## Phase N: [Phase Name]

**Prerequisites:** Phase N-1 completed (or "None" for Phase 1)
**Parallel tracks:** N plans execute concurrently in this phase

| Track | Plan | Tasks | Worktree Branch | Depends On | Skill | Domain |
|-------|------|-------|-----------------|------------|-------|--------|
| A | /path/to/plan-a.md | Tasks 1-3 | roadmap/phase-1/plan-a | None | /swarm | Complicated |
| B | /path/to/plan-b.md | Tasks 1-5 | roadmap/phase-1/plan-b | None | /swarm | Clear |

**Sync point:** All tracks must complete before Phase N+1 begins.
**Merge order:** Track A first (no conflicts expected), then Track B.
```

### Per-Track Table Columns

The per-phase table IS the canonical per-track metadata representation. All per-track fields
must appear as columns — do not add per-track metadata outside the table.

| Column | Required | Description |
|--------|----------|-------------|
| Track | Yes | Letter identifier (A, B, C...) |
| Plan | Yes | Absolute path to the plan file |
| Tasks | Yes | Task range from that plan (e.g., "Tasks 1-3", "Tasks 4-7", "All") |
| Worktree Branch | Yes | Convention: `roadmap/phase-N/plan-name` (derived from plan filename) |
| Depends On | Yes | Intra-phase track dependencies only — other tracks *within this phase* that must complete before this track starts. Inter-phase ordering is handled by the Prerequisites field. Use "None" when the track has no dependencies on other tracks in the same phase. |
| Skill | Yes | Execution skill. Valid values: `/swarm` (full agent swarm — default for most implementation work), `/speculative` (competing implementations), or a custom skill path. The orchestrator invokes the specified skill in the track's worktree with the plan file and task range as arguments. |
| Domain | Yes | Cynefin domain from the plan's header (Clear, Complicated, Complex, Chaotic, Disorder) |

### Per-Phase Fixed Fields

| Field | Required | Description |
|-------|----------|-------------|
| Prerequisites | Yes | "None" for Phase 1; "Phase N-1 completed" for subsequent phases; list specific tracks if only some are required |
| Parallel tracks | Yes | Count of concurrent tracks in this phase |
| Sync point | Yes | Always: "All tracks must complete before Phase N+1 begins." (or "Final phase." for last) |
| Merge order | Yes | Ordered list of tracks (A first, B second, etc.) with conflict risk rationale |

---

## Phase Transition Rules

1. **All tracks in a phase must complete** before the next phase begins — no partial advancement.
2. **Merge order** is specified per phase and determines conflict resolution priority. The first
   track in the merge order is considered authoritative when the same file is touched by multiple tracks.
3. **Failed tracks block phase completion.** A failed track must be remediated or explicitly
   skipped (with user approval) before the phase closes.
4. **Task splitting across phases** is allowed when a single plan has tasks that depend on
   another plan's output. In this case, the same plan file appears in multiple phases with
   non-overlapping task ranges (e.g., "Tasks 1-3" in Phase 1, "Tasks 4-7" in Phase 2).

---

## Completion Status Fields

These fields are written and read by the stateful roadmap skill modes (update, cleanup, status).
They are optional additions — freshly generated roadmaps do not contain them.

### Per-phase status

A `**Status:**` line may appear in a phase block after cleanup mode marks it complete:

```markdown
## Phase 1: Foundation

**Prerequisites:** None
**Parallel tracks:** 2 plans execute concurrently in this phase
**Status:** Completed

| Track | ...
```

Values: `Not Started` | `In Progress` | `Completed`

- `Not Started` — default; branch does not exist
- `In Progress` — branch exists but no merged PR found
- `Completed` — written explicitly by cleanup mode after all tracks merged and validated

Note: `Not Started` and `In Progress` are runtime-derived states (from branch checks) and are
**not written to the roadmap file**. Only `Completed` is persisted. Absence of a `**Status:**`
field means the phase has not been explicitly marked complete.

### Per-track validation result format

When subagent validators run (see Completion Tracking in SKILL.md), they return structured
verdicts — never raw diff content. The format is ephemeral (shown in chat, not persisted to
the roadmap file unless the user requests it):

```
Task N: implemented — [one-sentence evidence citing specific file path or code location]
Task N: partial — [one-sentence description of what was implemented vs. what was skipped]
Task N: deferred — [one-sentence description of deferred work, e.g., "TODO left in auth.py:42"]
```

---

## Archival

Cleanup mode archives completed work to prevent plan directory clutter.

### Archive location

`{plan-dir}/done/` — a subdirectory of the plan directory (`hack/plans/done/`,
`~/.claude/plans/done/`, etc.). Created if it doesn't exist.

### What gets archived (and when)

| Item | Archived when |
|------|---------------|
| Source plan file | All phases that reference this plan are `Completed` |
| Roadmap document itself | ALL phases in the roadmap are `Completed` |

Plans that span multiple phases (task splitting) are NOT archived until every phase that
references them is complete. The roadmap document is always the last thing archived.

### Naming

Files keep their original names when moved to `done/`. No renaming on archive.
Backup files created by update mode are written to `{plan-dir}/done/` using the pattern:
`{original-filename}.YYYY-MM-DDTHH-MM-SS.pre-update` (timestamped, unique per run).

---

## Backward Compatibility

The `**Status:**` and `**Last updated:**` document header fields and the per-phase
`**Status:**` field are **optional additions**. Existing roadmap documents without them
are valid and fully functional.

Consumers (e.g., `/swarm`) that parse roadmap documents MUST tolerate unknown fields
gracefully. The `/swarm` orchestration-playbook.md has been verified: its "phase block"
references are about its own internal pipeline phases (Phase 0-7 of swarm execution),
not roadmap document structure. Encountering `**Status:**` or `**Last updated:**` lines
in a roadmap document will not break `/swarm`'s parsing — it reads the per-track table
rows and fixed fields (Prerequisites, Parallel tracks, Sync point, Merge order) and
ignores unrecognized fields.

---

## Worktree Branch Naming Convention

```
roadmap/phase-{N}/{plan-name}
```

Where `{plan-name}` is the plan filename without date prefix and `.md` extension, with
spaces and underscores replaced by hyphens.

Examples:
- Plan file: `hack/plans/2026-03-20-auth-refactor.md` → Branch: `roadmap/phase-1/auth-refactor`
- Plan file: `hack/plans/2026-03-20-db-migration.md` → Branch: `roadmap/phase-2/db-migration`

Branches must not collide across tracks in the same phase or across phases.

---

## Concrete Examples

### Example 1: Simple (2 plans, 2 phases)

Two plans where Plan B depends on files created by Plan A's first two tasks.

```markdown
# Roadmap: Auth Refactor + Session Storage

**Source plans:**
- /home/user/project/hack/plans/2026-03-20-auth-refactor.md
- /home/user/project/hack/plans/2026-03-20-session-storage.md

**Total phases:** 2
**Critical path:** Auth Refactor (Phase 1, Tasks 1-2) → Session Storage (Phase 2) — session
storage depends on the auth middleware interface defined in Tasks 1-2 of Auth Refactor.

## Phase 1: Foundation

**Prerequisites:** None
**Parallel tracks:** 1 plan executes in this phase

| Track | Plan | Tasks | Worktree Branch | Depends On | Skill | Domain |
|-------|------|-------|-----------------|------------|-------|--------|
| A | /home/user/project/hack/plans/2026-03-20-auth-refactor.md | Tasks 1-2 | roadmap/phase-1/auth-refactor | None | /swarm | Complicated |

**Sync point:** All tracks must complete before Phase 2 begins.
**Merge order:** Track A only.

## Phase 2: Parallel Implementation

**Prerequisites:** Phase 1 completed
**Parallel tracks:** 2 plans execute concurrently in this phase

| Track | Plan | Tasks | Worktree Branch | Depends On | Skill | Domain |
|-------|------|-------|-----------------|------------|-------|--------|
| A | /home/user/project/hack/plans/2026-03-20-auth-refactor.md | Tasks 3-5 | roadmap/phase-2/auth-refactor | None | /swarm | Complicated |
| B | /home/user/project/hack/plans/2026-03-20-session-storage.md | All | roadmap/phase-2/session-storage | None | /swarm | Clear |

**Sync point:** All tracks must complete before merging to main. Final phase.
**Merge order:** Track A first (touches middleware layer), then Track B (additive session layer, no conflicts expected).
```

---

### Example 2: Complex (3 plans, 3 phases with task splitting)

Three plans with layered dependencies: Plan C requires Plan A's database schema (Phase 1)
and Plan B's API layer (Phase 2) before it can implement the frontend.

```markdown
# Roadmap: Full Stack Feature Launch

**Source plans:**
- /home/user/project/hack/plans/2026-03-20-db-schema.md
- /home/user/project/hack/plans/2026-03-20-api-layer.md
- /home/user/project/hack/plans/2026-03-20-frontend-ui.md

**Total phases:** 3
**Critical path:** DB Schema (Phase 1) → API Layer (Phase 2) → Frontend UI (Phase 3) —
each layer depends on the contract defined by the layer below it.

## Phase 1: Data Layer

**Prerequisites:** None
**Parallel tracks:** 2 plans execute concurrently in this phase

| Track | Plan | Tasks | Worktree Branch | Depends On | Skill | Domain |
|-------|------|-------|-----------------|------------|-------|--------|
| A | /home/user/project/hack/plans/2026-03-20-db-schema.md | All | roadmap/phase-1/db-schema | None | /swarm | Complicated |
| B | /home/user/project/hack/plans/2026-03-20-api-layer.md | Tasks 1-2 | roadmap/phase-1/api-layer | None | /swarm | Complicated |

**Sync point:** All tracks must complete before Phase 2 begins.
**Merge order:** Track A first (schema migrations must land before API references them), then Track B.

## Phase 2: API Layer

**Prerequisites:** Phase 1 completed
**Parallel tracks:** 1 plan executes in this phase

| Track | Plan | Tasks | Worktree Branch | Depends On | Skill | Domain |
|-------|------|-------|-----------------|------------|-------|--------|
| A | /home/user/project/hack/plans/2026-03-20-api-layer.md | Tasks 3-7 | roadmap/phase-2/api-layer | None | /swarm | Complicated |

**Sync point:** All tracks must complete before Phase 3 begins.
**Merge order:** Track A only.

## Phase 3: Frontend + API Hardening

**Prerequisites:** Phase 2 completed
**Parallel tracks:** 2 plans execute concurrently in this phase

| Track | Plan | Tasks | Worktree Branch | Depends On | Skill | Domain |
|-------|------|-------|-----------------|------------|-------|--------|
| A | /home/user/project/hack/plans/2026-03-20-frontend-ui.md | All | roadmap/phase-3/frontend-ui | None | /swarm | Complex |
| B | /home/user/project/hack/plans/2026-03-20-api-layer.md | Tasks 8-9 | roadmap/phase-3/api-layer | None | /swarm | Clear |

**Sync point:** All tracks must complete before merging to main. Final phase.
**Merge order:** Track B first (API hardening is additive), then Track A (frontend may reference finalized API contracts).
```

---

## Consumption

An orchestrator processes the roadmap document phase-by-phase:

1. **Parse** — Read the roadmap file. For each phase block, extract the per-track table rows.
2. **Fork** — For each track in the current phase, create an isolated git worktree on the specified branch.
3. **Execute** — In each worktree, invoke the track's Skill (default: `/swarm`) with:
   - Plan file path (from the Plan column)
   - Task range (from the Tasks column)
   - Worktree branch (already checked out)
4. **Sync** — Wait for all tracks in the phase to complete (sync point).
5. **Merge** — Merge worktree branches back to main in the specified merge order.
6. **Advance** — Move to the next phase. Repeat from step 2.

If any track fails, the phase is blocked. The orchestrator should surface the failure and await human resolution before retrying or advancing.
