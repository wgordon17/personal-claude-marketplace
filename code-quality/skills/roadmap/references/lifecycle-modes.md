# Roadmap Lifecycle Modes, Completion Tracking, and Edge Cases

Detailed procedures for `/roadmap` Phase 0 lifecycle modes (0.U Update, 0.C Cleanup, 0.S Status & Drift), Completion Tracking, and Edge Case Guidance.

---

## Phase 0.U — Update Mode

Executes when user selects "Update existing roadmap."

1. Parse the existing roadmap's `**Source plans:**` list to get all already-ingested plan paths.
2. Ask for additional plan paths via `AskUserQuestion`:
   > "The existing roadmap covers [N] source plans. Do you have new plan files to add?
   > Provide paths, or press Enter to re-sequence existing plans only."
3. Combine: existing source plans + any new plans provided → full plan list.
4. Run completion tracking (see **Completion Tracking** section below) to identify which phases
   are fully complete.
5. **Preserve completed phases** — do not re-sequence work already done. Only re-analyze
   incomplete and not-started phases.
6. **Backup before overwrite:** Copy the existing roadmap to
   `{plan-dir}/done/{filename}.YYYY-MM-DDTHH-MM-SS.pre-update` (timestamped, unique per run)
   before writing the regenerated version. Create `{plan-dir}/done/` if it doesn't exist.
   The `hack/` directory is gitignored — an explicit backup is the only recovery path.
7. Proceed to Phase 1 with the combined plan list, skipping tasks from completed phases.
   The regenerated roadmap MUST include `**Last updated:**` with the current ISO timestamp
   (e.g., `2026-03-20T14:30:00`) in the document header.

**Edge cases:**
- **Deleted plan:** If a source plan path no longer exists, surface via `AskUserQuestion`:
  "Plan [path] no longer exists. Remove from roadmap, or provide an updated path?"
- **All phases complete, no new plans:** Suggest cleanup instead:
  "All phases are complete. Did you mean to clean up? (Route to Cleanup mode, or continue
  with update anyway?)"
- **In-progress phase restructuring:** If re-analysis changes phase numbering and an
  in-progress branch (e.g., `roadmap/phase-2/plan-x`) would be affected, surface via
  `AskUserQuestion`:
  "Phase restructuring would affect in-progress branch [roadmap/phase-2/plan-x]. Options:
  (1) Rename the branch to match the new structure, (2) Keep current phase structure for
  in-progress work and only re-sequence not-started phases, (3) Abandon in-progress branch
  and start fresh."
  Default to option 2 (least disruptive).

---

## Phase 0.C — Cleanup Mode

Executes when user selects "Clean up completed work."

1. Run completion tracking (see **Completion Tracking** section below) to determine which phases
   are fully complete.
2. Present a summary in chat:
   > "Phases 1-2 complete. Phase 3 in progress. Cleanup actions available for completed phases."
3. Before any mutations, present all planned actions via `AskUserQuestion` and require
   explicit confirmation:
   > "Cleanup will:
   > - Archive source plans for Phase 1: [plan-a.md, plan-b.md] → {plan-dir}/done/
   > - Archive source plans for Phase 2: [plan-c.md] → {plan-dir}/done/
   > - Delete local branches: roadmap/phase-1/plan-a, roadmap/phase-1/plan-b,
   >   roadmap/phase-2/plan-c
   > - Add 'Status: Completed' to Phase 1 and Phase 2 blocks in the roadmap document
   > Proceed?"
4. **Remote branch deletion is a separate opt-in** (never default, never cached):
   > "Also delete these branches from origin? (This affects shared state — confirm per session)"
5. Execute cleanup actions **sequentially**: archive files first → delete local branches →
   delete remote branches (if opted in) → update roadmap document. If any step fails (e.g.,
   permission error, branch checked out elsewhere), **stop immediately** and surface the
   error in chat. Do not continue to remaining steps — inconsistent state is better diagnosed
   with a clear stopping point.
6. Update the roadmap document: append `**Status:** Completed` to each cleaned-up phase
   block (non-destructive — preserves phase content for reference).

**Partial cleanup guidance:**
- Plans that appear in tasks spanning multiple phases (task splitting) are NOT archived
  until ALL their phases are complete.
- If all phases are complete: add `**Status:** Completed` to the document header (not just
  per-phase blocks), then offer to archive the entire roadmap file itself:
  > "All phases are complete. Archive the roadmap file to {plan-dir}/done/ as well?"
  If the user confirms, update the document header `**Status:**` to `Archived` before
  moving the file to `{plan-dir}/done/`.

**Branch deletion scoping:** Only delete branches matching `roadmap/phase-N/` for the
specific completed phase numbers. Do NOT delete branches for in-progress or not-started
phases — filter by exact phase number, not a broad `roadmap/*` pattern.

**After cleanup completes**, announce: "Cleanup complete. [N] plans archived, [M] branches
deleted, [P] phase blocks marked Completed." If not all phases were cleaned up, note which
phases remain active.

---

## Phase 0.S — Status & Drift Report Mode

Executes when user selects "Show status / drift report." **This mode is read-only — it
never mutates files or branches.**

**Status report:**

1. Run completion tracking (see **Completion Tracking** section below) for all phases.
2. Present per-phase summary in chat:
   ```
   Phase 1: Completed (2/2 tracks merged, all tasks validated)
   Phase 2: In Progress (Track A merged, Track B in-progress)
   Phase 3: Not Started (blocked by Phase 2)
   ```
3. For any phase with `partial` or `deferred` tasks, list the specific tasks and their status.

**Drift detection (shown alongside status):**

- **Modified plans:** Determine the roadmap's generation timestamp: if the roadmap contains a
  `**Last updated:**` field, parse its ISO timestamp as the reference time. Otherwise, use
  the roadmap file's mtime via
  `Bash(uv run python3 -c "import os; print(os.path.getmtime('[file]'))")` (portable across
  macOS and Linux). For each source plan, compare its mtime against the reference time. If a source
  plan is newer, flag: "Plan [path] was modified after this roadmap was generated."
  On mtime anomaly (plan appears newer but changes unclear), fall back to content comparison:
  read both the plan and the roadmap's corresponding plan reference and diff to confirm
  whether real changes exist.
- **New untracked plans:** Glob for `*.md` in the plan directory that are NOT in the
  roadmap's source plans list AND are not the roadmap file itself. Flag only files that:
  (a) appear to be incremental-planning output (contain `**Goal:**` and `## Task N:` markers)
  AND (b) were created after the roadmap was generated (mtime comparison). This filtering
  prevents false positives from old completed plans or non-plan files.
  Flag: "New plan [path] not included in this roadmap."
- **Deleted plans:** For each source plan path, verify it still exists. Flag any missing:
  "Plan [path] no longer exists."
- **Orphaned branches:** Check `git branch | grep roadmap/` for any `roadmap/phase-*`
  branches that don't match any track in the current roadmap. Flag:
  "Orphaned branch [name] not in current roadmap (may be from a previous or renamed roadmap)."

**Follow-up actions:**

After presenting the report, offer via `AskUserQuestion`:
- "Update roadmap to incorporate changes" → Route to Phase 0.U (re-run from step 1; reuse
  the completion tracking results already gathered — do not re-run completion tracking)
- "Clean up completed work" → Route to Phase 0.C (reuse completion tracking results)
- "No action needed" → Exit

---

## Completion Tracking

Used by Phase 0.U (update), Phase 0.C (cleanup), and Phase 0.S (status/drift) to determine
the completion state of each phase and track. Run this logic whenever any mode needs phase
status before acting.

### Track status determination

For each track in each phase, determine status using the following strategy. Try the primary
method first; fall back if gh is unavailable or unauthenticated.

**Primary method (requires `gh` auth and network access):**

```bash
gh pr list --state merged --head roadmap/phase-N/plan-name --json number,mergedAt
```

Works regardless of merge strategy (merge commit, squash, or rebase). If this returns a
result, the track is `merged`.

**Fallback method (no network / no gh auth):**

```bash
# True merges
git branch --merged main | grep roadmap/phase-N/plan-name

# Squash merges (only works if PR title or commit message contains the branch path)
git log --oneline main | grep 'roadmap/phase-N/plan-name'
```

**Caveat:** GitHub's default squash commit message uses the PR title, not the branch name.
The `git log` grep only works if the PR title or commit message contains the branch path.
If neither fallback detects a merge, surface via `AskUserQuestion`:

> "Cannot determine if [branch] was merged. Was this PR squash-merged with a different
> commit message? Provide the PR number or commit SHA to confirm."

**Track status enum:**

| Status | Condition |
|--------|-----------|
| `not-started` | Branch `roadmap/phase-N/plan-name` does not exist locally or remotely |
| `in-progress` | Branch exists but no merged PR found |
| `merged` | PR merged (via gh) or branch content detected on main (via git fallback) |

### Subagent validation

For each track with `merged` status, spawn a **general-purpose** agent (not Explore — the
validator needs Bash for git commands, Read, and Grep) with this prompt:

> "Read the plan at [plan-path], tasks [task-range]. Find what the branch changed:
> First try `gh pr list --state merged --head roadmap/phase-N/plan-name --json number`
> to get the PR number, then `gh pr diff <number>` for the full diff. If gh is unavailable,
> find the merge/squash commit via `git log --oneline main | grep 'phase-N/plan-name'` and
> diff with `git diff <sha>~1..<sha>`. For each task, verify: (1) the files listed in
> **Files:** exist on main, (2) the diff shows changes consistent with the task's described
> steps (not just the file existing from before), (3) no task requirements were silently
> deferred or left as TODOs in the merged code. Report for each task: task number, status
> (implemented / partial / deferred), and a one-sentence evidence summary citing specific
> file paths or code locations — do NOT echo raw diff content."

**Important:** The subagent MUST NOT echo raw diff content to chat output (security
constraint — diffs may contain sensitive content). The validator returns structured verdicts
only: task number, status, evidence summary.

**Parallel spawning:** Spawn subagent validators for all merged tracks in a phase
simultaneously — they perform independent reads and can run in parallel.

### Phase status derivation

A phase is `Completed` only when:
1. ALL tracks in the phase have `merged` status, AND
2. ALL subagent validations report `implemented` for every task

If any track is `in-progress` or `not-started` → phase is `In Progress` or `Not Started`.
If all tracks are `merged` but any task is `partial` or `deferred` → phase is `Incomplete`
(all branches merged but not all tasks fully implemented — distinct from `In Progress` where
branches are still open).

**PR boundary completion:** For tracks derived from intra-plan PR boundaries, the
`**PRs:**` field in the plan file is the authoritative source for PR numbers. Read it
to determine which PR to check for merge status, rather than relying solely on branch
name detection.

### Handling validation gaps

When subagent validation finds `partial` or `deferred` tasks, surface via `AskUserQuestion`:

> "Phase [N] track [name]: Task [M] is [partial/deferred]. Evidence: [one-sentence summary].
> Options:
> 1. Create a follow-up plan for the deferred work
> 2. Mark as intentionally deferred (acknowledge and proceed)
> 3. Re-open the phase for remediation"

---

## Edge Case Guidance

### Single plan input

Trivial roadmap: one phase, one track. Still useful for documenting worktree isolation and
the execution skill to use. Write it — don't skip.

### All plans independent

Single phase with N parallel tracks. Skip dependency analysis (Phase 2) and go straight to
track assignment. Note in roadmap header: "No dependencies detected — all plans run in parallel."

### Circular dependencies detected

Surface immediately via `AskUserQuestion`:

> "I found a circular dependency: Plan A depends on Plan B, and Plan B depends on Plan A
> (via [specific files/tasks]). I cannot resolve this automatically. Options:
> 1. Break the cycle by extracting the shared work into a new prep task
> 2. Manually specify which plan should run first and accept the merge conflict
> 3. Re-examine whether the dependency is real"

Do not write a roadmap document until the cycle is resolved.

### Plan with no tasks

Skip it and note in the roadmap document header:

> **Skipped plans:** `/path/to/plan.md` — no `## Task N:` headings found.

### Non-conforming plan format

If a plan is missing `**Goal:**`, no `## Task N:` headings, or no `**Files:**` blocks, use
`AskUserQuestion` listing exactly what's missing. Offer:

1. Best-effort extraction (treat entire plan as one task)
2. Skip this plan
3. Let the user fix the plan format and retry

