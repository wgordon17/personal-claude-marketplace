---
name: speculative
description: >-
  Run competing implementations in parallel with isolated worktrees, then judge and select the
  best approach. Use when multiple viable approaches exist and "try both and compare" beats
  "guess and commit."
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, SendMessage, TaskCreate,
  TaskUpdate, TaskList, TaskGet, CronCreate, CronDelete]
---

# /speculative — Competing Implementations with Judge Selection

You MUST follow the phased approach described here. Do not collapse phases or implement
the task yourself. Your role is orchestration — you define the spec, spawn competitors,
coordinate the judge, and present the result to the user.

---

## Workflow Phases

### Phase 0: Specification

Gather the problem and success criteria before spawning anything.

1. Use `AskUserQuestion` to clarify the task if the request is ambiguous. Resolve:
   - What exactly are we comparing? (algorithm, architecture, approach)
   - What does "better" mean for this problem? (performance, readability, maintainability, correctness, simplicity)
   - How many competitors? (default: 2, max: 4 — more is slower, rarely more useful)
   - Are there specific approaches to try, or should competitors choose their own?

2. Define the evaluation criteria as a weighted list (weights must sum to 1.0):
   - Correctness (does it work correctly and handle edge cases?)
   - Readability (is the code clear and maintainable?)
   - Performance (is it efficient for the expected load?)
   - Simplicity (is it the minimum necessary complexity?)
   - Add or substitute criteria based on user priorities

3. Create the audit trail directory at `hack/speculative/YYYY-MM-DD/`. If the directory
   already exists (multiple runs same day), append a sequence number.

4. Create all tasks upfront with `TaskCreate` so the plan is visible from the start.

### Phase 1: Fork (parallel, isolated)

Spawn N competitor agents (sonnet, general-purpose) in parallel, each with `isolation: "worktree"`.

Each competitor receives a `SpeculativeSpec` (see `references/communication-schema.md`) containing:
- The problem description and success criteria
- An approach hint if the user provided one (otherwise: null — competitor chooses its own approach)
- Its competitor ID (e.g., `competitor-1`)
- The path to its dedicated worktree
- The output path for its `ImplementationResult`

Competitors work in full isolation — they do NOT communicate with each other or with the lead
during implementation. The lead does not intervene unless a competitor explicitly signals it
is blocked.

**CronCreate watchdog:** After spawning competitors, create a CronCreate job with a 60-second
interval. The watchdog checks `TaskList` for competitor tasks with no recent updates and sends
a status alert to the lead if any competitor has been idle for 2+ consecutive checks. The
watchdog monitors — it does NOT intervene directly. CronDelete the watchdog when Phase 1
completes (all competitors have submitted ImplementationResults).

**Completion:** Each competitor writes its `ImplementationResult` to
`{run_dir}/implementations/competitor-{id}.json` and signals completion via SendMessage.
The lead waits for all competitors before proceeding to Phase 2.

**Time cap:** If a competitor is significantly slower than others and all others have finished,
the lead sends one status check. If the competitor does not complete within a reasonable
additional time, the lead marks it as timed-out and proceeds with the results it has. A
run with 1 of 2 competitors can still proceed — the judge evaluates what it received.

**Failed competitor:** If a competitor sends an `ImplementationResult` with `status: "failed"`,
the lead records the failure, CronDeletes the watchdog, and proceeds with remaining results.
If ALL competitors fail, escalate to the user via `AskUserQuestion` before continuing.

### Phase 2: Evaluate

Spawn a single judge agent (opus, general-purpose) — this is a high-judgment task.

The judge receives a `JudgmentRequest` containing:
- All `ImplementationResult` objects from Phase 1
- The original `SpeculativeSpec` (problem + success criteria + weights)
- The audit trail path for writing its output

**Important — judge independence:** The judge receives competitor self-assessments and approach
descriptions, NOT raw code by default. This prevents bias from code style. If the judge needs
to inspect code to make an informed decision, it reads specific files from each worktree path
using the Read tool. The judge should only inspect code when self-reported results are
insufficient to distinguish approaches.

The judge produces a `JudgmentResult` (see `references/communication-schema.md`) with:
- A winner (or "hybrid" if combining elements is better than either alone)
- A scoring matrix with per-criterion scores for each competitor
- Clear rationale for the decision
- A `hybrid_recommended` flag with specific elements to combine (if applicable)

The judge writes its result to `{run_dir}/judgment.json` and signals the lead.

### Phase 3: Select & Merge

Present the judgment to the user and execute the selection.

1. Present the `JudgmentResult` to the user via `AskUserQuestion`:
   - Show the scoring matrix (per-criterion scores, weighted totals)
   - Show the winner and rationale
   - If `hybrid_recommended`, explain what elements could be combined and from which competitors
   - Ask: "Accept this selection, override to a different competitor, or proceed with hybrid?"

2. Based on user response:
   - **Accept winner:** Merge the winning worktree's changes into the main branch (see merge
     procedure below).
   - **Override:** User selects a different competitor — merge that worktree instead.
   - **Hybrid:** Proceed to Phase 3.5.

3. **Merge procedure for winner:**
   - Identify the winning worktree path from the ImplementationResult
   - Use `git diff <main-branch> <worktree-branch>` to show the changes
   - Apply the winner's changes to the main working tree
   - Run the test suite to verify the merge produced a clean state
   - Report files changed

4. **Cleanup:** Delete all loser worktrees. The winning worktree is cleaned up after its changes
   are confirmed merged. Write the final report to `{run_dir}/speculative-report.md`.

### Phase 3.5: Hybrid (conditional)

Only triggered if the judge recommends hybrid AND the user approves. This is NOT the default path.

1. The judge's `hybrid_elements` field lists specific elements to combine
   (e.g., "competitor-1's error handling approach with competitor-2's data structure choice").

2. Spawn a synthesis agent (sonnet, general-purpose) with:
   - The hybrid_elements list from JudgmentResult
   - Read access to both competitor worktrees
   - Write access to the main branch working tree

3. The synthesis agent combines the specified elements and produces a final implementation.
   It reports back with files changed and test results.

4. After synthesis, run the test suite to verify. Clean up all competitor worktrees.
   Write the final report to `{run_dir}/speculative-report.md`.

---

## Orchestration Flow Diagram

```
User request
     |
     v
Phase 0: Specification
  +-- AskUserQuestion (ambiguity, criteria, competitor count)
  +-- Create hack/speculative/YYYY-MM-DD/
  +-- TaskCreate for all phases
     |
     v
Phase 1: Fork (parallel, isolated)
  +-- Spawn N competitors (sonnet, worktree isolation)
  +-- CronCreate watchdog (60s interval)
  +-- Wait for all ImplementationResults
  +-- CronDelete watchdog
     |
     v
Phase 2: Evaluate
  +-- Spawn judge (opus)
  +-- Judge reads ImplementationResults (+ worktree code if needed)
  +-- Judge writes JudgmentResult to {run_dir}/judgment.json
     |
     v
Phase 3: Select & Merge
  +-- AskUserQuestion (present scoring matrix, get user approval)
  +-- Merge winner into main branch
  +-- Run tests
  +-- Cleanup loser worktrees
  +-- Write speculative-report.md
     |
     +--(if hybrid approved)---+
                               v
                     Phase 3.5: Hybrid
                       +-- Spawn synthesis agent
                       +-- Combine elements from multiple competitors
                       +-- Run tests
                       +-- Cleanup all worktrees
                       +-- Write speculative-report.md
```

---

## Lead Responsibilities

You are the orchestrator. You define the spec, route competitor results to the judge, present
the judgment to the user, and execute the merge. You never implement code yourself.

### Task Graph

Create tasks upfront in Phase 0 with `TaskCreate`. Mark them `in_progress` as each phase begins
and `completed` when done. Blocked tasks (e.g., Phase 3.5 pending user decision) should remain
`pending` until triggered.

### Competitor Monitoring

Track each competitor's status. When all competitors have reported, CronDelete the watchdog
and proceed to Phase 2. If a competitor goes idle (no messages, no task updates), the watchdog
alerts you — send one status check before assuming failure.

### User Communication

Phase 0 is the only mandatory user interaction before Phase 3. After gathering the spec and
getting user confirmation on evaluation criteria and competitor count, competitors run without
further user interaction until the judge has finished. The user resumes at Phase 3 to approve
the selection.

### Audit Trail

Write all structured outputs to `hack/speculative/YYYY-MM-DD/`:
- `implementations/competitor-{id}.json` — each competitor's ImplementationResult
- `judgment.json` — judge's JudgmentResult
- `speculative-report.md` — final human-readable completion report

---

## When to Skip Phases

| Phase | Skip When |
|-------|-----------|
| Phase 3.5: Hybrid | Judge does not set `hybrid_recommended: true`, OR user declines hybrid |
| NEVER SKIP | Phase 0 (specification), Phase 1 (competitors), Phase 2 (judge), Phase 3 (selection) |

---

## Cost Awareness

This skill spawns 2-4 competitor agents plus a judge. Each competitor runs a full implementation.
Use it only when the choice between approaches has real consequences and "try both" is genuinely
better than the architect choosing one.

### When to Use /speculative vs. Alternatives

| Scenario | Recommended Approach |
|----------|----------------------|
| One clearly correct approach | Implement directly (no speculative needed) |
| Multiple viable approaches, real trade-offs | `/speculative` |
| Architectural decision with broad impact | `/speculative` with 2-3 competitors |
| Algorithm selection (e.g., LRU vs TTL cache) | `/speculative` |
| "I want to see which is faster" | `/speculative` with performance as top criterion |
| Simple feature (1-3 files, obvious approach) | Single targeted subagent |
| Large feature needing full pipeline rigor | `/swarm` (optionally with Phase 2.7 speculative fork) |

### Model Cost Hierarchy

| Role | Model | Rationale |
|------|-------|-----------|
| Competitors | sonnet | Implementation work — capable but cost-efficient |
| Judge | opus | Judgment-heavy evaluation — worth the cost for fair comparison |
| Synthesis (Phase 3.5) | sonnet | Mechanical combination of known elements |

---

## References

| File | Content |
|------|---------|
| `references/communication-schema.md` | JSON schemas for SpeculativeSpec, ImplementationResult, JudgmentResult |
| `references/agent-prompts.md` | Full prompt templates for competitor and judge agents |
