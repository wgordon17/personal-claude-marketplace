---
name: map-reduce
description: Parallelized workload processing with structured chunking, mapper agents, and reducer synthesis. Use for codebase-wide analysis, bulk transformations, and large file audits (20+ files).
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, CronCreate, CronDelete, SendMessage, TaskCreate, TaskUpdate, TaskList, TaskGet]
---

# /map-reduce — Parallelized Workload Processing

Splits large workloads into parallelizable chunks, assigns independent mapper agents to each
chunk, then synthesizes results through a single reducer agent with cross-chunk validation.
Use for codebase-wide analysis, bulk transformations, and large file audits (20+ files).

## Quick Start

```
/map-reduce "find all files with TODO comments"          # Analysis workload
/map-reduce "apply deprecation fix across all files"     # Implementation workload
/map-reduce --split-strategy by-directory "audit auth/"  # Explicit split strategy
```

---

## Architecture

```
LEAD (you)
├── Phase 0: Plan & Split
│   ├── Analyze workload
│   ├── Build cross-reference manifest
│   ├── Split into N chunks (max 8, module-aware)
│   └── Write ChunkAssignments
│
├── Phase 1: Map (parallel)
│   ├── Spawn N mapper agents simultaneously
│   ├── Each mapper processes its chunk independently
│   ├── CronCreate watchdog monitors progress
│   └── Collect ChunkResults
│
├── Phase 2: Reduce
│   ├── Spawn single reducer agent (opus)
│   ├── Cross-chunk validation (4-step protocol)
│   └── Produce ReductionResult
│
└── Phase 3: Deliver
    ├── Fidelity report (>20% invalidation threshold)
    ├── Present or apply results
    └── Write map-reduce-report.md
```

---

## Workflow Phases

### Phase 0: Plan & Split

1. **Analyze the workload** — determine the split strategy:
   - `by-file`: each mapper gets an explicit list of files (best for heterogeneous workloads)
   - `by-directory`: each mapper gets a subtree (best for large, tree-structured codebases)
   - `by-item`: each mapper gets items from a list (best for non-file workloads: APIs, symbols, records)
   - `custom`: user-provided split logic (ask via AskUserQuestion if needed)

2. **Module-aware splitting:** when splitting by-directory, respect module boundaries — keep
   tightly coupled files in the same chunk. Use import/directory structure to determine coupling.
   Never split a single module (e.g., `auth/`) across chunks. If a module is too large for one
   chunk, treat it as its own chunk even if that makes the chunk larger than average.

3. **Cross-reference manifest:** before splitting, build a lightweight manifest of exported
   symbols per file — function names, class names, and file paths for all files NOT in each
   chunk. Include this manifest in every ChunkAssignment so mappers can distinguish between
   "unused in my chunk" vs "might be used elsewhere." See `references/fidelity-guide.md`.

4. **Cap at 8 mappers:** if the workload naturally splits into more, merge the smallest chunks.
   If the user wants more than 8, use AskUserQuestion to confirm — document in fidelity-guide.md
   that uncapped splitting is a known fidelity risk.

5. **Create audit trail:** `hack/map-reduce/YYYY-MM-DD/` (append `-2`, `-3` for multiple runs).
   Write `hack/map-reduce/YYYY-MM-DD/chunks/` subdirectory for ChunkResult files.

6. **Create tasks upfront:** use TaskCreate with addBlockedBy for the full task graph (one task
   per chunk + one for reduction + one for delivery) so progress is visible from the start.

### Phase 1: Map (parallel)

1. **Spawn N mapper agents in parallel** — all at once, not sequentially. Use `general-purpose`
   type with `sonnet` model. Each mapper receives a ChunkAssignment (see
   `references/communication-schema.md`).

2. **Mappers are fully isolated** — they do NOT communicate with each other. Each processes
   only the files/items in its chunk.

3. **Boundary-aware findings:** mappers MUST classify every finding with a `confidence` field:
   - `verified`: file-internal issues (syntax, style, complexity, security) — self-contained,
     no cross-chunk risk
   - `chunk-local`: findings that depend on cross-chunk context — specifically:
     - "Unused code" where the symbol is exported or public
     - "Missing dependency" where the import is from a path outside the chunk

4. **CronCreate watchdog:** after spawning all mappers, create a CronCreate job (60-second
   interval) that checks TaskList for `in_progress` tasks with no recent updates. If any mapper
   has been idle for 2+ consecutive checks, the watchdog pings the lead to investigate. The
   watchdog reports only — it never intervenes directly. CronDelete the watchdog when Phase 1
   completes (also delete in error/escalation paths to avoid orphaned crons).

5. **Failure handling:** if a mapper fails or times out, retry once with a fresh agent using
   the same ChunkAssignment. If the retry also fails, mark the chunk as `failed` in the audit
   trail, record it in `failed_chunks`, and continue to Phase 2 with the remaining results.

6. **Each mapper writes** its ChunkResult to `{run_dir}/chunks/chunk-{id}.json`.

### Phase 2: Reduce

1. **Spawn a single reducer agent** — `general-purpose` type, `opus` model (judgment-heavy
   synthesis). The reducer receives a ReductionInput pointing to all ChunkResult files.

2. **Cross-chunk validation (mandatory 4-step protocol):**

   **Step 1 — Unused code cross-check:** for every `chunk-local` "unused code" finding, search
   other chunks' results for references to that symbol. If found in another chunk → invalidate
   the finding. If not found anywhere → promote to `verified`.

   **Step 2 — Missing dependency cross-check:** for every `chunk-local` "missing dependency"
   finding, check if the dependency exists in another chunk's file list. If yes → invalidate.
   If no → promote to `verified`.

   **Step 3 — Deduplication:** for duplicate findings across chunks (same issue, different
   chunks found it), merge by evidence + location, keeping the most detailed description. Never
   silently drop a finding — if two chunks found the same issue with different evidence, keep
   both evidence strings in the merged finding.

   **Step 4 — Conflict resolution:** for conflicting findings (chunk A says "unused", chunk B
   references it), always resolve in favor of "used" — false negatives are better than false
   positives for destructive actions (deletions, removals).

3. **Reducer output:** a ReductionResult written to `{run_dir}/reduction-result.json`. All
   findings in the ReductionResult have `confidence: "verified"` — the reducer promotes or
   invalidates all `chunk-local` findings before outputting.

4. **For implementation workloads:** reducer also checks cross-chunk consistency — are there
   conflicting changes proposed by different mappers to the same shared interface or file?
   Flag these as `cross_chunk_issues` in the ReductionResult.

### Phase 3: Deliver

1. **Fidelity report:** check `invalidated_findings` count in ReductionResult. If more than 20%
   of total findings were invalidated during cross-chunk validation, warn the user:
   > "Chunk boundaries may have been poorly chosen — {N} of {total} findings ({pct}%) were
   > invalidated by cross-chunk validation. Consider re-running with different splits or a
   > single-agent analysis."

2. **For analysis workloads:** present the synthesized summary and findings to the user.
   Offer to write a detailed report or create actionable TODO items.

3. **For implementation workloads:** apply changes in priority order (by severity), run tests
   after each batch, verify nothing regressed. Roll back on test failure.

4. **Write final report** to `{run_dir}/map-reduce-report.md` with:
   - Summary statistics (files analyzed, total findings, deduplicated, invalidated)
   - Per-severity breakdown
   - Cross-chunk issues (if any)
   - Fidelity warnings (if any)
   - Chunk failure list (if any chunks failed)

---

## When to Use /map-reduce vs. Alternatives

| Scenario | Recommended Approach |
|----------|----------------------|
| Analysis across 20+ files | `/map-reduce` |
| Bulk transformation (same fix, many files) | `/map-reduce` |
| Large file audit (50+ files) | `/map-reduce` |
| Architectural analysis (circular deps, data flow) | Single agent (needs full-codebase view) |
| Small workload (<20 files) | Direct parallel agents (less overhead) |
| Tightly coupled codebase (>50% cross-imports) | Single agent (chunking is meaningless) |
| Full implementation task | `/swarm` |
| Codebase cleanup | `/unfuck` |

---

## Lead Responsibilities

### Context Bundle

Every mapper and the reducer receive a context bundle (see `references/communication-schema.md`).
The bundle includes: project name, task description, run_dir, the tool guard reminder, and the
cross-reference manifest (embedded in ChunkAssignment for mappers).

### Watchdog Management

Create the CronCreate watchdog after all mappers are spawned. Track its job ID. Delete it:
- After all Phase 1 mappers complete (success path)
- In any error or escalation path
- Before returning control to the user

Never leave an orphaned cron job.

### Audit Trail

```
hack/map-reduce/
└── YYYY-MM-DD/
    ├── chunks/
    │   ├── chunk-1.json        # ChunkResult from mapper 1
    │   ├── chunk-2.json        # ChunkResult from mapper 2
    │   └── ...
    ├── reduction-result.json   # ReductionResult from reducer
    └── map-reduce-report.md    # Final human-readable report
```

---

## References

| File | Content |
|------|---------|
| `references/communication-schema.md` | JSON schemas for ChunkAssignment, ChunkResult, ReductionInput, ReductionResult |
| `references/agent-prompts.md` | Full prompt templates for mapper and reducer agents |
| `references/fidelity-guide.md` | Fidelity risks, mitigations, and when NOT to use map-reduce |
