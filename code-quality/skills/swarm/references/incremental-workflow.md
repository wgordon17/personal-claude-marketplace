# Incremental Workflow — PR Boundaries & Checkpoints

This reference documents the incremental PR-boundary procedures across Phases 0, 3, 4, 4.5, and 5.5 of `/swarm`.

---

## Phase 0: Checkpoint Discovery, Initial Branch Naming, and Resume

- **Checkpoint discovery:** Glob `{memory_dir}/swarm/*/checkpoint.json` for any checkpoint
  files. For each found, read the `plan_file` field and match against the current plan file
  path. Use the matching checkpoint (there should be at most one per plan file). This handles
  the cross-run-ID case where the original run_dir has a different run-id than the current
  session's run-id. Store the original `run_dir` from the checkpoint's `run_dir` field.
- **Initial branch naming:** For the first PR boundary (no checkpoint exists), derive
  `{plan-slug}` from the plan file path using the same algorithm as checkpoint resume:
  extract the basename, strip the run-id prefix (`{branch-slug}-{timestamp}-`) and `.md`
  extension, then apply the Branch Slug Sanitization Rules from `[{skill_dir}/../../references/project-memory-reference.md](../../../references/project-memory-reference.md)`.
  Rename the Phase 0-created branch to `feat/{plan-slug}-pr1` (via `git branch -m`). This
  rename happens after the test baseline. The rename is a git-only operation that does not
  affect the audit trail, which is keyed to the run-id independent of the branch name.
  This establishes the naming convention that resume uses for subsequent boundaries.
If `fast` or absent: proceed with existing fire-and-forget behavior unchanged.

**Checkpoint resume (incremental only):** If checkpoint discovered via glob:
- Read checkpoint, extract `completed_prs`, `current_pr`, `tasks_remaining`
- **Null PR recovery:** If the most recent `completed_prs` entry has `pr_number: null`,
  the previous session completed implementation but crashed before creating the PR. The
  branch with the work should still exist. Before creating a PR: check if one already exists
  for the boundary's branch (`gh pr list --head feat/{plan-slug}-pr{N} --state open --json number`).
  If found, use that PR number. If not found, create the draft PR following the PR framing
  rules from step 5 of the boundary stop (standalone work, no serial numbering). Check
  branch exists via `git branch -l`, push if needed. Then update the checkpoint with the
  PR number.
- Verify merged PRs: for entries with non-null `pr_number`, `git fetch origin {branch_base}`
  then confirm they are merged (primary: `gh pr view <number> --json state` checking for
  `"state": "MERGED"`, fallback: `git branch --merged {branch_base}` — same two-method
  strategy as roadmap SKILL.md's completion tracking section).
- If `tasks_remaining` is empty (all boundaries completed but checkpoint was not cleaned up),
  delete the stale checkpoint and proceed with normal completion flow (Phase 6 and 7) rather
  than attempting resume.
- Derive `{plan-slug}` from the checkpoint's `plan_file` path: extract the basename
  (filename only), then strip the run-id prefix (`{branch-slug}-{timestamp}-`) and the
  `.md` extension. Example: `hack/plans/feat-auth-1711388400-session-auth.md` → basename
  `feat-auth-1711388400-session-auth.md` → strip prefix and extension → `session-auth`.
  Apply the Branch Slug Sanitization Rules from `[{skill_dir}/../../references/project-memory-reference.md](../../../references/project-memory-reference.md)`.
- Create new branch for the next PR boundary: `feat/{plan-slug}-pr{N}` from
  `origin/{branch_base}` (using the `branch_base` field from the checkpoint, typically `main`)
- Announce: "Resuming swarm. Prior work merged. Remaining tasks: {list}."
  Include the checkpoint's `context_summary` in the Implementer's briefing so it has context
  about what was built in earlier boundaries.
- **Resume Phase 0 variant:** Still run these Phase 0 steps for the new session: test
  baseline, git status check. The new session gets its own implicit team automatically —
  no team setup step is needed. Reuse the original run-id's `{run_dir}` (from checkpoint's `run_dir` field)
  for audit trail continuity — do NOT generate a new run-id. Read `architect-plan.json`
  from the original run_dir (path stored in checkpoint's `architect_plan` field) to restore
  architectural context.
- **Plan file discovery:** During incremental resume, do NOT rely on `**Branch:**` header
  matching (the branch name changes per boundary). Use the checkpoint's `plan_file` path
  directly. Pass this path to all downstream consumers (Phase 4 Plan Adherence, Phase 5.5
  Plan Reconciliation, quality-gate) rather than relying on branch-header discovery.
  Note: The `**Branch:**` header in the plan file is stale after boundary 1 (it was updated
  to boundary 1's branch by the Boundary Updater). During resume, update `**Branch:**` to the
  new boundary's branch name via the Boundary Updater at the start of Phase 3, not just at
  boundary stop.
- Skip Phase 1 (composition already decided), skip Phase 2 (architecture already done)
- Jump to Phase 3 implementation with only the current PR boundary's tasks

If checkpoint exists but a completed PR is NOT merged: announce the blocker and pause
via AskUserQuestion ("PR #{X} is not yet merged. Merge it first, then re-invoke /swarm.").

`--fresh` flag: If the user's invocation prompt contains the token `--fresh` (detected via
simple string match on the initial task description), ignore any existing checkpoint and
start a new swarm run. The old checkpoint is archived to
`{run_dir}/checkpoint.json.abandoned-{timestamp}`.

---

## Phase 3: PR Boundary Handling (Incremental Only)

Each PR boundary produces a standalone,
independently-reviewable unit of work. The PR title and body describe what THIS PR
accomplishes — never "Part X of Y", never referencing other PRs in the series. A reviewer
must be able to judge the PR on its own merits without knowing other PRs exist.

After completing all tasks in the current PR boundary (all components for tasks in
`**PR:** {current_pr}`):
1. Run Phase 4 reviews scoped to the current boundary's files only. The Lead spawns the
   same core review agents as full Phase 4 (Security, QA, Code-Reviewer, Performance, plus
   any auto-detected domain reviewers from Phase 1) with `pr_boundary_files` — NOT via
   /pr-review skill, which reviews existing PRs. Plan Adherence and Phase 4.5 structural
   analysts also run at boundary stops (structural analysts receive scoped-review briefing
   per the Phase 4.5 incremental workflow section).
2. Run Phase 5 Fixer scoped to the current boundary's files only — the Lead passes
   `pr_boundary_files` to the Fixer agent's prompt (same mechanism as Phase 4 reviewers).
   Structural scoping per [{skill_dir}/../../references/finding-classification.md](../../../references/finding-classification.md) Fixer Protocol.
   Note: Phase 5.5 (Plan Reconciliation) does NOT run at non-final boundary stops — it
   runs only at final completion, scoped to all tasks across all boundaries.
3. Spawn Verifier agent (tests + lint) — same agent as Phase 7 but invoked inline at
   boundary stop. Full test suite, not scoped. This is a Verifier agent invocation only,
   NOT the full Phase 7 pipeline (Phase 6 docs run only at final completion).
   **Boundary-scoped test failures:** Compare test results against the Phase 0 baseline.
   Any test that was passing in baseline and now fails is a regression — blocking. Any NEW
   test (added by the current boundary's tasks) that fails is blocking. Tests that were
   already failing in baseline are pre-existing (handled by existing swarm convention).
   This baseline comparison sidesteps the "which boundary owns this test" problem.
4. **Save checkpoint and update plan file via helper agent** (the Lead has Can Edit: No):
   Spawn a brief Boundary Updater agent (sonnet, bypassPermissions) that:
   a. Writes checkpoint to `{run_dir}/checkpoint.json` with `completed_prs` for ALL prior
      boundaries plus the current one (with `pr_number: null` until step 5 succeeds).
   b. Updates `**PRs:**` field in the plan file to mark the current boundary as `pending`
      (the real PR number is written by step 7 after PR creation).
   c. Updates `**Branch:**` field in plan file to `feat/{plan-slug}-pr{current_pr}`.
   The Lead provides all values; the helper agent only writes files.
   The Boundary Updater may ONLY write to: (a) `{run_dir}/checkpoint.json`, and (b) the plan
   file at `{plan_file}`. It must not create, modify, or delete any other files.
   The Boundary Updater agent remains alive through step 7 — the Lead sends it a follow-up
   message at step 7 with the PR number to write. It is shut down after step 7 completes.
5. Create draft PR — each PR is standalone work, not "Part X of Y":
   ```bash
   BRANCH=$(git branch --show-current)
   gh pr create --head "$BRANCH" --draft \
     --title "type(scope): what this PR accomplishes" \
     --body "## Summary
   - [what changed and why — describe as self-contained, independently-reviewable work]"
   ```
   **PR framing rules:** Never number PRs ("Part 1 of 3"), reference other PRs in the
   series, or describe a PR as part of a larger whole. Each PR must be understandable and
   valuable on its own. If a reviewer cannot judge the PR's merit without knowing about
   other PRs, the boundary grouping was wrong — fix the grouping, not the PR description.
6. If tracker issue exists: add comment to the issue with the PR reference
   (`gh issue comment {issue_number} --repo {repo} --body "PR #{pr_number}: [title]"`)
   Sanitize the PR title before interpolation: strip backticks, dollar signs, double quotes,
   and backslashes from any plan-derived text used in `--body` arguments to prevent shell
   interpretation.
7. Update checkpoint and plan file via the Boundary Updater agent: set `pr_number` for the
   current boundary in `completed_prs` (was `null` from step 4), and update `**PRs:**` field
   in the plan file with `#{pr_number}`.
8. Announce: "Draft PR #{pr_number} created. Checkpoint saved.
   After merge, invoke /swarm to continue with remaining tasks."
9. Clean up: send `SendMessage(to="<name>", message={"type": "shutdown_request", "reason":
   "PR boundary complete"})` to each still-active teammate (Architect, Implementer, Reviewer,
   Test-Writer, Test-Runner, and any others not already shut down), then exit swarm gracefully

If this is the LAST PR boundary: skip the boundary stop (steps 1-9 above). Instead, proceed
with normal Phase 4 → Phase 5 → Phase 6 (docs) → Phase 7 (verification) completion flow.
The PR is created during Phase 7 completion — follow the same standalone PR framing rules
(no serial numbering, no "Part X of Y"). Clean up any existing checkpoint file.

---

## Phase 4 & 4.5: Scoped Review

**Phase 4 — Incremental workflow scoping:** When `{workflow_mode}` is `incremental`, reviewers receive
only the diff for the current PR boundary's files (not the full branch diff). The Lead
constructs the file list from the current PR boundary's tasks' `**Files:**` blocks and passes
it to each reviewer's prompt as `pr_boundary_files`. Reviewers analyze only those files.

**Phase 4.5 — Incremental workflow — scoped review:** At non-final PR boundaries, Phase 4.5
structural analysts review only the current boundary's code as standalone work. The Lead
briefs analysts: "Review this code on its own merits as a self-contained unit of work.
Flag structural issues in THIS code. Do not flag missing integration points or incomplete
data flows that are addressed by tasks not yet implemented — those are separate work."
Findings about genuinely missing contracts within the current boundary are valid; findings
about not-yet-implemented work are not.
At the FINAL PR boundary: Phase 4.5 runs normally (full system review).

---

## Phase 5.5: PR Tracking Reconciliation

At final completion:
- **Scope the cumulative diff correctly:** `git diff origin/main..HEAD` only contains the
  FINAL boundary's changes (earlier boundaries were merged via separate PRs). For step 2
  (cross-reference tasks against diff), check only the current boundary's tasks against the
  diff. Tasks from earlier boundaries were already completed and merged — verify them via
  the checkpoint's `completed_prs` entries (each with `merged: true`) rather than the diff.
- Verify the `**PRs:**` field in the plan header is fully populated (all boundaries have PR
  numbers, no `pending` entries remain). Cross-reference against `{run_dir}/checkpoint.json`
  `completed_prs` if available.
- Check off all tasks from all PR boundaries (not just the final boundary's tasks).
