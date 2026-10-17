# Verification and Terminal Output Reference

Detailed specifications for Phase 1c (triage summary template), Phase 2 (verdict routing table and fallback rules), Phase 4 (8-bucket verification accounting), and Phase 5 (terminal output template and post-fix suggestions) of `/fix`.

---

## Phase 1c — Triage Summary Template

Present the triage summary to the user before proceeding (omit sections with zero findings):

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

Research-enriched fix ({count}):
  {id}: {description} [{location}] — /deep-research before investigation
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

UAT validation ({count}):
  {id}: {description} — investigator will check implementation against UAT scenarios
  ...

Unverified ({count}):
  {id}: {description} — will attempt verification in investigation
  ...

Proceeding with {actionable_count} findings ({direct_fix_count} direct +
{spike_count} spikes + {needs_refinement_count} needing refinement +
{uat_count} UAT validation + {unverified_count} unverified).
Skipping {skip_count} ({needs_plan_count} needs-plan + {oos_count} out-of-scope).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Phase 2 — Collecting and Routing Investigator Verdicts

After all investigators complete, route each result by verdict:

| Verdict | Action |
|---|---|
| `resolution` | Queue for Phase 3 implementation |
| `refinement_needed` | For each finding, present its options via `AskUserQuestion` (single-select — options are mutually exclusive). User picks an option → queue selected approach for Phase 3. User declines → record as `user-deferred`. |
| `invalid` | Remove from fix queue; note in final report |
| `spike_confirmed` | Queue plan update for Phase 3 |
| `spike_partial` | Queue plan update with partial evidence for Phase 3; note evidence gaps in report |
| `spike_invalidated` | Present to user via `AskUserQuestion` with three options: (1) Update plan with corrected information, (2) Skip — address during implementation, (3) Defer to `/incremental-planning` for replanning |
| `uat_validated` | Route to `findings_fixed` bucket — UAT scenario confirmed |
| `uat_mismatch` | Present mismatch to user via `AskUserQuestion` with scenario name, expected vs found. User chooses fix → `findings_fixed`; user defers → `user-deferred` |
| Agent failure (timeout, crash, empty output, or unparseable response) | Record all findings assigned to that agent as `blocked` with reason "investigator agent failed"; note in report |

**AskUserQuestion unavailable fallback:** If `AskUserQuestion` is unavailable (non-interactive
environment), record all `refinement_needed`, `spike_invalidated`, and `uat_mismatch` findings as `user-deferred`
with reason "non-interactive — AskUserQuestion unavailable". For `spike_invalidated` findings,
also preserve the spike verdict and plan impact details in the reason field so they surface in
the Phase 5 DEFERRED section.

**LoE escalation:** If an investigator estimates a finding's LoE as `significant` AND returned
verdict `refinement_needed`, include the LoE context in the AskUserQuestion presentation. If the
investigator returned `resolution` despite `significant` LoE, proceed with the resolution — the
investigator already determined a clear fix. Flag all `significant` LoE findings in the Phase 5
report regardless of verdict.

**Handling unverifiable findings:** If an investigator receives a finding with `verifier_verdict: "needs_context"` and cannot verify it (insufficient evidence to confirm or deny), it returns verdict `invalid` with reason "could not verify — insufficient evidence". The lead routes this to the `unverified_unresolved` bucket (not `findings_invalid`).

**UAT validation verdict routing:** For UAT validation findings, investigators return one of two
explicit verdict types:
- `uat_validated`: The UAT scenario was verified through code inspection or test execution —
  the implementation matches the UAT scenario expectations. Lead routes to `findings_fixed` bucket.
- `uat_mismatch`: The implementation differs from UAT scenario expectations — present the specific
  mismatch to the user via `AskUserQuestion` with the scenario name, what was expected, and what
  was found.

---

## Phase 4 — Verification Buckets and Category Mapping

Apply the Verification Protocol from `[{skill_dir}/../../references/finding-classification.md](../../../references/finding-classification.md)`, extended
with 7 outcome buckets to cover all standalone `/fix` outcomes.

### Outcome Buckets

| Bucket | What it contains |
|---|---|
| `total_findings_in` | All findings after Phase 0.5 normalization |
| `findings_fixed` | Successfully implemented (including spike-resolved plan updates) |
| `findings_invalid` | Investigator returned `invalid` verdict (finding no longer applies) |
| `user-deferred` | User declined via refinement prompt or conflict resolution choice |
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
| Spike invalidated (user chose skip) | `user-deferred` |
| Needs refinement (implemented after guidance) | `findings_fixed` |
| Needs refinement (user declined) | `user-deferred` |
| Needs plan | `needs_plan` |
| Out of scope | `out_of_scope` |
| Investigator returned invalid (finding no longer applies) | `findings_invalid` |
| Investigator returned invalid (could not verify — unverified finding) | `unverified_unresolved` |
| Test failure prevented fix | `blocked` |
| Agent failure (investigator crashed, timed out, or returned unparseable output) | `blocked` |
| Unverified, investigator confirmed valid | route through normal triage (direct fix / needs refinement / etc.) |
| Unverified, investigator could not resolve | `unverified_unresolved` |
| UAT validated (`uat_validated` verdict) | `findings_fixed` |
| UAT mismatch — user chose fix implementation | `findings_fixed` |
| UAT mismatch — user chose defer | `user-deferred` |

### Verification Check

```
total_findings_in == findings_fixed + findings_invalid + user-deferred
                   + needs_plan + out_of_scope + blocked + unverified_unresolved
```

If delta > 0: list the missing finding IDs as `UNACCOUNTED` in the Phase 5 report.

---

## Phase 5 — Terminal Output Template

Print the structured fix report:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Source: {source}
Fix target: {target_description}
Findings: {total_in} total → {fixed} fixed (incl. {spike_count} spikes), {invalid} invalid,
  {deferred} deferred, {plan} needs-plan, {oos} out-of-scope,
  {blocked} blocked, {unresolved} unresolved

FIXED ({count})
  1. [{id}] {description}
     {file}:{line_range} — {what was changed}

  ── Spike Detail ({count}) ──
  1. [{id}] {description}
     Result: {verdict} — {evidence summary}
     Plan updated: {section}
     Evidence gaps: {what could not be verified} (spike_partial only)

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

---

## Post-Fix Suggestion

After the report, print context-appropriate suggestions (show all that apply, one per line):

| Condition | Suggestion |
|---|---|
| Code changes were made | `Fixes applied. Consider running /quality-gate to verify.` |
| Plan-only changes | `Plan updated. Consider running /plan-review to verify the changes.` |
| Spikes invalidated assumptions | `Spikes invalidated {N} assumption(s). Review updated plan sections carefully — consider /plan-review to verify.` |
| BUGS.md was updated | `Bug fixes applied. BUGS.md entries updated to 'Fix Ready'. Verify manually and update to 'Fixed' when confirmed.` |

