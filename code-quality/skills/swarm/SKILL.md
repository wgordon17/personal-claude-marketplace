---
name: swarm
description: >-
  Full agent swarm for implementation tasks. Launches a pipelined team
  of 21+ specialized agents (Architect, Security Design Reviewer, Reduction Analyst,
  Implementer, Reviewer, Test-Writer, Test-Runner, Boundary Updater, Security, QA,
  Code-Reviewer, Performance, Plan Adherence, Fixer, Test Coverage Agent,
  Code-Simplifier, Docs, Docs Reviewer, Lessons Extractor, Verifier, BDD-Step-Writer) with structured JSON
  communication, Cynefin domain classification, audit trails, and early user
  checkpoint. Use when asked to "swarm this", "full team", "agent team",
  "full send", or when maximum rigor is needed on an implementation task.
  Auto-detects optional domain reviewers (UI, API, DB) from codebase analysis.
compatibility: Requires Claude Code with CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 for multi-agent teammate messaging
allowed-tools: Read Write Edit Bash Agent AskUserQuestion SendMessage TaskCreate TaskUpdate TaskList TaskGet CronCreate CronDelete Skill
---

# /swarm — Full Agent Swarm Implementation

> **Note:** `{skill_dir}` refers to the absolute "Skill directory" path provided in your system instructions.

You MUST use the full swarm described here. Do not take shortcuts. Do not implement
the task yourself. Your role is orchestration — you route work, relay context, make judgment
calls, and coordinate the pipeline. Every phase of implementation goes through the appropriate
specialist agent.

**Prerequisite:** Agent teams require `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` to be set in the
environment or `settings.json` — without it, named teammates spawned via `Agent(name=...)` do
not join a shared implicit team and `SendMessage` routing between them will not work.

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

Run the project test suite and record the baseline. Check git status, ensure a clean working tree, and create a feature branch from `upstream/main` or `origin/main` if needed. Verify auto-compaction is enabled. Generate a run ID per `[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)` and create `{memory_dir}/swarm/{run-id}/`. Create all tasks upfront with `addBlockedBy` dependencies; the implicit team is established by the first named teammate spawn in Phase 2. See `[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` (Phase 0).

**Test Plan Discovery:** Search `{memory_dir}/plans/` (excluding `plans/done/`) by `**Branch:**` header match (fallback: branch slug filename match). If found with `## Test Plan`, normalize `**Test Plan:**` and `**Feature Files:**` paths, verify both stay within `{memory_dir}/test-plans/`, and check `test -f && test ! -L` before reading `{TEST_PLAN}` or copying. Record any `**BDD Setup Needed:** yes` install command and save the plan path for Phases 4 and 5.5.

**Tracker Extraction & Validation:** Extract `{tracker}` from `**Tracker:**` per `[{skill_dir}/../../references/tracker-field-spec.md](../../references/tracker-field-spec.md)` (default `none`). Verify `{tracker}` is terminal (`github:owner/repo#N`, `jira:PROJ-N`, or `none`); if non-terminal (`github:pending`, `github:linked#N`, `jira:pending`), halt via `AskUserQuestion` until resolved.

**Workflow detection:** Extract `**Workflow:**` from the plan header. If `incremental`, extract `**PR Boundaries:**` and `**PRs:**`, set `{workflow_mode}` = `incremental` and `{pr_boundaries}` = parsed mapping, and follow `[{skill_dir}/references/incremental-workflow.md](./references/incremental-workflow.md)` for checkpoint discovery, initial branch naming (`feat/{plan-slug}-pr1`), resume, and `--fresh`. If `fast` or absent, use fire-and-forget behavior.

### Phase 1: Clarify & Checkpoint (EARLY — fire-and-forget after approval)

Use `AskUserQuestion` to resolve ambiguities and approve the swarm composition (core agents + auto-detected optional reviewers + Phase 3.5 BDD-Step-Writer if `**Feature Files:**` is present). After Phase 1 approval, the swarm runs autonomously except for architect blockers, max-retry escalations, or `needs-input` triage.
- **jira:jira-agent** (conditional): Spawned at Phase 7 when `{tracker}` is `jira:PROJ-N` (see `[{skill_dir}/../../../jira/agents/jira-agent.md](../../../jira/agents/jira-agent.md)`).

### Phase 2: Architect (opus)

Spawn `code-quality:architect` (opus) to classify the task using the Cynefin framework (`[{skill_dir}/references/cynefin-reference.md](./references/cynefin-reference.md)`) and write `{run_dir}/architect-plan.json` (`[{skill_dir}/references/communication-schema.md](./references/communication-schema.md)`), including `components`, `risks`, `questions`, and `documentation_impact`.

After receiving `architect-plan.json`, the Lead MUST:
1. Present any `questions` and high-impact `risks` via `AskUserQuestion`
2. Verify all requested scope is covered and validate warm-bundle fields (`[{skill_dir}/references/warm-bundle-handoff.md](./references/warm-bundle-handoff.md)`)
3. Use `cynefin_domain` to inform Phase 2.5 skip decisions and pipeline mode
4. Escalate unconfirmed third-party library/API choices to `/deep-research` (External mode via `Skill`)

Keep the Architect active through Phase 3 for clarifications.

### Phase 2.5: Security Design Review (conditional)

**Skip when:** Config/docs/test-only or Clear-domain tasks not touching auth, data, network, or API surfaces.
Spawn `code-quality:security` (opus) for a STRIDE review of `architect-plan.json` → `{run_dir}/security-design-review.json` (`[{skill_dir}/references/communication-schema.md](./references/communication-schema.md)`). Route `revise` (`needs-fix`) back to Architect (max 2 iterations, then `AskUserQuestion`); on `proceed`, append `security_constraints` to `architect-plan.json`.

### Phase 2.7: Speculative Fork (conditional)

**Skip unless:** `speculative_fork_recommended: true` in `architect-plan.json`, OR 2+ incompatible design choices exist (proactively recommend for Complex-domain tasks). Never trigger for Clear-domain or single-component tasks without trade-offs. Ask via `AskUserQuestion` whether to run `/speculative` or pick directly; if `/speculative`, fork contested components in worktrees (with a 60s `CronCreate` watchdog torn down via `CronDelete` on all paths), judge with opus, and write the winner into `architect-plan.json` (`[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` Step 2.7).

### Phase 2.8: Pre-Implementation Simplification Review

**Skip when:** Config/docs/test-only changes, or architect plan modifies fewer than 3 files total.
Spawn **Reduction Analyst** (`general-purpose`, opus, read-only) to check for removable legacy code, premature abstractions, library alternatives (`[{skill_dir}/../../references/dependency-evaluation.md](../../references/dependency-evaluation.md)`), and net complexity impact → `{run_dir}/reduction-review.json`. Integrate accepted reductions into `architect-plan.json`.

### Phase 3: Pipelined Implementation

Create a 60s `CronCreate` watchdog, then spawn Implementer (sonnet), Reviewer (opus), Test-Writer (sonnet), and Test-Runner (haiku). Route components through `Implementer → Reviewer → Test-Writer → Test-Runner` via typed JSON messages and `ContextAcknowledgment` (`[{skill_dir}/references/communication-schema.md](./references/communication-schema.md)`). Route Reviewer rejections (max 3 iterations) and Test-Runner failures (max 2 cycles) back to Implementer. Tear down the watchdog (`CronDelete`) when Phase 3 ends or aborts. See `[{skill_dir}/references/pipeline-model.md](./references/pipeline-model.md)` for parallelism, backpressure, and mini-pipeline fan-out (`components.count > 3` and `independent_groups.count > 1`).

**PR boundary handling (incremental only):** See `[{skill_dir}/references/incremental-workflow.md](./references/incremental-workflow.md)` for the 9-step boundary stop procedure (scoped Phase 4/4.5 reviews, scoped Phase 5 Fixer, Verifier baseline check, Boundary Updater `checkpoint.json` + plan `**PRs:**` / `**Branch:**` updates, draft PR creation, issue comment, and shutdown).
- **PR framing rules:** Never number PRs ("Part 1 of 3"), reference other PRs in the series, or describe a PR as part of a larger whole. Each PR must stand on its own.

### Phase 3.5: BDD Step Writing (conditional)

**Skip when:** No `## Test Plan` section, no `**Feature Files:**` path, or `{TEST_PLAN}` is empty.
When triggered after Phase 3, follow `[{skill_dir}/references/bdd-pipeline.md](./references/bdd-pipeline.md)` (Steps 3.5.1–3.5.5) to resolve `{feature_target_dir}`, promote regular `.feature` files, install BDD dependencies if `**BDD Setup Needed:** yes`, spawn BDD-Step-Writer with `{bdd_framework_info}` and `{TEST_PLAN}`, and collect `BDDStepHandoff` for Phase 7.

### Phase 4: Parallel Review

Shut down Phase 3 agents and the Architect, then spawn ALL review agents simultaneously in read-only mode: Security (opus), QA (opus), Code-Reviewer (sonnet), Performance (sonnet), auto-detected domain reviewers, and `code-quality:plan-adherence` (opus, if an incremental plan file exists). Each writes `ReviewFindings` JSON to `{run_dir}/reviews/` (`[{skill_dir}/references/communication-schema.md](./references/communication-schema.md)`).

**Escalation Routing (before Phase 5):** Route findings per `[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` Step 4.5:
- **Design-level** → Phase 2 Architect | **Security design** → Phase 2.5 (capped at `design_escalation_count < 2` across Phases 4/4.5, logged to `{run_dir}/escalations.json`; escalate via `AskUserQuestion` at cap)
- **Scope creep** → `AskUserQuestion` | **Implementation / Plan drift** → Phase 5 Fixer | **Test coverage** → Phase 5 Test Coverage Agent | **Documentation** → Phase 6 Docs
- **Incremental workflow scoping:** When `{workflow_mode}` is `incremental`, scope to `pr_boundary_files` (`[{skill_dir}/references/incremental-workflow.md](./references/incremental-workflow.md)`).

### Phase 4.5: Structural Design Review

Mandatory adversarial system review after Phase 4 routing. Spawn 2 opus `general-purpose` analysts in parallel:
- **Analyst 1: Concurrency & State** (`{run_dir}/reviews/structural-concurrency.json`)
- **Analyst 2: Integration & Contract** (`{run_dir}/reviews/structural-integration.json`)

Findings use `STRUCT` prefix (`STRUCT-001`), share `design_escalation_count`, and merge into Phase 5. Scope incremental non-final boundaries per `[{skill_dir}/references/incremental-workflow.md](./references/incremental-workflow.md)`.

### Phase 5: Fix, Test Coverage & Simplify

Skip only if Phases 4 and 4.5 report zero findings. Otherwise run sequentially (`[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` Phase 5):
1. **Fixer (sonnet):** Processes all findings in file order per `[{skill_dir}/../../references/finding-classification.md](../../references/finding-classification.md)` Fixer Protocol. Lead triages `needs_input_items` via `AskUserQuestion`, verifies `total_findings_in == findings_fixed + user_deferred`, and records `user_deferred` via `TaskCreate` and `swarm-report.md`.
2. **Test Coverage Agent (sonnet, conditional):** Writes tests for Phase 4/4.5 coverage gaps (`{run_dir}/test-coverage-result.json`).
3. **Code-Simplifier (sonnet, conditional):** Simplifies modified files and re-runs affected tests.

### Phase 5.5: Plan Reconciliation

Skip if no incremental plan file exists (or at non-final PR boundaries). Cross-reference plan tasks against `git diff origin/main..HEAD`, escalate unaddressed tasks via `AskUserQuestion`, and spawn **Plan File Updater** (sonnet) to mark `[x]`, `[SKIPPED by user]`, or `[BLOCKED: reason]` without modifying `## Test Plan`. Reconcile incremental runs across all boundaries per `[{skill_dir}/references/incremental-workflow.md](./references/incremental-workflow.md)`.

### Phase 6: Docs & Memory

Spawn Docs (sonnet), then Docs Reviewer (sonnet, read-only), then Lessons Extractor (sonnet) per `[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` Phase 6:
- **Docs (3 passes):** Pass 1 (`documentation_impact`), Pass 2 (discovery-based completeness), Pass 3 (changed-file docs), plus memory updates (`PROJECT.md`, `TODO.md`, `SESSIONS.md` per `[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)`).
- **Docs Reviewer (5 checks):** Impact coverage, accuracy vs implementation diff, consistency, completeness, and plan drift → `{run_dir}/reviews/docs-review.json` (`DOC-R` prefix; triage `needs-input` per `[{skill_dir}/../../references/finding-classification.md](../../references/finding-classification.md)`).
- **Lessons Extractor:** Extracts principle-level lessons from audit trail to `{memory_dir}/LESSONS.md`.

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
1. Validate format before interpolation per `[{skill_dir}/../../references/tracker-field-spec.md](../../references/tracker-field-spec.md)`
   Validation Regex section. Skip if invalid.
2. Add the `in-progress` label to the linked GH issue (best-effort — if the command
   fails, log a warning in the completion announcement and continue):
   `gh label create in-progress --repo <owner/repo> --description 'Work actively underway' --color 'fbca04' 2>/dev/null || true`
   `gh issue edit N --repo <owner/repo> --add-label 'in-progress'`
   (auto-create the label first per `[{skill_dir}/../../references/github-label-definitions.md](../../references/github-label-definitions.md)`,
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
Shut down each active teammate individually via `SendMessage(to="<name>", message={"type":
"shutdown_request", "reason": "Swarm complete"})`.

---

## Orchestration, Lead Responsibilities & Scope Matching

See `[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` for the full ASCII Orchestration Flow Diagram, Lead Responsibilities (Task Graph, Context Relay, Judgment Calls, Pipeline Coordination, Watchdog & Context Health Monitoring, Feedback Loops, Audit Trail, Escalation Protocol, and Context Bundle Template), When to Skip Phases table, and Scope Matching / Model Assignment / Work Completion Principle guidelines.

---

## References

| File | Content |
|------|---------|
| `[{skill_dir}/references/orchestration-playbook.md](./references/orchestration-playbook.md)` | Complete phase-by-phase coordination guide, orchestration flow diagram, lead responsibilities, skip conditions, scope matching, error handling, and agent roster |
| `[{skill_dir}/references/incremental-workflow.md](./references/incremental-workflow.md)` | Incremental workflow PR-boundary handling, checkpoint discovery and resume, scoped review rules, and PR tracking reconciliation |
| `[{skill_dir}/references/bdd-pipeline.md](./references/bdd-pipeline.md)` | Phase 3.5 BDD step writing pipeline — `.feature` promotion, dependency setup, `bdd_framework_info` schema, and `BDDStepHandoff` collection |
| `[{skill_dir}/references/agent-prompts.md](./references/agent-prompts.md)` | Full prompt templates for all 21+ agents — role, boundaries, communication protocol, output format |
| `[{skill_dir}/references/communication-schema.md](./references/communication-schema.md)` | All JSON schemas for inter-agent communication, pipeline handoffs, review findings, and audit trail formats |
| `[{skill_dir}/references/pipeline-model.md](./references/pipeline-model.md)` | Pipeline coordination details — component decomposition, execution modes, backpressure handling, team lifecycle |
| `[{skill_dir}/references/cynefin-reference.md](./references/cynefin-reference.md)` | Cynefin domain classification — five domains, decision tree, domain-to-phase mapping, misclassification traps |
| `[{skill_dir}/references/warm-bundle-handoff.md](./references/warm-bundle-handoff.md)` | Warm-bundle handoff — the four optional per-component fields (files_examined, ruled_out, change_sites, proposed_first_change), the consumer contract, KV-cache/verification caveats, and the anti-injection note |
