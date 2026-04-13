---
name: bug-investigation
description: >-
  Interactive bug investigation workflow using background agents. Use when the user says
  "I'm going to report bugs", "let's hunt bugs", "investigate these issues", or describes
  wanting to report issues one-by-one while agents investigate in parallel. Also activates
  when the user reports a bug/issue and a BUGS.md file exists or was recently created.
allowed-tools: [Read, Write, Edit, Glob, Grep, Agent, Bash, AskUserQuestion]
---

# Bug Investigation Workflow

Interactive workflow for reporting, investigating, and tracking bugs using background agents.
The user reports bugs conversationally while background agents investigate each one
autonomously, documenting root causes and resolution plans in a central tracking file.

## Role

You are a **coordination agent**. Your sole responsibilities are: dispatching investigation
agents, acknowledging bug reports, and summarizing results. You do not read source code,
trace call chains, or analyze root causes — that work belongs to the background investigation
agents you spawn.

## Activation

This skill activates when:
- User says they want to report bugs/issues for investigation
- User describes a bug and a `BUGS.md` exists in the memory directory or needs to be created
- User asks to "hunt bugs", "audit the UI", or "investigate issues"

## Phase 1: Setup

### Check for Existing BUGS.md

Before starting, detect the memory directory using the convention in
`code-quality/references/project-memory-reference.md` (Directory Detection section).
Then check if `{memory_dir}/BUGS.md` already exists:

```
Read {memory_dir}/BUGS.md
```

**If it exists:** Review it for stale entries.
- Entries with **Status: Fixed** should be removed (the fix is in git history)
- Entries with **Status: Fix Ready** should be confirmed — is the fix actually applied?
  - If yes, remove the entry
  - If no, keep it
- Entries with **Status: Root Cause Found** or **Investigating** stay as-is
- Report the cleanup to the user: "Cleaned BUGS.md: removed N fixed entries, M remain active"
- Entries missing the `**Tracked In:**` field (pre-existing entries created before this field
  was added): add `**Tracked In:** —` after the `**Impact:**` (or `**Severity:**`) line.
  This ensures all entries are compatible with cross-skill tracking.
- Determine the next bug ID from the highest existing BUG-NNN + 1

**If it doesn't exist:** Create it with the template below and confirm to the user.
**If no memory directory found:** Skip BUGS.md and notify the user that tracking requires a memory directory.

### BUGS.md Template

> **Note on Impact vs Classification:** The `Impact` field (Critical/High/Medium/Low) describes
> the bug's effect on users — it is NOT finding classification. All bugs are `needs-fix` by
> definition; Impact conveys urgency and scope. See
> `code-quality/references/finding-classification.md` for the classification taxonomy.

```markdown
# Bug Investigation & Resolution Tracking

Tracking document for bugs/issues. Each entry is investigated by a background agent
and documented here with root cause analysis and resolution plan.

---

<!-- Investigation agents: Add new entries at the top using this template:

## BUG-NNN: <Short Title>

**Status:** Investigating | Root Cause Found | Fix Ready | Fixed
**Reported:** YYYY-MM-DD
**Impact:** Critical | High | Medium | Low
**Tracked In:** — | Plan: {path} | Roadmap: {path} (Phase N) | Branch: {name} | PR: #N | PR: #N (merged YYYY-MM-DD)

### Problem
<What the user observed — plain language>

### Root Cause
<Specific code-level explanation with file:line references>

### Files Involved
- `path/to/file.py:NN` — description of relevance

### Resolution Plan
- [ ] Step 1 (specific, actionable)
- [ ] Step 2

### Notes
<Additional context, edge cases, related issues>

---
-->
```

### Acknowledge Setup

After setup, confirm readiness briefly:

```
BUGS.md is ready (N existing entries, next ID: BUG-NNN). Report bugs as you find them —
I'll spawn background investigation agents for each one.
```

## Phase 2: Bug Reporting Loop

**Do not investigate.** When a bug is reported, assign an ID and spawn a background agent
immediately. Your value is speed of dispatch, not analysis. Do not read source code files,
trace call chains, or hypothesize about root causes — the investigation agent handles all of that.

When the user reports a bug or issue:

### 1. Assign a Bug ID

Assign the next sequential BUG-NNN ID. Track the counter internally.

### 2. Spawn a Background Investigation Agent

Use the Agent tool with these parameters:
- `subagent_type: "general-purpose"`
- `run_in_background: true`
- `description: "BUG-NNN: <short title>"`
- `mode: "bypassPermissions"`

### 3. Agent Prompt Structure

Each investigation agent receives this prompt:

```
You are investigating a bug for [project description] at [absolute project path].

## Issue
"[user's exact words or close paraphrase]"

[1-2 sentences of additional context — what the system does, what the user expected]

## Your Task
1. [Specific investigation steps — which models, services, components, files to check]
2. [What to look for — the likely root cause area based on the issue description]
3. [What relationships to trace — data flow, references, rendering pipeline]
4. [Verify the issue — confirm the bug exists by reading the relevant code paths]

## Output
READ [absolute path to {memory_dir}/BUGS.md] first to find the insertion point, then EDIT it
to insert a new entry at the top (after the HTML template comment, before the first
existing BUG entry). Use this exact format:

## BUG-NNN: <Short Title>

**Status:** Root Cause Found
**Reported:** [today's date]
**Impact:** Critical | High | Medium | Low
**Tracked In:** —

### Problem
<What the user observed — plain language>

### Root Cause
<Specific code-level explanation with file:line references>

### Files Involved
- `path/to/file.py:NN` — description of relevance

### Resolution Plan
- [ ] Step 1 (specific, actionable)
- [ ] Step 2

### Notes
<Additional context, edge cases, related issues>

---

If you cannot determine the root cause, set Status to "Investigating" and document
what you found and what remains unclear.

Do not fabricate root causes — an honest "Investigating" status is better than a fabricated
"Root Cause Found" with speculative evidence. False diagnoses cost more than open investigations.
```

### 4. Batch Multiple Bugs

If the user reports multiple bugs in a single message, launch ALL investigation agents
in parallel (single message, multiple Agent tool calls). Each gets its own sequential
BUG-NNN ID.

### 5. Acknowledge Briefly

After launching agent(s), confirm with a brief acknowledgment:

```
BUG-014 launched — investigating [short description].
```

Do NOT:
- Repeat the user's bug description back to them
- Speculate about the root cause
- Provide a lengthy response
- Investigate the bug yourself (read source files, trace code paths, analyze root causes)

The user wants to keep reporting bugs. Stay out of their way.

### 6. Agent Completion Summaries

When a background agent completes, summarize in 1-2 sentences:

```
BUG-014 complete — [impact]. Root cause: [one sentence]. See BUGS.md for details.
```

Do NOT dump the full agent report into the conversation.

## Phase 3: Review & Cleanup

When the user is done reporting bugs or asks to review status:

1. Read `{memory_dir}/BUGS.md` and summarize:
   - Total bugs documented
   - Breakdown by impact (Critical/High/Medium/Low)
   - Breakdown by status (Investigating/Root Cause Found/Fix Ready/Fixed)
   - Breakdown by Tracked In state (untracked/plan/roadmap/branch/PR/merged)
   - Any agents still running

2. If the user asks to clean up:
   - Remove entries with **Status: Fixed** (the fixes are in git history)
   - Verify **Status: Fix Ready** entries — confirm the fix was applied
   - Keep all other entries

## Agent Prompt Guidelines

When constructing investigation prompts, follow these principles:

### Be Specific About Where to Look

Bad:
```
Check the codebase for the issue.
```

Good:
```
1. Check the Owner model in `backend/components/models.py` — what fields does it have?
2. Check how owners are created in `backend/sync/services/async_github_sync.py`
3. Look at the frontend rendering in `frontend/src/pages/ComponentDetail.tsx`
```

### Include Context About the System

The agent starts fresh with no conversation context. Include:
- Project type (Django + React, Rails, etc.)
- Absolute project path
- What the relevant subsystem does
- What the user expected vs what happened

### Direct the Output Location

Always include the absolute path to `{memory_dir}/BUGS.md` and the exact BUG-NNN ID to use.
Agents should READ the file first (to find the insertion point) then EDIT it.

### Keep Investigation Focused

Each agent investigates ONE bug. Don't combine multiple issues into a single agent.
The agent should:
1. Understand the problem
2. Read relevant code
3. Identify the root cause
4. Document findings in BUGS.md
5. Exit

## File Conflict Prevention

Multiple background agents writing to the same BUGS.md can cause conflicts.
Mitigation strategies:

- Each agent writes to a DIFFERENT section (its own BUG-NNN heading)
- Agents READ the file first to find the correct insertion point
- If launching many agents (5+), stagger launches slightly or have agents
  append to the end instead of inserting at the top
- For very high-volume sessions, consider having agents write to individual
  files (`{memory_dir}/bugs/BUG-NNN.md`) and consolidate afterward

## Quick Reference

### Setup
```
1. Detect memory dir (per project-memory-reference.md) → check for {memory_dir}/BUGS.md → clean up or create
2. Determine next BUG-NNN ID
3. Confirm readiness to user
```

### Per Bug
```
1. Assign BUG-NNN
2. Agent(subagent_type="general-purpose", run_in_background=true, description="BUG-NNN: title")
3. Acknowledge: "BUG-NNN launched — investigating [title]"
```

### Completion
```
1. Summarize: "BUG-NNN complete — [impact]. Root cause: [one sentence]"
2. Don't dump full report
```

### Cleanup
```
1. Remove Fixed entries
2. Verify Fix Ready entries
3. Keep Investigating/Root Cause Found entries
4. Migrate pre-existing entries missing `Tracked In:` → add `**Tracked In:** —` after Impact or Severity line
```

After bugs are documented with "Root Cause Found" status, run `/fix` to implement resolution
plans. For complex bugs (5+ files, architectural changes), `/fix` will recommend
`/incremental-planning` instead.
