---
name: fix
description: |
  Comprehensive finding fixer. Use when asked to "fix these findings", "fix the review",
  "address the issues", or after running /pr-review, /plan-review, or /bug-investigation
  and wanting to act on the findings. Reads findings from the current session context,
  investigates each finding via background agents, and implements all fixes with the lead.
  Auto-detects fix target (plan file, code, bugs). For plan-review Research Gaps and
  Unknown Unknowns, runs actual spikes and verification — executes the research, not just
  documents it.
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Agent, AskUserQuestion, WebSearch, WebFetch]
---

# Fix Skill

**Never auto-commits.** All fixes are left in the working tree for user review.

## Usage

```
/fix
```

No arguments required. Auto-detects the review source and fix target from session context.

Must run in the same session as the upstream review skill — /fix reads findings from session
output. Exception: bug-investigation findings persist in BUGS.md and work across sessions.

---

## Phase 0 — Context Detection

Examine the current session to determine which review skill ran and what the fix target is.

### Detection Rules

Scan the current session output for sentinel strings in this order:

| Sentinel | Fix Target | Extraction |
|---|---|---|
| `PLAN REVIEW —` | Plan file | Extract path from the header line: `PLAN REVIEW — {plan_file_path}` |
| `CODE REVIEW — PR #` | Code (PR diff) | Extract PR number from the header line: `CODE REVIEW — PR #{number}` |
| `{memory_dir}/BUGS.md` (file check) | Bug resolutions | Read BUGS.md; extract entries with `**Status:** Root Cause Found` or `**Status:** Fix Ready` |

Detect `memory_dir` per `code-quality/references/project-memory-reference.md` (Directory Detection and Worktree Resolution sections).

### Resolution

| Scenario | Action |
|---|---|
| Exactly one source detected | Proceed with that source as fix target |
| Multiple sources detected | `AskUserQuestion` — present the sources and ask which to fix |
| No sources detected | Stop: "No review output found in this session. Run `/pr-review`, `/plan-review`, or `/bug-investigation` first." |
| Source detected but zero actionable findings | Stop: "Nothing to fix — no actionable findings in the {source} output." |

### Confirmation

Print to the user:

```
Fix target: {source} — {target_description}
Actionable findings: {count}
```

---

## Phase 0.5 — Finding Normalization

Normalize all findings from the detected source(s) into a common internal format. This phase
produces a flat list of normalized findings that all subsequent phases consume.

### Common Finding Schema

Each normalized finding tracks:

- `id` — generated identifier using pattern `{source-prefix}-{category-abbrev}-{N}`:
  - pr-review prefix: `pr` (e.g., `pr-test-1`, `pr-sec-2`, `pr-corr-3`)
  - plan-review prefix: `pl` (e.g., `pl-feas-1`, `pl-rsch-2`, `pl-arch-3`)
  - bug-investigation prefix: `BUG` (e.g., `BUG-001`, matching the BUGS.md entry number)
- `source` — one of: `pr-review` | `plan-review` | `bug-investigation`
- `classification` — always `needs-fix` (see note below on needs-input)
- `category` — the category section heading from the upstream output (e.g., `TESTING GAPS`,
  `RESEARCH GAPS`, `CORRECTNESS`)
- `location` — `file:line` for code findings, plan section name for plan findings, `BUG-NNN` for
  bug entries
- `description` — the finding text as written in the review output
- `evidence` — the reviewer's evidence line from the review output
- `suggested_fix` — `null` for pr-review and plan-review (not present in terminal output);
  the Resolution Plan checklist items for bug-investigation entries
- `is_research_gap` — `true` if the finding appears under the `RESEARCH GAPS` category section OR
  has a `[Reviewer]` tag of `Unknown Unknowns`. Classification is structural (category + reviewer
  tag) — do NOT use keyword matching on description text.
- `verifier_verdict` — `"needs_context"` if from the upstream Needs Context section, otherwise `null`
- `spike_question` — the specific assumption to verify (extracted from Research Gap / Unknown Unknowns finding text). Populated only when `is_research_gap == true`.
- `plan_context` — surrounding plan text (5-10 lines around the affected section). Populated only when `is_research_gap == true`.

### Source-Specific Normalization

**pr-review findings:**

Categories (in display order): Testing Gaps, Correctness, Security, Architecture, Decisions Needed,
Performance, Style & Conventions. Extract all findings from each category section. Also extract
findings from the `─── Needs Context ───` section with `verifier_verdict: "needs_context"`.
Skip findings in the User Decisions section — these were already resolved by the user during the upstream review and should not be re-processed.

Category abbreviations for IDs: `test`, `corr`, `sec`, `arch`, `dec`, `perf`, `style`.

**plan-review findings:**

Categories (in display order): Research Gaps, Feasibility, Scope, Dependencies, Architecture,
Security, Specification. Extract all findings from each category section. Also extract findings
from the `─── Needs Context ───` section with `verifier_verdict: "needs_context"`. Set
`is_research_gap: true` per the structural rules above (category = RESEARCH GAPS, OR reviewer tag =
Unknown Unknowns) — not by scanning description text.
Skip findings in the User Decisions section — these were already resolved by the user during the upstream review and should not be re-processed.

Category abbreviations for IDs: `rsch`, `feas`, `scope`, `dep`, `arch`, `sec`, `spec`.

**bug-investigation findings:**

Read `{memory_dir}/BUGS.md` directly (do not rely on session output). Extract all `## BUG-NNN`
entries whose `**Status:**` field is `Root Cause Found` or `Fix Ready`. All bug entries are
`needs-fix` by definition — a bug at these statuses has a known root cause and is ready to fix.
The `suggested_fix` field maps to the entry's `### Resolution Plan` checklist.

### Note on needs-input

Upstream skills (pr-review, plan-review) resolve ALL `needs-input` findings via `AskUserQuestion`
before producing terminal output. By the time this skill runs, all findings visible in the terminal
output are actionable. Normalize all of them as `needs-fix`. Do not re-classify or re-triage based
on description text.

---

## Phase 1 — Triage, CWD Validation, and Scope Assessment

### Step 1a: CWD Boundary Validation

Resolve all finding locations to absolute paths. Normalize paths first: collapse `../` and `./` sequences without resolving symlinks. Then verify the normalized path falls within the project root. Any path that falls outside the current project
root is `out_of_scope` — mark it and exclude it from all subsequent phases. Never pass out-of-scope
paths to investigators or fixers.

### Step 1b: Triage Assessment

Assess each finding and assign a triage label:

| Assessment | Criteria | Action |
|---|---|---|
| Direct fix | Clear description, unambiguous location, no architectural decisions required | Queue for Phase 2 investigation |
| Spike execution | `is_research_gap == true` AND source is `plan-review` | Queue for Phase 2 spike investigation |
| Needs refinement | Multiple valid approaches, UX implications, or architectural tradeoffs that cannot be resolved without user input | Queue for Phase 2 (marked for user confirmation before implementation) |
| Needs plan | Any finding touching 5+ files OR requiring architectural redesign beyond localized changes | Recommend `/incremental-planning`; skip this finding |
| Out of scope | Path outside CWD (identified in Step 1a) | Skip with note |
| Unverified | `verifier_verdict == "needs_context"` | Queue for Phase 2 — investigator will attempt to verify before fixing |

### Step 1c: Triage Summary

Present the triage summary to the user before proceeding:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX TRIAGE — {source} — {target_description}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Direct fix ({count}):
  {id}: {description} [{location}]
  ...

Spike execution ({count}):
  {id}: {description} [{location}]
  ...

Needs refinement ({count}):
  {id}: {description} — will confirm approach before implementing
  ...

Needs plan ({count}):
  {id}: {description} — recommend /incremental-planning
  ...

Out of scope ({count}):
  {id}: {description} — outside project root, skipped
  ...

Unverified ({count}):
  {id}: {description} — will attempt verification in investigation
  ...

Proceeding with {actionable_count} findings ({direct_fix_count} direct +
{spike_count} spikes + {needs_refinement_count} needing refinement +
{unverified_count} unverified).
Skipping {skip_count} ({needs_plan_count} needs-plan + {oos_count} out-of-scope).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Omit sections with zero findings.

---

## Phase 2 — Parallel Investigation

Dispatch background investigator agents for all queued findings. Investigators are read-only — they
produce structured results that the lead uses in Phase 3 to implement fixes.

### Grouping Rules

Before dispatch, group findings by source and location to minimize agent count:

| Source | Grouping rule |
|---|---|
| pr-review (code) | Group by file path — all findings targeting the same file go to one agent |
| plan-review (non-spike) | Group ALL non-spike plan findings into one agent — they all target the same plan file |
| plan-review (spike, `is_research_gap == true`) | One agent PER finding — do NOT group spikes; parallel execution is more valuable than shared context |
| bug-investigation | One agent per BUG-NNN entry |

### Agent Dispatch

For each group of code, plan, or bug findings, spawn one standard investigator:

```
Agent(
  description="Investigate {N} finding(s) in {file/section/BUG-NNN}",
  subagent_type="general-purpose",
  model="sonnet",
  mode="bypassPermissions",
  run_in_background=true,
  prompt=<standard investigator template from code-quality/skills/fix/references/investigator-prompt.md,
          with one <finding-data> block per finding in the group>
)
```

For each spike finding (`is_research_gap == true`, source is plan-review), spawn one spike investigator:

```
Agent(
  description="Spike: {brief description of what to verify}",
  subagent_type="general-purpose",
  model="sonnet",
  mode="bypassPermissions",
  run_in_background=true,
  prompt=<spike investigator template from code-quality/skills/fix/references/investigator-prompt.md,
          with the finding's spike_question and plan_context fields populated>
)
```

> `mode="bypassPermissions"` is required — investigators run in background and cannot prompt for
> permissions interactively. Sonnet is used for both types — investigators collect evidence only;
> the lead reviews all results before acting.

Dispatch all agents in parallel. Wait for all to complete before proceeding.

> In practice, upstream reviewers produce 5-15 findings. With file-based grouping, this typically results in 3-10 investigator agents — well within Claude Code's concurrency limits.

### Collecting and Routing Results

After all investigators complete, route each result by verdict:

| Verdict | Action |
|---|---|
| `resolution` | Queue for Phase 3 implementation |
| `refinement_needed` | Present options via `AskUserQuestion` (multiSelect). User picks an option → queue selected approach for Phase 3. User declines all → record as `user_deferred`. |
| `invalid` | Remove from fix queue; note in final report |
| `spike_confirmed` | Queue plan update for Phase 3 |
| `spike_partial` | Queue plan update with partial evidence for Phase 3; note evidence gaps in report |
| `spike_invalidated` | Present to user via `AskUserQuestion` with three options: (1) Update plan with corrected information, (2) Skip — address during implementation, (3) Defer to `/incremental-planning` for replanning |
| Agent failure (timeout, crash, empty output) | Record all findings assigned to that agent as `blocked` with reason "investigator agent failed"; note in report |

**LoE escalation:** If an investigator upgraded a finding's LoE estimate to `significant` (from
`trivial` or `moderate`), move that finding to "needs refinement" and present to the user via
`AskUserQuestion` before queuing for implementation. Significant LoE means the finding may have
architectural impact — user should confirm before the lead proceeds.

**needs_context verdict path:** If an investigator receives a `needs_context` finding and cannot verify it (insufficient evidence to confirm or deny), it returns verdict `invalid` with reason "could not verify — insufficient evidence". The lead routes this to the `unverified_unresolved` bucket (not `findings_invalid`).

### Conflict Detection

After all investigators complete, scan all `resolution` verdicts for overlapping locations: two or
more resolutions targeting the same file and overlapping line ranges. Compatible overlaps (different
lines, no shared context) are handled sequentially in Phase 3 — if applying a resolution fails
because the target code has already been modified by a prior fix, re-read the file and adapt the
resolution. Incompatible overlapping resolutions (contradictory changes to the same lines) cannot
be applied automatically — present the conflict to the user via `AskUserQuestion` and ask which
resolution takes precedence.

---

## Phase 3 — Sequential Implementation

The lead implements all fixes sequentially, in file order (minimize context switching between files).

### Implementation Protocol

For each queued finding, in order:

1. Read the target file (always re-read before editing — prior fixes may have changed line numbers).
2. Apply the exact resolution from the investigator's Investigation Result.
3. **Plan-review fixes (non-spike):** Edit the plan file — task descriptions, missing steps, ordering corrections.
4. **Plan-review spike fixes:** Edit the plan file to incorporate spike results:
   - `spike_confirmed` → replace the open question or assumption with the resolved evidence
   - `spike_invalidated` → update the plan per the user's choice from Phase 2 (update / skip / replan)
   - `spike_partial` → update with verified parts; mark remaining open questions explicitly in the plan
5. **Code fixes:** Edit source files. Run tests after completing all edits to a single file (not after each finding — batch per file).
6. **Bug fixes:** Implement the resolution plan steps from BUGS.md. After implementation, update the entry's `**Status:**` field: if prior status was "Root Cause Found", set to "Fix Ready"; if prior status was already "Fix Ready", set to "Fixed".

### Test Execution

After all code changes to a file (skip for plan-only or BUGS.md-only changes):

Detection order for the test command:
1. `CLAUDE.md` — look for a `make test` or explicit test command
2. `Makefile` — check for a `test` target
3. Language-specific default — `pytest` (Python), `jest` (JS/TS), `go test ./...` (Go)
4. Skip with note if none detected

**Test failure** → revert all changes to that file (`git checkout -- <file>`), record ALL co-located findings as `blocked` in the report. Do not retry with alternative fixes — blocked findings surface in the Phase 5 report for user attention.

### Cross-Cutting Fixes

Apply in dependency order per the investigator's instructions. If investigator did not specify order, apply in the order: interface/type definitions → callers → call sites → tests.

### Commit Behavior

Do NOT commit. Leave all changes in the working tree for user review.

---

## Phase 4 — Verification

Apply the Verification Protocol from `code-quality/references/finding-classification.md`, extended
with 8 buckets to cover all standalone /fix outcomes.

### Outcome Buckets

| Bucket | What it contains |
|---|---|
| `total_findings_in` | All findings after Phase 0.5 normalization |
| `findings_fixed` | Successfully implemented (including spike-resolved plan updates) |
| `findings_invalid` | Investigator returned `invalid` verdict |
| `user_deferred` | User declined via refinement prompt or conflict resolution choice |
| `needs_plan` | Recommended for `/incremental-planning` (too large for direct fix) |
| `out_of_scope` | Outside CWD — excluded in Phase 1a |
| `blocked` | Test failure prevented fix |
| `unverified_unresolved` | `needs_context` findings that the investigator also could not resolve |

### Category Mapping

| Outcome | Bucket |
|---|---|
| Direct fix implemented | `findings_fixed` |
| Spike confirmed (plan updated) | `findings_fixed` |
| Spike partial (plan updated) | `findings_fixed` |
| Spike invalidated (user chose plan update) | `findings_fixed` |
| Spike invalidated (user chose replan) | `needs_plan` |
| Spike invalidated (user chose skip) | `user_deferred` |
| Needs refinement (implemented after guidance) | `findings_fixed` |
| Needs refinement (user declined) | `user_deferred` |
| Needs plan | `needs_plan` |
| Out of scope | `out_of_scope` |
| Investigator returned invalid | `findings_invalid` |
| Test failure prevented fix | `blocked` |
| Unverified, investigator confirmed valid | route through normal triage (direct fix / needs refinement / etc.) |
| Unverified, investigator could not resolve | `unverified_unresolved` |

### Verification Check

```
total_findings_in == findings_fixed + findings_invalid + user_deferred
                   + needs_plan + out_of_scope + blocked + unverified_unresolved
```

If delta > 0: list the missing finding IDs as `UNACCOUNTED` in the Phase 5 report.

---

## Phase 5 — Terminal Output

Print the structured fix report:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Source: {source}
Fix target: {target_description}
Findings: {total_in} total → {fixed} fixed, {invalid} invalid,
  {deferred} deferred, {plan} needs-plan, {oos} out-of-scope,
  {blocked} blocked, {unresolved} unresolved

FIXED ({count})
  1. [{id}] {description}
     {file}:{line_range} — {what was changed}

SPIKES EXECUTED ({count} — plan-review only; these are ALSO counted in FIXED above)
  1. [{id}] {description}
     Result: {verdict} — {evidence summary}
     Plan updated: {section}

INVALID ({count})
  1. [{id}] {description}
     Investigator: {reason}

BLOCKED ({count})
  1. [{id}] {description}
     Reason: {test failure}

DEFERRED ({count})
  1. [{id}] {description}
     Reason: {why}

NEEDS PLAN ({count})
  1. [{id}] {description}
     Recommended: /incremental-planning

OUT OF SCOPE ({count})
  1. [{id}] {description}
     {file} — outside working directory

UNRESOLVED ({count})
  1. [{id}] {description}
     Neither upstream verifier nor investigator could confirm — requires human judgment

UNACCOUNTED ({count} — if any)
  1. [{id}] {description}
     WARNING: Not processed

Verification: {total_in} in → {sum} accounted [PASS | FAIL]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Omit sections with zero count.

### Post-Fix Suggestion

After the report, print context-appropriate suggestions (show all that apply, one per line):

| Condition | Suggestion |
|---|---|
| Code changes were made | `Fixes applied. Consider running /quality-gate to verify.` |
| Plan-only changes | `Plan updated. Consider running /plan-review to verify the changes.` |
| Spikes invalidated assumptions | `Spikes invalidated {N} assumption(s). Review updated plan sections carefully — consider /plan-review to verify.` |
| BUGS.md was updated | `Bug fixes applied. BUGS.md entries updated to 'Fix Ready'. Verify manually and update to 'Fixed' when confirmed.` |

---

## Relationship to Other Skills

| Skill | Relationship |
|---|---|
| `code-quality:pr-review` | Produces findings that /fix acts on. Run /pr-review first, then /fix. |
| `code-quality:plan-review` | Produces plan findings that /fix acts on. /fix edits the plan file only. For Research Gaps and Unknown Unknowns, /fix executes actual spikes to verify assumptions. |
| `code-quality:quality-gate` | Not a source — quality-gate fixes findings inline. /fix suggests /quality-gate after code fixes for verification. |
| `code-quality:bug-investigation` | Documents bugs with root causes and resolution plans. /fix implements resolution plans or recommends /incremental-planning for complex bugs. |
| `code-quality:incremental-planning` | Recommended by /fix when a bug resolution requires significant scope, or when a spike invalidates a plan assumption requiring replanning. |
| `code-quality:swarm` | Swarm has its own internal Fixer (Phase 5). /fix is the standalone equivalent for non-swarm workflows. |

---

## Quick Reference

```
### Flow
Phase 0: Context Detection → Phase 0.5: Normalize → Phase 1: Triage + CWD Validation →
Phase 2: Investigate ALL findings (background, pre-implementation) →
Phase 3: Implement (lead, sequential, file order) → Phase 4: Verify → Phase 5: Report

### Fix Target Rules
plan-review findings        → edit plan file ONLY (never implement code)
plan-review Research Gaps   → RUN spikes, then update plan with resolved evidence
pr-review findings          → edit code on PR branch
bug-investigation findings  → implement resolution OR recommend /incremental-planning

### Not Supported
quality-gate → excluded (fixes findings inline, reports only aggregates)
```
