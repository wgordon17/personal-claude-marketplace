# PR Review Terminal Output Templates and Edge Cases

Detailed templates for Phase 4 terminal output (`Findings Exist` and `No Findings After Verification`) and edge-case handling for `/pr-review`.

---

## Phase 4 — Terminal Output Templates

Print the structured report. Use `━` (U+2501) for the divider lines.

### Findings Exist

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CODE REVIEW — PR #{number}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PR: {owner}/{repo}#{number} — "{title}"
Branch: {head_branch} → {base_branch}
Files changed: {changedFiles} | Additions: +{additions} | Deletions: -{deletions}

Findings: {verified_count} verified, {needs_context_count} needs context
  (from {total_raw} raw findings — {false_positive_count} false positives removed)

TESTING GAPS
  1. [{Reviewer}] {description} [{classification}]
     {file}:{line}
     Evidence: {evidence}

CORRECTNESS
  ...

SECURITY
  ...

ARCHITECTURE
  ...

DECISIONS NEEDED
  ...

PERFORMANCE
  ...

STYLE & CONVENTIONS
  ...

─── Needs Context ({needs_context_count}) ───
  1. [{Reviewer}] {description} [{classification}]
     {file}:{line}
     Investigation: {investigation_summary}

─── Deferred ({deferred_count}) ───
  1. [{Reviewer}] {description} [user-deferred]
     {file}:{line}

Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: {verified_count} | False positives removed: {false_positive_count} | Needs context: {needs_context_count} | Deferred: {deferred_count}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Group findings by **category** (Testing Gaps → Correctness → Security → Architecture →
Decisions Needed → Performance → Style & Conventions). Within each category,
sort by location (file path). All findings in category sections are `needs-fix` at this point —
either originally or promoted from `needs-input` via Phase 3.5 user confirmation.
For the `[Reviewer]` tag, use the short reviewer name: Security, QA, Performance, Code Quality,
Correctness, Plan Adherence.

Omit category sections with zero findings.

Findings with verdict `needs_context` appear in a dedicated section at the bottom — these
are items the verifier could not confirm or deny and require human judgment. They are NOT
hidden or filtered — they are surfaced transparently.

Findings confirmed by the user in Phase 3.5 are promoted to `needs-fix` and placed in their
normal category sections — they appear alongside other verified findings with no special
treatment. User-deferred findings appear in the "Deferred" section at the bottom.

### No Findings After Verification

Use this path only when `verified_count == 0` AND `needs_context_count == 0` AND
`deferred_count == 0` (all findings were false positives, or no findings were reported).
If any count is > 0, use the "Findings Exist" path.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CODE REVIEW — PR #{number}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PR: {owner}/{repo}#{number} — "{title}"
Branch: {head_branch} → {base_branch}
Files changed: {changedFiles} | Additions: +{additions} | Deletions: -{deletions}

No verified issues found.
Checked for: security, test coverage, performance, code quality, correctness, plan adherence.

Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: 0 | False positives removed: {false_positive_count} | Needs context: 0 | Deferred: 0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Edge Cases

| Condition | Action |
|-----------|--------|
| No `gh` CLI | Error: "gh CLI not found. Install it from https://cli.github.com and authenticate with `gh auth login`." |
| PR URL missing | Error: "Usage: /pr-review <PR-URL>" |
| PR in wrong repo | Error: "PR is from {url_owner}/{url_repo} but CWD is in {local_repo}. cd to the correct repo first." |
| PR is closed/merged | Stop: "PR #N is already {state}. Nothing to review." |
| PR is draft | Warn and continue: "Note: PR #N is a draft. Proceeding anyway." |
| Lock/generated files only | Stop: "PR #N changes only lock/generated files. No review needed." |
| Cannot checkout branch | Auto-stash and retry. If stash fails, stop: "Cannot checkout PR branch and stash failed." |
| All findings false positive | Output "no findings" report format (not an error) |
| Verification JSON parse fails | All findings get `unverified` verdict, treated as `needs_context` in output |
| Zero `needs-input` findings | Skip Phase 3.5, proceed directly to Phase 4 |
| AskUserQuestion unavailable | Treat all `needs-input` findings as `needs_context` in Phase 4 output (surface them, don't hide them) |

