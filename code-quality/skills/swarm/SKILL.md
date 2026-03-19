---
name: swarm
description: >-
  Full TeamCreate agent swarm for implementation tasks. Launches a pipelined team
  of 13+ specialized agents (Architect, Implementer, Reviewer, Test-Writer,
  Test-Runner, Security, QA, Code-Reviewer, Performance, Fixer, Code-Simplifier,
  Docs, Verifier) with structured JSON communication, audit trails, and early
  user checkpoint. Use when asked to "swarm this", "full team", "agent team",
  "full send", or when maximum rigor is needed on an implementation task.
  Auto-detects optional domain reviewers (UI, API, DB) from codebase analysis.
allowed-tools: [Read, Write, Edit, Glob, Grep, Task, Bash, AskUserQuestion, TeamCreate, TeamDelete,
  SendMessage, TaskCreate, TaskUpdate, TaskList, TaskGet, Skill]
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
| 3 | Implementer | general-purpose | sonnet | Yes | Write code, component by component |
| 3 | Reviewer | general-purpose | opus | No | Review each component before testing |
| 3 | Test-Writer | general-purpose | sonnet | Yes | Write tests for reviewed components |
| 3 | Test-Runner | dev-essentials:test-runner | haiku | No | Execute tests, report results |
| 4 | Security | code-quality:security | opus | No | OWASP, auth, secrets, injection review |
| 4 | QA | code-quality:qa | opus | No | Patterns, conventions, code quality |
| 4 | Code-Reviewer | superpowers:code-reviewer | sonnet | No | Broader review, complements QA |
| 4 | Performance | code-quality:performance | sonnet | No | Bottlenecks, N+1, memory issues |
| 5 | Fixer | general-purpose | sonnet | Yes | Address critical/high review findings |
| 5 | Code-Simplifier | code-simplifier:code-simplifier | sonnet | Yes | Post-fix simplification pass |
| 6 | Docs | general-purpose | haiku | Yes | Update repo docs and hack/ memory |
| 6 | Lessons Extractor | general-purpose | sonnet | Yes | Extract principle-level lessons from swarm run |
| 7 | Verifier | dev-essentials:test-runner | haiku | No | Final test suite + lint verification |

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
data model changes, and API surface changes. After receiving the plan, the Lead MUST:

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

### Phase 3: Pipelined Implementation

Spawn the full pipeline team at once: Implementer, Reviewer, Test-Writer, and Test-Runner. The
Lead routes work through the pipeline using structured JSON messages — components flow from
Implementer to Reviewer to Test-Writer to Test-Runner, with the Implementer moving to the next
component while earlier ones advance through the pipeline. Each handoff uses a typed JSON message
(see `references/communication-schema.md`). If the Reviewer rejects a component, the Lead routes
specific feedback back to the Implementer for targeted fixes and re-submission (max 3 iterations
per component). If Test-Runner reports failures, the Lead routes the failure details back to the
Implementer for fixes, then re-submits through Review and Test stages. See
`references/pipeline-model.md` for full parallelism rules, backpressure handling, and fallback
to sequential mode.

### Phase 4: Parallel Review

Spawn ALL review agents simultaneously: Security, QA, Code-Reviewer, Performance, and any
auto-detected optional reviewers (UI, API, DB). All reviewers operate in read-only mode on the
completed implementation. Each writes structured JSON findings to `hack/swarm/YYYY-MM-DD/reviews/`
(see schema in `references/communication-schema.md`). The Lead collects all findings, filters by
severity, and synthesizes into a consolidated view. Critical and high-severity findings go to the
Fixer in Phase 5. Low/informational findings are recorded in the audit trail but not acted on.

**Escalation Routing (before proceeding to Phase 5):**

After synthesizing findings, classify each critical/high finding by type and route accordingly:

| Finding Type | Examples | Routing |
|---|---|---|
| Design-level | Architecture mismatch, wrong abstraction, missing component entirely | Route back to Architect — respawn Phase 2, then re-run Phase 2.5, then re-implement Phase 3 |
| Security design | Trust boundary violation in architecture, missing auth layer, new attack surface from design decision | Route to Phase 2.5 Security Design Review for design-level fix |
| Scope creep | Feature implemented that was not in the plan, undiscussed behavior introduced | Escalate to human via AskUserQuestion — do not fix silently |
| Implementation | Bugs, quality issues, code-level security vulnerabilities, performance bottlenecks | Route to Phase 5 Fixer (existing flow) |

**Escalation counter:** Track a `design_escalation_count` across the swarm run. Each time findings
trigger a return to Phase 2 (design-level) or Phase 2.5 (security design), increment the counter.
Maximum 2 total design/security escalations per swarm run — if this cap is reached with unresolved
findings, escalate to the human via AskUserQuestion rather than re-running again. This caps
Phase 3 re-implementations at 2 regardless of escalation type.

All escalation events are recorded in `{run_dir}/escalations.json`
(see schema in `references/orchestration-playbook.md` Step 4.5).

### Phase 5: Fix & Simplify (conditional)

Skip this phase if ALL reviews report clean (zero critical or high findings). Otherwise, spawn
the Fixer with the consolidated critical/high findings and full context of the implementation.
The Fixer addresses each finding with targeted, minimal changes. After the Fixer completes,
check its output for `deferred` items — findings it couldn't resolve. For each deferred item:
1. Create a `TaskCreate` entry marked as blocked with the reason (visible in task list throughout)
2. Add to the "Scope Accountability" section of `swarm-report.md` (permanent record)
3. If any deferred item is critical or high severity, use `AskUserQuestion` to notify the user
   before proceeding — do NOT silently continue past critical unresolved findings

Then spawn the Code-Simplifier
for a post-fix pass — it looks for over-engineering, unnecessary abstractions, and complexity
introduced during implementation or fixing. Skip Code-Simplifier if the Fixer made no changes.
Re-run affected tests after any fixes to confirm nothing regressed.

### Phase 6: Docs & Memory

Spawn the Docs agent with the full list of modified files and a summary of what changed. The Docs
agent updates affected README files, API documentation, and inline docs. It also detects the
project's memory directory (`hack/`, `.local/`, `scratch/`, `.dev/`) and updates PROJECT.md with
architectural decisions, TODO.md with completed and new items, and SESSIONS.md with a 3-5 bullet
summary. Only update documentation that is actually affected by the implementation.

After the Docs agent completes, spawn a separate **Lessons Extractor** agent (sonnet model). This
agent scans the swarm run's audit trail and extracts principle-level lessons to `hack/LESSONS.md`
(creating the file if it does not exist). It reads:
- `{run_dir}/architect-plan.json` — Cynefin domain, questions raised, risks flagged
- `{run_dir}/reviews/` — recurring finding patterns across reviewers
- `{run_dir}/escalations.json` — escalation events (if the file exists)
- `{run_dir}/fix-summary.json` — what the Fixer had to address (if the file exists)
- Human checkpoint feedback from Phase 1 and Phase 2 (logged in `.swarm-run`)

Lessons are principle-level only (no file paths, no implementation details). Each lesson uses the
format from `dev-essentials/skills/incremental-planning/references/lessons-template.md`:
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
Phase 5: Fix & Simplify (if findings exist)
  +-- Fixer: critical/high findings
  +-- Code-Simplifier: post-fix pass
     |
     v
Phase 6: Docs & Memory
  +-- Docs agent: repo docs + hack/ updates
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
| Test-Writer | `--skip-tests` flag provided, or changes are purely config/docs with no logic |
| Domain Reviewers (UI/API/DB) | Not auto-detected from codebase analysis |
| Phase 5: Fix | ALL review agents report zero critical or high findings |
| Code-Simplifier | Fixer made no changes in Phase 5 |
| Phase 6: Docs | Purely internal refactor with no public API or documented behavior changes |
| /unfuck sweep | Fewer than 20 files modified and not an architectural change |
| NEVER SKIP | Phases 0, 1, 2, core Phase 3 (Implementer + Reviewer), Phase 7 (Verifier) |

---

## Cost Awareness

This skill spawns 13+ agents, each with their own context and API calls. Use it only when the
task genuinely warrants maximum rigor. For smaller tasks, use a targeted subagent or invoke
specific skills directly.

### When to Use /swarm vs. Alternatives

| Scenario | Recommended Approach |
|----------|----------------------|
| Simple bug fix (1-3 files) | Single targeted subagent |
| Medium feature (4-10 files) | 2-3 targeted subagents in sequence |
| Large feature or architectural change | `/swarm` |
| Codebase-wide cleanup | `/unfuck` |
| Security audit only | `code-quality:security` directly |
| Test coverage only | `dev-essentials:test-runner` directly |

### Model Cost Hierarchy

| Model | Cost | Used For |
|-------|------|---------|
| opus | Expensive | Architect, Reviewer, Security, QA — judgment-heavy tasks |
| sonnet | Moderate | Implementer, Test-Writer, Code-Reviewer, Performance, Fixer, Code-Simplifier, Lessons Extractor |
| haiku | Cheap | Test-Runner, Docs, Verifier — execution-only tasks |

Minimize opus usage to the phases where nuanced judgment is genuinely required.

---

## References

| File | Content |
|------|---------|
| `references/orchestration-playbook.md` | Complete phase-by-phase coordination guide, error handling, rollback procedures, TeamCreate config, and git workflow |
| `references/agent-prompts.md` | Full prompt templates for all 13+ agents — role, boundaries, communication protocol, output format |
| `references/communication-schema.md` | All JSON schemas for inter-agent communication, pipeline handoffs, review findings, and audit trail formats |
| `references/pipeline-model.md` | Pipeline coordination details — component decomposition, execution modes, backpressure handling, team lifecycle |
| `references/cynefin-reference.md` | Cynefin domain classification — five domains, decision tree, domain-to-phase mapping, misclassification traps |
