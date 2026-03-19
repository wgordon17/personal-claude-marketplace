---
description: Sync project memory and update hack/ files before ending session
---

# Session End — Sync Project Memory

Before ending this session, update the project's persistent memory in `hack/` (or equivalent directory).

## Your Task

### Step 1: Locate Memory Directory

Check for existing directories in this order:
1. `hack/`
2. `.local/`
3. `scratch/`
4. `.dev/`

If none exist and this session had meaningful work, create `hack/` and add it to `.gitignore`.

### Step 2: Read Current State

Read all markdown files in the memory directory to understand what's already documented.

---

## Content Placement Rules (CRITICAL)

**Where information belongs:**

| Content Type | Correct File | WRONG Files |
|--------------|--------------|-------------|
| Decisions, rationale, gotchas | PROJECT.md | SESSIONS.md, NEXT.md |
| Technical discoveries | PROJECT.md | SESSIONS.md |
| Future tasks/todos | TODO.md | SESSIONS.md, NEXT.md |
| What was done (3-5 bullets) | SESSIONS.md | - |
| Next direction pointer | NEXT.md | - |
| Principle-level lessons | LESSONS.md | PROJECT.md, SESSIONS.md |

---

## Step 3: Update Memory Files

### PROJECT.md — All project knowledge

Update these sections as needed:
- **Overview** — Only if project scope changed
- **Architecture** — If structural decisions were made
- **Decisions** — New decisions with rationale (include date)
- **Gotchas & Discoveries** — New things learned this session
- **Tooling** — If tooling preferences changed

Keep it current: Remove outdated info, update stale sections.

### TODO.md — Task list with session references

**Format requirements:**
- Use markdown checkboxes: `- [ ]` for active, `- [x]` for completed
- **Completed items MUST include session date:**

```markdown
## Active
- [ ] Add email verification
- [ ] Implement rate limiting

## Completed
- [x] Add password reset flow (2026-01-10)
- [x] Fix login redirect bug (2026-01-10)
- [x] Set up CI/CD pipeline (2026-01-08)
```

**Update process:**
1. Mark completed tasks with `[x]` and add today's date
2. Move completed tasks to bottom (under "Completed" section)
3. Add new tasks discovered during this session
4. Keep active tasks at top, prioritized

### SESSIONS.md — High-level summary (3-5 bullets max)

**Format:**
```markdown
## [Date]
- Main accomplishment 1
- Main accomplishment 2
- Main accomplishment 3
```

**FORBIDDEN in SESSIONS.md:**
- Implementation details (→ PROJECT.md)
- Technical decisions (→ PROJECT.md)
- Task lists or todos (→ TODO.md)
- Code snippets or commands
- Detailed explanations

**Example:**
```markdown
## 2026-01-10
- Implemented user authentication flow
- Fixed 3 login-related bugs
- Added password reset email template
```

**NOT this:**
```markdown
## 2026-01-10
- Added JWT tokens using the HS256 algorithm
- Decided to store tokens in httpOnly cookies because of XSS concerns
- Created AuthService class with methods for login, logout, refresh...
```

### NEXT.md — Pointer to next direction ONLY

**Contains:**
- Reference to specific TODO item(s) to work on next
- Brief context if needed (1 sentence max)
- Optionally: 2-3 options for user to choose from

**FORBIDDEN in NEXT.md:**
- Implementation details
- Full task descriptions (those belong in TODO.md)
- Decisions or rationale (→ PROJECT.md)

**Example:**
```markdown
## Next Session Focus

Continue with: "Add password reset flow" (TODO)

Or choose from:
1. Password reset flow
2. Email verification
3. User preferences page
```

---

## Step 3.5: Extract Lessons

After updating memory files, scan the session for lessons worth capturing:

**What to look for:**
- Human corrections ("that's wrong", "actually...", "no, use X instead")
- Rejected approaches (tried X, didn't work, switched to Y)
- Feedback patterns (repeated similar feedback across tasks)
- Surprises (things that behaved differently than expected)
- Tool or process improvements discovered during the session

**Extraction process:**
1. Identify principle-level patterns (not implementation-specific details)
2. Format as: `- [Category] Pattern → What to do differently → Why it matters (YYYY-MM-DD)`
3. Check existing `hack/LESSONS.md` for duplicates or contradictions
4. If contradicting an existing lesson, mark the old one `[SUPERSEDED by YYYY-MM-DD]`
5. Append new lessons to the `## Active` section of `hack/LESSONS.md`
6. If `hack/LESSONS.md` doesn't exist, create it with the Active and Archived sections

**Context compaction note:** In long sessions, early human corrections may be compacted
out of context. If the session was long, ask the user:
"Were there corrections or feedback I should capture as lessons? My context may not include the full session."

**Skip when:** Session was pure Q&A with no corrections, no rejected approaches, and no
surprising discoveries.

---

## Step 4: Determine Next Focus

After updating TODO.md, determine what NEXT.md should contain:

**Option A (Single clear next):**
If there's an obvious next priority, update NEXT.md to point to it.

**Option B (Multiple options):**
If unclear, use AskUserQuestion:
"What should be the focus for the next session?"
- Option 1: [High priority TODO item]
- Option 2: [Another candidate]
- Option 3: [Third option or "other"]

Capture user's choice (or present 2-3 options) in NEXT.md for next session.

---

## Step 5: Verify

- Ensure no stale or contradictory information remains
- Confirm a new session could pick up seamlessly from this documentation
- Verify completed TODO items have session date references

---

## Output

After updating, provide a brief summary:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ SESSION SYNCED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Files updated:
- PROJECT.md: [what was added/changed]
- TODO.md: [X tasks completed, Y new tasks added]
- SESSIONS.md: [today's summary added]
- NEXT.md: [next focus set]
- LESSONS.md: [N new lessons captured | no new lessons]

Next session focus:
[From NEXT.md]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Important Notes

- **Content placement matters:** Decisions go in PROJECT.md, not SESSIONS.md
- **SESSIONS.md is a log:** 3-5 bullets max, no details
- **TODO items need dates:** Completed items reference session for traceability
- **NEXT.md is a pointer:** Reference to TODO items, not full descriptions
- **LESSONS.md is principle-level:** No code, no file paths, no implementation details
- **Ask when unclear:** Use AskUserQuestion to determine next focus if multiple options
