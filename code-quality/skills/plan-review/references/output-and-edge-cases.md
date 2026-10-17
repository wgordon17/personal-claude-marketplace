# Plan Review Terminal Output Templates and Edge Cases

Detailed templates for Phase 4 terminal output (`Findings Exist` and `No Findings After Verification`) and edge-case handling for `/plan-review`.

---

## Phase 4 — Terminal Output Templates

Print the structured report. Use `━` (U+2501) for the divider lines.

### Findings Exist

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLAN REVIEW — {plan_file_path}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Goal: {plan_goal}
Domain: {plan_domain}
Tasks: {plan_tasks} | Files: {plan_files_count}

Findings: {verified_count} verified, {needs_context_count} needs context
  (from {total_raw} raw findings — {false_positive_count} false positives removed)

RESEARCH GAPS
  1. [{Reviewer}] {description} [{classification}]
     {location}
     Evidence: {evidence}

FEASIBILITY
  ...

SCOPE
  ...

DEPENDENCIES
  ...

ARCHITECTURE
  ...

SECURITY
  ...

SPECIFICATION
  ...

─── Needs Context ({needs_context_count}) ───
  1. [{Reviewer}] {description} [{classification}]
     {location}
     Investigation: {investigation_summary}

─── Deferred ({deferred_count}) ───
  1. [{Reviewer}] {description} [user-deferred]
     {location}

{verification_note}
{skipped_note}
Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: {verified_count} | False positives removed: {false_positive_count} | Needs context: {needs_context_count} | Deferred: {deferred_count}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

`{verification_note}` — if verification JSON parsing failed, print the warning line. Otherwise omit.
`{skipped_note}` — if any reviewers were skipped, print a line such as "Skipped: Dependency & Ordering (single-task plan)" or "Skipped: Security (no auth/security paths detected)". Otherwise omit.

Category sections contain only `verified` findings. Group by category in order: RESEARCH GAPS
(first — highest leverage to fix before implementation) → FEASIBILITY → SCOPE → DEPENDENCIES →
ARCHITECTURE → SECURITY → SPECIFICATION. Within each category, sort by location. All findings
in category sections are `needs-fix` at this point — either originally or promoted from
`needs-input` via Phase 3.5 user confirmation. For the `[Reviewer]` tag, use the short
reviewer name: Feasibility,
Scope, Dependencies, Unknown Unknowns, Architect, Security.

Omit category sections with zero verified findings.

`needs_context` and `unverified` findings appear ONLY in the dedicated "Needs Context" section
at the bottom — they do NOT appear in category sections above. These are items the verifier
could not confirm or deny and require human judgment.

Findings confirmed by the user in Phase 3.5 are promoted to `needs-fix` and placed in their
normal category sections — they appear alongside other verified findings with no special
treatment. User-deferred findings appear in the "Deferred" section at the bottom.

### No Findings After Verification

Use this path only when `verified_count == 0` AND `needs_context_count == 0` AND
`deferred_count == 0`. If any count is > 0, use the "Findings Exist" path.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLAN REVIEW — {plan_file_path}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Goal: {plan_goal}
Domain: {plan_domain}
Tasks: {plan_tasks} | Files: {plan_files_count}

No verified issues found.
Checked for: {checked_areas}

{skipped_note}
Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: 0 | False positives removed: {false_positive_count} | Needs context: 0 | Deferred: 0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Edge Cases

| Condition | Action |
|-----------|--------|
| No plan file path given and no memory dir | Error: "No memory directory found. Pass a plan file path explicitly: `/plan-review <path>`" |
| No `.md` files in `{memory_dir}/plans/` | Error: "No plan files found in `{memory_dir}/plans/`. Pass a plan file path explicitly: `/plan-review <path>`" |
| Plan file path given but file does not exist | Error: "Plan file not found: {path}" |
| Plan file exists but is empty | Error: "Plan file is empty: {path}" |
| Single plan file found | Auto-select with confirmation: "Using plan: {filename}" |
| Multiple plan files found | Present via AskUserQuestion with filename, goal line, relative age |
| `{plan_tasks}` < 2 | Skip Dependency & Ordering reviewer; include in `{skipped_note}` |
| Security reviewer skipped | Include in `{skipped_note}`: "Security (no auth/security paths detected)" |
| All findings false positive | Output "no findings" report format (not an error) |
| Verification JSON parse fails | All findings get `unverified` verdict, routed to Needs Context section; `{verification_note}` warns in output |
| Zero `needs-input` findings | Skip Phase 3.5, proceed directly to Phase 4 |
| AskUserQuestion unavailable | Treat all `needs-input` findings as `needs_context` in Phase 4 output (surface them, don't hide them) |

