---
name: completion-review
description: |
  Post-implementation and artifact quality gate. PROACTIVE — invoke when:
  - About to claim implementation work is complete
  - After team/subagent-driven development produces code changes
  - Before committing, creating PRs, or moving to next task
  - After completing a major feature or multi-file change
  - A subagent just completed and returned modified files
  - Reviewing non-code artifacts: plan files, specs, documentation, configs
  - User asks for a "comprehensive review" of any file or artifact
  Combines code quality analysis, dead code detection, document quality review,
  subagent output validation, and improvement application into a single review
  pass. Works on both code and non-code files.
---

# Completion Review

Quality gate for code and non-code artifacts. Run as a single comprehensive review pass.

## When to Invoke

**Mandatory before:**
- Claiming any implementation is complete
- Committing code from team/subagent work
- Creating PRs
- Moving to the next task after significant changes (3+ file edits)

**Also invoke for non-code artifacts when:**
- User asks for a comprehensive review of a plan, spec, or document
- A plan file in `hack/plans/` has been written or significantly updated
- Configuration files (YAML, JSON, TOML) have been created or changed
- Any artifact where quality, completeness, or correctness matters

**Skip when:**
- Only research/exploration was done (no Write/Edit calls)
- Single-line fixes with no structural impact

---

## Review Pipeline

Execute these steps in order. Each step builds on the previous.

### Step 1: Identify Files to Review

For code changes:
```bash
git diff --name-only          # Unstaged changes
git diff --cached --name-only # Staged changes
```

For non-code artifacts: use the specific file(s) the user references or that were just created/modified.

If no files to review, stop here.

### Step 2: Classify and Route

Separate files into two categories and apply the appropriate review track:

- **Code files** (`.py`, `.ts`, `.js`, `.go`, `.rs`, etc.) → Steps 3-4
- **Non-code artifacts** (`.md`, `.yaml`, `.json`, `.toml`, plan files, specs) → Step 3A

### Step 3: Code Quality Analysis

Invoke Skill `sc:analyze` on all modified code files. This covers:
- Code quality and readability
- Security vulnerabilities
- Performance concerns
- Architecture alignment

### Step 3A: Artifact Quality Analysis

For non-code files (plans, specs, configs, documentation), review across **all** dimensions — both document quality and substantive technical concerns.

**Document quality:**

| Aspect | What to Check |
|--------|---------------|
| **Completeness** | All sections filled in, no TBD/placeholder content, no gaps in coverage |
| **Consistency** | Terminology used consistently, no contradictions between sections |
| **Actionability** | Steps are concrete and unambiguous, not vague hand-waving |
| **Accuracy** | References to code, files, APIs match what actually exists in the codebase |
| **Structure** | Logical flow, appropriate level of detail, no redundant sections |
| **Staleness** | No references to removed code, old APIs, or outdated decisions |

**Security review:**
- Does the design introduce attack surface (injection points, auth gaps, privilege escalation)?
- Are secrets, credentials, or tokens handled safely (not hardcoded, not logged)?
- Are trust boundaries identified and enforced?
- Does the plan account for input validation at system boundaries?
- Config files: no secrets or credentials present, no overly permissive settings

**Performance review:**
- Are there N+1 patterns, unbounded loops, or missing pagination in the design?
- Does the approach scale with data/user growth?
- Are caching, batching, or lazy-loading strategies considered where relevant?
- Config files: no settings that degrade performance (excessive logging, debug mode, missing limits)

**Architecture review:**
- Does the design fit the existing codebase patterns and conventions?
- Are responsibilities cleanly separated (no god objects, no tangled dependencies)?
- Are integration points with existing code identified and compatible?
- Does the approach introduce unnecessary coupling or complexity?
- Will this be maintainable by someone unfamiliar with the original context?

For **plan files** specifically, also check:
- Each step has clear inputs and outputs
- Dependencies between steps are identified
- Edge cases and failure modes are addressed
- Scope matches the original request (no scope creep, no missing pieces)

For **config files** (YAML, JSON, TOML), also check:
- Valid syntax
- No commented-out blocks left as dead config
- Values match the environment/context they target

### Step 4: Apply Improvements

Invoke Skill `sc:improve` on all modified code. **Specifically target:**

| Issue | Action |
|-------|--------|
| Unused functions, unreachable branches | Delete entirely |
| Commented-out code blocks | Delete — VCS has history |
| Unused imports or variables | Remove |
| Renamed unused `_vars` (removed code artifacts) | Delete the variable |
| Re-exports for removed symbols | Delete the re-export |
| Compatibility layers with no consumers | Delete |
| Functions that merely delegate without adding value | Inline or delete |
| TODO/FIXME/HACK comments | Delete unless explicitly deferred by user |
| Placeholder or stub implementations | Complete or delete |

For non-code artifacts, apply improvements directly (fix issues found in Step 3A) rather than invoking `sc:improve`.

### Step 5: Completeness Check

Review all changes against the original user request:
- Does the implementation fully address what was asked?
- Are there gaps or missing edge cases?
- Ask the user if anything is missing

### Step 6: Reflection

Invoke Skill `sc:reflect` for final task reflection and validation.

### Step 7: Preserve Context

If subagents produced architecture findings, codebase structure, or key decisions:
- Save to `hack/PROJECT.md` or Serena memories
- Compaction can happen at any time and will erase unsaved context
- Update `hack/TODO.md` with completed/new tasks if applicable

---

## Subagent Output Validation

When the work being reviewed was produced by subagents (Task tool), apply extra scrutiny:

1. **Don't trust agent success reports** — verify via VCS diff
2. **Check each modified file** for the quality issues in Step 3
3. **Verify tests exist** when the task involved testable logic
4. **Confirm no regressions** — run test suite if applicable

---

## Output

After completing the review, summarize:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMPLETION REVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Files reviewed: [count]
Issues found: [count]
Improvements applied: [count]

Quality: [PASS | NEEDS WORK]
Completeness: [COMPLETE | GAPS FOUND]

[If gaps: list what's missing]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Relationship to Other Skills

| Skill | Relationship |
|-------|-------------|
| `verification-before-completion` | Complementary — verifies command output; this verifies code quality |
| `requesting-code-review` | Use AFTER this skill for external review; this is self-review |
| `session-end` | Run AFTER this skill for hack/ updates; this handles code quality |
