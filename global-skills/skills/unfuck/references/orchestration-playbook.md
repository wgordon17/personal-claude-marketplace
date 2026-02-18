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

### Parallel Setup

Phase 0 has several independent tasks that SHOULD run in parallel using TeamCreate:

1. **Create team immediately:** `TeamCreate(team_name="cleanup-swarm")` — needed before spawning any teammates
2. **Launch parallel setup teammates** — spawn these as background teammates in a single message:
   - `setup-indexer`: Runs `sc:index-repo` to create PROJECT_INDEX.md
   - `setup-tools`: Detects available external tools (version checks)
   - `setup-languages`: Detects project languages from config files
3. **While teammates work**, the orchestrator creates the run directory, feature branch, and output directories (Steps 0.4-0.5)
4. **Collect results** from setup teammates (they send summaries via SendMessage)
5. **Build context bundle** (Step 0.7) using collected results
6. **Shut down setup teammates** before spawning Phase 1 agents

This parallelism saves time since `sc:index-repo` and tool detection are the slowest setup steps.

### Step 0.1: Generate repo index

Spawn a `setup-indexer` teammate to invoke the `sc:index-repo` skill. This creates `PROJECT_INDEX.md` at the project root, giving every agent a ~3K-token project reference instead of reading the full codebase.

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

For each tool relevant to the detected languages (see `references/external-tools.md`), check availability by running version commands and checking exit codes. **Do NOT use `|| echo` or `&& echo` patterns** — these are blocked by the tool-selection-guard hook.

Run version checks as individual or semicolon-separated Bash calls:

```bash
# Python tools (semicolons, not && or ||)
uvx vulture --version 2>/dev/null; uvx ruff --version 2>/dev/null; uvx radon --version 2>/dev/null; uvx bandit --version 2>/dev/null

# Semgrep (must unset ALL proxy vars to avoid crash)
unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy ALL_PROXY all_proxy; uvx semgrep --version 2>/dev/null
```

Parse stdout for version strings. Non-zero exit or empty output = unavailable.

Write results to `{run_dir}/available-tools.json` using the Write tool:
```json
{
  "languages": ["python", "typescript"],
  "tools": {
    "ruff": {"available": true, "version": "0.15.1"},
    "semgrep": {"available": false, "fallback": "agent-analysis"},
    "vulture": {"available": true, "version": "2.14"}
  }
}
```

If a tool is unavailable, record its fallback from `references/external-tools.md`. The fallback is either another tool or `"agent-analysis"` (manual analysis by the discovery agent).

### Step 0.4: Create feature branch

```bash
git switch -c cleanup/comprehensive-YYYY-MM-DD origin/main
```

Use today's actual date. Always branch from `origin/main` (fetch first if needed). If the branch already exists (re-run scenario), append a sequence number: `cleanup/comprehensive-YYYY-MM-DD-2`.

**IMPORTANT:** Do NOT use "unfuck" in the branch name — use professional naming like `cleanup/comprehensive-YYYY-MM-DD`.

### Step 0.5: Create output directories

```bash
mkdir -p {run_dir}/discovery
```

This creates:
- `{run_dir}/` -- root for all cleanup artifacts for this run
- `{run_dir}/discovery/` -- Phase 1 agent outputs

### Step 0.6: Create team

```
TeamCreate(team_name="cleanup-swarm", description="Comprehensive repo cleanup swarm")
```

### Step 0.7: Prepare agent context bundle

Build a context string that will be prepended to every discovery agent prompt. The context bundle contains:

```
=== CLEANUP CONTEXT ===
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

Spawn **ALL 7 agents** as TeamCreate teammates in a **single message** (7 parallel Task calls). Every agent MUST be spawned — do not skip agents based on earlier runs or existing discovery files. If previous results exist from an earlier run, the orchestrator should mention them in the agent prompt as prior context, but the agent must still perform its own fresh analysis.

Each teammate runs in its own independent context window, writes its JSON findings to disk, and sends a brief summary to the team lead via `SendMessage`. This avoids context pressure on the orchestrator — the orchestrator never calls `TaskOutput` or reads full agent transcripts.

**Why teammates, not background Tasks:** Background `Task(run_in_background=true)` agents dump their full output into the orchestrator's context when checked via `TaskOutput`. With 7 agents producing detailed findings, this causes context overflow. TeamCreate teammates have independent context windows and communicate via short `SendMessage` summaries.

**CRITICAL:** All 7 agents MUST be launched in the SAME message. Do not conditionally skip agents. The orchestrator should wait for all 7 completion summaries before proceeding to Phase 2.

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
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 1 prompt from discovery-agents.md]")

Task(name="duplicate-detector", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 2 prompt from discovery-agents.md]")

Task(name="security-auditor", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 3 prompt from discovery-agents.md]")

Task(name="architecture-reviewer", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 4 prompt from discovery-agents.md]")

Task(name="ai-slop-detector", subagent_type="general-purpose", model="opus",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 5 prompt from discovery-agents.md]")

Task(name="complexity-auditor", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Agent 6 prompt from discovery-agents.md]")

Task(name="documentation-auditor", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
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

**IMPORTANT:** Do NOT perform synthesis directly in the orchestrator — this fills the lead agent's context with all 7 discovery files (~35K+ tokens). Instead, delegate to a **dedicated opus synthesis teammate** that has its own independent context window.

### Step 2.0: Spawn synthesis teammate

Spawn a single `synthesis-planner` teammate using opus:

```
Task(name="synthesis-planner", subagent_type="general-purpose", model="opus",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\n[Full synthesis instructions below]\n\nRun directory: {run_dir}")
```

The synthesis teammate performs Steps 2.1-2.6 independently and writes the cleanup plan to disk. When done, it sends a brief summary to the team lead via SendMessage (total findings, by-severity breakdown, categories with findings, any items needing user review).

The orchestrator then handles Step 2.7 (user checkpoint) based on the summary.

### Step 2.1: Read all discovery files

The synthesis teammate reads all 7 discovery output files in parallel:

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

Spawn a **single persistent collaborative team** that works through all categories sequentially. The team stays alive for the entire implementation phase, maintaining context as categories build on each other.

### Step 3.0: Spawn implementation team

Spawn 4 persistent teammates in a single message. They collaborate on each category together:

```
Task(name="impl-writer", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\nYou are the WRITER on the implementation team. You implement fixes from the cleanup plan.\nYour teammates are: impl-qa (reviews your changes), impl-tester (runs tests), impl-docs (updates docs).\n\nWait for the team lead to assign you a category. For each category:\n1. Read the category's findings from {run_dir}/cleanup-plan.md\n2. Apply fixes using the strategies from references/implementation-agents.md\n3. When done with a category, message impl-qa to review your changes\n4. After QA approval and tests pass, commit the category\n5. Message the team lead that the category is complete\n\n[Full implementation agent shared rules from references/implementation-agents.md]")

Task(name="impl-qa", subagent_type="general-purpose", model="opus",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\nYou are QA on the implementation team. You review changes made by impl-writer.\n\nWhen impl-writer messages you that a category is ready for review:\n1. Read the modified files (git diff)\n2. Verify changes match the cleanup plan findings\n3. Check for regressions, broken imports, missing error handling\n4. Use LSP findReferences to verify no callers are broken\n5. If issues found, message impl-writer with specific feedback\n6. If clean, message impl-tester to run the test suite\n\nApply code-review rigor: no assumptions, verify everything.")

Task(name="impl-tester", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\nYou are the TESTER on the implementation team. You run tests and formatters.\n\nWhen impl-qa messages you that changes are reviewed:\n1. Run the project's test suite (auto-detect: make test / uv run pytest / npm test / etc.)\n2. Run the formatter (make format / uvx ruff format / etc.)\n3. If tests pass: message impl-writer to commit\n4. If tests fail: message impl-writer with the failure details for rollback\n\nAuto-detect test commands from Makefile, pyproject.toml, or package.json.")

Task(name="impl-docs", subagent_type="general-purpose", model="sonnet",
     team_name="cleanup-swarm", mode="bypassPermissions",
     prompt="[context bundle]\n\nYou are the DOCUMENTER on the implementation team. You update docs after code changes.\n\nFor each completed category:\n1. Check if the changes affect any documentation (README, docstrings, config docs)\n2. If so, update documentation to reflect the changes\n3. Follow docs-sync patterns: match existing style, verify commands work\n4. Message impl-writer when doc updates are ready to include in the commit\n\nDo NOT create new documentation files. Only update existing ones.")
```

**Model selection:** `impl-qa` uses opus (reviewing requires stronger judgment). Others use sonnet.

### Execution Loop

The orchestrator assigns categories to the team in priority order by messaging `impl-writer`:

```
SendMessage(type="message", recipient="impl-writer",
  content="Begin Category 1: Security. Findings:\n[filtered findings from cleanup-plan.md]\n\nUse the security fixer strategy from implementation-agents.md.",
  summary="Assign security category")
```

The team works through the write → QA → test → docs → commit cycle for each category. The orchestrator waits for the `impl-writer` completion message before assigning the next category.

#### Category workflow (team-internal)

```
impl-writer: implements fixes → messages impl-qa
impl-qa: reviews changes → messages impl-tester (or impl-writer if issues)
impl-tester: runs tests → messages impl-writer (pass: commit, fail: rollback)
impl-docs: updates docs in parallel with QA/test, messages impl-writer when ready
impl-writer: commits changes → messages team lead
```

#### Commit or rollback

**If tests pass:**
```bash
# Stage only the files modified for this category
git add <list of modified files>
git commit -m "<category-specific commit message>"
```

Mark the category's task as completed:
```
TaskUpdate(task_id=<id>, status="completed")
```

**If tests fail -- rollback:**
```bash
git stash push -m "cleanup-<category>-blocked"
```

Create a blocked task:
```
TaskCreate("BLOCKED: <category> -- <failure summary>",
  "Implementation of <category> caused test failure:\n<test output snippet>\n\nStashed changes: cleanup-<category>-blocked\nManual review needed.\n\nFailed tests:\n<list of failing test names>")
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

**IMPORTANT:** Never use "unfuck" in commit messages, branch names, PR titles, or any user-facing output. Use professional terminology: "cleanup", "comprehensive", "refactor", "simplifies", etc.

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
git log --oneline cleanup/comprehensive-YYYY-MM-DD~N..HEAD
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
- No uncommitted changes remain (except `hack/` artifacts)
- No untracked source files created accidentally
- Linter passes (if available)

### Step 4.4: Generate cleanup report

Write `{run_dir}/cleanup-report.md`:

```markdown
# Cleanup Report

Generated: YYYY-MM-DD HH:MM
Project: <project name>
Branch: cleanup/comprehensive-YYYY-MM-DD

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
- **Stash:** `cleanup-<category>-blocked`
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

### Step 4.6: Reflect on completeness

Invoke `sc:reflect` to verify the cleanup is comprehensive and nothing was missed:

```
Skill(skill="sc:reflect")
```

This performs a final cross-check against the original cleanup plan, verifying:
- All planned categories were addressed (or documented as blocked/deferred)
- No regression was introduced
- The cleanup report accurately reflects what was done
- Version bumps were applied where needed

If reflection identifies gaps, address them before announcing completion.

### Step 4.7: Announce completion

Report to the user:

```
Cleanup complete. Branch: cleanup/comprehensive-YYYY-MM-DD

Summary: N files modified, -N lines net, N issues fixed across M categories.
N items blocked (need manual review).

Full report: {run_dir}/cleanup-report.md
Cleanup plan: {run_dir}/cleanup-plan.md

Next steps:
- Review the branch diff: git diff main..cleanup/comprehensive-YYYY-MM-DD
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
  team_name: "cleanup-swarm"
  description: "Comprehensive repo cleanup -- setup, discovery, synthesis, and implementation swarm"
```

### Setup Members (Phase 0 -- spawned in parallel for initialization)

| Name | Type | Model | Purpose |
|------|------|-------|---------|
| setup-indexer | general-purpose | sonnet | Runs sc:index-repo to create PROJECT_INDEX.md |
| setup-tools | general-purpose | sonnet | Detects available external tools via version checks |
| setup-languages | general-purpose | sonnet | Detects project languages from config files |

### Synthesis Member (Phase 2 -- single agent for dedup and planning)

| Name | Type | Model | Purpose |
|------|------|-------|---------|
| synthesis-planner | general-purpose | opus | Reads all discovery files, deduplicates, cross-references, writes cleanup plan |

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

### Implementation Members (Phase 3 -- single persistent collaborative team)

| Name | Type | Mode | Model | Role |
|------|------|------|-------|------|
| impl-writer | general-purpose | bypassPermissions | sonnet | Implements fixes from cleanup plan |
| impl-qa | general-purpose | bypassPermissions | opus | Reviews changes for correctness |
| impl-tester | general-purpose | bypassPermissions | sonnet | Runs tests and formatters |
| impl-docs | general-purpose | bypassPermissions | sonnet | Updates documentation |

All 4 teammates are spawned once and persist through all categories. The orchestrator assigns categories sequentially via SendMessage to `impl-writer`. The team collaborates: write → QA → test → docs → commit per category.

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
