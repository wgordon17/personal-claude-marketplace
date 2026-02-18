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
1. Run `sc:index-repo` to create PROJECT_INDEX.md (~3K token project reference)
2. Detect project languages from config files (package.json, pyproject.toml, go.mod, etc.)
3. Detect available external tools (Knip, Semgrep, radon, gitleaks, etc.) — see `references/external-tools.md`
4. Create feature branch: `cleanup/unfuck-YYYY-MM-DD`
5. Create `hack/unfuck/YYYY-MM-DD/discovery/` directory for agent output (date-scoped per run)
6. Create TeamCreate swarm: `unfuck-cleanup`

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

The orchestrator (not a subagent) directly:
1. Reads all 7 discovery JSON files
2. Deduplicates overlapping findings across agents
3. Cross-references compound patterns (dead + divergent = safe to remove)
4. Prioritizes: security → dead code → duplicates → AI slop → complexity → architecture → docs
5. Writes `hack/unfuck/YYYY-MM-DD/cleanup-plan.md` with per-finding detail
6. Creates TaskList with one task per category
7. **AskUserQuestion** if: public API deletions, 5+ file architectural changes, ambiguous findings,
   security policy decisions, or >100 total findings

### Phase 3: Implementation (sequential per category)

For each category in priority order, spawn an implementation agent with its full prompt from
`references/implementation-agents.md`. Each agent:
- Receives filtered findings for its category
- Uses paired existing skills for execution (sc:cleanup, project-dev:refactor, code-simplifier, etc.)
- Applies custom fix strategies from its prompt
- After EACH category: run tests → run formatter → commit or rollback (git stash on failure)

| Agent | Category | Paired Skills | Model | Commit Format |
|-------|----------|---------------|-------|---------------|
| fix-security | Security fixes | `security-review` | sonnet | `fix(security): <vuln>` |
| fix-dead-code | Dead code removal | `sc:cleanup --aggressive` | sonnet | `refactor: removes dead code` |
| fix-duplicates | Duplicate consolidation | `project-dev:refactor` | sonnet | `refactor: consolidates duplicates` |
| fix-ai-slop | AI slop simplification | `code-simplifier`, `sc:improve` | opus | `refactor: simplifies over-engineered code` |
| fix-complexity | Complexity reduction | `sc:improve --type maintainability` | sonnet | `refactor: reduces complexity` |
| fix-architecture | Architecture unification | `project-dev:refactor`, `superclaude:architect` | sonnet | `refactor: unifies <pattern>` |
| fix-documentation | Documentation sync | `docs-sync` | sonnet | `docs: syncs documentation` |

### Phase 4: Verification & Report

1. Run full test suite via `test-execution:test-runner`
2. Run `project-dev:code-quality` on all modified files
3. Apply `superpowers:verification-before-completion` patterns
4. Generate `hack/unfuck/YYYY-MM-DD/cleanup-report.md` with:
   - Summary stats (files modified, lines added/removed, net delta, issues fixed by category)
   - Per-category breakdown with specific changes and commit SHAs
   - Blocked items (test failures, needs-review) with stash names and manual fix guidance
   - External tools used vs agent-only analysis
   - Remaining tech debt intentionally deferred
5. Clean up intermediate discovery files
6. Report completion to user with summary and report location

## Git Workflow

- Creates feature branch: `cleanup/unfuck-YYYY-MM-DD`
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
