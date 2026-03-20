---
name: incremental-planning
description: >-
  Incremental planning workflow that replaces native plan mode. Use when Claude tries to enter
  plan mode (EnterPlanMode is denied by hook), when asked to "plan", "design an approach",
  "how should we implement", or before any multi-file implementation task. Asks clarifying
  questions first, writes plan to file incrementally with file structure mapping, per-task
  adversarial review (sonnet subagent), tiered breakpoints for scope vs detail ambiguity, and
  assumption surfacing in Phase 6. Provides research context and summaries in chat for
  feedback. Never displays full plan content in chat.
allowed-tools: [Read, Write, Edit, Glob, Grep, Agent, Bash, AskUserQuestion, LSP, Skill, ToolSearch]
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

Before starting the full workflow, classify the task domain and assess planning depth.

### Cynefin Classification

Classify the task using this decision tree:

```
Are there documented best practices that clearly apply, and any competent
engineer would reach the same answer?
  YES → Clear: Apply best practice. Straightforward execution.

Is the situation in active failure/crisis mode requiring immediate action?
  YES → Chaotic: Stabilize first. Defer analysis.

Do you have enough information to define the problem space at all?
  NO  → Disorder: Gather information first. Probe as Complex.

Can expert analysis determine the correct approach (even if multiple
valid approaches exist)?
  YES → Complicated: Full analysis. Expert decomposition.

Are outcomes uncertain even after analysis? Does the solution space feel
unbounded, or do experts disagree on whether the problem is solvable as stated?
  YES → Complex: Probe design. Smaller iterations. More checkpoints.
```

**Domain summaries:**
- **Clear** — Known cause→effect. Best practice exists. No meaningful uncertainty.
- **Complicated** — Discoverable through analysis. Multiple valid approaches, one best answer.
- **Complex** — Cause→effect only visible in retrospect. Emergent behavior. Probe first.
- **Chaotic** — No discernible pattern. Stabilize immediately. Analyze after.
- **Disorder** — Unknown which domain applies. Probe as Complex until classified.

**Chat output (required):**
> "Cynefin domain: [X]. Justification: [one sentence explaining the classification]."

Classification feeds into depth assessment below but does not override it. If Phase 1
exploration reveals the task belongs to a different domain, update the classification and
state it explicitly: "Reclassifying to [domain] because [reason]."

### Depth Assessment

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

- Launch an **Explore agent** (`Agent` with `subagent_type: "Explore"`) for relevant codebase areas
- Read `hack/PROJECT.md` (or equivalent memory file) for past architectural decisions
- Read `hack/LESSONS.md` for relevant past lessons (if exists). Silently incorporate applicable
  lessons — especially Architecture and Planning categories — into your approach without
  announcing each one. Do not quote lessons verbatim in chat.
- Search **claude-mem** MCP for relevant past work, decisions, and learnings
- Use **Serena** MCP `get_symbols_overview` for component-level understanding (if applicable)
- Use **sequential-thinking** MCP to reason about scope boundaries
- **Discover documentation surfaces** — Use the detection patterns in
  `code-quality/references/documentation-taxonomy.md` (Documentation Surfaces section) to
  find all surfaces in the project. Note which exist and what they document. This inventory
  feeds Phase 4 and Phase 5.

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

Launch specialized agents in parallel using the `Agent` tool:

- **`code-quality:architect`** — "Given [Phase 1 context] and [Phase 2 requirements],
  what are the key architectural considerations?"
- **`code-quality:security`** — "Review these requirements for security implications"
  (only if auth, data, or API work)
- **`code-quality:qa`** — "What testing approach covers these requirements?"
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

Write the plan file with a header containing:

**Always include (light and full planning):**
- **Agentic directive** — A blockquote at the top of the plan file (above the Goal):
  `> **For agentic workers:** REQUIRED: Use /swarm to implement this plan. Each task within
  a phase should run in an isolated worktree.`
- **Goal** — 1 sentence
- **Cynefin Domain** — the domain classified in Phase 0
- **Domain Justification** — 2-4 sentences explaining why this domain applies and what that
  means for the plan (e.g., whether a probe is needed, whether outcomes are predictable)
- **Architecture Summary** — 2-3 sentences
- **Tech Stack**
- **Key Decisions** — from Phase 2

**The following header sections apply to full planning only (skip for light planning):**
- **Documentation Impact** — which documentation surfaces are affected by this work and how.
  Use the trigger definitions from `code-quality/references/documentation-taxonomy.md` to
  determine if changes require documentation. Reference surfaces discovered in Phase 1.
  Format: `surface → action (add/update/remove) → why`. Omit if no documentation triggers apply.
- **Options Considered** — for architecture-level plans, list the alternatives evaluated and
  why they were rejected (1-2 sentences per option). Minimum 2 options if a meaningful choice
  was made. Omit if the approach was never in question.
- **Trade-offs Accepted** — what the chosen approach gives up and why that's acceptable
  (e.g., "Chose X over Y because Z; accepts the risk of W"). One bullet per trade-off.
  Required for Complicated or Complex domain tasks.
- **Security Flags** — concerns identified during Phase 1/2/3 that implementation must address.
  Include mitigations if known. Omit if none identified.
- **Open Questions** — questions that remain unresolved at plan-write time, each with an owner:
  - `[human]` — requires user input before or during implementation
  - `[agent]` — can be resolved by the implementer during execution

**Chat output:** "Wrote plan header. Goal: [1 sentence]. Architecture: [1 sentence]."

#### 2. File Structure Mapping

Before writing tasks, map all files this plan will touch.

Create a `## File Structure` section in the plan file, placed between the header (after the
`---` separator) and Task 1. Use this layout:

```markdown
## File Structure

### Files to Modify
| File | Responsibility | Change |
|------|----------------|--------|
| `path/to/file.ts` | Brief responsibility | What changes |

### Files to Create
| File | Responsibility |
|------|----------------|
| `path/to/new.ts` | Brief responsibility |

### File Design Notes
- **`path/to/file.ts`** — Why it exists here and not elsewhere (for non-obvious decisions only)
```

**File design philosophy to apply:**
- Design units with clear boundaries and well-defined interfaces
- Prefer smaller, focused files over large ones — a file that does one thing is easier to test
- Files that change together should live together — split by responsibility, not technical layer
- In existing codebases, follow established patterns (check adjacent files before choosing placement)

This step writes to the plan file only. **Chat output:** "Wrote file structure. N files mapped."

#### 3. Write Each Task

Append one task at a time using the Edit tool. Each task should follow bite-sized structure:
- Files to create/modify (exact paths)
- Steps (each step is one action: write test, run test, implement, verify, commit)
- Test commands with expected output
- Documentation updates (what docs to create/update/remove. "None" if no documentation
  triggers apply per `code-quality/references/documentation-taxonomy.md`. Reference surfaces
  discovered in Phase 1.)
- Commit message

**Chat output per task:** "Task N written: [1 sentence description]. N steps."

**After writing each task (full planning only — skip for light plans):**

Dispatch a reviewer subagent using the template in
`code-quality/skills/incremental-planning/references/task-reviewer-prompt.md`:

```
Agent(
  subagent_type="general",
  description="Review plan Task N",
  model="claude-sonnet-4-6",
  prompt=<constructed from template + plan file path + task number + prior task summaries>
)
```

Provide: plan file path, task number to focus on, and 1-2 sentence summaries of all prior
tasks. As you write more tasks, the summary context grows — keep prior summaries concise.

**If reviewer returns Approved:** proceed to next task.

**If reviewer returns Issues Found:**
1. Fix the specific issues listed in the task
2. Re-dispatch the reviewer (same task, updated content)
3. Cap at 5 total review dispatches (initial + up to 4 revision cycles)
4. If still not approved after 5 dispatches, escalate via `AskUserQuestion` with the
   outstanding issues and ask the user how to resolve them

**Collect assumptions:** Any `[ASSUMPTION: ...]` items detected by the reviewer accumulate
across all tasks. These are surfaced in the Phase 6 flags report.

#### 3.5 Reactive Breakpoints

While writing a task, if you encounter ambiguity, apply this decision:

**Scope/Architecture ambiguity** — could change the plan's shape, affect other tasks, or
alter the file structure:
- STOP writing the current task immediately
- Use `AskUserQuestion` with the specific ambiguity and the context that caused it
- Do NOT continue writing until the user answers
- Example: "While writing Task 4, I realized the auth flow could go through middleware OR
  a decorator pattern. This affects Tasks 5-7. Which approach?"

**Implementation detail ambiguity** — resolvable during execution, doesn't change plan shape:
- Flag inline in the task as `[ASSUMPTION: description]`
- Continue writing
- The assumption accumulates for Phase 6 surfacing
- Example: `[ASSUMPTION: Redis session TTL should be 24h — adjustable during implementation]`

**Classification test:** "If I'm wrong about this, would it change other tasks?"
- YES → scope ambiguity → hard gate, stop and ask
- NO → detail ambiguity → flag inline and continue

#### 4. Checkpoint Every 2-3 Tasks

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

Mention in the checkpoint: "N assumptions flagged so far — will surface all in Phase 6."

#### 5. Incorporate Feedback Immediately

When the user gives feedback on a specific task, rewrite ONLY that task. Don't regenerate
the entire plan.

## Phase 5: Validate

After all tasks are written:

1. Re-read the complete plan file
2. Use **sequential-thinking** MCP to check for gaps: missing error handling, untested paths,
   dependency ordering issues
3. Cross-reference against Phase 2 requirements: does the plan cover everything?
4. **Documentation coverage check:** For every task whose changes match the documentation
   triggers in `code-quality/references/documentation-taxonomy.md`, verify the plan includes
   corresponding documentation updates. Cross-reference surfaces discovered in Phase 1.
   Check both trigger coverage (every trigger has a doc update) and surface coverage (every
   affected surface is updated).
5. **Collect flags for Phase 6:**
   - Collect all `[ASSUMPTION: ...]` flags from the plan file (from Phase 4 breakpoints and
     reviewer-detected assumptions)
   - Collect all open questions from the plan header's "Open Questions" section marked `[human]`
   - Build a consolidated flags report to present in Phase 6

**Chat output:**
> "Validation complete. N flags collected. Proceeding to completion report."

## Phase 6: Complete

The plan is the deliverable. Present the completion report.

**Chat output (required):**

1. **Summary** — "Plan complete. N tasks, M steps total. Covers: [areas]. Plan file: [path]."
2. **Flags Report** — Surface everything flagged during the entire flow:
   - Assumptions made (from Phase 4 breakpoints and reviewer detection), each labeled
     `scope` or `detail`
   - Open questions from the plan header marked `[human]`
   - Any reviewer recommendations that warrant user attention
3. **AskUserQuestion** — If there are ANY `[human]` open questions or scope-level assumptions
   remaining, present them via `AskUserQuestion`. Hard requirement — never bury open questions
   in the plan doc without surfacing them here.

If no flags remain: "No open flags. Plan is ready for implementation via `/swarm`."

**Do NOT offer execution options. Do NOT ask "should I implement this?"**

## Quick Reference

### Flow
```
Phase 0: Assess depth → Phase 1: Explore (findings in chat) →
Phase 2: Clarify (min 3 questions) → Phase 3: Consult (complex only) →
Phase 4: Write incrementally (summaries in chat, content to file) →
Phase 5: Validate → Phase 6: Complete
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
