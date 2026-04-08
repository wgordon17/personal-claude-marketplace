---
name: pr-review
description: |
  Multi-agent PR review with finding verification. Use when asked to "review PR",
  "review this PR", "code review", or given a PR URL to review. Spawns 6 parallel specialized
  reviewers (security, QA, performance, code quality, correctness, plan adherence),
  verifies findings by investigating source code, categorizes by type, and prints a
  structured report to the terminal. Never comments on GitHub PRs.
allowed-tools: [Read, Glob, Grep, Bash, Agent, AskUserQuestion]
---

# PR Review Skill

Multi-agent pull request review. Spawns 6 parallel reviewers (5 Sonnet + 1 Opus) — security, QA,
performance, code quality, correctness, and plan adherence — each required to investigate and
verify findings before reporting. A Sonnet verification agent then reads source files to confirm
or disprove each finding. Results are categorized by type (testing gaps, correctness, security,
architecture, decisions needed, etc.) and printed as a structured terminal report.

**Never comments on GitHub PRs.** Output is terminal-only.

## Usage

```
/pr-review <PR-URL>
```

- `<PR-URL>` — required. Full GitHub PR URL (e.g. `https://github.com/owner/repo/pull/123`).

---

## Phase 0 — Setup & Worktree Alignment

### Parse Arguments

Extract from `$ARGUMENTS`:
- PR URL (first token — required; error if absent)

### Preflight Checks

1. Verify `gh` CLI is available: `gh --version`. If not found, stop with:
   "gh CLI not found. Install it from https://cli.github.com and authenticate with `gh auth login`."

2. Parse the PR URL to extract owner, repo, and PR number.

3. Verify the CWD repo matches the PR repo:
   ```
   gh repo view --json nameWithOwner -q .nameWithOwner
   ```
   Compare against the owner/repo from the URL. If mismatch, stop with:
   "PR is from {url_owner}/{url_repo} but CWD is in {local_repo}. cd to the correct repo first."

### Fetch PR Metadata

```
gh pr view <PR-URL> --json headRefName,baseRefName,number,title,additions,deletions,changedFiles,files,body,state,isDraft
```

Store: head branch, base branch, PR number, title, body, additions, deletions, changed file count,
state (OPEN/CLOSED/MERGED), isDraft flag. The `files` field returns an array of `{path, additions, deletions,
changeType}` objects — extract the file paths to build `{changed_files}`.

### Triage Checks (deterministic — no agent)

Run before any expensive operations:

- **Closed/Merged:** If state is CLOSED or MERGED, stop with:
  "PR #N is already {state}. Nothing to review."

- **Draft:** If isDraft is true, print warning and continue:
  "Note: PR #N is a draft. Proceeding anyway."

- **Trivially simple (lock files only):** Extract the list of changed file paths from the `files`
  array in PR metadata. If ALL changed files match these patterns — `*.lock`, `*.sum`, `package-lock.json`,
  `yarn.lock`, `pnpm-lock.yaml`, `*.generated.*` — stop with:
  "PR #N changes only lock/generated files. No review needed."
  If ANY file outside these patterns is changed, proceed.

### Worktree Alignment

Record the original branch/worktree before any checkout.

1. Run `git worktree list --porcelain` to list all worktrees.
2. Check if any worktree has a branch matching the PR's head branch.
3. If a matching worktree is found: use that worktree's path as the working directory for all
   subsequent git and file operations.
4. If no matching worktree: check if the current branch matches the PR's head branch. If yes, use the current working directory — no checkout needed.
5. If neither: run `gh pr checkout <number>`. If it fails due to a dirty working tree,
   run `git stash --include-untracked`, retry the checkout, and remember to `git stash pop`
   after review. If stash also fails, stop with: "Cannot checkout PR branch and stash failed."
6. After the review completes (Phase 4) — or if any phase fails — return to the original
   branch or worktree. If changes were stashed in step 5, run `git stash pop` to restore
   them. If `git stash pop` fails due to conflicts, do NOT run `git stash drop` — inform
   the user: "Your changes are in git stash. Run `git stash pop` manually and resolve
   conflicts." Always attempt stash restoration on error paths, not just success paths.

### Read Project Rules

From the repo root in the correct worktree:
- Read `CLAUDE.md`. If missing, use: `"No CLAUDE.md found."`
- Read `CONTRIBUTING.md`. If missing, use: `"No CONTRIBUTING.md found."`

Store as `{claude_md_rules}` and `{contributing_md_rules}`.

### Discover Implementation Plan

Search for a plan file that matches the PR's topic. Detect the memory directory using the
convention in `code-quality/references/project-memory-reference.md` (Directory Detection and
Worktree Resolution sections).

**Primary:** Search `{memory_dir}/plans/` files and parse each file's `**Branch:**` header
field. Match the value against the PR's head branch name. If a match is found, this is the
plan file.

**Fallback:** If no `**Branch:**` header match is found, check `{memory_dir}/plans/` for
files whose name relates to the PR title or branch name.

If a matching plan file is found, read it and store as `{plan_content}` and record the file
path as `{plan_file_path}`. If no plan exists, use `"No implementation plan found."` for
`{plan_content}` and `""` (empty string) for `{plan_file_path}`. The Correctness Reviewer
and Plan Adherence Reviewer use this to detect plan drift.

### Extract Test Plan

If a plan file was found (i.e., `{plan_file_path}` is non-empty), check whether the plan
file contains a `## Test Plan` section. If such a section exists, read the `**Test Plan:**` path
annotation from it.

Path boundary validation: normalize the extracted path (resolve `..` segments)
and verify it falls within `{memory_dir}/test-plans/`. If the normalized path escapes that
directory, set `{plan_test_plan}` to `""` (empty string) and log a warning:
"Warning: test plan path escapes {memory_dir}/test-plans/ boundary — setting {plan_test_plan} to empty string."

If the path is valid, attempt to read the file. If the file does not exist, set
`{plan_test_plan}` to `""` (empty string) (graceful fallback — no warning). If the file exists and is
readable, read its contents and store as `{plan_test_plan}`.

If the plan file has no `## Test Plan` section, or if no plan file was found, set
`{plan_test_plan}` to `""` (empty string).

Pass `{plan_test_plan}` to the QA Reviewer, Correctness Reviewer, and Plan Adherence Reviewer.

### Fetch PR Diff

After worktree alignment, ensure the base branch ref is available and compute the diff locally:
```
git fetch origin {base_branch}
git diff $(git merge-base origin/{base_branch} HEAD)..HEAD
```

This is faster than `gh pr diff` and gives agents natural access to individual files via
`Read`, `Grep`, and per-file `git diff -- <file>` without needing to parse a monolithic
diff string.

Store as `{diff}`.

### Build Input Context

Assemble these values — they are passed to reviewers in Phase 2:
- `{pr_description}` = "PR #N: {title}\n\n{body}"
- `{diff}` = full diff output
- `{claude_md_rules}` = CLAUDE.md content or placeholder
- `{contributing_md_rules}` = CONTRIBUTING.md content or placeholder
- `{changed_files}` = newline-separated list of changed file paths
- `{plan_content}` = implementation plan content or placeholder
- `{plan_file_path}` = path to discovered plan file or empty string

---

## Phase 1 — Reviewer Applicability

Determine which of the 6 reviewers apply based on changed file types.

Default: all 6 reviewers run. Skip a reviewer only if its domain has zero applicability:

| Reviewer | Skip condition |
|----------|----------------|
| Performance | ALL changed files are `.md`, `.txt`, `.rst`, or other documentation-only formats |
| Security | (never skip — security concerns appear in config and docs too) |
| QA | (never skip) |
| Code Quality | (never skip) |
| Correctness | (never skip) |
| Plan Adherence | Skip if no implementation plan found in Phase 0 |

Record which reviewers will run.

---

## Phase 2 — Parallel Review

Read `references/reviewer-prompts.md`. For each applicable reviewer, locate the corresponding
prompt template, substitute all placeholders with actual values, and spawn an agent. Most
reviewers use `model="sonnet"`; the Plan Adherence Reviewer uses `model="opus"`.

Spawn all applicable reviewers simultaneously (parallel Agent calls).

### Domain Reviewers (5 parallel)

```
Agent(
  description="Security review of PR #{number}",
  model="sonnet",
  prompt=<Security Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="QA review of PR #{number}",
  model="sonnet",
  prompt=<QA Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Performance review of PR #{number}",
  model="sonnet",
  prompt=<Performance Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Code quality review of PR #{number}",
  model="sonnet",
  prompt=<Code Quality Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Correctness review of PR #{number}",
  model="sonnet",
  prompt=<Correctness Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)
```

Each domain reviewer receives: `{pr_description}`, `{diff}`, `{claude_md_rules}`,
`{contributing_md_rules}`, `{changed_files}`. The Correctness Reviewer also receives
`{plan_content}` and `{plan_test_plan}`. The QA Reviewer also receives `{plan_test_plan}`.

### Plan Adherence Reviewer (6th, parallel with above)

Only spawned if a plan was found in Phase 0 (i.e., `{plan_file_path}` is non-empty).

```
Agent(
  description="Plan adherence review of PR #{number}",
  model="opus",
  prompt=<Plan Adherence Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)
```

The Plan Adherence Reviewer receives: `{plan_content}`, `{plan_file_path}`, `{plan_test_plan}`,
`{diff}`, `{changed_files}`, `{pr_description}`, `{claude_md_rules}`, and `{contributing_md_rules}`.

When `{plan_test_plan}` is empty string, omit the `UAT CROSS-REFERENCE` section entirely from
the QA Reviewer prompt, omit the `UAT SCENARIOS` section from the Correctness Reviewer and Plan
Adherence Reviewer prompts — do not render the heading, label, or empty placeholder in any of
these reviewers.

### Collect Findings

After all agents complete, collect all findings into a consolidated list. Assign each finding a
unique ID (e.g., `sec-1`, `qa-1`, `perf-1`, `cq-1`, `cor-1`, `pa-1`). Preserve: description, file:line,
classification, evidence, source reviewer.

---

## Phase 3 — Verification (batched)

If no findings were reported by any reviewer, skip verification and proceed directly to
Phase 4 with the 'no findings' output path.

Spawn a **single** Sonnet agent with ALL findings in one call. Do NOT spawn one agent per
finding — that pattern is catastrophically slow in Claude Code where each Agent() has
significant startup overhead. A single batched call takes ~15 seconds; per-finding agents
with 15-20 findings would take 2-5 minutes.

Build the findings JSON array:
```json
[
  {
    "id": "sec-1",
    "reviewer": "Security",
    "description": "...",
    "location": "file/path.py:42",
    "classification": "needs-fix | needs-input",
    "evidence": "...",
    "diff_context": "±10 lines of diff surrounding the finding location"
  },
  ...
]
```

For each finding, extract ±10 lines from the diff around the finding's file:line location and
include it as the `diff_context` field. This gives the verifier surrounding context to
investigate each finding.

```
Agent(
  description="Finding verification for PR #{number}",
  model="sonnet",
  prompt=<Finding Verifier template from references/reviewer-prompts.md, placeholders substituted>
)
```

The verifier receives: `{findings_json}` (the array above), `{claude_md_rules}`,
`{contributing_md_rules}`, and `{changed_files}`.

The verifier **investigates each finding** — it reads the actual source files, traces call
chains, and checks whether the finding is real. It returns a JSON array with a verdict for
each finding:
`[{finding_id, verdict, investigation_summary, category, classification}, ...]`

Verdicts: `verified` (confirmed real), `false_positive` (investigated and disproven),
`needs_context` (cannot confirm or deny — requires human judgment).

Parse the verifier's response as JSON. If parsing fails, extract JSON from between the first
`[` and last `]` markers. If that also fails, include all findings with verdict `unverified`
and a note: "Verification failed — showing all findings unverified."

### Reconcile

**Finding fidelity check:** Before categorizing, verify the verifier returned a verdict for
every submitted finding. For each finding ID in the original `{findings_json}`, check if a
matching `finding_id` exists in the verifier's response. Any finding without a returned verdict
is assigned verdict `unverified` with `investigation_summary`: "Verifier did not return a
verdict for this finding." This prevents silent finding loss during verification - the same
principle as the Fixer verification protocol in `code-quality/references/finding-classification.md`.

### Categorize

The verifier assigns each finding to a category based on its nature (not its classification):

| Category | Examples |
|----------|----------|
| **Testing Gaps** | Missing tests, untested paths, coverage gaps |
| **Correctness** | Logic errors, wrong behavior, contract violations |
| **Security** | Vulnerabilities, auth issues, injection, secrets |
| **Architecture** | Design issues, pattern violations, structural problems |
| **Decisions Needed** | Ambiguous intent, trade-offs requiring human judgment |
| **Performance** | Bottlenecks, N+1, memory issues |
| **Style & Conventions** | CLAUDE.md violations, naming, code quality |

### Filter

Remove findings with verdict `false_positive`. Keep all `verified`, `needs_context`, and
`unverified` findings. Treat `unverified` findings as `needs_context` for output purposes —
they appear in the Needs Context section with the verification failure note.

---

## Phase 3.5 — Needs-Input Resolution

If any surviving findings (after filtering false positives) have classification `needs-input`,
present them to the user before producing the report. Do NOT skip this step - the skill must
not exit with unresolved `needs-input` items.

If zero `needs-input` findings remain after Phase 3, skip to Phase 4.

### Present to User

Present each `needs-input` finding individually via AskUserQuestion. Each finding gets its own
question with full context so the user can make an informed decision. Batch up to 4 findings
per AskUserQuestion call (the tool's question limit):

```
AskUserQuestion(questions=[
  {
    "question": "[{id}] [{Reviewer}] {description}\n\nLocation: {file}:{line}\nDecision needed: {input_needed}\n▸dp:file={file},line={line},cat={Reviewer},skill=pr-review",
    "header": "{id}",
    "options": [
      {"label": "Fix", "description": "Confirm this finding needs work — promoted to needs-fix"},
      {"label": "Defer", "description": "Skip for now — user-deferred"}
    ],
    "multiSelect": false
  },
  ... (one question per finding, up to 4 per call)
])
```

If more than 4 `needs-input` findings exist, make multiple AskUserQuestion calls.

### Record Decisions

For each `needs-input` finding:
- **Fix selected:** Promote to `needs-fix` with verdict `verified`. Place the finding in its
  normal category section alongside other verified findings. The user has confirmed this
  finding needs work — it is no longer ambiguous.
- **Defer selected:** Update the finding's classification to `user-deferred` in the output.
  The user explicitly chose not to act on it now.

Every `needs-input` item gets a recorded user decision, not silent deferral.

---

## Phase 4 — Terminal Output

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

### Cleanup

After output, return to the original branch or worktree recorded in Phase 0.

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

---

## Reviewer Prompt Templates

Prompt templates are in `references/reviewer-prompts.md`. Read that file and substitute
placeholders before passing to each Agent call. The templates are not executable — they are
documentation that Claude reads and fills in. Do not reference `quality-gate/references/` at
runtime; this skill owns its own copies adapted for PR review context.

### Placeholder Reference

| Placeholder | Value | Used by |
|-------------|-------|---------|
| `{pr_description}` | "PR #N: {title}\n\n{body}" | All reviewers (not Verifier) |
| `{diff}` | Local `git diff` against merge base | Security, QA, Performance, Code Quality, Correctness, Plan Adherence |
| `{claude_md_rules}` | CLAUDE.md content or "No CLAUDE.md found." | All reviewers + Verifier |
| `{contributing_md_rules}` | CONTRIBUTING.md content or "No CONTRIBUTING.md found." | All reviewers + Verifier |
| `{changed_files}` | Newline-separated file paths (from `files` in PR metadata) | All reviewers + Verifier |
| `{plan_content}` | Implementation plan content or "No implementation plan found." | Correctness Reviewer, Plan Adherence Reviewer |
| `{plan_file_path}` | Path to discovered plan file or empty string | Plan Adherence Reviewer only |
| `{plan_test_plan}` | Full test plan document content or empty string | QA Reviewer, Correctness Reviewer, Plan Adherence Reviewer |
| `{findings_json}` | JSON array of all findings with diff_context | Finding Verifier only |

---

## Relationship to Other Skills

| Skill | Relationship |
|-------|-------------|
| `code-quality:fix` | Acts on findings from pr-review. Run /fix after /pr-review to implement fixes. For findings flagged with `Recommended resolution: /deep-research`, /fix invokes `/deep-research` before dispatching investigators. |
