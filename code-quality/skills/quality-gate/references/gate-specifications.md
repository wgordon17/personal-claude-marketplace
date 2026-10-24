# Quality Gate Specifications & Output Template

Detailed check tables, procedures, and terminal output format for the 6 blocking/conditional gates in `/quality-gate`.

---

## Layer 1.5 & Layer 2 Dispatch Blocks

```
Spawn all 4 reviewers simultaneously:

Reviewer 1 — Security (code-quality:security):
  Agent(
    description="Security domain review",
    model="sonnet",
    prompt=<see [{skill_dir}/references/subagent-prompts.md](./subagent-prompts.md), Domain Reviewer: Security>
  )

Reviewer 2 — QA (code-quality:qa):
  Agent(
    description="QA domain review",
    model="sonnet",
    prompt=<see [{skill_dir}/references/subagent-prompts.md](./subagent-prompts.md), Domain Reviewer: QA>
  )

Reviewer 3 — Performance (code-quality:performance):
  Agent(
    description="Performance domain review",
    model="sonnet",
    prompt=<see [{skill_dir}/references/subagent-prompts.md](./subagent-prompts.md), Domain Reviewer: Performance>
  )

Reviewer 4 — Code Review (code-quality:code-reviewer):
  Agent(
    description="Code style and maintainability review",
    model="sonnet",
    prompt=<see [{skill_dir}/references/subagent-prompts.md](./subagent-prompts.md), Domain Reviewer: Code-Reviewer>
  )
```

**Subagent A: Completeness Reviewer (opus)**

```
PASS 1:
  pass1_result = Agent(
    description="Completeness review",
    model="opus",
    prompt=<see [{skill_dir}/references/subagent-prompts.md](./subagent-prompts.md), Subagent A Pass 1>
  )
  → Save pass1_result.agentId
  → Fix ALL findings

PASS 2 (MANDATORY — do not skip):
  SendMessage(
    to=<agentId from Pass 1>,    ← MUST be agentId, not agent name
    message="Here are the fixes I made: {summary_of_fixes}.
             Review them. Also: what did YOU miss on your first pass?
             You had fresh eyes but still have blind spots. Look again.",
    summary="Pass 2 completeness re-review"
  )
  → WAIT for response (do not proceed to gates)
  → Fix ALL findings from Pass 2
```

**Subagent B: Adversarial Reviewer (opus)**

```
PASS 1:
  pass1_result = Agent(
    description="Adversarial review",
    model="opus",
    prompt=<see [{skill_dir}/references/subagent-prompts.md](./subagent-prompts.md), Subagent B Pass 1>
  )
  → Save pass1_result.agentId
  → Fix ALL findings

PASS 2 (MANDATORY — do not skip):
  SendMessage(
    to=<agentId from Pass 1>,    ← MUST be agentId, not agent name
    message="Here are the fixes: {summary_of_fixes}.
             Verify they're correct. What else breaks in production?",
    summary="Pass 2 adversarial re-review"
  )
  → WAIT for response (do not proceed to gates)
  → Fix ALL findings from Pass 2
```

---

## Lifecycle Gate (BLOCKING)

Validates artifact lifecycle counters on the plan file. Cannot proceed past this gate without
completing all applicable checks. Runs BEFORE the Memory Gate to ensure the plan file still
exists (Memory Gate may delete completed plans).

**Trigger:** Reuse `{plan_file_path}` and `{plan_content}` from Step 0. If no plan file was
discovered, skip entire gate with `SKIP (no plan)`.

**Backward compatibility:** If the plan file exists but has no `**Iterations:**` block
(pre-feature plan), skip with `SKIP (pre-feature plan)`. Do NOT fail structural checks on
plans created before this feature existed.

### Structural Checks

- All 5 counters present (review-cycle, fix-cycle, pr-review-cycle, pr-fix-cycle, quality-gate) → PASS/FAIL
- All values are integers >= 0 → PASS/FAIL

**Classification:** Structural failures are `needs-fix` (blocking).

**Remediation:** Missing individual counters → add missing counters at 0, re-read the plan
file from disk, then re-run structural checks. If structural checks still fail after one
remediation attempt, report FAIL (do not loop). Non-integer values → report FAIL, do not
auto-fix (data corruption).

### Logical Checks (advisory — WARN, not blocking)

- At least one review counter > 0 (`review-cycle` > 0 OR `pr-review-cycle` > 0) → WARN if
  neither is > 0 (suggests reviewing before claiming done, but does not block — common workflow
  runs quality-gate immediately after incremental-planning before any review has occurred)

### Behavioral Checks (advisory — WARN, not blocking)

Session transcript markers verify a minimum floor: "a review/fix occurred in this session."
They prove "this happened at some point" — NOT "this happened after the most recent change."

**Reliability caveat:** Behavioral checks are advisory for two reasons: (1) cross-session
workflows — markers from session A are absent in session B, and (2) LLM recall limits — markers
from early output may be outside the active context window. Failures are advisory warnings only.

- If session contains `PLAN REVIEW —` marker: verify `review-cycle` > 0 → WARN if mismatch
- If session contains `CODE REVIEW — PR #` marker: verify `pr-review-cycle` > 0 → WARN if mismatch
- If session contains `FIX REPORT` marker: parse the `Source:` line to determine fix source.
  If source was `plan-review` → verify `fix-cycle` > 0. If source was `pr-review` → verify
  `pr-fix-cycle` > 0. If source was `bug-investigation` → skip this check.
  WARN if mismatch.

### Counter Increment (runs AFTER Lifecycle Gate, only on PASS)

Only proceed if the Lifecycle Gate result is PASS (including PASS after successful
remediation). For FAIL, SKIP (no plan), and SKIP (pre-feature plan), do not increment.

1. Re-read the plan file from disk using `{plan_file_path}` from Step 0
2. Find the `**Iterations:**` block
3. Read the current `quality-gate` value N from the line matching `- quality-gate: {N}`
4. Verify N is a non-negative integer (digits only, e.g. `0`, `1`, `12`). If N is not a valid
   integer, skip the increment silently
5. Increment `quality-gate` by 1: use Edit to replace `- quality-gate: {N}` with
   `- quality-gate: {N+1}`, where `{N}` is the actual integer read in step 3
6. If no plan file or no block, skip silently

---

## Memory Gate (BLOCKING)

Cannot proceed past this gate without completing all applicable checks.
Memory that drifts from reality is worse than no memory.

Update project memory files per the content placement rules in
`[{skill_dir}/../../references/project-memory-reference.md](../../../references/project-memory-reference.md)`. Key surfaces:

| Memory Surface | Action |
|----------------|--------|
| **{memory_dir}/TODO.md** | Mark completed items `[x]` with date. Add new tasks. Remove stale items. |
| **{memory_dir}/PROJECT.md** | Document decisions, gotchas, architecture changes from this work. |
| **{memory_dir}/NEXT.md** | Update pointer to actual next task. |
| **{memory_dir}/SESSIONS.md** | Append 3-5 bullet summary (if session-end isn't imminent). |
| **{memory_dir}/plans/** | Delete completed plans. Update partial plans with progress. Delete superseded plans. |
| **claude-mem** | Search for related observations. Flag stale ones. Save new cross-session insights. |
| **Serena memories** | List, verify accuracy, update stale, write new project knowledge. |
| **Auto-memory** | Check project memory path, verify and update if relevant. |
| **{memory_dir}/LESSONS.md** | Check if current work produced principle-level lessons worth capturing. If human corrections or rejected approaches occurred, extract and write lessons before proceeding. |

**Skip conditions:** No memory dir found (per Directory Detection section of the reference) → skip
memory-dir checks. No plans directory → skip plan checks. claude-mem MCP unavailable → skip
claude-mem checks. Serena MCP unavailable → skip Serena memory checks. Pure question with no
persistent insights → skip memory saves. LESSONS.md doesn't exist → skip LESSONS check.

---

## Artifact Gate (BLOCKING)

Ensures work products aren't lost AND project conventions are satisfied.

| Work Type | Gate Criteria |
|-----------|---------------|
| **Code** | Tests pass. Changes committed, pushed, and PR created (or ready to — do not commit without user request). "In the working tree" is not sufficient. |
| **Planning** | Plan file written. Tasks concrete and actionable. No hand-waving. |
| **Research** | Key findings documented persistently (claude-mem, PROJECT.md, or file). |
| **Config** | Valid syntax. Consistent with existing patterns. Validated. |
| **Question** | No gate (unless answer revealed something worth persisting). |

**Project-specific checks (all work types) — final re-verification:**
Round 2 checked project rules during review. This gate re-verifies AFTER all fixes are applied
(Layer 1 fixes + Layer 2 subagent fixes may have changed the state). Re-read CLAUDE.md:
- Version bumps in plugin/package manifests (if project requires them)
- Registry/marketplace entries updated to match
- Will the change actually reach users after merge? (cache invalidation, version detection)
- Any required companion files (changelogs, migration guides, etc.)

**Upstream state verification (if a PR exists):**
Do not assume prior pushes landed or that the PR is still open. Verify:
- the GitHub MCP `pull_request_read` tool (preferred) or `gh pr view` — is the PR still open, or was it already merged/closed?
- `git log origin/main..HEAD` — does the branch contain ALL intended commits?
- If force-pushing to an already-merged branch, commits are silently orphaned
- After merge: `git log origin/main` — confirm the merge includes your changes

---

## BUGS.md Verification Gate (CONDITIONAL)

Ensures bug tracking stays current as work progresses. This gate only runs when a
`{memory_dir}/BUGS.md` file exists — skip entirely if no memory directory is found or
no BUGS.md exists.

**Branch prerequisite:** Run `git diff --name-only origin/main...HEAD`. If the output is
empty (e.g., running on main itself, or no commits ahead of origin/main), skip the
Stale Tracked In and PR detection checks silently — there are no branch-specific changes
to match against. The Merged detection and Stale entries checks still apply (they operate
on BUGS.md state alone, not branch context).

| Check | Action |
|-------|--------|
| **Stale Tracked In** | *Requires non-empty git diff (skip if on main or no ahead commits).* For each entry with `**Status:** Fix Ready` and `**Tracked In:** —` (or missing field): check if the current branch's changed files (from `git diff --name-only origin/main...HEAD`) overlap with the entry's `### Files Involved` paths. Path matching: strip `:NN` line number suffixes, then match by exact file path OR common directory prefix of at least 3 components — prefixes of 1 or 2 components are too broad and do not count. If overlap exists, update `**Tracked In:**` to `PR: #N` (via `gh pr view --json number`) or `Branch: {branch-name}` if no PR exists. |
| **PR detection** | *Requires non-empty git diff (skip if on main or no ahead commits).* For entries with `**Tracked In:** Plan: ...`, `**Tracked In:** Roadmap: ...`, or `**Tracked In:** Branch: ...`: check if a PR exists for the current branch (via `gh pr view --json number`). If a PR exists, verify the entry's `### Files Involved` paths overlap with the current branch's changed files using the same path matching as Stale Tracked In (strip `:NN`, exact match or depth-3 prefix). For `**Tracked In:** Branch: {name}` entries specifically, also verify the branch name matches the current branch (`git branch --show-current`) — skip entries where the branch name differs (they belong to a different session's scope). If overlap confirmed (and branch name matches for Branch entries), update to `**Tracked In:** PR: #N`. |
| **Merged detection** | For entries with `**Tracked In:** PR: #N`: check PR status via the GitHub MCP `pull_request_read` tool or `gh pr view #N --json state,mergedAt`. If merged, update to `**Tracked In:** PR: #N (merged YYYY-MM-DD)` and set `**Status:** Fixed` if still at `Fix Ready`. |
| **Stale entries** | Flag any entry with `**Status:** Root Cause Found` and `**Reported:**` date older than 14 days where `**Tracked In:**` is either absent or `—` (untracked). Report in gate output as informational (not blocking). |

**Backward compatibility:** Entries created before the `Tracked In` field was added may lack
the field entirely. Treat a missing `**Tracked In:**` line the same as `**Tracked In:** —`
for all checks. If updating such an entry, insert `**Tracked In:** {value}` after the
`**Impact:**` or `**Severity:**` line.

**Gate behavior:** This gate is CONDITIONAL, not BLOCKING. It performs best-effort updates
and reports findings, but does not prevent proceeding to the Documentation Gate. Bugs that
can't be matched are noted, not escalated.

**Chat output:** If any updates were made, report:
> "BUGS.md: Updated N entries (M to PR status, K to Merged). L stale entries flagged."

If no BUGS.md exists or no updates needed: omit from gate output entirely.

---

## Documentation Gate (BLOCKING)

Ensures documentation accurately reflects the current state of the codebase after all changes.
Cannot proceed past this gate without completing all applicable checks.

**This gate catches code→docs gaps** — features that exist on disk but aren't documented.
Round 2 checks docs→code (do documented claims match reality). This gate checks the inverse.

Use `[{skill_dir}/../../references/documentation-taxonomy.md](../../../references/documentation-taxonomy.md)` for all definitions.

| Check | Action |
|-------|--------|
| **Trigger check** | Do any changes in this work match documentation triggers (taxonomy § Triggers)? If no triggers fire, skip remaining checks. |
| **Component inventory** | Use ecosystem-specific discovery patterns (taxonomy § Component Discovery) to count on-disk components. Compare against documentation surfaces (taxonomy § Surfaces). Mismatches are blocking. |
| **Feature coverage** | For each trigger that fired: is there a corresponding documentation update? New component without doc entry = fail. Removed component with stale references = fail. |
| **Cross-surface consistency** | Apply consistency rules (taxonomy § Cross-Surface Consistency). All surfaces must agree on names, counts, and descriptions. |

**Skip conditions:** No documentation triggers fire (per taxonomy § "Changes that DO NOT require
documentation updates"). When in doubt, run the check.

---

## No Data Loss Gate (BLOCKING)

Ensures work artifacts are persisted to a **durable, externally-trackable location** — not just
in the conversation context or the local working tree. Cannot proceed past this gate without
confirming each applicable item.

**THE WORKING TREE TRAP:**
"Changes are in the working tree" is NOT a durable location. Working tree changes are:
- Lost on `git checkout`, `git stash`, branch switches, or worktree cleanup
- Invisible to other sessions, agents, or future conversations
- Not findable by anyone searching git history, PRs, or project memory
- Equivalent to "I wrote it on a sticky note" — it exists, but nobody will find it

Durable means: **another agent in a fresh session can find the work without being told where
to look.** PRs show up in `gh pr list`. Commits show up in `git log`. Plan files show up in
`{memory_dir}/plans/`. Project decisions show up in `{memory_dir}/PROJECT.md`. Conversation-only knowledge
and working-tree-only changes fail this test.

| Work Type | Persistence Check |
|-----------|-------------------|
| **Code** | Changes are on a **pushed feature branch with a PR** (verifiable via `gh pr view`). Not just committed locally — pushed and PR-visible. Uncommitted or committed-but-not-pushed work is NOT durable. |
| **Planning** | Plan written to `{memory_dir}/plans/{run-id}-<topic>.md`. New tasks added to `{memory_dir}/TODO.md`. Decisions documented in `{memory_dir}/PROJECT.md`. |
| **Research** | Findings written to `{memory_dir}/research/{run-id}-<topic>.md` (if memory dir exists), or to `{memory_dir}/PROJECT.md`, or saved to claude-mem. Key findings must survive session end. |
| **All types** | For each significant artifact, state WHERE it is persisted and HOW a future agent finds it. "It's in the working tree" or "it's in the conversation" fails this gate. |

**Skip conditions:** Pure question with no artifacts → skip. Work explicitly scoped as
"conversation only" by the user → skip.

---

## Final Verification

| Work Type | Verification |
|-----------|-------------|
| **Code** | Run test suite + lint. Compare against baseline. |
| **Planning** | Re-read plan end-to-end. Walk through mentally step-by-step. |
| **Research** | Re-read findings against original question. |
| **Config** | Validate syntax. Verify integration. |

Final check:
```
serena::think_about_whether_you_are_done → "Is this genuinely complete?"
```

---

## Output

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUALITY GATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Work type: [code / research / plan / config / question / mixed]

Layer 1 — Self-Review:
  Rounds completed: [N/6]
  Issues found: [count]
  Issues fixed: [count]
  Identified-but-unactioned caught: [count]
  Project rules violations caught: [count]
  Round 4 (Simplicity gate): [PASS — 0 needs-fix | BLOCKED — N needs-fix fixed]
  Net lines delta: +[added] / -[removed] (net: [+/-X])

Layer 1.5 — Domain Expert Review: [N/A for non-code | COMPLETE]
  Security reviewer: [count] findings ([needs-fix/needs-input breakdown])
  QA reviewer: [count] findings ([needs-fix/needs-input breakdown])
  Performance reviewer: [count] findings ([needs-fix/needs-input breakdown])
  Code-reviewer: [count] findings ([needs-fix/needs-input breakdown])
  needs-fix fixed before Layer 2: [count]
  needs-input resolved via user decision: [count]

Layer 1.75 — Plan Adherence: [N/A (no plan) | COMPLETE]
  Plan file: [path or "not found"]
  Tasks verified: [N/M]
  Tasks escalated: [count] ([approved/blocked/returned-for-implementation])
  File structure: [MATCH | N discrepancies]

Layer 2 — Subagent Reviews:
  Subagent A (Completeness): [count] findings across 2 passes — agent ID: [id]
  Subagent B (Adversarial): [count] findings across 2 passes — agent ID: [id]
  (Layer 2 MUST show agent IDs. "SUBSTITUTED" or "N/A" is NEVER valid here.)

Lifecycle Gate: [PASS / FAIL / SKIP (no plan) / SKIP (pre-feature plan)]
  (For SKIP variants, omit the sub-lines below — show only the top-level result)
  Counters (pre-increment): review-cycle=[N] | fix-cycle=[N] | pr-review-cycle=[N] | pr-fix-cycle=[N] | quality-gate=[N]
  Structural: [all 5 counters, valid integers]
  Logical (advisory): [review>0 or pr-review>0 — WARN only]
  Behavioral (advisory): [markers vs counters — WARN only]

Memory Gate: [PASS / UPDATED]
  hack/ files: [updated / N/A]
  Plans: [N completed, N updated, N unchanged]
  Stale memories flagged: [count]
  New memories saved: [count]

Artifact Gate: [PASS / N/A]
  [work-type-specific status]

BUGS.md Gate: [PASS / UPDATED] (omit section if no BUGS.md or no updates)
  Entries updated: [count] ([PR status / Merged / Tracked In set])
  Stale entries flagged: [count]

Documentation Gate: [PASS / SKIP / N/A]
  Components on disk: [count] | Documented: [count]
  Surfaces checked: [list]
  Gaps found: [count]

No Data Loss Gate: [PASS / N/A]
  [artifact persistence status]

Final Verification: [PASS / FAIL]

Overall: [PASS / NEEDS WORK]
[If NEEDS WORK: list what remains]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Relationship to Other Skills

| Skill | Relationship |
|-------|-------------|
| `code-quality:code-simplifier` | Spawned in Round 4 (Simplicity lens, BLOCKING sub-gate) for dead code, unnecessary abstractions. Uses `[{skill_dir}/../../references/simplification-checklist.md](../../../references/simplification-checklist.md)` and `[{skill_dir}/../../references/dependency-evaluation.md](../../../references/dependency-evaluation.md)`. |
| `code-quality:plan-adherence` | Spawned in Layer 1.75 for plan file verification (planning/mixed work types). |
| `code-quality:reflect` | Invoked for metacognitive checkpoints via Serena reflection tools. |
| `code-quality:security` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `code-quality:qa` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `code-quality:performance` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `code-quality:code-reviewer` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `session-end` | Complementary — quality-gate reviews accuracy; session-end does final save. |
| `swarm` Phase 7 | Invokes quality-gate as the final validation step. |
| `code-quality:fix` | Standalone finding fixer for non-swarm workflows. Not a source — quality-gate fixes findings inline. Suggest /quality-gate after /fix for verification. |
| `code-quality:bug-investigation` | BUGS.md Verification Gate updates `Tracked In` field for entries created by `/bug-investigation`. |
| `code-quality:incremental-planning` | Producer: sets `Tracked In: Plan: ...` on overlapping BUGS.md entries when a plan is created. quality-gate verifies and advances those references. |
| `code-quality:roadmap` | Producer: sets `Tracked In: Roadmap: plans/{filename}.md (Phase N)` on overlapping BUGS.md entries during roadmap generation. quality-gate verifies and advances those references. |

---

## Stop Hook Safety Net

A global Stop hook in `dev-guard` (`type: command`) catches premature completion claims that bypass
this skill. It uses a two-script architecture for speed: `stop-hook.py` (stdlib-only, <200ms)
performs deterministic triage — loop guard, transcript delta, signal detection (write tools, MCP
writes, completion claim regex, git diff hash change) — and fast-exits for low-signal cases.
When signals warrant deeper evaluation, it delegates to `stop-hook-llm.py` which calls
claude-sonnet-4-6 via Vertex AI with an adaptive prompt based on work type (code, research,
questions, planning) and trigger reasons. Fails open on any infrastructure error.
Uses `stop_hook_active` guard to prevent infinite loops.
