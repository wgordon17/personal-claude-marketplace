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

### Step 5: Challenge Deferred Work

Search all modified files and conversation context for deferred work. **Bias toward implementation — defer only when there is a genuine, concrete reason.**

**Scan for deferral signals:**
- `TODO`, `FIXME`, `HACK`, `XXX` comments
- "later", "for now", "temporarily", "future work", "out of scope", "follow-up"
- Placeholder implementations, stub functions, empty catch blocks
- Plan steps marked as "optional" or "stretch goal"
- Items moved to a backlog or "phase 2" without justification

**For each deferred item, evaluate:**

| Question | If YES → | If NO → |
|----------|----------|---------|
| Can it be implemented right now with reasonable effort? | Implement it | Defer is acceptable |
| Is it part of the original user request? | Must implement — deferring violates the request | Defer is acceptable |
| Does deferring it leave the system in a broken or inconsistent state? | Must implement | Defer is acceptable |
| Is the deferral reason vague ("we can do this later", "out of scope")? | Challenge — ask user | — |

**When deferral reason is ambiguous or weak:**
Use `AskUserQuestion` to present the deferred item and ask the user:
- "This was deferred because [reason]. Should I implement it now or is deferring intentional?"
- Include concrete options: implement now, defer with explicit rationale, or remove entirely

**Legitimate deferral reasons** (do not challenge these):
- Requires external dependency not yet available
- User explicitly said to skip it
- Blocked by another task that hasn't been completed
- Would require a separate design decision the user hasn't made

### Step 6: AI Slop Detection and Removal

Scan all modified files (code and non-code) for AI-generated filler that adds no value. **Remove or rewrite every instance found.**

**In code — detect and remove:**

| Pattern | Example | Fix |
|---------|---------|-----|
| Narrating the obvious | `# Increment the counter` above `counter += 1` | Delete the comment |
| Sycophantic variable names | `beautiful_handler`, `elegant_solution` | Rename to describe function |
| Filler docstrings | `"""This function does what it does."""` | Delete or write a real one |
| Hedge comments | `# This should work`, `# Might need adjustment` | Delete — either fix it or it's fine |
| Ceremonial error handling | `try/except` that just re-raises or logs and continues | Remove the try/except |
| Over-abstraction | `AbstractBaseSingletonFactoryManager` for one use case | Inline it |
| Unnecessary type aliases | `StringList = List[str]` used once | Use the type directly |
| Aspirational code | Functions that exist but are never called | Delete |

**In prose (plans, docs, specs) — detect and rewrite:**

| Pattern | Example | Fix |
|---------|---------|-----|
| Filler phrases | "It's worth noting that", "It's important to understand that" | Delete — just state the thing |
| Unnecessary hedging | "This could potentially maybe help with" | State it directly or remove |
| Redundant transitions | "Now, let's move on to discuss the next topic" | Delete |
| Empty superlatives | "This robust, scalable, enterprise-grade solution" | Describe what it actually does |
| Echoing the question | "Great question! When it comes to X, X is..." | Just answer |
| Padding sections | Paragraphs that restate the heading without adding info | Delete |
| Mealy-mouthed conclusions | "In conclusion, we have successfully outlined..." | Delete or state the actual conclusion |
| Bullet point bloat | 10 bullets where 3 carry the meaning | Consolidate to the meaningful ones |

**Tone check:** The output should sound like it was written by a competent engineer, not generated by a chatbot trying to sound helpful. Direct, concrete, no filler.

### Step 7: Completeness Check

Review all changes against the original user request:
- Does the implementation fully address what was asked?
- Are there gaps or missing edge cases?
- Ask the user if anything is missing

### Step 8: Reflection

Invoke Skill `sc:reflect` for final task reflection and validation.

### Step 9: Preserve Context

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
Deferred items challenged: [count]
AI slop removed: [count]

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
