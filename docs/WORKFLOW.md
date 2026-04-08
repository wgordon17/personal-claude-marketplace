# Recommended Skill Workflow

This guide describes the recommended sequence of skills for common development workflows in this marketplace. Skills are invoked as `/skill-name` in Claude Code.

---

## Primary Workflow

```
deep-research ──► incremental-planning ──► test-plan ──► plan-review ──► fix
                                                                          │
                  ┌───────────────────────────────────────────────────────┘
                  ▼
              roadmap (if multi-plan) ──► swarm ──► pr-review ──► fix
```

**Pre-merge:** `review-commits ──► push`

### Step Notes

- **deep-research** (skill) — Comprehensive investigation using background agents, web search, and documentation lookup. Use when requirements need validation, technology choices need comparison, or a feature area needs thorough exploration before planning. Produces a research report.

- **incremental-planning** (skill) — Replaces native plan mode. Interviews you with targeted questions, then writes a phased implementation plan to `hack/plans/`. Use before any multi-file implementation or when EnterPlanMode is denied by hook. Produces a plan file.

- **test-plan** (skill) — User-guided test plan and acceptance criteria definition. Defines what success looks like before implementation begins. Produces UAT scenarios and acceptance criteria for the plan.

- **plan-review** (skill) — Spawns 6 parallel specialized reviewers (feasibility, scope, dependencies, unknowns, correctness, plan adherence) to independently evaluate a plan file. Catches gaps before work starts. Produces a consolidated review with findings.

- **fix** (skill) — Comprehensive finding fixer. Reads findings from the current session (from `/pr-review`, `/plan-review`, `/bug-investigation`, or similar) and addresses them. Use after any review produces actionable findings.

- **roadmap** (skill) — Multi-plan phase sequencing and dependency management. Use when the plan produces multiple sub-plans that need coordination into parallel and sequential phases. Produces a roadmap file in `hack/plans/`.

- **swarm** (skill) — Full pipelined agent swarm for implementation tasks. Launches 21+ specialized agents (Architect, Security, Reduction Analyst, Implementer, Reviewer, Test-Writer, Test-Runner, QA, Performance, Code-Reviewer, and more) in a coordinated pipeline. Use for substantial implementation tasks defined by a plan file.

- **pr-review** (skill) — Spawns 6 parallel specialized reviewers (security, QA, performance, code quality, correctness, plan adherence) to review a pull request. Produces verified findings with false-positive filtering. Use before merging any PR.

- **review-commits** (command) — AI-assisted commit review for PR readiness. Checks commit message quality, conventional commit format, and whether commits are logically grouped. Use after completing all commits on a branch and before pushing.

- **push** (manual) — Standard `git push` to the remote. Run after `review-commits` confirms commits are clean.

---

## Quality Gate

`/quality-gate` runs automatically inside swarm Phase 7 and should also be run standalone after any significant work. The Stop hook in dev-guard is the safety net — it catches incomplete work before the session ends.

**quality-gate** (skill) — Validates that claimed work is actually complete. Checks for deferred tasks, unverified claims, missing tests, and documentation gaps. Use before declaring any deliverable done.

---

## Session Lifecycle

```
session-start ──► work ──► quality-gate ──► session-end
```

- **session-start** (command) — Loads project context from `hack/` files (NEXT.md, PROJECT.md, WORK_ETHIC.md, LESSONS.md) or initializes a new project. Run at the start of every session.

- **session-end** (command) — Syncs project memory and updates `hack/` files (NEXT.md, PROJECT.md, TODO.md, SESSIONS.md, LESSONS.md) before ending the session. Run before closing Claude Code.

---

## Side Workflows

### Bug Investigation

```
bug-investigation ──► fix
```

- **bug-investigation** (skill) — Interactive workflow for reporting bugs one-by-one while background agents investigate in parallel. Stores findings in `hack/BUGS.md` for cross-session persistence. Use when you have a list of bugs to triage and investigate.

### Codebase Cleanup

```
unfuck
```

- **unfuck** (skill) — Comprehensive one-shot repo cleanup swarm. Removes dead code, de-AI-slops the codebase, eliminates duplication, and simplifies over-engineered patterns. Standalone — does not require a prior plan.

### Competing Approaches

```
speculative ──► (winner feeds into) swarm
```

- **speculative** (skill) — Runs competing implementations in parallel in isolated worktrees, then judges and selects the best approach. Use when multiple viable approaches exist and comparison beats guessing. Integrated into swarm Phase 2.7.

### Codebase Analysis

```
file-audit ──► map-reduce
```

- **file-audit** (skill) — Deep code quality audit: unused code, duplicates, library misuse, anti-patterns. Analyzes files in parallel using LSP and Context7. Produces a structured findings report.

- **map-reduce** (skill) — Parallelized workload processing for codebase-wide analysis and bulk transformations. Use for large file sets (20+ files) where mapper agents process chunks and a reducer synthesizes results.
