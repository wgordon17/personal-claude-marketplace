---
name: reflect
description: |
  Mid-task self-reflection checkpoint using Serena metacognitive tools. Use when you need to
  pause and evaluate: "am I on track?", "have I gathered enough info?", "am I actually done?".
  Triggers on: completing a significant chunk of work, before claiming done, when feeling uncertain
  about direction, after a long sequence of tool calls, before making large code changes.
allowed-tools: [mcp__serena__think_about_task_adherence, mcp__serena__think_about_collected_information, mcp__serena__think_about_whether_you_are_done, mcp__serena__summarize_changes, mcp__serena__read_memory, mcp__serena__write_memory, mcp__serena__list_memories, Read, Glob, Grep]
---

# Reflect — Mid-Task Self-Reflection Checkpoint

Structured metacognitive pause using Serena's reflection tools. Forces you to stop, evaluate
your progress, and course-correct before continuing.

## When to Use

- **Before large code changes** — are you still aligned with the task?
- **After a research/exploration phase** — do you have enough information to act?
- **Before claiming work is done** — have you actually completed everything?
- **When the conversation has gone long** — have you drifted from the original goal?
- **When uncertain** — should you ask the user or keep going?

## Reflection Sequence

Run these Serena tools in order. Each returns a structured prompt — follow its instructions
before proceeding to the next.

### Step 1: Task Adherence

```
mcp__serena__think_about_task_adherence()
```

Evaluates: Am I deviating from the task? Have I loaded relevant project memories?
Should I stop and ask the user rather than make potentially misaligned changes?

**Act on the result.** If it identifies deviation, correct course before continuing.
If it suggests loading memories, do so.

### Step 2: Information Completeness

```
mcp__serena__think_about_collected_information()
```

Evaluates: Have I gathered all the information I need? What's missing?
Can I acquire it with available tools, or do I need to ask the user?

**Act on the result.** If information gaps are identified, fill them before continuing.

### Step 3: Completion Check

```
mcp__serena__think_about_whether_you_are_done()
```

Evaluates: Have I performed all required steps? Should I run tests? Update docs?
Write new tests? Read completion-related memory files?

**Act on the result.** If steps remain, do them. Don't claim done until this passes.

## Optional: Summarize Changes

When wrapping up a significant body of work (not just a mid-task checkpoint):

```
mcp__serena__summarize_changes()
```

Explores the diff, summarizes all changes, explains test coverage, flags dangers
and breaking changes, identifies documentation needs.

## Optional: Persist Reflection

If the reflection surfaces important insights (architectural decisions, gotchas,
patterns discovered), persist them:

```
mcp__serena__write_memory(memory_name="reflections/<topic>", content="...")
```

## Invocation Modes

**Full reflection** (default) — run all 3 steps in sequence:
```
/reflect
```

**Targeted reflection** — run only the relevant step:
- Before coding: Step 1 only (task adherence)
- After research: Step 2 only (information completeness)
- Before claiming done: Step 3 only (completion check)
- After finishing: summarize_changes only

## Fallback (Serena unavailable)

If Serena MCP tools are not available (server not running, tools not enabled), perform the
same metacognitive checks using extended thinking. For each step, explicitly pause and
think through:

1. **Task adherence:** "Am I deviating from what was asked? Have I loaded relevant context?"
2. **Information completeness:** "Do I have all the information I need? What's missing?"
3. **Completion check:** "Have I done all required steps? Tests? Docs? Edge cases?"

The tools enforce structured reflection; without them, be explicit about reasoning through
the same questions.
