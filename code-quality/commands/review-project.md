---
description: Comprehensive project review with TODO validation and claim verification
---

# Review Project - Comprehensive TODO Validation

Perform comprehensive project review and TODO validation with **actual testing and execution**.

**CRITICAL:** This skill validates FEATURES, not just files. "Encryption works" ≠ "encryption.py exists".

**CAPABILITIES:**
1. **Legacy Migration** - Migrates CONTEXT.md + NOTES.md → PROJECT.md (new format)
2. **Claim Verification** - Verifies what we SAID we did against codebase reality using LSP + grep
3. **Document Hygiene** - Cleans up `hack/` files by removing stale information and consolidating
4. **Session Verification & Archiving** - Verifies session claims, archives old sessions
5. **User Clarification** - Collects ambiguous findings and asks user before making changes

---

## Your Task

Run thorough validation of all items in `hack/TODO.md` by TESTING implementations, not just checking files.

---

## Step 0: Legacy Format Migration

**Before any review, check for legacy file structure:**

If `PROJECT.md` does NOT exist but `CONTEXT.md` or `NOTES.md` exist:

### 0.1 Inform User of Migration

```
Migrating to new project memory format...
- CONTEXT.md + NOTES.md → PROJECT.md
```

### 0.2 Read Both Legacy Files Completely

Read all content from CONTEXT.md and NOTES.md.

### 0.3 Create PROJECT.md with Merged Content

```markdown
# Project

## Overview
[From CONTEXT.md overview/description section]

## Architecture
[From CONTEXT.md architecture section]

## Decisions
[From CONTEXT.md decisions section]

## Gotchas & Discoveries
[All content from NOTES.md]

## Tooling
[From CONTEXT.md tooling section, if exists]
```

### 0.4 Delete Legacy Files

- Delete CONTEXT.md
- Delete NOTES.md

### 0.5 Report Migration

```
✓ Migrated to new format:
  - Created PROJECT.md (merged from CONTEXT.md + NOTES.md)
  - Deleted CONTEXT.md
  - Deleted NOTES.md

Continuing with project review...
```

### 0.6 Continue with Remaining Review Steps Using PROJECT.md

---

## Step 1: Claim Verification (Use LSP + Grep)

**CRITICAL:** Verify what we've SAID we did actually matches the codebase reality.

### 1a. Extract Claims from Project Memory

**Sources to parse:**
1. **hack/PROJECT.md** - Architecture decisions, migrations, removals, gotchas
2. **hack/SESSIONS.md** - Session summaries of work completed
3. **hack/TODO.md** - Completed items (marked `[x]`)

**Example claims to extract:**
- "Migrated all fonts from Roboto to Inter"
- "Removed all jQuery dependencies"
- "Replaced all /v1/ API endpoints with /v2/"

### 1b. Design Verification Strategy Per Claim

**For code symbol claims (functions, classes, imports):**
Use LSP: `workspaceSymbol`, `findReferences`, `goToImplementation`

**For text/pattern claims (libraries, fonts, URLs):**
Use grep: `grep -ri "pattern" .`

**For structural claims (files, directories):**
Use file operations: `ls`, `find`

### 1c. Execute Verification

For each claim:
1. Choose primary search method (LSP, grep, or file ops)
2. Execute comprehensive search across codebase
3. Filter results to distinguish:
   - Active code (src/, apps/, lib/) - POTENTIAL ISSUES
   - Archived code (archive/, old/) - LIKELY INTENTIONAL
   - Tests (tests/) - USUALLY SAFE TO IGNORE
   - Dependencies (node_modules/, .venv/) - IGNORE

### 1d. Collect Findings (DO NOT AUTO-FIX)

For each discrepancy found, collect for user clarification later:
- File path and line number
- Whether file appears active or archived
- Suggested action (but don't execute)

---

## Step 2: Document Hygiene

### 2a. Content Placement Audit

**Verify files contain appropriate content per guidelines:**

**SESSIONS.md Audit:**
- Each entry should be 3-5 bullets max
- Flag detailed entries: "This session entry has implementation details that belong in PROJECT.md"
- Flag task lists: "This session entry has TODOs that belong in TODO.md"

**NEXT.md Audit:**
- Should be a pointer only, not detailed description
- Flag if contains implementation details
- Flag if contains decisions/rationale (→ PROJECT.md)

**PROJECT.md Cleanup:**
- Is content still accurate?
- Any duplicated sections?
- Any outdated information?

### 2b. Session Verification (Before Archiving)

**For each session in SESSIONS.md, verify claimed work was done:**

1. Extract the claimed accomplishments (bullets)
2. Verify with codebase evidence:
   - Run related tests if applicable
   - Check git log for related commits
   - Verify feature actually exists/works
3. If NOT implemented: Flag for follow-up, do NOT archive

**Example:**
```
Session: "2026-01-08 - Implemented password reset flow"
Verify:
- grep -r "password_reset" apps/ → Found views ✓
- git log --oneline --grep="password reset" → 3 commits found ✓
→ VERIFIED: Safe to archive
```

### 2c. Session Archiving

**After verification, archive all except last 2-3 sessions:**

1. Create archive file: `hack/archived/SESSIONS-archive-[YYYY-MM].md`
2. Move verified sessions older than last 3 to archive
3. Add footer to SESSIONS.md:

```markdown
---
## Archived Sessions
Previous sessions archived to: `hack/archived/SESSIONS-archive-*.md`
Note: These should rarely be needed. If you find yourself referencing
archives frequently, important context may be missing from PROJECT.md.
```

---

## Step 3: TODO Validation

### 3a. Validate Completed Items

For each `[x]` item in TODO.md:

1. Verify the feature actually works (run tests, execute code)
2. Check that code was committed
3. Confirm session date reference exists

**Report format:**
- **VERIFIED COMPLETE** - Feature works, tests pass
- **MISSING** - Item marked done but feature doesn't work
- **PARTIAL** - Some parts work, some don't

### 3b. Validate Uncompleted Items

For each `[ ]` item:

1. Test if feature actually works (might already be done)
2. Check for duplicates
3. Identify superseded items

**Report format:**
- **ACTUALLY DONE** - Should be marked [x] with date
- **TRULY INCOMPLETE** - Confirm incomplete
- **NO LONGER RELEVANT** - Feature superseded
- **DUPLICATE** - Same as another item

---

## Step 4: User Clarification (CRITICAL)

**BEFORE making any changes, present collected ambiguities to the user.**

### Collect Questions About:

1. **Intentional vs Oversight**
   - "Found 'jQuery' in archive/demo.html. Is this intentionally preserved or should it be removed?"
   - "SESSIONS.md entry from 2025-11 has detailed implementation notes. Migrate to PROJECT.md or delete?"

2. **Stale vs Still Relevant**
   - "PROJECT.md mentions 'evaluating Stripe vs PayPal' but only Stripe is in codebase. Update to reflect decision, or is evaluation ongoing?"

3. **Conflicting Information**
   - "TODO says 'password reset done' but no tests found. Mark as incomplete, or are tests elsewhere?"

4. **Archive Decisions**
   - "Ready to archive 8 sessions (older than 3). Proceed, or keep specific sessions?"

### Format Questions Using AskUserQuestion

```
Question 1: "Found jQuery in archive/demo.html - what should happen?"
- Option A: "Remove it (was oversight)"
- Option B: "Keep it (intentionally preserved)"
- Option C: "Document as known exception in PROJECT.md"

Question 2: "Ready to archive 8 old sessions. Proceed?"
- Option A: "Yes, archive all"
- Option B: "Keep specific session(s)"
- Option C: "Don't archive yet"
```

### NEVER Assume:
- Old = deletable
- Missing = incomplete
- Archived = safe to ignore
- Conflict = error to fix

### Always Ask When:
- Action could delete information
- Finding could be intentional exception
- Multiple valid interpretations exist
- Change affects project direction

---

## Step 5: Execute Changes (After User Approval)

Only after user has answered clarification questions:

1. Apply approved changes to PROJECT.md
2. Archive sessions as approved
3. Update TODO.md with corrections
4. Fix content placement issues as approved

---

## Step 6: Generate Report

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 PROJECT REVIEW COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Legacy Migration
[If performed: Created PROJECT.md, deleted CONTEXT.md + NOTES.md]

## Claim Verification
- Claims verified: X
- Fully verified: Y ✓
- Discrepancies found: Z ❌
- User decisions applied: W

## Document Hygiene
- Content placement issues fixed: X
- Stale content removed: Y
- Sessions archived: Z

## TODO Validation
- Completed items verified: X/Y
- Items marked as done: Z
- Items removed as irrelevant: W

## Files Modified
- PROJECT.md: [changes]
- TODO.md: [changes]
- SESSIONS.md: [changes]
- hack/archived/: [if created]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Important Notes

- **User approval required:** Never auto-fix ambiguous findings
- **Verify before archive:** Confirm session work was actually done
- **PROJECT.md is authoritative:** All decisions and gotchas belong there
- **SESSIONS.md is a log:** 3-5 bullets max, archive old sessions
- **TODO items need dates:** Completed items reference session for traceability
- Run every 5-10 sessions to prevent documentation rot
- Use Opus model for comprehensive analysis when available
