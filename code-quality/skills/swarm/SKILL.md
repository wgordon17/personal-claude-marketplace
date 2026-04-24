---
name: swarm
description: >-
  Full TeamCreate agent swarm for implementation tasks. Launches a pipelined team
  of 21+ specialized agents (Architect, Security Design Reviewer, Reduction Analyst,
  Implementer, Reviewer, Test-Writer, Test-Runner, Boundary Updater, Security, QA,
  Code-Reviewer, Performance, Plan Adherence, Fixer, Test Coverage Agent,
  Code-Simplifier, Docs, Docs Reviewer, Lessons Extractor, Verifier, BDD-Step-Writer) with structured JSON
  communication, Cynefin domain classification, audit trails, and early user
  checkpoint. Use when asked to "swarm this", "full team", "agent team",
  "full send", or when maximum rigor is needed on an implementation task.
  Auto-detects optional domain reviewers (UI, API, DB) from codebase analysis.
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Agent, AskUserQuestion, TeamCreate, TeamDelete,
  SendMessage, TaskCreate, TaskUpdate, TaskList, TaskGet, CronCreate, CronDelete, Skill]
---

# /swarm — Full Agent Swarm Implementation

You MUST use the full TeamCreate swarm described here. Do not take shortcuts. Do not implement
the task yourself. Your role is orchestration — you route work, relay context, make judgment
calls, and coordinate the pipeline. Every phase of implementation goes through the appropriate
specialist agent.

---

## Team Composition

### Core Agents (always spawned)

| Phase | Role | Agent Type | Model | Can Edit | Purpose |
|-------|------|------------|-------|----------|---------|
| 0 | Lead (YOU) | — | — | No | Orchestration, routing, judgment |
| 2 | Architect | code-quality:architect | opus | No | Design, decomposition, risk analysis |
| 2.5 | Security Design | code-quality:security | opus | No | Pre-implementation threat surface review |
| 2.7 | Speculative Competitors (×N) | general-purpose | sonnet | Yes | Competing implementations in isolated worktrees (conditional) |
| 2.7 | Speculative Judge | general-purpose | opus | No | Evaluate competitors, select winner (conditional) |
| 2.8 | Reduction Analyst | general-purpose | opus | No | Pre-implementation simplification review |
| 3 | Implementer | general-purpose | sonnet | Yes | Write code, component by component |
| 3 | Reviewer | general-purpose | opus | No | Review each component before testing |
| 3 | Test-Writer | general-purpose | sonnet | Yes | Write tests for reviewed components |
| 3 | Test-Runner | code-quality:test-runner | haiku | No | Execute tests, report results |
| 3 | Boundary Updater (incremental only) | general-purpose | sonnet | Yes | Write checkpoint.json and update plan file at PR boundary stops |
| 4 | Security | code-quality:security | opus | No | OWASP, auth, secrets, injection review |
| 4 | QA | code-quality:qa | opus | No | Patterns, conventions, code quality |
| 4 | Code-Reviewer | code-quality:code-reviewer | sonnet | No | Broader review, complements QA |
| 4 | Performance | code-quality:performance | sonnet | No | Bottlenecks, N+1, memory issues |
| 4 | Plan Adherence | code-quality:plan-adherence | opus | No | Verify implementation matches incremental plan |
| 4.5 | Structural Analyst (×2) | general-purpose | opus | No | Adversarial structural design review |
| 5 | Fixer | general-purpose | sonnet | Yes | Address ALL review findings |
| 5 | Test Coverage Agent | general-purpose | sonnet | Yes | Write tests for coverage gaps from Phase 4 |
| 5 | Code-Simplifier | code-quality:code-simplifier | sonnet | Yes | Post-fix simplification pass |
| 5.5 | Plan File Updater (conditional) | general-purpose | sonnet | Yes | Update plan file checkboxes after Lead reconciliation |
| 6 | Docs | general-purpose | sonnet | Yes | Update repo docs and hack/ memory |
| 6 | Docs Reviewer | general-purpose | sonnet | No | Verify Docs agent's work against architect's documentation_impact |
| 6 | Lessons Extractor | general-purpose | sonnet | Yes | Extract principle-level lessons from swarm run |
| 7 | Verifier | code-quality:test-runner | haiku | No | Final test suite + lint verification |

### Optional Domain Reviewers (auto-detected in Phase 1)

| Role | Agent Type | Model | Trigger |
|------|------------|-------|---------|
| UI Reviewer | general-purpose | sonnet | Glob finds `*.tsx`, `*.vue`, `*.svelte`, `*.css` files |
| API Reviewer | general-purpose | sonnet | Grep finds router/endpoint/handler patterns |
| DB Reviewer | general-purpose | sonnet | Grep finds migration/schema/model/query patterns |
| Plugin Validator | general-purpose | sonnet | `Glob("**/.claude-plugin/plugin.json")` finds results |
| Skill Reviewer | general-purpose | sonnet | `Glob("**/skills/*/SKILL.md")` finds results |

### Phase 7 Conditional Agents

| Phase | Role | Agent Type | Model | Can Edit | Purpose |
|-------|------|------------|-------|----------|---------|
| 7 | Jira Agent | jira:jira-agent | — | No | (conditional) Verify and transition Jira card to In Progress |

### Phase 3.5 Pipeline Agent

| Phase | Role | Agent Type | Model | Can Edit | Purpose |
|-------|------|------------|-------|----------|---------|
| 3.5 | BDD-Step-Writer | general-purpose | sonnet | Yes | (conditional) BDD step definition writing |

**Detection:** BDD-Step-Writer is spawned when the plan file `## Test Plan` section includes a
`**Feature Files:**` path. It is distinct from the read-only optional domain reviewers — it has
write access and runs as a pipeline agent in Phase 3.5, not as a Phase 4 reviewer.

---

## Workflow Phases

### Phase 0: Pre-flight & Setup

Run the project test suite and record the baseline (pass/fail count, any pre-existing failures).
Check git status — ensure the working tree is clean, identify the current branch, and determine
whether a feature branch is needed. If not already on a feature branch, create one from
`upstream/main` or `origin/main`. Verify that auto-compaction is enabled — the /swarm skill
depends on it for reliable agent operation (warn the user if disabled). Generate a run ID using the convention in `code-quality/references/project-memory-reference.md`
(Run-ID Naming Convention section) and create the audit trail directory at `{memory_dir}/swarm/{run-id}/`.
Call `TeamCreate("swarm-{run_id}")` (using the run ID generated above), then create all tasks upfront with `addBlockedBy` dependencies
so the full task graph is visible from the start.

**Test Plan Discovery:** After branch creation and run-ID generation, discover the plan file
using Branch-header matching: search `{memory_dir}/plans/` for files whose `**Branch:**` header
matches the current branch, excluding `plans/done/`. Fallback: filename slug matching against
the current branch slug. If a plan file is found AND it contains a `## Test Plan` section:
read the `**Test Plan:**` path from the annotation. Before reading: normalize the path (resolve
`..` segments) and verify it falls within `{memory_dir}/test-plans/`. If the normalized path
escapes `{memory_dir}/test-plans/`, set `{TEST_PLAN}` to empty string and log a warning. Before
copying any staged file, verify it is a regular file (not a symlink). If the path is valid: read
the test plan document and store as `{TEST_PLAN}` context. If `**Feature Files:**` path exists:
normalize and validate it falls within `{memory_dir}/test-plans/`. If `**BDD Setup Needed:** yes`,
record the install command. Store the plan file path for Phase 4 Plan Adherence and Phase 5.5
Plan Reconciliation to reuse (both phases skip re-discovery when the path is already known).

**Tracker Extraction:** If a plan file is found, extract `{tracker}` — the value of the
`**Tracker:**` field from the plan file header (see
`code-quality/references/tracker-field-spec.md` for field values and parsing spec).
Phase 7 references `{tracker}` without re-reading the plan file. This keeps plan file
parsing centralized at Phase 0, consistent with the existing `{TEST_PLAN}` pattern. If
the `**Tracker:**` field is absent from the plan file header (pre-feature plans), set
`{tracker}` to `none`.

**Tracker validation:** After extraction, verify `{tracker}` is a terminal state
(`github:owner/repo#N`, `jira:PROJ-N`, or `none`) per `code-quality/references/tracker-field-spec.md`
Finalization Constraint section. If `{tracker}` is a non-terminal state (`github:pending`,
`github:linked#N`, or `jira:pending`), warn via `AskUserQuestion`: "Plan file has unresolved
tracker state '{tracker}'. Run /incremental-planning Phase 6 to resolve it, or set to 'none'
to skip issue tracking." Do not proceed to Phase 1 until the tracker is resolved or set to
`none`.

**Workflow detection:** After discovering the plan file, extract `**Workflow:**` from the header.
If `incremental`:
- Extract `**PR Boundaries:**` and parse task-to-PR mapping
- Extract `**PRs:**` for any already-created PR numbers
- Set `{workflow_mode}` = `incremental`, `{pr_boundaries}` = parsed mapping
- **Checkpoint discovery:** Glob `{memory_dir}/swarm/*/checkpoint.json` for any checkpoint
  files. For each found, read the `plan_file` field and match against the current plan file
  path. Use the matching checkpoint (there should be at most one per plan file). This handles
  the cross-run-ID case where the original run_dir has a different run-id than the current
  session's run-id. Store the original `run_dir` from the checkpoint's `run_dir` field.
- **Initial branch naming:** For the first PR boundary (no checkpoint exists), derive
  `{plan-slug}` from the plan file path using the same algorithm as checkpoint resume:
  extract the basename, strip the run-id prefix (`{branch-slug}-{timestamp}-`) and `.md`
  extension, then apply the Branch Slug Sanitization Rules from `project-memory-reference.md`.
  Rename the Phase 0-created branch to `feat/{plan-slug}-pr1` (via `git branch -m`). This
  rename happens AFTER TeamCreate and test baseline — the team name uses the run-id, not the
  branch name. The rename is a git-only operation that does not affect the team or audit trail.
  This establishes the naming convention that resume uses for subsequent boundaries.
If `fast` or absent: proceed with existing fire-and-forget behavior unchanged.

**Checkpoint resume (incremental only):** If checkpoint discovered via glob:
- Read checkpoint, extract `completed_prs`, `current_pr`, `tasks_remaining`
- **Null PR recovery:** If the most recent `completed_prs` entry has `pr_number: null`,
  the previous session completed implementation but crashed before creating the PR. The
  branch with the work should still exist. Before creating a PR: check if one already exists
  for the boundary's branch (`gh pr list --head feat/{plan-slug}-pr{N} --state open --json number`).
  If found, use that PR number. If not found, create the draft PR (check branch exists via
  `git branch -l`, push if needed). Then update the checkpoint with the PR number.
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
  Apply the Branch Slug Sanitization Rules from `project-memory-reference.md`.
- Create new branch for the next PR boundary: `feat/{plan-slug}-pr{N}` from
  `origin/{branch_base}` (using the `branch_base` field from the checkpoint, typically `main`)
- Announce: "Resuming swarm. Prior work merged. Remaining tasks: {list}."
  Include the checkpoint's `context_summary` in the Implementer's briefing so it has context
  about what was built in earlier boundaries.
- **Resume Phase 0 variant:** Still run these Phase 0 steps for the new session:
  TeamCreate with session-scoped name (`swarm-{original_run_id}-s{session_counter}` where
  `{original_run_id}` is extracted from the checkpoint's `run_dir` field and
  session_counter increments from the count of completed_prs + 1), test baseline, git
  status check. Reuse the original run-id's `{run_dir}` (from checkpoint's `run_dir` field)
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

### Phase 1: Clarify & Checkpoint (EARLY — fire-and-forget after approval)

Use `AskUserQuestion` to resolve any ambiguity in the task before spawning agents. Auto-detect
optional reviewers: Glob for UI files (`*.tsx`, `*.vue`, `*.svelte`, `*.html`, `*.css`); Grep
for API patterns (`router`, `endpoint`, `handler`, `@api`); Grep for DB patterns (`migration`,
`schema`, `model`, `query`). Present the proposed swarm composition to the user — list all core
agents plus any auto-detected optional reviewers. Allow the user to add or remove reviewers.
After the user approves the composition and confirms they understand the scope, proceed without
further checkpoints. The user can walk away after Phase 1 approval.

Announce BDD-Step-Writer as part of the composition if Phase 0 discovered a plan file with a
`**Feature Files:**` path in its `## Test Plan` section. BDD-Step-Writer is a Phase 3.5 pipeline
agent with write access — it is distinct from read-only optional domain reviewers and does not
run in Phase 4. Include it in the composition count presented to the user.

**jira:jira-agent** (conditional) — Spawned at Phase 7 completion when `{tracker}` contains
`jira:PROJ-N`. Verifies card status and transitions to In Progress. Cross-plugin agent
spawning is validated: jira-agent.md explicitly lists "swarm implementers, quality-gate
verifiers, or any agent" as valid spawners.

### Phase 2: Architect (opus)

Spawn the architect agent with the full task description, codebase context, and audit trail path.
The architect begins by classifying the task using the Cynefin framework
(see `references/cynefin-reference.md`), then designs the solution accordingly:

- **Clear / Complicated:** Standard decomposition into independent components with dependency graph.
- **Complex:** Probe design — experiments and signals rather than a full plan. Smaller components
  with explicit checkpoints. Phase 2.5 Security Design Review is mandatory regardless of surface area.
- **Chaotic:** Stabilization brief only — immediate action plan to stop the bleeding. Phase 2.5
  is deferred until stabilization completes. Single Implementer, no pipeline parallelism.
- **Disorder:** Investigation plan first; classify domain before committing to implementation.

The Cynefin classification (`cynefin_domain` and `domain_justification`) is written to
`architect-plan.json` and is advisory — it informs how phases run, never whether mandatory phases
run. The Lead reads the classification to inform Phase 2.5 skip decisions.

Output is a structured JSON plan written to `{run_dir}/architect-plan.json`
(see schema in `references/communication-schema.md`). The architect also identifies global risks,
data model changes, API surface changes, and **documentation impact** — a `documentation_impact`
array listing which documentation surfaces are affected and why (READMEs, manifests, registries,
component tables, descriptions, dependency matrices, user-facing docs). This array feeds Phase 6.
After receiving the plan, the Lead MUST:

1. Read `architect-plan.json` and check the `questions` array
2. If `questions` is non-empty, present EVERY question to the user via `AskUserQuestion`
3. Verify scope: compare the plan's components against the original task — if any requested
   work is missing from the plan, add it back or ask the user via `AskUserQuestion`
4. Raise any high-impact risks to the user before proceeding
5. Note the `cynefin_domain` — use it to inform Phase 2.5 skip decision and pipeline mode
6. **External research escalation** — If the architect's design names third-party libraries,
   APIs, or architectural patterns that the Lead cannot confirm are current and well-understood
   from existing project context, invoke `/deep-research` (via the `Skill` tool) in External mode before proceeding to Phase 2.5.
   Pass the architect's technology recommendations as the research question. Feed findings back to
   the architect via SendMessage for plan revision if external evidence contradicts the
   recommendation.

Do NOT proceed to Phase 3 until all architect questions are resolved and scope is verified.
The Architect remains active through Phase 3 to answer clarification questions from the
Implementer and Reviewer.

### Phase 2.5: Security Design Review (conditional)

Skip this phase when: task is config-only, docs-only, or test-only AND does not touch
authentication, authorization, data storage, network, or API surface. Clear-domain tasks
(per `cynefin_domain` in architect-plan.json) that don't touch auth, data, network, or API
surfaces can also skip this phase. When in doubt, run the review.

Spawn the `code-quality:security` agent (opus model) with the architect's plan as input.
The agent reviews architect-plan.json for:
- Attack surface introduced or modified
- STRIDE threat categories (Spoofing, Tampering, Repudiation, Information Disclosure,
  Denial of Service, Elevation of Privilege)
- Trust boundary violations
- Security constraints the implementer must respect

Output: SecurityDesignReview JSON written to `{run_dir}/security-design-review.json`
(see schema in `references/communication-schema.md`).

**Routing:**
- needs-fix findings requiring architect plan revision → Route back to Architect (Phase 2) with security feedback for
  redesign. Architect revises plan, re-run Phase 2.5. Maximum 2 Architect↔Security iterations.
- no needs-fix findings blocking implementation → Append security constraints to architect-plan.json as
  `security_constraints` array and proceed to Phase 3.
- If 2 iterations exhausted with unresolved findings → Escalate to human via AskUserQuestion.

### Phase 2.7: Speculative Fork (conditional)

Skip this phase unless the architect's plan signals genuine implementation uncertainty — multiple
viable approaches with real trade-offs where "try both and compare" beats guessing. The trigger
is the architect explicitly flagging `speculative_fork_recommended: true` in
`architect-plan.json`, OR the Lead identifying 2+ incompatible design choices in the plan that
would significantly affect outcomes.

When triggered, the Lead presents the competing approaches to the user via `AskUserQuestion`:
"The architect identified N competing approaches for [component] (all vetted by security review).
Want to run /speculative to compare them, or pick one directly?"

If the user chooses `/speculative`, the Lead acts as the speculative orchestrator:

1. Extract the contested component(s) from `architect-plan.json`
2. Define evaluation criteria based on the architect's stated trade-offs
3. Run `/speculative` Phases 1–3 (competitors fork in isolated worktrees, judge evaluates,
   winning approach merged back)
4. The winning approach is written back into `architect-plan.json` as the implementation spec
   for that component, replacing the ambiguous description
5. Proceed to Phase 3 with the now-resolved plan

If the user picks one approach directly, skip /speculative and continue to Phase 3 as normal.

**Scope:** Only the contested component(s) go through the speculative fork. Uncontested
components proceed directly to Phase 3 without waiting (if pipeline-feasible).

**Watchdog:** CronDelete the Phase 2.7 watchdog after all competitors complete (or are timed
out/failed) and before merging the winning approach back into architect-plan.json. Delete on
all paths including abort — never leave orphaned cron jobs at phase boundaries.

**Escalation:** If the judge recommends hybrid AND the Lead agrees it's genuinely better,
run Phase 3.5 of the speculative skill (synthesis agent) before continuing to swarm Phase 3.
Note it in the audit trail.

When the architect classifies the domain as Complex AND competing approaches exist,
proactively recommend /speculative even if `speculative_fork_recommended` is not explicitly
set — Complex-domain tasks benefit most from speculative execution.

**Never trigger Phase 2.7 for:**
- Clear-domain tasks where the architect chose one approach without hesitation
- Tasks with a single component and no stated approach trade-offs
- Tasks where the user specified an explicit implementation approach in Phase 1

### Phase 2.8: Pre-Implementation Simplification Review

Before any code is written, the Lead spawns a **Reduction Analyst** (general-purpose,
opus model, read-only) to review the architect's plan against the existing codebase and recommend
simplifications. This phase catches over-engineering *before* implementation, when changes are
cheapest.

The Reduction Analyst receives:
- `architect-plan.json` (proposed components, files to create/modify)
- The existing codebase context (key files from architect's `key_files` list)
- Security constraints from Phase 2.5 (if any)

The Reduction Analyst evaluates:

1. **Existing code that can be removed:** Does the plan replace existing functionality? If so,
   identify the old code for deletion. Implementation should prefer removing the old path over
   adding a compatibility layer.

2. **Proposed abstractions that are premature:** Does any proposed component introduce
   an interface with one implementation, a factory for one type, or a plugin architecture with
   one plugin? Flag these for simplification.

3. **Dependency opportunities:** For each proposed custom implementation, check whether a
   well-maintained library already solves the problem (per
   `code-quality/references/dependency-evaluation.md`). Use WebSearch to verify recency and
   popularity against today's actual date.

4. **Net complexity assessment:** Will the plan result in a net increase or decrease in codebase
   complexity? If net increase, recommend specific reductions (merge files, inline abstractions,
   delete superseded code).

Output: `{run_dir}/reduction-review.json` with:
- `removals_recommended`: files/functions/classes to delete during implementation
- `abstractions_flagged`: proposed abstractions to simplify or skip
- `dependency_alternatives`: libraries that could replace custom code
- `net_complexity_assessment`: expected impact on codebase size and complexity

The Lead integrates reduction recommendations into the architect plan before Phase 3:
- Accepted removals → added to implementation instructions ("delete X before building Y")
- Accepted dependency alternatives → architect plan updated with library references
- Rejected recommendations → documented in audit trail with justification

**Skip conditions:** Config-only, docs-only, or test-only changes. Tasks where the architect's
plan modifies fewer than 3 files total.

### Phase 3: Pipelined Implementation

Before spawning pipeline agents, the Lead creates a CronCreate watchdog (60-second interval) to
monitor agent idle status. The watchdog reports to the Lead — it never intervenes directly. The
Lead then spawns the full pipeline team at once: Implementer, Reviewer, Test-Writer, and
Test-Runner. The Lead routes work through the pipeline using structured JSON messages — components
flow from Implementer to Reviewer to Test-Writer to Test-Runner, with the Implementer moving to
the next component while earlier ones advance through the pipeline. Each agent sends a
ContextAcknowledgment immediately upon receiving an assignment (see
`references/communication-schema.md`). Each handoff uses a typed JSON message. If the Reviewer
rejects a component, the Lead routes specific feedback back to the Implementer for targeted fixes
and re-submission (max 3 iterations per component). If Test-Runner reports failures, the Lead
routes the failure details back to the Implementer for fixes, then re-submits through Review and
Test stages. The watchdog is torn down (CronDelete) when Phase 3 completes or on any abort path.
See `references/pipeline-model.md` for full parallelism rules, backpressure handling, and
fallback to sequential mode.

#### Parallel Mini-Pipelines (when applicable)

If the architect's dependency graph contains 2+ independent component groups (no shared files,
no dependency edges between groups), the Lead SHOULD spawn parallel mini-pipelines rather than
serializing all components through one Implementer:

- Each mini-pipeline: Implementer (sonnet, worktree) → Reviewer (opus) → Test-Writer (sonnet)
  → Test-Runner (haiku)
- The Lead coordinates merge order based on the architect's merge priority
- Components with dependencies between them still flow through a single pipeline sequentially
- Each mini-pipeline's Implementer stays within context limits — no lossy handoffs needed

**Decision heuristic:**
```
IF architect.components.count > 3 AND architect.independent_groups.count > 1:
    Fan out independent groups to parallel mini-pipelines
ELSE:
    Use single pipeline (current behavior)
```

This reduces context pressure on individual Implementers and improves output quality for large
tasks. The Lead decides based on the dependency graph — never based on cost or token concerns.

**PR boundary handling (incremental only):** Each PR boundary produces a standalone,
independently-reviewable unit of work. The PR title and body describe what THIS PR
accomplishes — never "Part X of Y", never referencing other PRs in the series. A reviewer
must be able to judge the PR on its own merits without knowing other PRs exist.

After completing all tasks in the current PR boundary (all components for tasks in
`**PR:** {current_pr}`):
1. Run Phase 4 reviews scoped to the current boundary's files only. The Lead spawns the
   same core review agents as full Phase 4 (Security, QA, Code-Reviewer, Performance, plus
   any auto-detected domain reviewers from Phase 1) with `pr_boundary_files` — NOT via
   /pr-review skill, which reviews existing PRs. Plan Adherence and Phase 4.5 structural
   analysts also run at boundary stops (structural analysts receive partial-feature briefing
   per the Phase 4.5 incremental workflow section).
2. Run Phase 5 Fixer scoped to the current boundary's files only — the Lead passes
   `pr_boundary_files` to the Fixer agent's prompt (same mechanism as Phase 4 reviewers).
   Structural scoping per finding-classification.md Fixer Protocol.
   Note: Phase 5.5 (Plan Reconciliation) does NOT run at non-final boundary stops — it
   runs only at final completion, scoped to all tasks across all boundaries.
3. Spawn Verifier agent (tests + lint) — same agent as Phase 7 but invoked inline at
   boundary stop. Full test suite, not scoped. This is a Verifier agent invocation only,
   NOT the full Phase 7 pipeline (Phase 6 docs run only at final completion).
   **Partial-feature test failures:** Compare test results against the Phase 0 baseline.
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
9. Clean up: TeamDelete, exit swarm gracefully

If this is the LAST PR boundary: do NOT checkpoint. Instead, proceed with normal Phase 6
(docs) and Phase 7 (verification) completion flow. Clean up any existing checkpoint file.

### Phase 3.5: BDD Step Writing (conditional)

(Not to be confused with /speculative's internal Phase 3.5 synthesis within Phase 2.7.)

**Skip when:** The plan file has no `## Test Plan` section, OR the `## Test Plan` section has no
`**Feature Files:**` path, OR the `**Feature Files:**` path fails boundary validation (path
escapes `{memory_dir}/test-plans/`). If `{TEST_PLAN}` is empty string (failed path validation),
skip entirely.

When triggered, run the following steps after Phase 3 completes and before Phase 4 begins:

**Step 3.5.1 — Determine .feature target directory:** Check for an existing `features/` directory
in the repo root. If not found, check for `tests/acceptance/`. If neither exists, create
`tests/acceptance/`. Record the resolved target as `{feature_target_dir}`.

**Step 3.5.2 — Promote .feature files:** Copy each `.feature` file from the staging path
(`**Feature Files:**` directory in the test plan annotation) into `{feature_target_dir}`.
Before copying each file, verify it is a regular file and not a symlink (SEC-004). If a
`.feature` file with the same name already exists in `{feature_target_dir}`, use
`AskUserQuestion` to resolve the collision: offer "Overwrite", "Keep existing", or "Rename
incoming" for each conflict. Do not overwrite silently.

**Step 3.5.3 — BDD dependency installation (if needed):** If `**BDD Setup Needed:** yes`,
run the recorded install command (from `**BDD Setup Needed:**` annotation value, e.g.,
`uv add --dev pytest-bdd==7.x.x`) on the current feature branch. Use the version specifier
as recorded — do not strip versions.

**Step 3.5.4 — Build BDD context and spawn BDD-Step-Writer:** Construct `{bdd_framework_info}`:

```json
{
  "framework": "<value of **BDD Framework:** from test plan annotation>",
  "feature_dir": "<{feature_target_dir}>",
  "step_dir": "<value of **BDD Step Dir:** from test plan annotation>",
  "scaffold_cmd": "<value of **BDD Scaffold Command:** from test plan annotation>",
  "test_cmd": "<value of **BDD Test Command:** from test plan annotation>",
  "feature_files": ["<list of promoted .feature files>"]
}
```

Spawn the BDD-Step-Writer (general-purpose, sonnet, bypassPermissions) with the full context
bundle, `{TEST_PLAN}` content, `{bdd_framework_info}`, and the promoted `.feature` files.
The BDD-Step-Writer generates step definition skeletons for all unimplemented steps in the
`.feature` files. Inject `{TEST_PLAN}` into the BDD-Step-Writer prompt the same way it is
injected into the Implementer — the BDD-Step-Writer must understand user personas and scenario
intent to name steps correctly.

**Step 3.5.5 — Collect BDDStepHandoff:** Receive the BDD-Step-Writer's completion message
(`BDDStepHandoff` type) listing files created, step count, and any steps it could not scaffold.
Store `{bdd_framework_info}` for Phase 7 (Verifier scope). Shut down the BDD-Step-Writer.

**Phase 3.5 does NOT run BDD tests.** BDD test execution is deferred to Phase 7 — the Verifier
runs both the unit test suite and the BDD acceptance suite when `.feature` files are present.

### Phase 4: Parallel Review

Spawn ALL review agents simultaneously: Security, QA, Code-Reviewer, Performance, and any
auto-detected optional reviewers (UI, API, DB). Also spawn the Plan Adherence reviewer if an
incremental plan file is found (see below). All reviewers operate in read-only mode on the
completed implementation. Each writes structured JSON findings to `{run_dir}/reviews/`
(see schema in `references/communication-schema.md`). The Lead collects ALL findings and
synthesizes into a consolidated view. Every finding — regardless of classification — is routed to
Phase 5 for action. No finding is silently dropped or left unactioned in the audit trail.

**Plan Adherence reviewer:** If Phase 0 already discovered a plan file, reuse that path directly
(do not re-discover). If Phase 0 did not find a plan file, search `{memory_dir}/plans/` for an
incremental plan file whose `**Branch:**` header field matches the swarm's current feature branch.
If no Branch header match is found, fall back to filename matching using the branch slug. If a
plan file is found, spawn the `code-quality:plan-adherence` agent (opus model, read-only) alongside
the other Phase 4 reviewers. Provide it: the incremental plan file path, `{run_dir}/architect-plan.json`,
and the full diff (`git diff origin/main..HEAD`). If no incremental plan file is found, skip the
Plan Adherence reviewer entirely — `architect-plan.json` alone is insufficient to trigger this agent.

**Escalation Routing (before proceeding to Phase 5):**

After synthesizing findings, classify each finding by type and route accordingly:

| Finding Type | Examples | Routing |
|---|---|---|
| Design-level | Architecture mismatch, wrong abstraction, missing component entirely | Route back to Architect — respawn Phase 2, then re-run Phase 2.5, then re-implement Phase 3 |
| Security design | Trust boundary violation in architecture, missing auth layer, new attack surface from design decision | Route to Phase 2.5 Security Design Review for design-level fix |
| Scope creep | Feature implemented that was not in the plan, undiscussed behavior introduced | Escalate to human via AskUserQuestion — do not fix silently |
| Implementation | Bugs, quality issues, code-level security vulnerabilities, performance bottlenecks | Route to Phase 5 Fixer |
| Test coverage | Missing tests, untested paths, coverage gaps for the deliverable | Route to Phase 5 Test Coverage Agent |
| Documentation | Missing or incorrect documentation for implemented features | Route to Phase 6 Docs agent |
| Plan drift | Implementation diverges from plan task spec, missing tasks, unimplemented steps | Route to Phase 5 Fixer for fixable items; AskUserQuestion for scope-level deviations |

**Escalation counter:** Track a `design_escalation_count` across the swarm run. Each time findings
trigger a return to Phase 2 (design-level) or Phase 2.5 (security design), increment the counter.
Maximum 2 total design/security escalations per swarm run — if this cap is reached with unresolved
findings, escalate to the human via AskUserQuestion rather than re-running again. This caps
Phase 3 re-implementations at 2 regardless of escalation type.

All escalation events are recorded in `{run_dir}/escalations.json`
(see schema in `references/communication-schema.md` under "Escalation Events Schema").

**Incremental workflow scoping:** When `{workflow_mode}` is `incremental`, reviewers receive
only the diff for the current PR boundary's files (not the full branch diff). The Lead
constructs the file list from the current PR boundary's tasks' `**Files:**` blocks and passes
it to each reviewer's prompt as `pr_boundary_files`. Reviewers analyze only those files.

### Phase 4.5: Structural Design Review

This phase always runs after Phase 4 escalation routing completes. It is not conditional on
whether Phase 4 found issues — it is a mandatory adversarial pass that reviews the full
implementation as a system.

Spawn 2 adversarial structural analysts simultaneously (both `general-purpose`, opus model):

- **Analyst 1: Concurrency & State** — race conditions, state management gaps, error propagation
  across component boundaries, dependency ordering issues, shared mutable state
- **Analyst 2: Integration & Contract** — API contract violations, cross-component assumptions,
  data flow integrity, failure cascading between components, contract drift from architect plan

Both analysts review the entire implementation as a system, not file-by-file. They look for
structural problems that emerge from the *combination* of components, not issues within a single
component (those are covered by Phase 4 reviewers).

Findings use the existing `ReviewFindings` schema with the `STRUCT` prefix (STRUCT-001, etc.)
and are written to `{run_dir}/reviews/structural-concurrency.json` and
`{run_dir}/reviews/structural-integration.json`.

**Routing:**
- STRUCT findings requiring architectural redesign follow the Phase 4 escalation routing rules
- STRUCT escalations count toward the cumulative `design_escalation_count` cap (max 2 total
  re-implementations before human escalation)
- STRUCT escalation events are logged to `{run_dir}/escalations.json`

If Phase 4 escalation routing triggers a return to Phase 2, Phase 4.5 runs on the re-implemented version after the next Phase 4 completes — not on the current (superseded) implementation.

Phase 5 receives findings from both Phase 4 AND Phase 4.5 in its consolidated findings list.

**Incremental workflow — scoped review:** At non-final PR boundaries, Phase 4.5
structural analysts review only the current boundary's code as standalone work. The Lead
briefs analysts: "Review this code on its own merits as a self-contained unit of work.
Flag structural issues in THIS code. Do not flag missing integration points or incomplete
data flows that are addressed by tasks not yet implemented — those are separate work."
Findings about genuinely missing contracts within the current boundary are valid; findings
about not-yet-implemented work are not.
At the FINAL PR boundary: Phase 4.5 runs normally (full system review).

### Phase 5: Fix, Test Coverage & Simplify

Skip this phase only if ALL reviews (Phases 4 and 4.5) report zero findings.
Otherwise, spawn agents in this order:

**Step 5.1 — Fixer:** Spawn the Fixer with ALL consolidated findings (all classifications)
and full context of the implementation. The Fixer processes all findings — process in file order
per `code-quality/references/finding-classification.md` Fixer Protocol. After the Fixer
completes, the Lead handles user triage for `needs_input_items` and structural verification
per Step 5.2.1. For each `user_deferred` item:
1. Create a `TaskCreate` entry marked as blocked with the reason (visible in task list throughout)
2. Add to the "Scope Accountability" section of `swarm-report.md` (permanent record)

**Step 5.2 — Test Coverage Agent:** If ANY Phase 4 or 4.5 reviewer identified test coverage
gaps, missing tests, or untested code paths, spawn the Test Coverage Agent (sonnet,
bypassPermissions). This agent writes tests that Phase 3's Test-Writer could not have written —
coverage gaps identified only after the full implementation was reviewed. The Test Coverage Agent
receives:
- All test-related findings from Phase 4/4.5 (coverage gaps, missing edge case tests, untested
  components, untested error paths)
- The full list of files modified by the swarm
- The existing test suite location and testing conventions
- The Phase 3 Test-Writer's TestHandoff summaries (what was already tested)

The Test Coverage Agent writes the missing tests and reports a TestCoverageResult listing
test files created, test count, and which findings were addressed. Run the test suite after
to confirm all new tests pass.

**Step 5.3 — Code-Simplifier:** Spawn the Code-Simplifier for a post-fix pass — it looks for
over-engineering, unnecessary abstractions, and complexity introduced during implementation or
fixing. Skip Code-Simplifier only if neither the Fixer nor the Test Coverage Agent made any
changes. Re-run affected tests after any fixes to confirm nothing regressed.

### Phase 5.5: Plan Reconciliation

If no incremental plan file exists (as determined during Phase 4 plan file discovery), skip this
phase entirely.

If a plan file was found, the Lead performs reconciliation:

1. **Discover the plan file** — If Phase 0 already discovered a plan file, reuse that path
   directly. Otherwise, use the same Branch-header matching as Phase 4: search
   `{memory_dir}/plans/` for a file whose `**Branch:**` field matches the swarm's feature branch.
   Fall back to branch-slug filename matching if no header match is found.

2. **Cross-reference tasks against the cumulative diff** — Run `git diff origin/main..HEAD` to
   produce the full cumulative diff. Read the plan file and extract every task (checked and
   unchecked). For each task, determine whether the diff fully addresses it.

3. **Escalate unaddressed tasks** — For any task that is not fully addressed by the diff, use
   `AskUserQuestion` to escalate with: the task description, what was done toward it (if anything),
   and what remains unimplemented. Do NOT silently skip unaddressed tasks.

4. **Spawn a plan file updater** — After escalation decisions are made, spawn a general-purpose
   sonnet agent (Can Edit: Yes) with the plan file path and reconciliation results. This agent:
   - Checks off tasks that were completed (`[x]`)
   - Marks tasks skipped by user decision as `[SKIPPED by user]`
   - Marks tasks blocked due to unresolved issues as `[BLOCKED: reason]`
   - Do NOT modify the `## Test Plan` section or any content below it — it is a
     machine-readable annotation consumed by downstream skills with exact field label matching.

   The Lead does NOT write to the plan file directly (Can Edit: No).

**PR tracking reconciliation (incremental only):** At final completion:
- **Scope the cumulative diff correctly:** `git diff origin/main..HEAD` only contains the
  FINAL boundary's changes (earlier boundaries were merged via separate PRs). For step 2
  (cross-reference tasks against diff), check only the current boundary's tasks against the
  diff. Tasks from earlier boundaries were already completed and merged — verify them via
  the checkpoint's `completed_prs` entries (each with `merged: true`) rather than the diff.
- Verify the `**PRs:**` field in the plan header is fully populated (all boundaries have PR
  numbers, no `pending` entries remain). Cross-reference against `{run_dir}/checkpoint.json`
  `completed_prs` if available.
- Check off all tasks from all PR boundaries (not just the final boundary's tasks).

### Phase 6: Docs & Memory

Spawn the Docs agent (**sonnet model** — documentation requires judgment about what's user-facing
and what's changed) with the full list of modified files, a summary of what changed, and the
architect's `documentation_impact` array from `architect-plan.json`.

The Docs agent performs three passes:

**Pass 1: Architect-guided updates** — Work through each entry in `documentation_impact`:
- For each affected documentation surface, read the current state and update it
- If a new feature/skill/command/agent was added: add entries to README tables, update component
  counts, add to plugin manifest descriptions, add to marketplace registry descriptions
- If a feature was removed/renamed: remove or update all references across all doc surfaces
- If dependencies changed: update dependency matrices and requirements sections

**Pass 2: Discovery-based completeness check** — Independent of the architect's list:
- Glob for all documentation surfaces (READMEs, manifests, registries, CONTRIBUTING.md)
- Compare on-disk components against documented components (e.g., count skills in `skills/*/SKILL.md`
  vs skills listed in README table — they must match)
- Grep for stale references to renamed/removed features
- Verify component counts, descriptions, and cross-references are internally consistent

**Pass 3: Changed-file documentation** — For each file modified in the swarm, check if
corresponding documentation needs updating (README behavior descriptions, API docs, config docs,
CONTRIBUTING.md). Update only what is directly affected.

The Docs agent also detects the project's memory directory (per `code-quality/references/project-memory-reference.md`,
Directory Detection section) and updates PROJECT.md with architectural decisions, TODO.md with completed and new
items, and SESSIONS.md with a 3-5 bullet summary.

**Skip conditions for Phase 6 docs (memory updates always run):**
Only skip documentation updates for purely internal refactors with no public API, documented
behavior, feature, or component changes. When in doubt, run the docs pass — the cost of a
no-op docs check is low; the cost of missing documentation is high.

After the Docs agent completes, spawn a **Docs Reviewer** agent (sonnet, read-only) to verify
the Docs agent's work. The Docs Reviewer receives:
- The architect's `documentation_impact` array from `architect-plan.json`
- The Docs agent's completion report (surfaces updated, counts, gaps found)
- `git diff` of documentation changes made by the Docs agent
- `git diff` of **implementation changes** (code, config, skills — everything NOT docs) so the
  reviewer can verify docs against what was actually written, not just what the architect planned

The Docs Reviewer checks:
1. **Impact coverage** — Was every entry in `documentation_impact` addressed? If the architect
   flagged a surface and the Docs agent didn't touch it, that's a finding.
2. **Accuracy vs implementation** — Assume the docs are wrong until proven otherwise. Read the
   implementation diff and verify every documented claim against the actual code. Do component
   counts match on-disk reality? Do descriptions accurately describe the implemented behavior
   (not just the planned behavior)? If a feature was implemented differently than the architect
   planned, do the docs reflect what was actually built?
3. **Consistency** — Do all documentation surfaces agree with each other after the Docs agent's
   edits? (README ↔ manifest ↔ registry ↔ root README)
4. **Completeness** — Did the Docs agent miss any documentation surface the Reviewer can
   discover independently via Glob?
5. **Plan drift** — Does the implementation diff reveal behavior that diverged from the
   architect's plan? If so, do the docs follow the plan (wrong) or the code (right)?
   Docs must always describe what the code does, never what the plan said it would do.

Findings are written to `{run_dir}/reviews/docs-review.json` using the standard ReviewFindings
schema with `DOC-R` prefix. `needs-fix` findings are routed back to the Docs agent for fixes
(max 1 iteration — the Docs Reviewer re-reviews after fixes). `needs-input` findings are
presented to the user via AskUserQuestion using the option-based format from
`code-quality/references/finding-classification.md` Fixer Protocol. All findings are recorded
in the audit trail.

After the Docs Reviewer completes (or confirms clean), spawn a separate **Lessons Extractor** agent (sonnet model). This
agent scans the swarm run's audit trail and extracts principle-level lessons to
`{memory_dir}/LESSONS.md` (creating the file if it does not exist, where `{memory_dir}` is the
project memory directory detected per `code-quality/references/project-memory-reference.md`). It reads:
- `{run_dir}/architect-plan.json` — Cynefin domain, questions raised, risks flagged
- `{run_dir}/reviews/` — recurring finding patterns across reviewers
- `{run_dir}/escalations.json` — escalation events (if the file exists)
- `{run_dir}/fix-summary.json` — what the Fixer had to address (if the file exists)
- Human checkpoint feedback from Phase 1 and Phase 2 (logged in `.swarm-run`)

Lessons are principle-level only (no file paths, no implementation details). Each lesson uses the
format from `code-quality/skills/incremental-planning/references/lessons-template.md`:
`- [Category] Pattern observed → What to do differently → Why it matters (YYYY-MM-DD)`

The Lessons Extractor runs after Docs completes to avoid audit trail races.

### Phase 7: Verification & Completion

Spawn the Verifier to run the full test suite and lint. Compare results against the Phase 0
baseline — all tests that passed before must still pass; net-new failures are a blocker. If BDD
`.feature` files were promoted in Phase 3.5, pass `{bdd_framework_info}` to the Verifier — it
runs BOTH the unit test suite AND the BDD acceptance suite. BDD failures are blockers on the
same terms as unit test failures. After Verifier reports green, invoke the `quality-gate` skill
for automated multi-pass review with rotating adversarial lenses, fresh-context subagent reviews,
and blocking memory/artifact gates.
If there are 20 or more modified files, run an `/unfuck` sweep to
catch any issues introduced at scale.

**Issue Tracking (before completion announcement):**

If `{tracker}` matches `github:owner/repo#N`:
1. Validate format before interpolation per `code-quality/references/tracker-field-spec.md`
   Validation Regex section. Skip if invalid.
2. Add the `in-progress` label to the linked GH issue (best-effort — if the command
   fails, log a warning in the completion announcement and continue):
   `gh label create in-progress --repo <owner/repo> --description 'Work actively underway' --color 'fbca04' 2>/dev/null || true`
   `gh issue edit N --repo <owner/repo> --add-label 'in-progress'`
   (auto-create the label first per `code-quality/references/github-label-definitions.md`,
   using create-if-missing without `--force` to avoid overwriting existing repo customizations)
3. Include in the completion announcement: "Include `Closes #N` in the PR body
   to auto-close the linked GH issue when merged. After merge, remove the
   `in-progress` label: `gh issue edit N --repo <owner/repo> --remove-label 'in-progress'`."

If `{tracker}` matches `jira:PROJ-N`:
1. Spawn `jira:jira-agent` to check the card's current status. If NOT already
   "In Progress", transition it. (Jira transitions are not idempotent — attempting
   to transition from "In Progress" to "In Progress" will fail on most workflows.)
2. Include in the completion announcement: "Linked Jira card PROJ-N is In Progress."

Generate the final audit report at
`{run_dir}/swarm-report.md`. Announce completion with a summary and report path.
Shut down all teammates via `SendMessage(to="*", message={"type": "shutdown_request", "reason": "Swarm complete"})` and call `TeamDelete`.

---

## Orchestration Flow Diagram

```
User request
     |
     v
Phase 0: Pre-flight
  +-- Baseline tests
  +-- Git branch check/create
  +-- Generate run-ID, create {memory_dir}/swarm/{run-id}/
  +-- TeamCreate + TaskGraph
  +-- Extract {tracker} from plan file (default: none)
  +-- Workflow detection: extract **Workflow:** from plan header
       |
       +-- [incremental] Checkpoint glob: {memory_dir}/swarm/*/checkpoint.json
       |        |
       |        +-- [checkpoint found, PR not merged] --> AskUserQuestion (blocker)
       |        |
       |        +-- [checkpoint found, PRs merged] --> Resume:
       |        |     fetch origin/main, create feat/{plan-slug}-pr{N} branch
       |        |     skip Phase 1 + Phase 2, jump to Phase 3 (current PR boundary tasks)
       |        |
       |        +-- [no checkpoint] --> rename branch to feat/{plan-slug}-pr1, continue
       |
       +-- [fast / absent] --> existing behavior unchanged
     |
     v
Phase 1: Clarify & Checkpoint <-- AskUserQuestion (ambiguity resolution)
  +-- Auto-detect optional reviewers
  +-- Present composition to user
  +-- User approves -------------- FIRE-AND-FORGET AFTER THIS
     |
     v
Phase 2: Architect (opus)
  +-- Analyzes codebase
  +-- Decomposes into components
  +-- Writes architect-plan.json
  +-- Lead reviews --> AskUserQuestion if risky
     |
     v
Phase 2.5: Security Design Review (conditional, opus)
  +-- Reviews architect-plan.json for threat surface
  +-- STRIDE analysis of proposed architecture
  +-- needs-fix findings requiring revision --> back to Architect (max 2 iterations)
  +-- no needs-fix findings blocking implementation --> append security_constraints, proceed
  +-- Unresolved findings after 2 iterations --> AskUserQuestion
     |
     v
Phase 2.7: Speculative Fork (conditional)
  +-- Triggered by speculative_fork_recommended: true in architect-plan.json
  +-- OR Lead identifies 2+ incompatible design choices with real trade-offs
  +-- Spawn N competitors (sonnet, isolated worktrees) for contested components
  +-- CronCreate watchdog (60s interval)
  +-- Spawn judge (opus) --> JudgmentResult selects winner
  +-- Winning approach written back into architect-plan.json
  +-- (optional) Phase 3.5 synthesis if hybrid recommended and approved
     |
     v
Phase 2.8: Pre-Implementation Simplification Review (conditional)
  +-- Reduction Analyst (opus, read-only): reviews plan against codebase
  +-- Identifies: removable code, premature abstractions, dependency alternatives
  +-- Net complexity assessment: will this increase or decrease codebase size?
  +-- Lead integrates accepted recommendations into architect plan
     |
     v
Phase 3: Pipelined Implementation
  +-------------------------------------------------------+
  | Implementer --> Reviewer --> Test-Writer --> Test-Runner |
  |      |              |              |               |    |
  | (next comp)   (feedback)     (next comp)    (results)   |
  |      <-------- reject ----------------------------------  |
  +-------------------------------------------------------+
     |
     +-- [incremental, non-final PR boundary] ---------------+
     |   PR boundary stop:                                   |
     |   +-- Phase 4 review (scoped to boundary files)       |
     |   +-- Phase 5 Fixer (scoped to boundary files)        |
     |   +-- Verifier (full test suite, baseline comparison) |
     |   +-- Boundary Updater agent: write checkpoint.json   |
     |   +-- gh pr create --draft feat/{slug}-pr{N}          |
     |   +-- gh issue comment (if tracker exists)            |
     |   +-- Boundary Updater: set pr_number in checkpoint + update plan **PRs:**   |
     |   +-- Announce PR + checkpoint, TeamDelete, exit      |
     |   [user merges PR, invokes /swarm again → Phase 0]    |
     +-------------------------------------------------------+
     |
     v  [fast workflow OR final PR boundary]
Phase 3.5: BDD Step Writing (conditional — only when Feature Files in Test Plan annotation)
  +-- Promote .feature files from staging → {feature_target_dir}
  +-- Install BDD dependency if BDD Setup Needed: yes
  +-- BDD-Step-Writer (sonnet, bypassPermissions): generate step definition skeletons
  +-- Collect BDDStepHandoff, store {bdd_framework_info} for Phase 7
     |
     v
Phase 4: Parallel Review
  +-- Security (opus) --------------------------------+
  +-- QA (opus) --------------------------------------|
  +-- Code-Reviewer (sonnet) -------------------------+--> Lead synthesizes findings
  +-- Performance (sonnet) ---------------------------|
  +-- Optional: UI / API / DB reviewers -------------+
  [incremental: reviewers receive pr_boundary_files, analyze only those files]
     |
     v
Phase 4.5: Structural Design Review (always runs)
  +-- Analyst 1: Concurrency & State (opus) ---------+
  +-- Analyst 2: Integration & Contract (opus) -------+--> Lead merges STRUCT findings
  [incremental non-final: analysts briefed on partial feature — don't flag future-boundary gaps]
     |
     v
Phase 5: Fix, Test Coverage & Simplify (if any findings exist)
  +-- Fixer: ALL findings (process in file order — needs-fix first, then needs-input to Lead)
  +-- Test Coverage Agent: coverage gaps from Phase 4/4.5
  +-- Code-Simplifier: post-fix pass
     |
     v
Phase 5.5: Plan Reconciliation (if incremental plan file found)
  +-- Lead: discover plan file via Branch-header matching
  +-- Lead: cross-reference tasks against git diff origin/main..HEAD
  +-- Lead: AskUserQuestion for any unaddressed tasks
  +-- Plan File Updater (sonnet): check off completed, mark skipped/blocked
  [incremental: verify **PRs:** fully populated, check off tasks from ALL boundaries]
     |
     v
Phase 6: Docs & Memory
  +-- Docs agent: repo docs + hack/ updates
  +-- Docs Reviewer: verify Docs agent's work
  +-- Lessons Extractor: audit trail → {memory_dir}/LESSONS.md
     |
     v
Phase 7: Verification & Completion
  +-- Verifier: full test + lint
  +-- quality-gate skill
  +-- /unfuck sweep (if 20+ files changed)
  +-- Issue tracking: add in-progress label (GH) or transition card (Jira)
  +-- Generate {run_dir}/swarm-report.md
  +-- Shutdown all teammates, TeamDelete
```

---

## Lead Responsibilities

You are the orchestrator. You coordinate the pipeline, relay context, and make judgment calls.
You never implement, review, or test code yourself.

### Task Graph

Create all tasks upfront in Phase 0 using TaskCreate with addBlockedBy dependencies. The full
task graph should be visible from the start so the user can see the plan at any time.

### Context Relay

Every agent receives a structured context bundle when spawned (see schema in
`references/communication-schema.md`). The bundle includes: project name, task description,
current branch, key files from the architect's plan, audit trail path (`run_dir`), and the
tool guard reminder. Never spawn an agent without the full context bundle.

### Judgment Calls

You decide: which review findings are critical vs. noise; when to escalate to the user; whether
a component rejection warrants escalation vs. another iteration; whether a test failure is a
blocker or a pre-existing issue. Lean toward escalation for ambiguous decisions.

### Pipeline Coordination

Monitor the pipeline flow actively. Route handoff messages between agents using the schemas in
`references/communication-schema.md`. Detect backpressure (see `references/pipeline-model.md`)
and throttle the Implementer when the Reviewer queue is full.

### Watchdog Monitoring

Create a CronCreate watchdog (60-second interval) in Phase 3 Step 3.0 before spawning pipeline
agents. On each tick, check `TaskList` for `in_progress` tasks and cross-reference with agent
message timestamps. If any agent has been idle for 2+ consecutive checks, alert yourself and
trigger Silent Failure Detection (Step 3.6). Always CronDelete the watchdog when Phase 3 ends
or on any abort path — do not leave orphaned cron jobs.

### Context Health Monitoring

Track `turn_count` from every agent's structured messages. Proactively recycle agents approaching
context limits (default: 25 turns for Implementer/Test-Writer, 30 for Reviewer) using the
HandoffRequest → HandoffSummary → shutdown → respawn protocol (see `references/orchestration-playbook.md`
Step 3.6). Detect silent failures: if a teammate goes idle without completing their task or sending
a final message, initiate recovery (status check → replacement spawn → escalate if repeated).

### Feedback Loops

When the Reviewer rejects a component: collect the specific issues JSON, add context about what
the Implementer tried, and route the combined feedback back to the Implementer. Track iteration
counts per component — after 3 failed iterations, stash the component, create a blocked task,
and report to the user with full context.

### Audit Trail

Write all structured outputs to `{run_dir}/`:
- `architect-plan.json` — architect's component plan
- `reviews/security.json`, `reviews/qa.json`, etc. — review findings
- `escalations.json` — escalation events for Lessons extraction
- `swarm-report.md` — final completion report

### Escalation Protocol

Escalate to the user (via `AskUserQuestion`) when: the architect flags a decision point; a
reviewer finds a security issue that might require business logic changes; a component fails
3 iterations; the Verifier fails after 2 fix attempts; a git conflict blocks the pipeline.
After escalation, wait for user input before proceeding.

### Context Bundle Template

```json
{
  "project": "<project name>",
  "task": "<original task description>",
  "branch": "<current git branch>",
  "run_dir": "{memory_dir}/swarm/{run-id}",
  "key_files": ["<files identified by architect as central>"],
  "tool_guard": "Use Read/Write/Edit/Glob/Grep/Bash for file ops. No raw shell for file reads."
}
```

---

## When to Skip Phases

| Phase / Agent | Skip When |
|---------------|-----------|
| Phase 2.5: Security Design Review | Config-only, docs-only, or test-only changes that don't touch auth/data/network/API surfaces. Clear-domain tasks without auth/data/network/API involvement. |
| Phase 2.7: Speculative Fork | Architect did NOT flag `speculative_fork_recommended: true` AND Lead does not identify 2+ incompatible approach choices. Skip for single-component tasks and tasks where the user specified an approach. |
| Phase 2.8: Reduction Review | Config-only, docs-only, or test-only changes. Tasks where architect plan modifies fewer than 3 files total. |
| Test-Writer | `--skip-tests` flag provided, or changes are purely config/docs with no logic |
| Domain Reviewers (UI/API/DB) | Not auto-detected from codebase analysis |
| Phase 3.5: BDD Step Writing | No `## Test Plan` section in plan file, OR `## Test Plan` has no `**Feature Files:**` path, OR test plan path validation failed (test plan or Feature Files path escapes `{memory_dir}/test-plans/`). |
| Phase 5: Fix | ALL Phase 4 AND Phase 4.5 review agents report zero findings |
| Test Coverage Agent | No Phase 4 or 4.5 reviewer identified any test coverage gaps |
| Code-Simplifier | Neither Fixer nor Test Coverage Agent made any changes in Phase 5 |
| Phase 5.5: Plan Reconciliation | No incremental plan file found in `{memory_dir}/plans/` matching the feature branch. Also skipped at non-final PR boundary stops during incremental workflow (runs only at final completion). |
| Phase 6: Docs | Purely internal refactor with no public API or documented behavior changes |
| /unfuck sweep | Fewer than 20 files modified and not an architectural change |
| NEVER SKIP | Phases 0, 1, 2, core Phase 3 (Implementer + Reviewer), Phase 4, Phase 4.5, Phase 7 (Verifier) |

---

## Scope Matching

Match the tool to the task scope:

| Scenario | Recommended Approach |
|----------|----------------------|
| Simple bug fix (1-3 files) | Single targeted subagent |
| Medium feature (4-10 files) | 2-3 targeted subagents in sequence |
| Large feature or architectural change | `/swarm` |
| Codebase-wide cleanup | `/unfuck` |
| Security audit only | `code-quality:security` directly |
| Test coverage only | `code-quality:test-runner` directly |
| Codebase-wide analysis or bulk transformation (20+ files) | `/map-reduce` |
| Multiple viable approaches, need to compare | `/speculative` |
| Architectural analysis (cross-cutting concerns) | Single agent (NOT /map-reduce) |

### Model Assignment

Use opus for any task requiring judgment, evaluation, or nuanced reasoning. Use sonnet for
implementation and mechanical tasks. Use haiku for execution-only tasks. When in doubt,
prefer opus — one strong pass beats multiple weaker passes.

| Model | Used For |
|-------|---------|
| opus | Architect, Reduction Analyst, Reviewer, Security, QA, Structural Analysts, **Plan Adherence** — judgment-heavy tasks |
| sonnet | Implementer, Test-Writer, Test Coverage Agent, Code-Reviewer, Performance, Fixer, Code-Simplifier, Boundary Updater, Plan File Updater, Docs, Docs Reviewer, Lessons Extractor |
| haiku | Test-Runner, Verifier — execution-only tasks |

### Work Completion Principle

Never defer, skip, or reduce the scope of work to save tokens or reduce agent count.
If the task requires an agent, spawn the agent. If a finding needs fixing, fix it.
If tests need writing, write them. The only valid reasons to skip work are:
(1) the user explicitly opted out, (2) the skip condition in the phase table applies,
or (3) the work is genuinely out of scope for the current task.

"It would be expensive" is NEVER a valid reason to skip work. Prefer spawning one opus
agent that does the job right over multiple sonnet agents that require rework.

---

## References

| File | Content |
|------|---------|
| `references/orchestration-playbook.md` | Complete phase-by-phase coordination guide, error handling, rollback procedures, TeamCreate config, and git workflow |
| `references/agent-prompts.md` | Full prompt templates for all 21+ agents — role, boundaries, communication protocol, output format |
| `references/communication-schema.md` | All JSON schemas for inter-agent communication, pipeline handoffs, review findings, and audit trail formats |
| `references/pipeline-model.md` | Pipeline coordination details — component decomposition, execution modes, backpressure handling, team lifecycle |
| `references/cynefin-reference.md` | Cynefin domain classification — five domains, decision tree, domain-to-phase mapping, misclassification traps |
