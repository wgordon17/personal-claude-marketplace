# Orchestration Playbook

This is the complete coordination reference for the `/unfuck` cleanup skill. The orchestrator agent reads this file and SKILL.md -- nothing else. Every phase, every agent spawn, every decision point is documented here.

---

## Phase Overview

| Phase | Name | Mode | Duration |
|-------|------|------|----------|
| 0 | Index & Setup | Orchestrator direct | ~30s |
| 1 | Discovery | 7 parallel agents | Variable |
| 2 | Synthesis & Planning | Orchestrator direct | ~60s |
| 3 | Implementation | Sequential agents per category | Variable |
| 4 | Verification & Report | Orchestrator direct + test agent | ~60s |

---

## Phase 0: Index & Setup

### Run directory

All artifacts for a single `/unfuck` run go under a date-scoped directory so multiple runs don't clash:

```
{run_dir} = hack/unfuck/YYYY-MM-DD
```

Use today's actual date (e.g., `hack/unfuck/2026-02-18`). If the directory already exists (re-run scenario), append a sequence number: `hack/unfuck/2026-02-18-2`.

All paths in this playbook use `{run_dir}` to refer to this directory. The orchestrator resolves it once and passes the resolved path to all agents via the context bundle.

### Step 0.1: Generate repo index

Invoke the `sc:index-repo` skill via the Skill tool. This creates `PROJECT_INDEX.md` at the project root, giving every agent a ~3K-token project reference instead of reading the full codebase.

```
Skill(skill="sc:index-repo")
```

Wait for completion. Read the generated `PROJECT_INDEX.md`.

### Step 0.2: Detect project languages

Check the project root for language indicator files. Use Glob to detect:

| Indicator Files | Language |
|----------------|----------|
| `package.json` / `tsconfig.json` / `*.ts` / `*.tsx` | JavaScript/TypeScript |
| `pyproject.toml` / `setup.py` / `setup.cfg` / `requirements.txt` | Python |
| `go.mod` | Go |
| `Cargo.toml` | Rust |
| `pom.xml` / `build.gradle` | Java/Kotlin |
| `Gemfile` | Ruby |
| `composer.json` | PHP |
| `*.csproj` / `*.sln` | .NET |

Multiple languages are common. Store the list of detected languages.

### Step 0.3: Detect available external tools

For each tool relevant to the detected languages (see `references/external-tools.md`), check availability using Bash:

```bash
command -v knip 2>/dev/null && knip --version || echo "NOT_AVAILABLE"
command -v semgrep 2>/dev/null && semgrep --version || echo "NOT_AVAILABLE"
# ... repeat for each tool from external-tools.md
```

Also check npx-based tools:
```bash
npx knip --version 2>/dev/null || echo "NOT_AVAILABLE"
```

Write results to `{run_dir}/available-tools.json`:
```json
{
  "languages": ["python", "typescript"],
  "tools": {
    "knip": {"available": true, "version": "5.1.0"},
    "semgrep": {"available": false, "fallback": "agent-analysis"},
    "ruff": {"available": true, "version": "0.8.0"},
    "vulture": {"available": false, "fallback": "agent-analysis"},
    "ts-prune": {"available": false, "fallback": "knip"}
  }
}
```

If a tool is unavailable, record its fallback from `references/external-tools.md`. The fallback is either another tool or `"agent-analysis"` (manual analysis by the discovery agent).

### Step 0.4: Create feature branch

```bash
git switch -c cleanup/unfuck-YYYY-MM-DD
```

Use today's actual date. If the branch already exists (re-run scenario), append a sequence number: `cleanup/unfuck-YYYY-MM-DD-2`.

### Step 0.5: Create output directories

```bash
mkdir -p {run_dir}/discovery
```

This creates:
- `{run_dir}/` -- root for all unfuck artifacts for this run
- `{run_dir}/discovery/` -- Phase 1 agent outputs

### Step 0.6: Create team

```
TeamCreate(team_name="unfuck-cleanup", description="Comprehensive repo cleanup swarm")
```

### Step 0.7: Prepare agent context bundle

Build a context string that will be prepended to every discovery agent prompt. The context bundle contains:

```
=== UNFUCK CLEANUP CONTEXT ===
Project path: /absolute/path/to/project
Languages: python, typescript
Run directory: {run_dir}
Available tools: <contents of {run_dir}/available-tools.json>

IMPORTANT — Tool Selection Guard:
This repo has a tool-selection-guard hook. You MUST use native tools:
- Glob (not ls/find), Read (not cat/head/tail), Grep (not grep/rg)
- Write/Edit (not echo/sed/awk), direct output text (not echo/printf)
- Bash is ONLY for: git, uv/uvx, npx, go run, make, and system commands
If a Bash command is blocked, switch to the equivalent native tool.

=== PROJECT INDEX ===
<contents of PROJECT_INDEX.md, truncated to 3K tokens if needed>
=== END CONTEXT ===
```

Read `PROJECT_INDEX.md` content. If it exceeds ~3,000 tokens (~12,000 characters), truncate to the summary and directory structure sections only.

Store this context string for use in Phase 1.

---

## Phase 1: Discovery (7 Parallel Teammates)

Spawn all 7 agents as **TeamCreate teammates** in a single message. Each teammate runs in its own independent context window, writes its JSON findings to disk, and sends a brief summary to the team lead via `SendMessage`. This avoids context pressure on the orchestrator — the orchestrator never calls `TaskOutput` or reads full agent transcripts.

**Why teammates, not background Tasks:** Background `Task(run_in_background=true)` agents dump their full output into the orchestrator's context when checked via `TaskOutput`. With 7 agents producing detailed findings, this causes context overflow. TeamCreate teammates have independent context windows and communicate via short `SendMessage` summaries.

### Agent Roster

| # | Agent Name | Agent Type | Model | Output File |
|---|-----------|-----------|-------|-------------|
| 1 | dead-code-hunter | general-purpose | sonnet | `{run_dir}/discovery/dead-code.json` |
| 2 | duplicate-detector | general-purpose | sonnet | `{run_dir}/discovery/duplicates.json` |
| 3 | security-auditor | general-purpose | sonnet | `{run_dir}/discovery/security.json` |
| 4 | architecture-reviewer | general-purpose | sonnet | `{run_dir}/discovery/architecture.json` |
| 5 | ai-slop-detector | general-purpose | opus | `{run_dir}/discovery/ai-slop.json` |
| 6 | complexity-auditor | general-purpose | sonnet | `{run_dir}/discovery/complexity.json` |
| 7 | documentation-auditor | general-purpose | sonnet | `{run_dir}/discovery/documentation.json` |

**Note:** The ai-slop-detector uses `opus` because detecting AI-generated patterns, unnecessary abstractions, and over-engineering requires stronger judgment. All other agents use `sonnet` for cost efficiency.

### Launching Pattern

In a single message, issue 7 parallel Task calls with `team_name` to spawn as teammates:

```
Task(name="dead-code-hunter", subagent_type="general-purpose", model="sonnet",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 1 prompt from discovery-agents.md]")

Task(name="duplicate-detector", subagent_type="general-purpose", model="sonnet",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 2 prompt from discovery-agents.md]")

Task(name="security-auditor", subagent_type="general-purpose", model="sonnet",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 3 prompt from discovery-agents.md]")

Task(name="architecture-reviewer", subagent_type="general-purpose", model="sonnet",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 4 prompt from discovery-agents.md]")

Task(name="ai-slop-detector", subagent_type="general-purpose", model="opus",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 5 prompt from discovery-agents.md]")

Task(name="complexity-auditor", subagent_type="general-purpose", model="sonnet",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 6 prompt from discovery-agents.md]")

Task(name="documentation-auditor", subagent_type="general-purpose", model="sonnet",
     team_name="unfuck-cleanup", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 7 prompt from discovery-agents.md]")
```

### Communication Protocol

Each teammate, after writing its JSON output file, sends a summary to the team lead:

```
SendMessage(type="message", recipient="team-lead",
  content="[Agent name] complete. Results: {run_dir}/discovery/[file].json\n\n
    Summary: N findings (X critical, Y high, Z medium, W low)\n
    Key findings: [1-3 sentence highlights]\n
    External tools used: [list]\n
    Gaps: [any analysis areas that couldn't be covered]",
  summary="[Agent name]: N findings")
```

The orchestrator receives these summaries as automatic message deliveries — no polling or TaskOutput needed. Each summary is ~200 tokens instead of ~5,000+ tokens for a full agent transcript.

### Monitoring

Teammates send messages automatically when complete and go idle. The orchestrator receives these as new conversation turns. Track completion by counting received summaries — when all 7 are received (or after a reasonable wait), proceed to Phase 2.

**If a teammate fails or goes idle without sending results:**
1. Check if the JSON output file exists via `Glob("{run_dir}/discovery/*.json")`
2. If the file exists, read it directly — the teammate may have written output before failing
3. If the file is missing, note the gap and proceed with available results
4. Partial coverage is better than blocking

**Expected outputs:** Each teammate writes a JSON file following the shared discovery output schema (defined at the end of this document). The orchestrator validates that each file is valid JSON and contains the required fields before proceeding to Phase 2.

### Team Shutdown

After Phase 2 synthesis is complete and all discovery JSON files have been read, shut down the discovery teammates:

```
SendMessage(type="shutdown_request", recipient="dead-code-hunter",
  content="Discovery phase complete, shutting down")
# ... repeat for each teammate
```

This frees resources before Phase 3 implementation agents are spawned.

---

## Phase 2: Synthesis & Planning

The orchestrator performs this phase directly. No subagents.

### Step 2.1: Read all discovery files

Read all 7 discovery output files in parallel:

```
Read {run_dir}/discovery/dead-code.json
Read {run_dir}/discovery/duplicates.json
Read {run_dir}/discovery/security.json
Read {run_dir}/discovery/architecture.json
Read {run_dir}/discovery/ai-slop.json
Read {run_dir}/discovery/complexity.json
Read {run_dir}/discovery/documentation.json
```

If a file is missing (agent failed), skip it and note the gap.

### Step 2.2: Deduplicate findings

Many findings will overlap across agents. Apply these dedup rules:

| Overlap Pattern | Resolution |
|----------------|------------|
| Same file + same line range across agents | Merge into one finding, keep the more detailed description |
| Dead code + architecture finding for same symbol | Merge into architecture (broader context wins) |
| Security + any other category overlap | Security takes priority, absorb the other finding |
| AI slop + complexity for same function | Merge into AI slop (more actionable category) |
| Duplicate + dead code for same function | If the function is unused everywhere, keep as dead code; if used once, keep as duplicate |

### Step 2.3: Cross-reference findings

Look for these compound patterns that change the fix strategy:

| Pattern | Tag | Meaning |
|---------|-----|---------|
| Dead code agent found unused function AND architecture agent found it's part of a divergent pattern | `dead-divergent` | Safe to remove -- the divergent pattern confirms it's abandoned |
| Duplicate detector found copy-paste AND ai-slop found over-abstraction in same area | `consolidation-candidate` | Extract a shared utility rather than choosing one copy |
| Security agent found issue AND architecture agent found the pattern is used elsewhere | `systemic-security` | Fix ALL instances of the pattern, not just the flagged one |
| Complexity finding AND the function has zero test coverage | `risky-refactor` | Flag for user review before implementation |

Add appropriate tags to each finding's `related_findings` array.

### Step 2.4: Prioritize and group

Assign findings to implementation categories in this priority order:

| Priority | Category | Rationale |
|----------|----------|-----------|
| 1 | **Security** | Critical fixes, highest value, often smallest changes |
| 2 | **Dead code** | Safest changes (deletion), reduces surface area for everything after |
| 3 | **Duplicates** | Fewer copies = fewer places to fix in remaining categories |
| 4 | **AI slop** | Simplification makes remaining refactoring easier |
| 5 | **Complexity** | Refactoring is cleaner after slop removal |
| 6 | **Architecture** | Structural changes last, most risk, benefits from prior cleanup |
| 7 | **Documentation** | Always last -- reflects all prior changes accurately |

Within each category, order findings by severity (critical > high > medium > low), then by risk of fix (low > medium > high).

### Step 2.5: Generate cleanup plan

Write `{run_dir}/cleanup-plan.md`:

```markdown
# Cleanup Plan

Generated: YYYY-MM-DD
Project: <project name from PROJECT_INDEX.md>
Languages: <detected languages>
Tools used: <list of available tools that were actually used by agents>

## Summary
- Total findings: N
- By severity: N critical, N high, N medium, N low
- By category: N dead-code, N duplicates, N security, N ai-slop, N complexity, N architecture, N documentation
- Estimated risk: low/medium/high per category

## Category 1: Security (N findings)

### Finding S-001: <title>
- **Severity:** critical|high|medium|low
- **File:** path/to/file.py:42-58
- **Description:** <what's wrong>
- **Evidence:** <how it was detected>
- **Source:** <agent name> + <tool name if applicable>
- **Suggested fix:** <from implementation-agents.md strategy>
- **Risk:** low|medium|high (risk of the fix breaking something)
- **Tags:** systemic-security (if cross-referenced)

### Finding S-002: ...

## Category 2: Dead Code (N findings)
...

## Category 3: Duplicates (N findings)
...

## Category 4: AI Slop (N findings)
...

## Category 5: Complexity (N findings)
...

## Category 6: Architecture (N findings)
...

## Category 7: Documentation (N findings)
...

## Deferred Items
Items intentionally excluded (too risky for automated cleanup):
- <item>: <reason>
```

### Step 2.6: Create task list

Create one TaskCreate per implementation category that has findings:

```
TaskCreate("Fix security findings", "Apply N security fixes from cleanup-plan.md Category 1")
TaskCreate("Remove dead code", "Remove N dead code items from cleanup-plan.md Category 2")
TaskCreate("Consolidate duplicates", "Consolidate N duplicate patterns from cleanup-plan.md Category 3")
TaskCreate("Simplify AI slop", "Simplify N over-engineered patterns from cleanup-plan.md Category 4")
TaskCreate("Reduce complexity", "Reduce complexity in N functions from cleanup-plan.md Category 5")
TaskCreate("Unify architecture", "Unify N architectural patterns from cleanup-plan.md Category 6")
TaskCreate("Sync documentation", "Update N documentation items from cleanup-plan.md Category 7")
```

Skip categories with zero findings.

### Step 2.7: User checkpoint

Use AskUserQuestion if ANY of these conditions are true:

| Condition | Why |
|-----------|-----|
| Any finding proposes deleting a public API export | Could break downstream consumers |
| Any finding proposes changing an architectural pattern used in 5+ files | High blast radius |
| Any security finding requires a policy decision | e.g., "secrets in env vars or vault?" |
| Total findings exceed 100 | Confirm user wants full cleanup |
| Any finding is ambiguous | Multiple valid interpretations |
| Any `risky-refactor` tagged findings exist | Needs human judgment |

AskUserQuestion format:

```
AskUserQuestion(
  questions=[{
    "question": "The cleanup plan includes N findings across M categories. [specific concern if applicable]. How should we proceed?",
    "header": "Cleanup Scope",
    "options": [
      {"label": "Full cleanup", "description": "Apply all N findings across M categories"},
      {"label": "Skip [risky category]", "description": "Apply all except [category] (N findings deferred)"},
      {"label": "Security + dead code only", "description": "Conservative cleanup — only the safest categories"},
      {"label": "Review plan first", "description": "I'll review {run_dir}/cleanup-plan.md before proceeding"}
    ],
    "multiSelect": false
  }]
)
```

If user selects "Review plan first", wait for their follow-up message before proceeding.

If none of the checkpoint conditions are met, proceed directly to Phase 3 without asking.

---

## Phase 3: Implementation

Execute implementation categories sequentially in priority order. Within each category, a single implementation agent works autonomously.

### Execution Loop

For each category (in priority order from Step 2.4):

#### 3a. Extract findings

Read the category's findings from `{run_dir}/cleanup-plan.md`. Convert them into a structured prompt section.

#### 3b. Spawn implementation agent

```
Task(name="fix-<category>", subagent_type="general-purpose",
     mode="bypassPermissions",
     prompt="[context bundle]\n\n[Implementation agent prompt from references/implementation-agents.md for this category]\n\nFindings to fix:\n[filtered findings from cleanup-plan.md as JSON]")
```

**Model selection for implementation agents:**
- `fix-ai-slop` uses `model="opus"` (requires judgment to simplify without losing functionality)
- All other implementation agents use default model (sonnet)

Do NOT run implementation agents in background -- they must complete sequentially to avoid file conflicts.

#### 3c. Verify results

After each agent completes:

**Run tests:**
```
Task(name="verify-<category>", subagent_type="test-execution:test-runner",
     prompt="Run the full test suite and report pass/fail")
```

If no test-runner agent type is available, run tests directly via Bash. Auto-detect the test command:

| Check | Command |
|-------|---------|
| `Makefile` with `test` target | `make test` |
| `pyproject.toml` exists | `uv run pytest` |
| `package.json` with `test` script | `npm test` |
| `package.json` without `test` script + jest | `npx jest` |
| `package.json` without `test` script + vitest | `npx vitest run` |
| `go.mod` exists | `go test ./...` |
| `Cargo.toml` exists | `cargo test` |

**Run linter/formatter (if available):**

| Check | Command |
|-------|---------|
| `pyproject.toml` with ruff | `uv run ruff check . && uv run ruff format --check .` |
| `Makefile` with `lint` target | `make lint` |
| `package.json` with `lint` script | `npm run lint` |
| `.eslintrc*` exists | `npx eslint .` |
| `Makefile` with `format` target | `make format` (auto-fix) |

#### 3d. Commit or rollback

**If tests pass:**
```bash
# Stage only the files this agent modified
git add <list of modified files>
git commit -m "$(cat <<'EOF'
<category-specific commit message>
EOF
)"
```

Mark the category's task as completed:
```
TaskUpdate(task_id=<id>, status="completed")
```

**If tests fail -- rollback:**
```bash
git stash push -m "unfuck-<category>-blocked"
```

Create a blocked task:
```
TaskCreate("BLOCKED: <category> -- <failure summary>",
  "Implementation of <category> caused test failure:\n<test output snippet>\n\nStashed changes: unfuck-<category>-blocked\nManual review needed.\n\nFailed tests:\n<list of failing test names>")
```

Continue to the next category. Do not retry.

### Commit Message Formats

| Category | Commit Message Format |
|----------|----------------------|
| Security | `fix(security): <specific vulnerability description>` |
| Dead code | `refactor: removes dead code -- N unused items` |
| Duplicates | `refactor: consolidates duplicate code in <area>` |
| AI slop | `refactor: simplifies over-engineered code in <area>` |
| Complexity | `refactor: reduces complexity in <area>` |
| Architecture | `refactor: unifies <pattern> across codebase` |
| Documentation | `docs: syncs documentation with code changes` |

If a category has many findings, summarize in the commit message rather than listing each one. Keep commit messages under 72 characters for the subject line; use the body for details.

### Sequential Execution Rationale

Categories execute sequentially because:
1. Dead code removal changes file contents that later agents need to read
2. Duplicate consolidation may move code that complexity/architecture agents target
3. Each commit creates a clean baseline for the next category
4. Rollback via `git stash` is clean when changes don't interleave

---

## Phase 4: Verification & Report

### Step 4.1: Full test suite

Run the complete test suite one final time. This catches any cross-category regressions that individual category tests might miss.

```
Task(name="final-verification", subagent_type="test-execution:test-runner",
     prompt="Run the full test suite. Report all pass/fail results.")
```

Or via Bash using the auto-detected test command from Phase 3.

If final tests fail, identify which commit introduced the regression:
```bash
git log --oneline cleanup/unfuck-YYYY-MM-DD~N..HEAD
```
Then bisect or check each commit's changes against the failing tests.

### Step 4.2: Code quality review

Get the list of all modified files across all implementation commits:

```bash
git diff --name-only $(git merge-base HEAD main)..HEAD
```

Spawn a code quality review agent:

```
Task(name="quality-review", subagent_type="general-purpose",
     prompt="Review these files for code quality issues introduced during cleanup:\n<file list>\n\nCheck for:\n- Broken imports after dead code removal\n- Inconsistent naming after duplicate consolidation\n- Missing error handling after simplification\n- Incomplete refactoring (half-done changes)")
```

### Step 4.3: Verification patterns

Apply `superpowers:verification-before-completion` patterns:
- Every commit compiles/parses cleanly
- Test suite passes on HEAD
- No uncommitted changes remain (except `hack/unfuck/` artifacts)
- No untracked source files created accidentally
- Linter passes (if available)

### Step 4.4: Generate cleanup report

Write `{run_dir}/cleanup-report.md`:

```markdown
# Cleanup Report

Generated: YYYY-MM-DD HH:MM
Project: <project name>
Branch: cleanup/unfuck-YYYY-MM-DD

## Summary
- **Files modified:** N
- **Lines removed:** N
- **Lines added:** N
- **Net delta:** -N lines (or +N if documentation additions outweigh removals)
- **Issues fixed:** N (across M categories)
- **Issues blocked:** N (need manual review)

## Changes by Category

### Security (N fixes)
- S-001: <one-line description of what was fixed>
- S-002: ...
Commit: `abc1234` -- `fix(security): <message>`

### Dead Code (N removals)
- DC-001: <one-line description>
- DC-002: ...
Commit: `def5678` -- `refactor: removes dead code -- N unused items`

### Duplicates (N consolidations)
- DUP-001: ...
Commit: `ghi9012` -- `refactor: consolidates duplicate code in <area>`

### AI Slop (N simplifications)
- SLOP-001: ...
Commit: `jkl3456` -- `refactor: simplifies over-engineered code in <area>`

### Complexity (N reductions)
- CX-001: ...
Commit: `mno7890` -- `refactor: reduces complexity in <area>`

### Architecture (N unifications)
- ARCH-001: ...
Commit: `pqr1234` -- `refactor: unifies <pattern> across codebase`

### Documentation (N updates)
- DOC-001: ...
Commit: `stu5678` -- `docs: syncs documentation with code changes`

## Blocked Items (need manual review)

### BLOCKED: <category> -- <failure summary>
- **Stash:** `unfuck-<category>-blocked`
- **Reason:** <why tests failed>
- **Failing tests:** <list>
- **Suggested manual fix:** <guidance>
- **To restore:** `git stash apply stash@{N}` (find with `git stash list | grep unfuck-<category>`)

## Discovery Agent Failures
(If any agents failed in Phase 1)
- <agent name>: <error summary>
- Impact: <what categories may have incomplete coverage>

## External Tools Used
| Tool | Version | Findings Contributed |
|------|---------|---------------------|
| Knip | 5.1.0 | 12 dead exports |
| Semgrep | 1.50.0 | 3 security issues |
| Ruff | 0.8.0 | 5 code quality items |
| (agent-analysis) | -- | Used for: duplicates, complexity, architecture |

## All Commits
| SHA | Message |
|-----|---------|
| abc1234 | fix(security): removes hardcoded secrets |
| def5678 | refactor: removes dead code -- 15 unused items |
| ... | ... |

## Remaining Tech Debt
Items intentionally deferred (low severity, high risk, or requires human decision):
- [ ] <item>: <reason for deferral>
- [ ] <item>: <reason for deferral>
```

Get the line stats with:
```bash
git diff --stat $(git merge-base HEAD main)..HEAD
git diff --shortstat $(git merge-base HEAD main)..HEAD
```

### Step 4.5: Cleanup intermediate files

```bash
rm -rf {run_dir}/discovery/
rm -f {run_dir}/available-tools.json
```

Keep these files for user reference:
- `{run_dir}/cleanup-plan.md` -- what was planned
- `{run_dir}/cleanup-report.md` -- what was done

### Step 4.6: Announce completion

Report to the user:

```
Cleanup complete. Branch: cleanup/unfuck-YYYY-MM-DD

Summary: N files modified, -N lines net, N issues fixed across M categories.
N items blocked (need manual review).

Full report: {run_dir}/cleanup-report.md
Cleanup plan: {run_dir}/cleanup-plan.md

Next steps:
- Review the branch diff: git diff main..cleanup/unfuck-YYYY-MM-DD
- Check blocked items in the report (if any)
- Create a PR when satisfied: gh pr create
```

---

## Shared JSON Schema for Discovery Output

All 7 discovery agents MUST write their output using this schema. The orchestrator validates each file against this schema before proceeding to Phase 2.

```json
{
  "$schema": "discovery-output",
  "agent": "<agent-name>",
  "timestamp": "2026-02-18T12:00:00Z",
  "summary": {
    "total_findings": 15,
    "by_severity": {
      "critical": 0,
      "high": 3,
      "medium": 8,
      "low": 4
    },
    "external_tools_used": ["knip"],
    "agent_analysis_used": true
  },
  "findings": [
    {
      "id": "DC-001",
      "severity": "high",
      "category": "dead-code",
      "title": "Unused exported function",
      "file": "src/utils/helpers.ts",
      "line_start": 42,
      "line_end": 58,
      "description": "Function `formatLegacyDate` is exported but has zero references across the codebase.",
      "evidence": "LSP findReferences returned 0 results. Knip also flagged this export.",
      "source": "agent-analysis+knip",
      "suggested_fix": "Remove the function and its export statement.",
      "risk": "low",
      "related_findings": []
    }
  ]
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `$schema` | string | Always `"discovery-output"` |
| `agent` | string | Agent name matching the roster (e.g., `"dead-code-hunter"`) |
| `timestamp` | string | ISO 8601 timestamp of when the agent completed |
| `summary.total_findings` | number | Total number of findings in this output |
| `summary.by_severity` | object | Breakdown by severity level |
| `summary.external_tools_used` | array | List of external tools that produced findings |
| `summary.agent_analysis_used` | boolean | Whether the agent performed manual code analysis |
| `findings[].id` | string | Unique within this agent's output. Format: category prefix + sequential number |
| `findings[].severity` | string | `critical` = security vulnerability or data loss risk; `high` = definite issue, safe fix available; `medium` = probable issue, review recommended; `low` = style/preference, optional fix |
| `findings[].category` | string | Primary category: `dead-code`, `duplicates`, `security`, `architecture`, `ai-slop`, `complexity`, `documentation` |
| `findings[].title` | string | Short descriptive title |
| `findings[].file` | string | Relative path from project root |
| `findings[].line_start` | number | Starting line number |
| `findings[].line_end` | number | Ending line number |
| `findings[].description` | string | Detailed explanation of the issue |
| `findings[].evidence` | string | How it was detected -- tool output, analysis reasoning, etc. |
| `findings[].source` | string | `"agent-analysis"`, `"<tool-name>"`, or `"agent-analysis+<tool-name>"` for confirmed by both |
| `findings[].suggested_fix` | string | Recommended fix strategy |
| `findings[].risk` | string | Risk of the suggested fix breaking something: `low` (safe), `medium` (needs testing), `high` (needs review) |
| `findings[].related_findings` | array | IDs of related findings in OTHER agents' output (populated during Phase 2 synthesis) |

### ID Prefix Conventions

| Category | Prefix | Example |
|----------|--------|---------|
| Dead code | DC | DC-001 |
| Duplicates | DUP | DUP-001 |
| Security | S | S-001 |
| Architecture | ARCH | ARCH-001 |
| AI slop | SLOP | SLOP-001 |
| Complexity | CX | CX-001 |
| Documentation | DOC | DOC-001 |

---

## TeamCreate Configuration

```
TeamCreate:
  team_name: "unfuck-cleanup"
  description: "Comprehensive repo cleanup -- discovery and implementation swarm"
```

### Discovery Members (Phase 1 -- all spawned in parallel)

| Name | Type | Model | Purpose |
|------|------|-------|---------|
| dead-code-hunter | general-purpose | sonnet | Finds unused code, exports, imports, files |
| duplicate-detector | general-purpose | sonnet | Finds copy-paste code and near-duplicates |
| security-auditor | general-purpose | sonnet | Finds security vulnerabilities and bad practices |
| architecture-reviewer | general-purpose | sonnet | Finds structural issues, inconsistencies, divergent patterns |
| ai-slop-detector | general-purpose | opus | Finds AI-generated bloat, over-abstraction, unnecessary complexity |
| complexity-auditor | general-purpose | sonnet | Finds overly complex functions, deep nesting, god objects |
| documentation-auditor | general-purpose | sonnet | Finds stale docs, missing docs, doc/code drift |

### Implementation Members (Phase 3 -- spawned sequentially as needed)

| Name | Type | Mode | Model | Purpose |
|------|------|------|-------|---------|
| fix-security | general-purpose | bypassPermissions | sonnet | Applies security fixes |
| fix-dead-code | general-purpose | bypassPermissions | sonnet | Removes dead code |
| fix-duplicates | general-purpose | bypassPermissions | sonnet | Consolidates duplicates |
| fix-ai-slop | general-purpose | bypassPermissions | opus | Simplifies over-engineered code |
| fix-complexity | general-purpose | bypassPermissions | sonnet | Refactors complex code |
| fix-architecture | general-purpose | bypassPermissions | sonnet | Unifies architectural patterns |
| fix-documentation | general-purpose | bypassPermissions | sonnet | Updates documentation |

---

## Error Handling

### Agent Failure (Phase 1)

If a discovery agent fails to produce output:
1. Log the failure in the cleanup report (agent name, error message)
2. Skip that category in synthesis -- other agents may have partial coverage of the same issues
3. Note the gap in the cleanup plan so the user knows coverage is incomplete
4. Do NOT retry automatically -- the failure likely indicates a fundamental issue

### Test Failure During Implementation (Phase 3)

See the rollback procedure in Phase 3, Step 3d. Key points:
- Stash changes with a descriptive name
- Create a blocked task with full failure details
- Continue to the next category (do not retry)
- Report all blocked items in Phase 4

### External Tool Failure (Phase 0/1)

If an external tool fails during detection (Phase 0):
- Mark it as unavailable in `available-tools.json`
- Agents will use fallback strategies from `references/external-tools.md`

If an external tool fails during agent execution (Phase 1):
- The agent falls back to manual analysis
- The agent notes the tool failure in its output's `evidence` field
- This is expected and handled -- agents are designed to work without external tools

### Git Conflicts

Conflicts between implementation categories should not occur because categories execute sequentially. If a conflict somehow appears:
1. Stash the current category's changes
2. Log it as a blocked item
3. Continue to the next category
4. Report the conflict in the cleanup report for manual resolution

### AskUserQuestion Timeout (Phase 2)

If the user doesn't respond to the Phase 2 checkpoint:
1. Wait -- do not proceed without user input if the checkpoint was triggered
2. The checkpoint only triggers for high-risk conditions that genuinely need human judgment
3. If the user explicitly says "proceed" or "just do it" without selecting an option, use "Full cleanup" as default

### Insufficient Findings

If Phase 1 produces fewer than 3 total findings across all agents:
1. Report this to the user -- the codebase may already be clean
2. Still generate the cleanup plan and report (even if nearly empty)
3. Skip Phase 3 implementation if there's truly nothing actionable
4. Suggest running individual focused skills instead (e.g., `sc:cleanup`, `security-review`)
