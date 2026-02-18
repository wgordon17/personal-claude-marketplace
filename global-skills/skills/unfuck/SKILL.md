---
name: unfuck
description: >-
  Comprehensive one-shot repo cleanup skill. This skill should be used when the user asks to
  "clean up the repo", "remove dead code", "de-AI-slop", "unfuck this codebase",
  "comprehensive cleanup", "remove unused code", "simplify the codebase", "fix code quality",
  "clean everything up", "audit the codebase", "fix tech debt", "remove duplicates",
  "unify the architecture", "security review and fix", or wants a thorough, automated cleanup
  of their entire repository. Launches a full agent swarm to discover issues, plan fixes, and
  implement changes autonomously. Combines detailed custom analysis with existing skills
  (file-audit, security-review, sc:cleanup, sc:improve, docs-sync, sc:index-repo,
  superclaude:architect, superclaude:security, superclaude:qa, code-simplifier,
  project-dev:refactor, test-execution:test-runner) into a unified cleanup workflow.
allowed-tools: [Read, Write, Edit, Glob, Grep, Task, Bash, AskUserQuestion, TeamCreate, SendMessage, TaskCreate, TaskUpdate, TaskList, TaskGet, LSP, Skill]
---

# /unfuck — Comprehensive Repo Cleanup

One-shot command that launches a full agent swarm to discover, plan, and fix everything wrong
with a codebase: dead code, duplicates, security issues, AI slop, architectural drift,
complexity, and documentation rot.

## Quick Start

```
/unfuck                     # Full cleanup of entire repo
/unfuck src/                # Scope to a directory
/unfuck --dry-run           # Discovery + plan only, no implementation
```

## What It Does

Runs a 5-phase workflow combining 12+ existing skills/agents with detailed custom analysis
logic and optional external CLI tools. Phase 0 indexes the repo. Phase 1 spawns 7 parallel
discovery agents that scan every file. Phase 2 synthesizes findings into a prioritized cleanup
plan. Phase 3 spawns sequential implementation agents that fix each category and commit.
Phase 4 verifies everything passes and generates a report.

## Workflow Phases

### Phase 0: Index & Setup
1. Create TeamCreate swarm: `cleanup-swarm`
2. Spawn parallel setup teammates for: repo indexing (`sc:index-repo`), language detection, tool detection
3. Create feature branch: `cleanup/comprehensive-YYYY-MM-DD` (from `origin/main`)
4. Create `hack/unfuck/YYYY-MM-DD/discovery/` directory for agent output (date-scoped per run)
5. Collect setup results and build context bundle for discovery agents

### Phase 1: Discovery (7 parallel agents)

All agents run simultaneously in background. Each writes structured JSON findings to
`hack/unfuck/YYYY-MM-DD/discovery/`. Full prompts in `references/discovery-agents.md`.

| Agent | Role | Paired Skills | External Tools | Output |
|-------|------|---------------|----------------|--------|
| dead-code-hunter | Unused code, exports, imports, files, deps | `file-audit` | Knip, Vulture, deadcode | `dead-code.json` |
| duplicate-detector | Copy-paste code, near-duplicates, redundant wrappers | `file-audit` | jscpd | `duplicates.json` |
| security-auditor | OWASP Top 10, secrets, CVEs, auth gaps | `security-review`, `superclaude:security` | Semgrep, gitleaks, Bandit | `security.json` |
| architecture-reviewer | Circular deps, divergent patterns, god objects | `superclaude:architect`, `sc:analyze` | dependency-cruiser, Madge | `architecture.json` |
| ai-slop-detector | Over-abstraction, unnecessary wrappers, catch-rethrow, comment noise | (novel — see `references/ai-slop-checklist.md`) | — | `ai-slop.json` |
| complexity-auditor | Long functions, deep nesting, magic values, parameter bloat | `superclaude:qa` | radon | `complexity.json` |
| documentation-auditor | README drift, broken links, stale TODOs, missing docs | `docs-sync`, `file-audit` | — | `documentation.json` |

**Model note:** ai-slop-detector uses `opus` (detecting AI patterns requires stronger judgment).
All others use `sonnet`.

**Tool fallback:** When external tools are unavailable, agents perform equivalent analysis
manually using LSP, Grep, and file reading. See `references/external-tools.md` fallback table.

### Phase 2: Synthesis & Planning

A dedicated **opus synthesis teammate** (not the orchestrator) performs synthesis to avoid filling the lead agent's context:
1. Reads all 7 discovery JSON files
2. Deduplicates overlapping findings across agents
3. Cross-references compound patterns (dead + divergent = safe to remove)
4. Prioritizes: security → dead code → duplicates → AI slop → complexity → architecture → docs
5. Writes `hack/unfuck/YYYY-MM-DD/cleanup-plan.md` with per-finding detail
6. Creates TaskList with one task per category
7. Sends summary to orchestrator; orchestrator **AskUserQuestion** if: public API deletions,
   5+ file architectural changes, ambiguous findings, security policy decisions, or >100 total findings

### Phase 3: Implementation (persistent collaborative team)

A single persistent team of 4 specialists works through all categories sequentially together:

| Teammate | Role | Model | Responsibility |
|----------|------|-------|----------------|
| impl-writer | Implementer | sonnet | Applies fixes from cleanup plan per category |
| impl-qa | Reviewer | opus | Reviews changes for correctness, verifies with LSP |
| impl-tester | Tester | sonnet | Runs test suite and formatters after each category |
| impl-docs | Documenter | sonnet | Updates documentation affected by code changes |

**Workflow per category:** writer implements → QA reviews → tester verifies → docs updates → commit or rollback.
The orchestrator assigns categories in priority order (security → dead code → duplicates → AI slop → complexity → architecture → docs). The team commits after each category passes.

### Phase 4: Verification & Report

1. Run full test suite via `test-execution:test-runner`
2. Run `project-dev:code-quality` on all modified files
3. Apply `superpowers:verification-before-completion` patterns
4. Invoke `sc:reflect` to verify completeness against the original cleanup plan
5. Generate `hack/unfuck/YYYY-MM-DD/cleanup-report.md` with:
   - Summary stats (files modified, lines added/removed, net delta, issues fixed by category)
   - Per-category breakdown with specific changes and commit SHAs
   - Blocked items (test failures, needs-review) with stash names and manual fix guidance
   - External tools used vs agent-only analysis
   - Remaining tech debt intentionally deferred
5. Clean up intermediate discovery files
6. Report completion to user with summary and report location

## Git Workflow

- Creates feature branch: `cleanup/comprehensive-YYYY-MM-DD` (from `origin/main`)
- One commit per cleanup category (security, dead-code, duplicates, ai-slop, complexity, architecture, docs)
- Conventional commit messages
- Blocked categories are stashed with descriptive names for manual recovery

## Safety

- Tests verified after every implementation category — rollback on failure
- LSP `findReferences` before every deletion, move, or rename
- Security fixes NEVER auto-applied if they could change business logic
- AskUserQuestion for all ambiguous or high-risk decisions
- `--dry-run` flag for discovery + planning without implementation
- Agents use `needs-review` escape hatch for uncertain changes

## Output Files

| File | Purpose | Persistent |
|------|---------|------------|
| `hack/unfuck/YYYY-MM-DD/cleanup-plan.md` | Prioritized findings with fix strategies | Yes |
| `hack/unfuck/YYYY-MM-DD/cleanup-report.md` | Final report with stats and blocked items | Yes |
| `hack/unfuck/YYYY-MM-DD/discovery/*.json` | Raw agent findings (7 files) | Cleaned up |
| `hack/unfuck/YYYY-MM-DD/available-tools.json` | Detected external tools | Cleaned up |

## References

| File | Content |
|------|---------|
| `references/orchestration-playbook.md` | Complete phase-by-phase coordination guide with JSON schemas, dedup algorithm, rollback procedures, TeamCreate config, and error handling |
| `references/discovery-agents.md` | Full prompts for all 7 discovery agents — role, methodology, external tool commands, LSP patterns, severity/risk guides, output format |
| `references/implementation-agents.md` | Full prompts for all 7 implementation agents — fix strategies, paired skills, safety rules, test cadence, commit formats |
| `references/ai-slop-checklist.md` | 32 anti-patterns across 7 categories (structural, error handling, naming, comments, testing, imports, types) with before/after examples and false-positive guidance |
| `references/external-tools.md` | Tool matrix by language, detection commands, JSON output schemas, parsing instructions, Phase 0 detection script, fallback strategies |
