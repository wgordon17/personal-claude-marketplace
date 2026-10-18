# Plan File Format & BUGS.md Cross-Reference

This reference defines the exact plan header fields, `## File Structure` section, `2.5. BUGS.md Cross-Reference` algorithm, and `## Task N` template used in Phase 4 of `/incremental-planning`.

---

## 1. Write the Header

Write the plan file with a header containing:

**Always include (light and full planning).** Use `**Field:**` bold format for each field
(e.g., `**Goal:**`, `**Cynefin Domain:**`) — this format is machine-parseable by `/roadmap`:

- **Agentic directive** — A blockquote at the top of the plan file (above the Goal):
  `> **For agentic workers:** REQUIRED: Use /swarm to implement this plan. Each task within
  a phase should run in an isolated worktree.`
  For light plans (1-3 tasks), the directive may reference direct implementation instead of
  `/swarm` if the scope doesn't warrant a full agent swarm.
- **Goal:** — 1 sentence
- **Cynefin Domain:** — the domain classified in Phase 0
- **Domain Justification:** — 2-4 sentences explaining why this domain applies and what that
  means for the plan (e.g., whether a probe is needed, whether outcomes are predictable)
- **Architecture Summary:** — 2-3 sentences
- **Tech Stack:**
- **Key Decisions:** — from Phase 2
- **Branch:** — The current git branch name at plan-writing time, or "not yet created" if the
  plan is written before branching. Example: `**Branch:** feat/my-feature`
  The `**Branch:**` field is used by the plan-adherence agent for cross-session plan discovery.
  Always populate it, even if the branch doesn't exist yet — update it when the branch is created.
- **Iterations:** — Lifecycle counters tracking review/fix/gate iterations. Initialize all 5
  counters at 0 when the plan is created. Include for both light and full planning.
  Format:
  ```
  **Iterations:**
  - review-cycle: 0
  - fix-cycle: 0
  - pr-review-cycle: 0
  - pr-fix-cycle: 0
  - quality-gate: 0
  ```
- **Tracker:** — The issue tracking selection from Phase 2's Tracker Question. See
  `[{skill_dir}/../../references/tracker-field-spec.md](../../../references/tracker-field-spec.md)` for the full field value table, parsing
  spec, validation regex, and finalization constraint.
- **Workflow:** — `fast` (default) or `incremental`. When `incremental`, PR boundary markers
  are required in tasks. When absent, defaults to `fast`. For light planning, `**Workflow:**`
  defaults to `fast` regardless of project configuration — light plans are inherently small
  enough that PR boundaries add no value.
  Precedence for determining the value:
  1. Explicit `**Workflow:**` answer in Phase 2 (if the user specifies it)
  2. WORK_ETHIC.md `**Workflow:**` field in the memory directory
  3. Project CLAUDE.md `## Workflow` section with `mode: incremental` or `mode: fast`
  4. Default: `fast`
- **PR Boundaries:** — (only when `**Workflow:** incremental`) Summary of task-to-PR mapping.
  Format: `PR 1: Tasks 1-3, PR 2: Tasks 4-6, PR 3: Tasks 7-9`. Each PR boundary groups
  tasks that form a coherent, independently-reviewable unit targeting 200-400 lines changed.
  The grouping is determined during Phase 4 task writing based on logical cohesion, file
  overlap, and dependency structure — not mechanical line counting. Initially left blank in
  the header — populated after all tasks are written and PR assignments are finalized during
  Phase 5 validation. These internal labels (`PR 1`, `PR 2`) are plan-file plumbing only —
  they must NOT appear in PR titles, PR bodies, or user-facing messages. Each PR created
  from a boundary is standalone work described by what it accomplishes, not by its position
  in a sequence.
- **PRs:** — (only when `**Workflow:** incremental`, populated during implementation)
  Tracks created PR numbers. Format: `PR 1: #42, PR 2: pending, PR 3: pending`. Updated
  by swarm as PRs are created. Initially all `pending`.

**The following header sections apply to full planning only (skip for light planning):**
- **Documentation Impact** — which documentation surfaces are affected by this work and how.
  Use the trigger definitions from `[{skill_dir}/../../references/documentation-taxonomy.md](../../../references/documentation-taxonomy.md)` to
  determine if changes require documentation. Reference surfaces discovered in Phase 1.
  Format: `surface → action (add/update/remove) → why`. Omit if no documentation triggers apply.
- **Options Considered** — for architecture-level plans, list the alternatives evaluated and
  why they were rejected (1-2 sentences per option). Minimum 2 options if a meaningful choice
  was made. Omit if the approach was never in question.
- **Trade-offs Accepted** — what the chosen approach gives up and why that's acceptable
  (e.g., "Chose X over Y because Z; accepts the risk of W"). One bullet per trade-off.
  Required for Complicated or Complex domain tasks.
- **Security Flags** — concerns identified during Phase 1/2/3 that implementation must address.
  Include mitigations if known. Omit if none identified.
- **Open Questions** — questions that remain unresolved at plan-write time, each with an owner:
  - `[human]` — requires user input before or during implementation
  - `[agent]` — can be resolved by the implementer during execution

**Chat output:** "Wrote plan header. Goal: [1 sentence]. Architecture: [1 sentence]."

---

## 2. File Structure Mapping

Before writing tasks, map all files this plan will touch.

Create a `## File Structure` section in the plan file, placed between the header (after the
`---` separator) and Task 1. Use this layout:

```markdown
## File Structure

### Files to Modify
| File | Responsibility | Change |
|------|----------------|--------|
| `path/to/file.ts` | Brief responsibility | What changes |

### Files to Create
| File | Responsibility |
|------|----------------|
| `path/to/new.ts` | Brief responsibility |

### File Design Notes
- **`path/to/file.ts`** — Why it exists here and not elsewhere (for non-obvious decisions only)
```

**File design philosophy to apply:**
- Design units with clear boundaries and well-defined interfaces
- Prefer smaller, focused files over large ones — a file that does one thing is easier to test
- Files that change together should live together — split by responsibility, not technical layer
- In existing codebases, follow established patterns (check adjacent files before choosing placement)

This step writes to the plan file only. **Chat output:** "Wrote file structure. N files mapped."

---

## 2.5. BUGS.md Cross-Reference

After writing the File Structure section, check if this plan addresses any open bugs:

1. Detect the memory directory and check if `{memory_dir}/BUGS.md` exists
2. If it exists, read it and extract all entries with `**Status:**` of `Investigating`,
   `Root Cause Found`, or `Fix Ready`
3. For each open bug entry that has a `### Files Involved` section, compare those paths
   against the plan's `## File Structure` paths. Skip entries without a `### Files Involved`
   section (investigation may not be complete).
4. Path comparison: strip line number suffixes (`:NN`) from BUGS.md paths before comparing
   (e.g., `src/auth/login.ts:42` becomes `src/auth/login.ts`). Then match using this
   precedence:
   - **Exact file match** (after stripping): always counts, strongest signal.
   - **Common directory prefix of at least 3 components** (e.g., `code-quality/skills/fix/`
     matches `[{skill_dir}/../fix/SKILL.md](../../fix/SKILL.md)`; `src/auth/handlers/` matches
     `src/auth/handlers/login.ts`): counts as overlap. Prefixes of 1 or 2 components
     (e.g., `code-quality/` or `code-quality/skills/`) are too broad and do not count.
   Rationale: depth-1 and depth-2 prefixes cover entire plugins or categories (20+ files
   each), producing false positives. Depth-3 is the capability level — the smallest
   meaningful unit of work.
5. If any overlap is found and the entry's `**Tracked In:**` is currently `—` (or the field
   is missing entirely — backward compatibility), update it to
   `Plan: plans/{plan-filename}.md` (relative path from the memory directory, e.g.,
   `Plan: plans/feat-xyz-1234567890.md`). If a missing field, insert the line after
   `**Impact:**` or `**Severity:**`. If `**Tracked In:**` already has a non-dash value
   (PR, another plan, etc.), skip it — do not overwrite existing tracking references.

If no BUGS.md exists or no overlaps are found, skip silently — do not mention it in chat.

If overlaps ARE found, announce in chat:
> "Updated Tracked In for N BUGS.md entries that overlap with this plan: BUG-001, BUG-003."

---

## 3. Write Each Task

Append one task at a time using the Edit tool. Use the heading format `## Task N: [Title]`
for each task. Each task should follow this structure:

```markdown
## Task N: [Short Title]

**Files:**
- Modify: `path/to/file.ts`
- Create: `path/to/new.ts`

**Depends on:** Task N-1 (if applicable, or "None")
**PR:** 1 (only when **Workflow:** incremental; omit for fast workflow)

- [ ] **Step 1: [action]**
  [details]
  Test: `command` → expected output
- [ ] **Step 2: [action]**
  [details]

**Documentation updates:** [surfaces to update, or "None"]
**Commit:** `type(scope): description`
```

Each task includes:
- Files to create/modify (exact paths, using `**Files:**` block)
- Steps (each step is one concrete action — include test commands with expected output
  inline within the relevant step)
- Documentation updates (what docs to create/update/remove. "None" if no documentation
  triggers apply per `[{skill_dir}/../../references/documentation-taxonomy.md](../../../references/documentation-taxonomy.md)`. Reference surfaces
  discovered in Phase 1.)
- Commit message

**Chat output per task:** "Task N written: [1 sentence description]. N steps."
