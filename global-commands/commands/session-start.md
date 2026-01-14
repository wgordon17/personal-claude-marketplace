---
description: Start a session by loading project context or initializing new project
---

# Session Start — Load Context or Initialize Project

Prepare for productive work by loading existing project memory or setting up a new project.

## Your Task

### Step 1: Detect Project State

Check for existing memory directories in this order:
1. `hack/`
2. `.local/`
3. `scratch/`
4. `.dev/`

**If memory directory exists:** → Execute **Existing Project Flow**
**If no memory directory:** → Execute **New Project Flow**

---

## Existing Project Flow

### Step 1: Load Project Memory (Selective)

**Read ONLY these files:**
- `NEXT.md` — Immediate focus for this session
- `PROJECT.md` — Project knowledge (overview, architecture, decisions, gotchas)
- `WORK_ETHIC.md` — Agent behavior rules (if exists)

**DO NOT read at startup:**
- `TODO.md` — Only consult when user asks "what else to work on"
- `SESSIONS.md` — Only consult when historical context is explicitly needed

### Step 2: Check Project Standards

**Read CONTRIBUTING.md** (if exists) to understand:
- Commit message conventions
- Defined scopes
- Development workflow

### Step 3: Check Git State

```bash
git status
git branch
git log --oneline -5
```

Understand:
- Current branch
- Uncommitted changes
- Recent commits

### Step 4: Present Project Orientation

Provide a structured summary:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📂 PROJECT: [Project Name from PROJECT.md or directory name]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 IMMEDIATE FOCUS (from NEXT.md):
[Specific next actions - pointer to TODO item]

📊 PROJECT CONTEXT (from PROJECT.md):
[Brief summary of current state]

⚠️ GOTCHAS (from PROJECT.md):
[Key things to watch out for]

🌿 GIT STATUS:
Branch: [branch name]
Changes: [summary]
Last commit: [last commit message]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ready to work! Start with the immediate focus above.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## New Project Flow

### Step 1: Confirm New Project Setup

Ask the user:
```
This appears to be a new project. Would you like me to:
1. Set up project memory (hack/ directory)?
2. Initialize CONTRIBUTING.md with commit standards?
3. Both?
```

If user declines, exit gracefully.

### Step 2: Interview About the Project

Ask targeted questions:

**About the project:**
- What is this project? (1-2 sentence description)
- What language/framework? (Python, Go, TypeScript, etc.)
- What's the primary goal or immediate milestone?

**About commit conventions:**
- Do you want to generate CONTRIBUTING.md now? (recommend yes)
- Any specific scopes you already know about? (optional)

**About initial setup:**
- Any immediate tasks to track?
- Any architectural decisions made?

### Step 3: Create Memory Directory

```bash
mkdir -p hack
echo "hack/" >> .gitignore
```

Or use `.local/`, `scratch/`, or `.dev/` if user prefers.

### Step 4: Initialize Memory Files

**NEXT.md:**
```markdown
# Next Steps

## Immediate Focus

[First concrete action based on user's goal]
```

**PROJECT.md:**
```markdown
# Project

## Overview
[Project description from interview]
[Language/Framework]
[Primary goal/milestone]

## Architecture
[Initial architectural decisions, if any]

## Decisions
### [Date] - Initial Setup
- Language/Framework: [from interview]
- Memory system: hack/ directory
- Commit standards: [if CONTRIBUTING.md created]

## Gotchas & Discoveries
[Leave space for discoveries]

## Tooling
[For Python projects:]
**CRITICAL: ALWAYS use `uv run` instead of direct `python` invocation**
- Use: `uv run script.py` NOT `python script.py`
- Use: `uv add package` NOT `pip install package`
```

**TODO.md:**
```markdown
## Active

- [ ] [Initial task 1]
- [ ] [Initial task 2]

## Completed

[Empty for now]
```

**SESSIONS.md:**
```markdown
# Session Log

## [Date]
- Initialized project memory
- [Created CONTRIBUTING.md if applicable]
- Set up initial TODO list
```

### Step 5: Optionally Generate CONTRIBUTING.md

If user requested it, run:
```
/contributing [project description]
```

### Step 6: Present Setup Summary

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✨ PROJECT INITIALIZED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📁 Created: hack/
   ├── NEXT.md       ← Start here
   ├── PROJECT.md    ← Project knowledge
   ├── TODO.md       ← Task tracking
   └── SESSIONS.md   ← Session log

[✓] Added hack/ to .gitignore
[✓] Initialized memory files
[✓] Created CONTRIBUTING.md (if applicable)

🎯 NEXT STEPS:
[From NEXT.md]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ready to start! Use /session-end before finishing to sync.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Important Notes

- **Be selective:** Only load NEXT.md and PROJECT.md at startup
- **Be concise:** Summaries should be scannable, not essays
- **Don't overwhelm:** Focus on immediate focus from NEXT.md
- **Respect existing structure:** If files have custom formats, preserve them
- **Set expectations:** Remind user about `/session-end` at the end

## Output Format

Keep it visual and structured:
- Use boxes/separators for clarity
- Bullet points for key info
- Clear "Ready to work!" statement at the end

This command should make starting work frictionless—load only what you need to start working immediately.
