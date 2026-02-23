---
name: completion-review
description: |
  Post-implementation quality gate. PROACTIVE — invoke when:
  - About to claim implementation work is complete
  - After team/subagent-driven development produces code changes
  - Before committing, creating PRs, or moving to next task
  - After completing a major feature or multi-file change
  - A subagent just completed and returned modified files
  Combines code quality analysis, dead code detection, subagent output validation,
  and improvement application into a single review pass. Replaces former Stop and
  SubagentStop hooks with an on-demand skill.
---

# Completion Review

Post-implementation quality gate. Run this as a single comprehensive review pass instead of per-turn automatic hooks.

## When to Invoke

**Mandatory before:**
- Claiming any implementation is complete
- Committing code from team/subagent work
- Creating PRs
- Moving to the next task after significant changes (3+ file edits)

**Skip when:**
- Only research/exploration was done (no Write/Edit calls)
- Only documentation or markdown was updated
- Single-line fixes with no structural impact

---

## Review Pipeline

Execute these steps in order. Each step builds on the previous.

### Step 1: Identify Modified Files

```bash
git diff --name-only          # Unstaged changes
git diff --cached --name-only # Staged changes
```

If no modified files, stop here — nothing to review.

### Step 2: Quality Analysis

Invoke Skill `sc:analyze` on all modified code files. This covers:
- Code quality and readability
- Security vulnerabilities
- Performance concerns
- Architecture alignment

### Step 3: Apply Improvements

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

### Step 4: Completeness Check

Review all changes against the original user request:
- Does the implementation fully address what was asked?
- Are there gaps or missing edge cases?
- Ask the user if anything is missing

### Step 5: Reflection

Invoke Skill `sc:reflect` for final task reflection and validation.

### Step 6: Preserve Context

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
