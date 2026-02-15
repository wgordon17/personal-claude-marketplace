---
name: incremental-planning
description: >-
  Incremental planning workflow that replaces native plan mode. Use when Claude tries to enter
  plan mode (EnterPlanMode is denied by hook), when asked to "plan", "design an approach",
  "how should we implement", or before any multi-file implementation task. Asks clarifying
  questions first, writes plan to file incrementally, provides research context and summaries
  in chat for feedback. Never displays full plan content in chat.
allowed-tools: [Read, Write, Edit, Glob, Grep, Task, Bash, AskUserQuestion, LSP, Skill, ToolSearch]
---

# Incremental Planning

Replaces native plan mode with a question-first, file-based, incremental workflow.
The plan lives in a file. Chat contains research, questions, and summaries — never full plan content.

## Activation

This skill activates when:
- `EnterPlanMode` is denied by the tool-selection-guard hook (message includes "incremental-planning")
- User asks to "plan", "design", "how should we build", "let's think about how to implement"
- You're about to start a multi-file implementation task

**Announce at start:** "Using incremental-planning to design the approach."

## Core Rules

1. **Research and context go in chat.** Share what you find, your reasoning, and your questions
   directly in conversational output. The user needs this context to give informed answers.
2. **Plan content goes to file.** Write plan sections to the plan file using Write/Edit tools.
   Provide 1-2 sentence summaries in chat after each section.
3. **Never dump plan content into chat.** If the user wants to read the plan, tell them
   the file path. Only paste specific sections if the user explicitly asks to see them.
4. **Questions before writing.** Do not write ANY plan content until Phase 2 is complete.
5. **Incremental writing.** Write one section/task at a time, never the whole plan at once.

## Phase 0: Assess

Before starting the full workflow, assess whether planning is needed and at what depth.

**Decision matrix:**

| Signal | Action |
|--------|--------|
| Single file, clear requirement | Skip planning. Just do the work. |
| Multi-file, clear requirements | Light planning: 1-2 questions in Phase 2, skip Phase 3 |
| Multi-file, unclear requirements | Full planning: all phases |
| Architecture or design change | Full planning + Phase 3 expert consultation |

**Chat output:**
> "This touches [areas] and involves [scope]. I'll do [full/light] planning."

The user can override your assessment.

## Phase 1: Explore

Gather context before asking questions. You need to understand the landscape to ask informed,
specific questions — not generic ones.

### Actions

- Launch an **Explore agent** (`Task` with `subagent_type: "Explore"`) for relevant codebase areas
- Read `hack/PROJECT.md` (or equivalent memory file) for past architectural decisions
- Search **claude-mem** MCP for relevant past work, decisions, and learnings
- Use **Serena** MCP `get_symbols_overview` for component-level understanding (if applicable)
- Use **sequential-thinking** MCP to reason about scope boundaries

### Chat Output

Share your findings in full. The user needs this context to answer Phase 2 questions accurately.
This is NOT a summary — include specific files, patterns, past decisions, and anything relevant.

Example:
> "I explored the codebase. Here's what's relevant:
>
> - The auth system uses middleware in `src/middleware/auth.ts` with JWT strategy only
> - `src/stores/redis.ts` has a connection pool, currently used for caching
> - Tests in `tests/middleware/` cover JWT validation but no session tests exist
> - PROJECT.md notes a Jan 15 decision to keep auth stateless — this would reverse that
> - Your last session refactored the Redis connection pool, which is relevant here
>
> This shapes my questions."

## Phase 2: Clarify

Ask targeted questions using the `AskUserQuestion` tool. Questions must be informed by
Phase 1 findings — reference specific code, files, and past decisions.

### Hard Gate

**You MUST ask at least 3 rounds of `AskUserQuestion` and receive answers before writing
ANY plan content to a file. No exceptions.**

"Simple" tasks are where unexamined assumptions cause the most wasted work. If the task is
truly simple, the questions will be quick to answer.

### Exit Condition

You can proceed to Phase 4 (or Phase 3 for complex tasks) when you can articulate ALL of:

1. **Scope** — what the user wants built
2. **Behavior** — how it should work
3. **Constraints** — technical, security, compatibility requirements
4. **Non-scope** — what's explicitly out of scope

If you can't articulate all four, ask another question.

### Question Design

- Use `AskUserQuestion` with structured options when possible (easier to answer)
- Include research context WITH each question — don't make the user remember Phase 1 findings
- One primary question per `AskUserQuestion` call
- Question categories: **Scope** (multi-select), **Approach** (single-select),
  **Constraints** (open-ended ok), **Priority** (single-select)

### Anti-Pattern: Proposing Approaches Too Early

Do NOT propose "2-3 approaches" during clarification. That's the brainstorming skill's pattern.
Here, the approach **emerges from answers**. Ask about requirements, not solutions.

Bad: "I see two approaches: A or B. Which do you prefer?"
Good: "Should sessions replace JWT for web clients, or coexist alongside JWT?"

The difference: the first imposes Claude's framing. The second discovers the user's intent.

## Phase 3: Consult (Conditional)

**Only for complex tasks** (architecture changes, security-sensitive work, major features).
Skip this phase for light planning.

### Actions

Launch specialized agents in parallel using the `Task` tool:

- **`superclaude:architect`** — "Given [Phase 1 context] and [Phase 2 requirements],
  what are the key architectural considerations?"
- **`superclaude:security`** — "Review these requirements for security implications"
  (only if auth, data, or API work)
- **`superclaude:qa`** — "What testing approach covers these requirements?"
  (only if test strategy is non-obvious)

### Chat Output

Present findings and decision points directly in chat. Use `AskUserQuestion` for
decisions that came out of expert review.

Example:
> "Expert agents flagged two things:
>
> **Architecture:** Recommends separating session middleware from JWT middleware.
> The existing chain pattern in `src/middleware/index.ts` supports this.
>
> **Security:** Flags that Redis sessions need session fixation protection and
> key expiry. Current Redis config doesn't set expiry."

Then:
```
AskUserQuestion: "Should we address session fixation in this work or note it as follow-up?"
```

## Phase 4: Write Incrementally

Now write the plan. One section at a time.

### Determine Plan File Location

Before creating the plan file, check where it belongs:

1. Check if the project uses a local memory directory — look for `hack/`, `.local/`,
   `scratch/`, or `.dev/` in the project root (check in this order)
2. **If found:** Create plans in `{memory-dir}/plans/YYYY-MM-DD-<feature>.md`
   (create the `plans/` subdirectory if it doesn't exist)
3. **If none found:** Fall back to `~/.claude/plans/YYYY-MM-DD-<feature>.md`
   (create `~/.claude/plans/` if it doesn't exist)

**Do NOT create a `hack/` directory if one doesn't exist.** That's a project-level decision.

Announce the location: "Plan file: `hack/plans/2026-02-15-session-auth.md`"

### Writing Sequence

#### 1. Write the Header

Write the plan file with a header containing: goal (1 sentence), architecture summary
(2-3 sentences), tech stack, and key decisions from Phase 2.

**Chat output:** "Wrote plan header. Goal: [1 sentence]. Architecture: [1 sentence]."

#### 2. Write Each Task

Append one task at a time using the Edit tool. Each task should follow bite-sized structure:
- Files to create/modify (exact paths)
- Steps (each step is one action: write test, run test, implement, verify, commit)
- Test commands with expected output
- Commit message

**Chat output per task:** "Task N written: [1 sentence description]. N steps."

#### 3. Checkpoint Every 2-3 Tasks

After every 2-3 tasks:

```
AskUserQuestion: "I've written tasks N-M covering [summary].
Tasks so far: 1) X, 2) Y, 3) Z.
Any adjustments before I continue?"

Options:
- "Looks good, continue"
- "Let me review the plan file"
- "Adjust something"
```

If "Let me review" → wait for the user to read the file and come back.
If "Adjust something" → discuss, rewrite just that task, continue.

#### 4. Incorporate Feedback Immediately

When the user gives feedback on a specific task, rewrite ONLY that task. Don't regenerate
the entire plan.

## Phase 5: Validate

After all tasks are written:

1. Re-read the complete plan file
2. Use **sequential-thinking** MCP to check for gaps: missing error handling, untested paths,
   dependency ordering issues
3. Cross-reference against Phase 2 requirements: does the plan cover everything?

**Chat output:**
> "Plan complete. N tasks, M steps total. Covers: [list of areas].
>
> One gap I noticed: [description]. Should I add a task for that?"

## Phase 6: Handoff

Offer execution options:

```
AskUserQuestion: "How do you want to execute this plan?"

Options:
- "Subagent-driven (this session)" → invoke superpowers:subagent-driven-development
- "Executing-plans (new session)" → guide to superpowers:executing-plans
- "I'll handle it manually"
- "Let me review the full plan first"
```

## Quick Reference

### Flow
```
Phase 0: Assess depth → Phase 1: Explore (findings in chat) →
Phase 2: Clarify (min 3 questions) → Phase 3: Consult (complex only) →
Phase 4: Write incrementally (summaries in chat, content to file) →
Phase 5: Validate → Phase 6: Handoff
```

### What Goes Where
```
CHAT: Research findings, reasoning, questions, 1-sentence task summaries, checkpoints
FILE: Plan header, task definitions, code snippets, test commands, commit messages
NEVER IN CHAT: Full plan content, task details, code blocks from the plan
```

### Plan File Location
```
1. hack/plans/ (or .local/plans/, scratch/plans/, .dev/plans/) → if memory dir exists
2. ~/.claude/plans/ → fallback for all other cases
```
