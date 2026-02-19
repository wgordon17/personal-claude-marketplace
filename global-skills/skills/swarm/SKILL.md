---
name: swarm
description: Force full TeamCreate agent swarm for any implementation task. Launches Architect, Implementer, Tester, Security, QA, Docs, and Verifier agents in a coordinated pipeline. Use when asked to "swarm this", "full team", "agent team", or when you want maximum rigor on an implementation.
---

# /swarm — Full Agent Swarm Implementation

You MUST implement the given task using a **full TeamCreate agent swarm**. No shortcuts. No doing it yourself. Every phase gets a dedicated agent.

## Team Composition

| Step | Role | Agent Type | Model | Can Edit | Purpose |
|------|------|-----------|-------|----------|---------|
| 1 | Architect | `superclaude:architect` | sonnet | No | Analyze codebase, design solution, produce plan |
| 2 | Implementer | `project-dev:feature-writer` | sonnet | **Yes** | Write code following architect's plan |
| 3 | Tester | `project-dev:test-writer` | haiku | **Yes** | Write tests for the implementation |
| 4a | Security | `superclaude:security` | sonnet | No | OWASP review, secrets, auth gaps |
| 4b | QA | `superclaude:qa` | sonnet | No | Code quality, patterns, conventions |
| 5 | Fixer | `project-dev:feature-writer` | sonnet | **Yes** | Fix issues from Security + QA reviews |
| 6 | Docs | `general-purpose` | haiku | **Yes** | Update repo docs + project memory |
| 7 | Verifier | `test-execution:test-runner` | haiku | No | Run tests + lint, final validation |

## Orchestration Flow

```
User: "/swarm <task>"
    |
    v
[Lead: YOU] ── TeamCreate("swarm-{timestamp}")
    |
    |── TaskCreate: Phase 1 (Architect)
    |── TaskCreate: Phase 2 (Implement) ── blockedBy: Phase 1
    |── TaskCreate: Phase 3 (Tests) ── blockedBy: Phase 2
    |── TaskCreate: Phase 4a (Security) ── blockedBy: Phase 3
    |── TaskCreate: Phase 4b (QA) ── blockedBy: Phase 3
    |── TaskCreate: Phase 5 (Fix) ── blockedBy: Phase 4a, 4b
    |── TaskCreate: Phase 6 (Docs) ── blockedBy: Phase 5
    |── TaskCreate: Phase 7 (Verify) ── blockedBy: Phase 6
    |
    v
[Spawn teammates sequentially as tasks unblock]
```

## Execution Protocol

### Step 0: Create Team and Tasks

```
TeamCreate(team_name="swarm-impl", description="Full swarm: <task summary>")
```

Create ALL tasks upfront with dependencies. Use `addBlockedBy` so tasks auto-unblock:

```
TaskCreate: "Phase 1: Architect designs solution for <task>"
TaskCreate: "Phase 2: Implement solution" → blockedBy: [Phase 1]
TaskCreate: "Phase 3: Write tests" → blockedBy: [Phase 2]
TaskCreate: "Phase 4a: Security review" → blockedBy: [Phase 3]
TaskCreate: "Phase 4b: QA review" → blockedBy: [Phase 3]
TaskCreate: "Phase 5: Fix review findings" → blockedBy: [Phase 4a, Phase 4b]
TaskCreate: "Phase 6: Update documentation" → blockedBy: [Phase 5]
TaskCreate: "Phase 7: Run tests and verify" → blockedBy: [Phase 6]
```

### Step 1: Architect

Spawn with `superclaude:architect` (read-only — designs, does not code):

```
Task(
  subagent_type="superclaude:architect",
  team_name="swarm-impl",
  name="architect",
  prompt="""You are the Architect for this implementation.

TASK: <full task description>

Your job:
1. Analyze the codebase to understand the current architecture
2. Identify which files need to be created or modified
3. Design the solution approach with specific file paths and function signatures
4. Identify potential risks and edge cases
5. Produce a detailed implementation plan

OUTPUT FORMAT — send this to the lead via SendMessage:
- Goal (1 sentence)
- Files to create/modify (exact paths)
- Implementation steps (ordered, specific)
- Data model changes (if any)
- API/interface changes (if any)
- Edge cases to handle
- Testing strategy recommendations
"""
)
```

**When architect completes:** Read the plan, mark Phase 1 complete, assign Phase 2.

### Step 2: Implementer

Spawn with `project-dev:feature-writer` (has Write/Edit/Bash):

```
Task(
  subagent_type="project-dev:feature-writer",
  team_name="swarm-impl",
  name="implementer",
  prompt="""You are the Implementer. Write code following this plan from the Architect:

<paste architect's plan here>

RULES:
- Follow the plan precisely — the Architect already designed the solution
- Write clean, idiomatic code matching existing project patterns
- Do NOT write tests — the Tester handles that
- Do NOT add documentation — the Docs agent handles that
- Commit nothing — leave changes unstaged

When done, send a summary of all files created/modified to the lead.
"""
)
```

### Step 3: Tester

Spawn with `project-dev:test-writer` (has Write/Edit):

```
Task(
  subagent_type="project-dev:test-writer",
  team_name="swarm-impl",
  name="tester",
  prompt="""You are the Tester. Write tests for the implementation just completed.

ARCHITECT'S PLAN:
<paste plan>

FILES MODIFIED BY IMPLEMENTER:
<paste implementer's summary>

RULES:
- Write tests that verify the implementation works correctly
- Cover happy path, edge cases, and error conditions
- Follow existing test patterns in the project (check conftest.py, existing tests)
- Use the project's test framework and fixtures
- Run the tests to verify they pass: use `uv run pytest <test_file> -v`
- If tests fail, fix them until they pass

When done, send test results and file paths to the lead.
"""
)
```

### Step 4a + 4b: Security and QA (parallel)

Spawn BOTH simultaneously — they're read-only reviewers:

```
Task(
  subagent_type="superclaude:security",
  team_name="swarm-impl",
  name="security",
  prompt="""You are the Security Reviewer. Review the implementation for vulnerabilities.

FILES TO REVIEW:
<paste all modified/created files>

CHECK FOR:
- Injection vulnerabilities (SQL, command, XSS)
- Authentication/authorization gaps
- Hardcoded secrets or credentials
- Input validation at system boundaries
- Sensitive data exposure (logging, error messages)
- OWASP Top 10 issues

OUTPUT: Send findings to lead. For each issue:
- Severity (critical/high/medium/low)
- File and line
- Description
- Recommended fix

If no issues found, say "No security issues found."
"""
)

Task(
  subagent_type="superclaude:qa",
  team_name="swarm-impl",
  name="qa",
  prompt="""You are the QA Reviewer. Review the implementation for code quality.

FILES TO REVIEW:
<paste all modified/created files>

CHECK FOR:
- Code follows project conventions and patterns
- No unnecessary complexity or over-engineering
- Error handling is appropriate
- No dead code or unused imports
- Functions are focused (single responsibility)
- Variable/function names are clear
- No duplicated logic
- Edge cases handled

OUTPUT: Send findings to lead. For each issue:
- Severity (must-fix/should-fix/nitpick)
- File and line
- Description
- Recommended fix

If no issues found, say "Code quality looks good."
"""
)
```

### Step 5: Fixer (conditional)

**Only spawn if Security or QA found issues.** Skip if both report clean.

```
Task(
  subagent_type="project-dev:feature-writer",
  team_name="swarm-impl",
  name="fixer",
  prompt="""You are the Fixer. Address these review findings:

SECURITY FINDINGS:
<paste security findings>

QA FINDINGS:
<paste QA findings>

RULES:
- Fix all critical and high severity issues
- Fix must-fix QA items
- Use judgment on medium/should-fix items
- Do NOT refactor beyond what's needed for fixes
- Run affected tests after fixes: `uv run pytest <test_file> -v`

When done, send summary of fixes to the lead.
"""
)
```

**If no issues found:** Mark Phase 5 complete immediately, proceed to Docs.

### Step 6: Docs

Spawn with `general-purpose` (needs Write/Edit for updating files):

```
Task(
  subagent_type="general-purpose",
  model="haiku",
  team_name="swarm-impl",
  name="docs",
  prompt="""You are the Documentation Updater. Update all documentation to reflect the implementation.

WHAT WAS IMPLEMENTED:
<paste architect's plan + implementer's summary>

YOUR TASKS:
1. **Repo documentation** — Update any README, CONTRIBUTING.md, or doc files that reference
   the changed functionality. Only update if the changes affect documented behavior.

2. **Project memory** — Update these files if they exist:
   - hack/PROJECT.md — Add architectural decisions, new patterns, gotchas discovered
   - hack/TODO.md — Check off completed items, add new follow-up items if any
   - hack/SESSIONS.md — Add 3-5 bullet summary of this implementation

3. **Inline documentation** — Only add comments where the logic is non-obvious.
   Do NOT add docstrings to every function. Match existing project style.

RULES:
- Do NOT create new doc files unless the project has none
- Match existing documentation tone and density
- Keep hack/ updates concise (SESSIONS.md is a log, not documentation)
- Only update docs that are actually affected by the changes
"""
)
```

### Step 7: Verifier

Spawn with `test-execution:test-runner` (runs tests, does not edit):

```
Task(
  subagent_type="test-execution:test-runner",
  team_name="swarm-impl",
  name="verifier",
  prompt="""You are the Verifier. Run the full test suite and lint checks.

Run these commands SEQUENTIALLY (not in parallel):

1. Lint check (if project has one):
   - Python: `uv run ruff check .`
   - Or check Makefile for lint target: `make lint`

2. Full test suite:
   - `uv run pytest --tb=short -v`
   - Or check Makefile: `make test`

3. If the project has a combined target: `make all`

REPORT:
- Total tests: X passed, Y failed
- Lint: clean / N issues
- Any failures: specific test paths and error messages

If everything passes, report SUCCESS.
If failures exist, report FAILURE with details so the lead can spawn a fixer.
"""
)
```

### Step 8: Handle Failures

If Verifier reports failures:
1. Spawn another `project-dev:feature-writer` as "fixer-2" with the failure details
2. After fixes, spawn Verifier again
3. Loop until green or 3 iterations max

### Step 9: Shutdown

When all phases complete successfully:
1. Send shutdown requests to all teammates
2. Report final summary to user
3. Delete the team: `TeamDelete()`

## Lead Responsibilities

As the lead, YOU:
- **Create all tasks upfront** with proper `addBlockedBy` dependencies
- **Spawn teammates** as their blocked tasks become unblocked
- **Relay context** between agents — paste the architect's plan into the implementer's prompt, paste file lists into reviewer prompts, etc.
- **Make judgment calls** — if Security says "medium" and QA says "nitpick," skip the fixer
- **Handle the feedback loop** — route review findings back to fixer, re-verify after fixes
- **Never do the work yourself** — every phase gets an agent, even if it seems simple

## Context Bundle Template

Every agent prompt should include this context header:

```
PROJECT: <project name from repo>
TASK: <the user's original request>
CURRENT BRANCH: <git branch>
KEY FILES: <relevant files from architect's analysis>
```

## When to Skip Phases

- **Skip Fixer (Phase 5)** if Security and QA both report clean
- **Skip Docs (Phase 6)** if the change is purely internal with no user-facing behavior
- **Never skip** Architect, Implementer, Tester, or Verifier

## Cost Awareness

This workflow spawns 7-8 agents. Each gets its own context window. This is intentionally expensive — it's the "full send" option for maximum rigor. For simple changes, use regular subagents instead.
