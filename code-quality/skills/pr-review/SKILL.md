---
name: pr-review
description: |
  Multi-agent PR review with finding verification. Use when asked to "review PR",
  "review this PR", "code review", or given a PR URL to review. Spawns 6 parallel specialized
  reviewers (security, QA, performance, code quality, correctness, git history),
  verifies findings by investigating source code, categorizes by type, and prints a
  structured report to the terminal. Never comments on GitHub PRs.
allowed-tools: [Read, Glob, Grep, Bash, Agent]
---

# PR Review Skill

Multi-agent pull request review. Spawns 6 parallel Sonnet reviewers (security, QA, performance,
code quality, correctness, git history), each required to investigate and verify findings before
reporting. A Sonnet verification agent then reads source files to confirm or disprove each
finding. Results are categorized by type (testing gaps, correctness, security, architecture,
decisions needed, etc.) and printed as a structured terminal report.

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

Search for a plan file that matches the PR's topic. Check both the repo root and any active
worktree:
- `hack/plans/` — glob for files whose name relates to the PR title or branch name
- If the current repo is in a worktree, also check the main worktree's `hack/plans/`

If a matching plan file is found, read it and store as `{plan_content}`. If no plan exists,
use: `"No implementation plan found."` The Correctness Reviewer uses this to detect plan drift.

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

### Pre-Collect Git History Context

Pre-collect git history context in the orchestrator to avoid redundant git commands across
multiple reviewer agents.

For each changed file (cap at 15 files; if more, select the 15 with the most changed lines):

Skip files that are newly created in the PR (diff header shows `--- /dev/null`) — new files have no blame history.

1. **Commit history:**
   ```
   git log --oneline -20 -- <file>
   ```

2. **Blame for changed hunks only** (not full-file blame — that produces unbounded output):
   Parse `@@ -a,b +c,d @@` headers from `{diff}` for this file.
   For each hunk: `start = a`, `end = a + b - 1` (using the `-a,b` side — the old-file lines).
   If `b = 0` (addition-only hunk with no prior lines), skip blame for that hunk.
   Run: `git blame origin/{base_branch} -L <start>,<end> -- <file>`
   Using the old-file side shows who last touched the lines being modified — the actual
   historical context the Git History Reviewer needs. Blaming the new-file side (`+c,d`)
   would only show the PR author's own commits.

If more than 15 files changed, append a note listing the skipped files.

Store the combined output as `{git_history_context}`.

### Build Input Context

Assemble these values — they are passed to reviewers in Phase 2:
- `{pr_description}` = "PR #N: {title}\n\n{body}"
- `{diff}` = full diff output
- `{claude_md_rules}` = CLAUDE.md content or placeholder
- `{contributing_md_rules}` = CONTRIBUTING.md content or placeholder
- `{changed_files}` = newline-separated list of changed file paths
- `{git_history_context}` = combined blame/log output from above
- `{plan_content}` = implementation plan content or placeholder

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
| Git History | Skip only if git history collection returned empty (e.g., brand-new repo with no history) |

Record which reviewers will run.

---

## Phase 2 — Parallel Review

Read `references/reviewer-prompts.md`. For each applicable reviewer, locate the corresponding
prompt template, substitute all placeholders with actual values, and spawn a Sonnet agent.

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
`{plan_content}` — see below.

### Git History Reviewer (6th, parallel with above)

```
Agent(
  description="Git history review of PR #{number}",
  model="sonnet",
  prompt=<Git History Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)
```

The Git History Reviewer receives `{git_history_context}` (from Phase 0) instead of `{diff}`.
It also receives `{pr_description}`, `{changed_files}`, `{claude_md_rules}`, and `{contributing_md_rules}`. Do NOT re-collect git history here —
use the output already stored from Phase 0.

### Collect Findings

After all agents complete, collect all findings into a consolidated list. Assign each finding a
unique ID (e.g., `sec-1`, `qa-1`, `perf-1`, `cq-1`, `cor-1`, `gh-1`). Preserve: description, file:line,
severity, evidence, source reviewer.

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
    "severity": "HIGH",
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
chains, and checks whether the finding is real. It does NOT just score confidence from the
diff context. It returns a JSON array with a verdict for each finding:
`[{finding_id, verdict, investigation_summary, category}, ...]`

Verdicts: `verified` (confirmed real), `false_positive` (investigated and disproven),
`needs_context` (cannot confirm or deny — requires human judgment).

Parse the verifier's response as JSON. If parsing fails, extract JSON from between the first
`[` and last `]` markers. If that also fails, include all findings with verdict `unverified`
and a note: "Verification failed — showing all findings unverified."

### Categorize

The verifier assigns each finding to a category based on its nature (not its severity):

| Category | Examples |
|----------|----------|
| **Testing Gaps** | Missing tests, untested paths, coverage gaps |
| **Correctness** | Logic errors, wrong behavior, contract violations |
| **Security** | Vulnerabilities, auth issues, injection, secrets |
| **Architecture** | Design issues, pattern violations, structural problems |
| **Decisions Needed** | Ambiguous intent, trade-offs requiring human judgment |
| **Performance** | Bottlenecks, N+1, memory issues |
| **Style & Conventions** | CLAUDE.md violations, naming, code quality |
| **Historical** | Pattern contradictions, churn, reverted patterns |

### Filter

Remove findings with verdict `false_positive`. Keep all `verified` and `needs_context`
findings.

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
  1. [{Reviewer}] {description} [{severity}]
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

HISTORICAL
  ...

─── Needs Context ({needs_context_count}) ───
  1. [{Reviewer}] {description} [{severity}]
     {file}:{line}
     Investigation: {investigation_summary}

Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: {verified_count} | False positives removed: {false_positive_count} | Needs context: {needs_context_count}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Group findings by **category** (Testing Gaps → Correctness → Security → Architecture →
Decisions Needed → Performance → Style & Conventions → Historical). Within each category,
sort by severity (CRITICAL → HIGH → MEDIUM → LOW). For the `[Reviewer]` tag, use the short
reviewer name: Security, QA, Performance, Code Quality, Correctness, Git History.

Omit category sections with zero findings.

Findings with verdict `needs_context` appear in a dedicated section at the bottom — these
are items the verifier could not confirm or deny and require human judgment. They are NOT
hidden or filtered — they are surfaced transparently.

### No Verified Findings

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CODE REVIEW — PR #{number}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PR: {owner}/{repo}#{number} — "{title}"
Branch: {head_branch} → {base_branch}
Files changed: {changedFiles} | Additions: +{additions} | Deletions: -{deletions}

No verified issues found.
Checked for: security, test coverage, performance, code quality, correctness, historical consistency.

Reviewed by: {reviewer_list}
Total raw: {total_raw} | All resolved as false positives
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
| `{diff}` | Local `git diff` against merge base | Security, QA, Performance, Code Quality, Correctness |
| `{claude_md_rules}` | CLAUDE.md content or "No CLAUDE.md found." | All reviewers + Verifier |
| `{contributing_md_rules}` | CONTRIBUTING.md content or "No CONTRIBUTING.md found." | All reviewers + Verifier |
| `{changed_files}` | Newline-separated file paths (from `files` in PR metadata) | All reviewers + Verifier |
| `{plan_content}` | Implementation plan content or "No implementation plan found." | Correctness Reviewer only |
| `{git_history_context}` | Pre-collected blame/log output | Git History Reviewer only |
| `{findings_json}` | JSON array of all findings with diff_context | Finding Verifier only |
