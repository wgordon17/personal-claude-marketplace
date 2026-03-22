---
name: swarm
description: >-
  Full TeamCreate agent swarm for implementation tasks. Launches a pipelined team
  of 18+ specialized agents (Architect, Security Design Reviewer, Implementer,
  Reviewer, Test-Writer, Test-Runner, Security, QA, Code-Reviewer, Performance,
  Fixer, Test Coverage Agent, Code-Simplifier, Docs, Lessons Extractor, Verifier) with structured JSON
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
| 3 | Implementer | general-purpose | sonnet | Yes | Write code, component by component |
| 3 | Reviewer | general-purpose | opus | No | Review each component before testing |
| 3 | Test-Writer | general-purpose | sonnet | Yes | Write tests for reviewed components |
| 3 | Test-Runner | code-quality:test-runner | haiku | No | Execute tests, report results |
| 4 | Security | code-quality:security | opus | No | OWASP, auth, secrets, injection review |
| 4 | QA | code-quality:qa | opus | No | Patterns, conventions, code quality |
| 4 | Code-Reviewer | code-quality:code-reviewer | sonnet | No | Broader review, complements QA |
| 4 | Performance | code-quality:performance | sonnet | No | Bottlenecks, N+1, memory issues |
| 4.5 | Structural Analyst (×2) | general-purpose | opus | No | Adversarial structural design review |
| 5 | Fixer | general-purpose | sonnet | Yes | Address ALL review findings |
| 5 | Test Coverage Agent | general-purpose | sonnet | Yes | Write tests for coverage gaps from Phase 4 |
| 5 | Code-Simplifier | code-quality:code-simplifier | sonnet | Yes | Post-fix simplification pass |
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

---

## Workflow Phases

### Phase 0: Pre-flight & Setup

Run the project test suite and record the baseline (pass/fail count, any pre-existing failures).
Check git status — ensure the working tree is clean, identify the current branch, and determine
whether a feature branch is needed. If not already on a feature branch, create one from
`upstream/main` or `origin/main`. Verify that auto-compaction is enabled — the /swarm skill
depends on it for reliable agent operation (warn the user if disabled). Create the audit trail
directory at `hack/swarm/YYYY-MM-DD/` (append sequence number if the directory already exists).
Call `TeamCreate("swarm-impl")`, then create all tasks upfront with `addBlockedBy` dependencies
so the full task graph is visible from the start.

### Phase 1: Clarify & Checkpoint (EARLY — fire-and-forget after approval)

Use `AskUserQuestion` to resolve any ambiguity in the task before spawning agents. Auto-detect
optional reviewers: Glob for UI files (`*.tsx`, `*.vue`, `*.svelte`, `*.html`, `*.css`); Grep
for API patterns (`router`, `endpoint`, `handler`, `@api`); Grep for DB patterns (`migration`,
`schema`, `model`, `query`). Present the proposed swarm composition to the user — list all core
agents plus any auto-detected optional reviewers. Allow the user to add or remove reviewers.
After the user approves the composition and confirms they understand the scope, proceed without
further checkpoints. The user can walk away after Phase 1 approval.

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

Output is a structured JSON plan written to `hack/swarm/YYYY-MM-DD/architect-plan.json`
(see schema in `references/communication-schema.md`). The architect also identifies global risks,
data model changes, API surface changes, and **documentation impact** — a `documentation_impact`
array listing which documentation surfaces are affected and why (READMEs, manifests, registries,
component tables, descriptions, dependency matrices, user-facing docs). This array feeds Phase 6.
After receiving the plan, the Lead MUST:

1. Read `architect-plan.json` and check the `questions` array
2. If `questions` is non-empty, present EVERY question to the user via `AskUserQuestion`
3. Verify scope: compare the plan's components against the original task — if any requested
   work is missing from the plan, add it back or ask the user via `AskUserQuestion`
4. Raise any high-severity risks to the user before proceeding
5. Note the `cynefin_domain` — use it to inform Phase 2.5 skip decision and pipeline mode

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
- Critical/High findings → Route back to Architect (Phase 2) with security feedback for
  redesign. Architect revises plan, re-run Phase 2.5. Maximum 2 Architect↔Security iterations.
- Medium/Low/None → Append security constraints to architect-plan.json as
  `security_constraints` array and proceed to Phase 3.
- If 2 iterations exhausted with unresolved Critical findings → Escalate to human via
  AskUserQuestion.

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

### Phase 4: Parallel Review

Spawn ALL review agents simultaneously: Security, QA, Code-Reviewer, Performance, and any
auto-detected optional reviewers (UI, API, DB). All reviewers operate in read-only mode on the
completed implementation. Each writes structured JSON findings to `hack/swarm/YYYY-MM-DD/reviews/`
(see schema in `references/communication-schema.md`). The Lead collects ALL findings and
synthesizes into a consolidated view. Every finding — regardless of severity — is routed to
Phase 5 for action. No finding is silently dropped or left unactioned in the audit trail.

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

**Escalation counter:** Track a `design_escalation_count` across the swarm run. Each time findings
trigger a return to Phase 2 (design-level) or Phase 2.5 (security design), increment the counter.
Maximum 2 total design/security escalations per swarm run — if this cap is reached with unresolved
findings, escalate to the human via AskUserQuestion rather than re-running again. This caps
Phase 3 re-implementations at 2 regardless of escalation type.

All escalation events are recorded in `{run_dir}/escalations.json`
(see schema in `references/communication-schema.md` under "Escalation Events Schema").

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
- Critical/High STRUCT findings follow the Phase 4 escalation routing rules
- STRUCT escalations count toward the cumulative `design_escalation_count` cap (max 2 total
  re-implementations before human escalation)
- STRUCT escalation events are logged to `{run_dir}/escalations.json`

If Phase 4 escalation routing triggers a return to Phase 2, Phase 4.5 runs on the re-implemented version after the next Phase 4 completes — not on the current (superseded) implementation.

Phase 5 receives findings from both Phase 4 AND Phase 4.5 in its consolidated findings list.

### Phase 5: Fix, Test Coverage & Simplify

Skip this phase only if ALL reviews (Phases 4 and 4.5) report zero findings of any severity.
Otherwise, spawn agents in this order:

**Step 5.1 — Fixer:** Spawn the Fixer with ALL consolidated findings (every severity level)
and full context of the implementation. The Fixer addresses each finding with targeted, minimal
changes — critical/high first, then medium, then low. After the Fixer completes, check its
output for `deferred` items — findings it couldn't resolve. For each deferred item:
1. Create a `TaskCreate` entry marked as blocked with the reason (visible in task list throughout)
2. Add to the "Scope Accountability" section of `swarm-report.md` (permanent record)
3. If any deferred item is critical or high severity, use `AskUserQuestion` to notify the user
   before proceeding — do NOT silently continue past critical unresolved findings

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

The Docs agent also detects the project's memory directory (`hack/`, `.local/`, `scratch/`,
`.dev/`) and updates PROJECT.md with architectural decisions, TODO.md with completed and new
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
schema with `DOC-R` prefix. Critical/High findings are routed back to the Docs agent for fixes
(max 1 iteration — the Docs Reviewer re-reviews after fixes). Low/Medium findings are recorded
in the audit trail.

After the Docs Reviewer completes (or confirms clean), spawn a separate **Lessons Extractor** agent (sonnet model). This
agent scans the swarm run's audit trail and extracts principle-level lessons to `hack/LESSONS.md`
(creating the file if it does not exist). It reads:
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
baseline — all tests that passed before must still pass; net-new failures are a blocker. After
Verifier reports green, invoke the `quality-gate` skill for automated multi-pass review with
rotating adversarial lenses, fresh-context subagent reviews, and blocking memory/artifact gates.
If there are 20 or more modified files, run an `/unfuck` sweep to
catch any issues introduced at scale. Generate the final audit report at
`hack/swarm/YYYY-MM-DD/swarm-report.md`. Announce completion with a summary and report path.
Shut down all teammates via `SendMessage(type="shutdown_request")` and call `TeamDelete`.

---

## Orchestration Flow Diagram

```
User request
     |
     v
Phase 0: Pre-flight
  +-- Baseline tests
  +-- Git branch check/create
  +-- Create hack/swarm/YYYY-MM-DD/
  +-- TeamCreate + TaskGraph
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
  +-- Critical/High findings --> back to Architect (max 2 iterations)
  +-- Medium/Low/None --> append security_constraints, proceed
  +-- Unresolved Critical after 2 iterations --> AskUserQuestion
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
Phase 3: Pipelined Implementation
  +-------------------------------------------------------+
  | Implementer --> Reviewer --> Test-Writer --> Test-Runner |
  |      |              |              |               |    |
  | (next comp)   (feedback)     (next comp)    (results)   |
  |      <-------- reject ----------------------------------  |
  +-------------------------------------------------------+
     |
     v
Phase 4: Parallel Review
  +-- Security (opus) --------------------------------+
  +-- QA (opus) --------------------------------------|
  +-- Code-Reviewer (sonnet) -------------------------+--> Lead synthesizes findings
  +-- Performance (sonnet) ---------------------------|
  +-- Optional: UI / API / DB reviewers -------------+
     |
     v
Phase 4.5: Structural Design Review (always runs)
  +-- Analyst 1: Concurrency & State (opus) ---------+
  +-- Analyst 2: Integration & Contract (opus) -------+--> Lead merges STRUCT findings
     |
     v
Phase 5: Fix, Test Coverage & Simplify (if any findings exist)
  +-- Fixer: ALL findings (critical → high → medium → low)
  +-- Test Coverage Agent: coverage gaps from Phase 4/4.5
  +-- Code-Simplifier: post-fix pass
     |
     v
Phase 6: Docs & Memory
  +-- Docs agent: repo docs + hack/ updates
  +-- Docs Reviewer: verify Docs agent's work
  +-- Lessons Extractor: audit trail → hack/LESSONS.md
     |
     v
Phase 7: Verification & Completion
  +-- Verifier: full test + lint
  +-- quality-gate skill
  +-- /unfuck sweep (if 20+ files changed)
  +-- Generate swarm-report.md
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

Write all structured outputs to `hack/swarm/YYYY-MM-DD/`:
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
  "run_dir": "hack/swarm/YYYY-MM-DD",
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
| Test-Writer | `--skip-tests` flag provided, or changes are purely config/docs with no logic |
| Domain Reviewers (UI/API/DB) | Not auto-detected from codebase analysis |
| Phase 5: Fix | ALL Phase 4 AND Phase 4.5 review agents report zero findings of any severity |
| Test Coverage Agent | No Phase 4 or 4.5 reviewer identified any test coverage gaps |
| Code-Simplifier | Neither Fixer nor Test Coverage Agent made any changes in Phase 5 |
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
| opus | Architect, Reviewer, Security, QA, Structural Analysts — judgment-heavy tasks |
| sonnet | Implementer, Test-Writer, Test Coverage Agent, Code-Reviewer, Performance, Fixer, Code-Simplifier, Docs, Lessons Extractor |
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
| `references/agent-prompts.md` | Full prompt templates for all 15+ agents — role, boundaries, communication protocol, output format |
| `references/communication-schema.md` | All JSON schemas for inter-agent communication, pipeline handoffs, review findings, and audit trail formats |
| `references/pipeline-model.md` | Pipeline coordination details — component decomposition, execution modes, backpressure handling, team lifecycle |
| `references/cynefin-reference.md` | Cynefin domain classification — five domains, decision tree, domain-to-phase mapping, misclassification traps |
