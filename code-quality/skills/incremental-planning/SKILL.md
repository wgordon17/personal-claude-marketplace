---
name: incremental-planning
description: >-
  Incremental planning workflow that replaces native plan mode with issue tracking integration
  (GH issues, Jira cards). Use when Claude tries to enter plan mode (EnterPlanMode is denied
  by hook), when asked to "plan", "design an approach", "how should we implement", or before
  any multi-file implementation task. Asks clarifying questions first, writes plan to file
  incrementally with file structure mapping, per-task quality review (sonnet subagent), tiered
  breakpoints for scope vs detail ambiguity, and assumption surfacing in Phase 6. Provides
  research context and summaries in chat for feedback. Never displays full plan content in chat.
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

- Launch an **Explore agent** (`Agent` with `subagent_type: "general-purpose"`) for relevant codebase areas
- Read `PROJECT.md` from the memory directory (detect using `code-quality/references/project-memory-reference.md` Directory Detection section) for past architectural decisions
- Read `LESSONS.md` (if exists) from the memory directory for relevant past lessons. Silently incorporate applicable
  lessons — especially Architecture and Planning categories — into your approach without
  announcing each one. Do not quote lessons verbatim in chat.
- Search **claude-mem** MCP for relevant past work, decisions, and learnings
- Use **Serena** MCP `get_symbols_overview` for component-level understanding (if applicable)
- Use **sequential-thinking** MCP to reason about scope boundaries
- **Discover documentation surfaces** — Use the detection patterns in
  `code-quality/references/documentation-taxonomy.md` (Documentation Surfaces section) to
  find all surfaces in the project. Note which exist and what they document. This inventory
  feeds Phase 4 and Phase 5.
- **Evaluate external research need** — If the task description or user request names a
  third-party library, framework, or service not already present in the codebase, invoke
  `/deep-research` (via the `Skill` tool) in External mode before proceeding to Phase 2. If the
  task involves evaluating how the current codebase uses an existing third-party component, invoke
  `/deep-research` (via the `Skill` tool) in Bridged mode. Feed the research findings into Phase 2 questions as informed context.

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

### Tracker Question

After the Hard Gate minimum of 3 clarification rounds is satisfied — as the final
question before the Exit Condition check — ask the user about issue tracking via
`AskUserQuestion`. This question does NOT count toward the 3-round minimum.
For light planning (1-2 questions), ask the Tracker Question after the clarification
questions are complete, regardless of round count.

Present these 5 options:

1. **Create GitHub issue** — "Create a new GH issue in the detected repo with a sanitized summary"
2. **Link existing GitHub issue** — "Link to an existing GH issue by number (e.g., #42)"
3. **Create Jira card** — "Create a new Jira card via the jira-agent with a sanitized summary"
4. **Link existing Jira card** — "Link to an existing Jira card by key (e.g., PROJ-123)"
5. **None** — "No external issue tracking for this plan"

If the user selects "Link existing" for either GH or Jira, follow up with an
`AskUserQuestion` asking for the issue number/key.

### Exit Condition

You can proceed to Phase 3 (or Phase 4 directly for light planning) when you can articulate ALL of:

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
- **`/deep-research`** (via `Skill` tool, not `Agent`) — "Research [specific technology/pattern
  question] to inform the plan" (invoke when Phase 1 identified a named third-party technology
  and Phase 2 answers did not resolve the technology choice)

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

1. Detect the project memory directory using the convention in
   `code-quality/references/project-memory-reference.md` (Directory Detection section).
2. **If found:** Generate a run ID per the Run-ID Naming Convention in that reference, then
   create the plan at `{memory_dir}/plans/{run-id}-<feature>.md`
   (create the `plans/` subdirectory if it doesn't exist)
3. **If none found:** Fall back to `~/.claude/plans/{run-id}-<feature>.md`
   (create `~/.claude/plans/` if it doesn't exist)

**Do NOT create a `hack/` directory if one doesn't exist.** That's a project-level decision.

Announce the location: "Plan file: `hack/plans/feat-auth-1711388400-session-auth.md`"

### Writing Sequence

#### 1. Write the Header

Write the plan file with a header containing:

**Always include (light and full planning).** Use `**Field:**` bold format for each field
(e.g., `**Goal:**`, `**Cynefin Domain:**`) — this format is machine-parseable by `/roadmap`:

- **Agentic directive** — A blockquote at the top of the plan file (above the Goal):
  `> **For agentic workers:** REQUIRED: Use /swarm to implement this plan. Each task within
  a phase should run in an isolated worktree.`
  For light plans (1-3 tasks), the directive may reference direct implementation instead of
  `/swarm` if the scope doesn't warrant a full agent swarm.
- **Goal:** — 1 sentence
- **Cynefin Domain:** — the domain classified in Phase 0
- **Domain Justification:** — 2-4 sentences explaining why this domain applies and what that
  means for the plan (e.g., whether a probe is needed, whether outcomes are predictable)
- **Architecture Summary:** — 2-3 sentences
- **Tech Stack:**
- **Key Decisions:** — from Phase 2
- **Branch:** — The current git branch name at plan-writing time, or "not yet created" if the
  plan is written before branching. Example: `**Branch:** feat/my-feature`
  The `**Branch:**` field is used by the plan-adherence agent for cross-session plan discovery.
  Always populate it, even if the branch doesn't exist yet — update it when the branch is created.
- **Iterations:** — Lifecycle counters tracking review/fix/gate iterations. Initialize all 5
  counters at 0 when the plan is created. Include for both light and full planning.
  Format:
  ```
  **Iterations:**
  - review-cycle: 0
  - fix-cycle: 0
  - pr-review-cycle: 0
  - pr-fix-cycle: 0
  - quality-gate: 0
  ```
- **Tracker:** — The issue tracking selection from Phase 2's Tracker Question. See
  `code-quality/references/tracker-field-spec.md` for the full field value table, parsing
  spec, validation regex, and finalization constraint.

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

Append one task at a time using the Edit tool. Use the heading format `## Task N: [Title]`
for each task. Each task should follow this structure:

```markdown
## Task N: [Short Title]

**Files:**
- Modify: `path/to/file.ts`
- Create: `path/to/new.ts`

**Depends on:** Task N-1 (if applicable, or "None")

- [ ] **Step 1: [action]**
  [details]
  Test: `command` → expected output
- [ ] **Step 2: [action]**
  [details]

**Documentation updates:** [surfaces to update, or "None"]
**Commit:** `type(scope): description`
```

Each task includes:
- Files to create/modify (exact paths, using `**Files:**` block)
- Steps (each step is one concrete action — include test commands with expected output
  inline within the relevant step)
- Documentation updates (what docs to create/update/remove. "None" if no documentation
  triggers apply per `code-quality/references/documentation-taxonomy.md`. Reference surfaces
  discovered in Phase 1.)
- Commit message

**Chat output per task:** "Task N written: [1 sentence description]. N steps."

**After writing each task (full planning only — skip for light plans):**

Dispatch a reviewer subagent. Read the template at
`references/task-reviewer-prompt.md`, fill in the placeholders (`{PLAN_FILE_PATH}`,
`{TASK_NUMBER}`, `{PRIOR_TASK_SUMMARIES}`), and pass the result as the prompt:

```
Agent(
  description="Review plan Task N",
  model="sonnet",
  prompt=<template with placeholders filled in>
)
```

As you write more tasks, the `{PRIOR_TASK_SUMMARIES}` context grows — keep prior summaries
concise (1-2 sentences each).

**If reviewer returns Approved:** proceed to next task.

**If reviewer returns Issues Found:**
1. Fix the specific issues listed in the task
2. Re-dispatch the reviewer (same task, updated content)
3. Cap at 5 total review dispatches (initial + up to 4 revision cycles)
4. If still not approved after 5 dispatches, escalate via `AskUserQuestion` with the
   outstanding issues and ask the user how to resolve them

**If reviewer crashes or returns unparseable output:** retry once. If the second attempt also
fails, mark the task as `[UNREVIEWED]` in the plan file and continue. `[UNREVIEWED]` tasks
are surfaced in the Phase 6 flags report.

**Collect assumptions:** When the reviewer detects `[ASSUMPTION: ...]` items, write them into
the plan file immediately (append to the task body) — do not hold them only in memory.
Assumptions must persist in the file so they survive context recycling.

If a reviewer flags a **scope-level** assumption, treat it as a reactive breakpoint: stop
and use `AskUserQuestion` immediately (same as agent-initiated scope ambiguity in step 3.5
below). Do not defer scope assumptions to Phase 6.

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
4. **File structure reconciliation:** Compare the `## File Structure` section against the
   files actually referenced in all tasks. If tasks discovered new files not in the original
   mapping, update the File Structure section. If planned files were dropped, remove them.
5. **Documentation coverage check:** For every task whose changes match the documentation
   triggers in `code-quality/references/documentation-taxonomy.md`, verify the plan includes
   corresponding documentation updates. Cross-reference surfaces discovered in Phase 1.
   Check both trigger coverage (every trigger has a doc update) and surface coverage (every
   affected surface is updated).
6. **Collect flags for Phase 6:**
   - Collect all `[ASSUMPTION: ...]` flags from the plan file (from Phase 4 breakpoints and
     reviewer-detected assumptions)
   - Collect all open questions from the plan header's "Open Questions" section marked `[human]`
   - Build a consolidated flags report to present in Phase 6
7. **Suggest `/test-plan` if applicable:** If this plan involves user-facing behavior changes
   (new features, modified user workflows, UI changes), output in chat:
   "This plan includes user-facing changes. Consider running `/test-plan` with this plan file
   to generate UAT scenarios and acceptance criteria before implementation."
   This suggestion is informational only — do not gate, block, or use `AskUserQuestion` for it.
   The determination of "user-facing behavior changes" is a runtime judgment call, not a
   structured detection.

**Chat output:**
> "Validation complete. N flags collected. Proceeding to completion report."

## Phase 6: Complete

The plan is the deliverable. Present the completion report.

**Chat output (required):**

1. **Summary** — "Plan complete. N tasks, M steps total. Covers: [areas]. Plan file: [path]."
2. **Flags Report** — Surface everything flagged during the entire flow:
   - Assumptions (from Phase 4 breakpoints and reviewer detection), each labeled
     `scope` or `detail`. Advisory recommendations from the reviewer are informational
     only — do not surface them as flags.
   - Open questions from the plan header marked `[human]`
3. **AskUserQuestion** — If there are ANY `[human]` open questions or scope-level assumptions
   remaining, present them via `AskUserQuestion`. Hard requirement — never bury open questions
   in the plan doc without surfacing them here.

   After receiving answers, update the plan file: resolve open questions in the header and
   apply any scope-level assumption resolutions to affected tasks. Then re-state the summary
   with updated counts.

If no flags remain: "No open flags. Plan is ready for implementation via `/swarm`."

### Repo Detection

When `**Tracker:**` is `github:pending` or `github:linked#N`,
detect the target repo for GH issue creation:

1. Check for `upstream` remote: `git remote get-url upstream`
2. If no upstream, check `origin`: `git remote get-url origin`
3. If neither exists or CWD is not a git repo, skip GH issue creation
4. Extract owner/repo from the remote URL (handles both HTTPS and SSH formats)
5. Validate: owner/repo must match the regex in `code-quality/references/tracker-field-spec.md`.
   If validation fails, skip GH issue creation and warn the user.

If repo detection fails (no remote found), warn the user via `AskUserQuestion` and
offer to set Tracker to `none` or provide a repo manually.

### Issue Body Sanitization

When generating the GH or Jira issue summary, follow these sanitization rules:

**Strip (never include in issue body):**
- Plan file paths (e.g., `hack/plans/...`, `~/.claude/plans/...`)
- Agent/subagent references (swarm, subagent, Claude, Opus, Sonnet, Haiku)
- Internal skill names (`/fix`, `/swarm`, `/quality-gate`, `/plan-review`)
- Hook names, guard names, plugin infrastructure
- Model names or AI tooling references
- PII (names, emails, internal URLs)
- Cynefin domain classification (internal methodology)
- Iteration counters, review cycles

**Keep (include in issue body):**
- High-level goal (1 sentence)
- 2-4 feature highlights (user-facing behavior, not implementation steps)
- Tech stack if relevant to the issue
- Breaking changes if any

**Post-generation forbidden-term check:** After generating the draft issue body, scan it
for any of the following terms (case-insensitive): `swarm`, `subagent`, `Claude`, `Opus`,
`Sonnet`, `Haiku`, `/fix`, `/swarm`, `/quality-gate`, `/plan-review`, `/incremental-planning`,
`hack/plans`, `SKILL.md`, `Cynefin`, `review-cycle`, `fix-cycle`. If any match is found,
flag the specific terms in the AskUserQuestion approval text so the user can see what leaked
before approving. This is a two-pass process: generate → check → present with flags.

**Shell safety for `gh` commands:** The `--title` and `--body` values are LLM-generated
and may contain shell metacharacters (quotes, backticks, `$`, newlines). Do NOT interpolate
them directly into command strings. Assign them to shell variables first and pass with proper
quoting. Do NOT use `--body-file` (prohibited by project policy).
Example: `TITLE="..."; BODY="..."; gh issue create --title "$TITLE" --body "$BODY"`.

### GitHub Label Definitions

Label definitions (names, colors, branch-prefix mappings) are maintained in
`code-quality/references/github-label-definitions.md`. Read that file for the full table
and the create-if-missing pattern. If the branch prefix does not match any row in the
table, create the issue without a label.

### Issue Creation

The full Phase 6 ordering is:
1. Resolve flags and open questions (existing steps above)
2. Issue creation (this section)
3. Chat output with tracker result
4. Terminator (do not offer execution)

**If Tracker is `github:pending`:**

a. Detect repo (per Repo Detection rules above)
b. LLM-summarize the plan per the sanitization rules in Issue Body Sanitization above
c. Map branch prefix to label name (per label definitions table). If no mapping exists
   (unrecognized prefix), skip steps e and the `--label` flag in step f — create the
   issue without a label.
d. Present the draft title and body via `AskUserQuestion` for user approval
e. Auto-create the label if it doesn't exist (label values are from the static definitions
   table — standard quoting is sufficient):
   `gh label create <name> --description "<desc>" --color "<hex>" --repo <owner/repo> 2>/dev/null || true`
   (create-if-missing without `--force` — avoids overwriting existing repo label customizations)
f. Create the issue (title and body are LLM-generated — use variable assignment):
   `TITLE="..."; BODY="..."; gh issue create --repo <owner/repo> --title "$TITLE" --body "$BODY" --label "<label>"`
   (omit `--label` if step c found no mapping)
   (`gh issue create` outputs a URL like `https://github.com/owner/repo/issues/N`)
g. Extract the issue number from the URL (last path segment)
h. Update the plan file: change `**Tracker:** github:pending` → `**Tracker:** github:owner/repo#N`
i. **Error handling:** If `gh issue create` returns non-zero, inform the user via
   `AskUserQuestion` with the exit code and a short error reason (do not surface the
   full stderr/API response) and offer: (1) retry (max 3 attempts total — after 3 failures,
   remove retry option), (2) set Tracker to `none`, (3) provide a manually-created issue
   number. For rate-limit errors (HTTP 429), suggest waiting before retry.
   Do not leave `github:pending` in the plan file after Phase 6 completes.

All `gh` commands use `--repo <owner/repo>` (from repo detection) to handle fork scenarios
where `upstream` is the target but `origin` is the fork.

**If Tracker is `github:linked#N` (linked existing, pre-repo-detection):**

a. Detect repo (per Repo Detection rules) — same as the create path
b. Validate N is a pure integer: N must match `^[0-9]+$` (per
   `code-quality/references/tracker-field-spec.md`). If not, re-prompt the user via
   `AskUserQuestion` for a corrected issue number.
c. Validate existence: `gh issue view N --repo <owner/repo> --json title,state` — if non-zero
   exit, inform user the issue doesn't exist and ask for a corrected issue number via
   `AskUserQuestion`. Repeat until validation passes or user selects "set Tracker to none".
d. Update the plan file: change `**Tracker:** github:linked#N` → `**Tracker:** github:owner/repo#N`

**If Tracker is `jira:pending`:**

a. **Jira project key:** Present an `AskUserQuestion` asking for the target Jira
   project key. If the jira plugin's OSAC conventions are detected (e.g., CLAUDE.md
   mentions OSAC/MGMT), default to `MGMT`. Otherwise, require the user to provide
   the project key.
b. LLM-summarize the plan per the sanitization rules in Issue Body Sanitization above
c. Present the draft via `AskUserQuestion` for user approval
d. Spawn `jira:jira-agent` with the approved summary and target project key to create
   the card. Pass the project key in the spawn prompt.
e. Parse the card key from the jira-agent's response. Extract using these patterns in order:
   1. Bare URL: `https://[^/]+/browse/([A-Z]+-[0-9]+)`
   2. Markdown link: `\(https://[^)]+/browse/([A-Z]+-[0-9]+)\)`
   3. Key-only fallback: `[A-Z]+-[0-9]+` (unambiguous Jira key format)
   If none match, treat as a creation failure and fall into the error handling path below.
f. Update the plan file: change `**Tracker:** jira:pending` → `**Tracker:** jira:PROJ-N`
g. **Error handling:** If `jira:jira-agent` fails to create the card, inform the user via
   `AskUserQuestion` and offer: (1) retry (max 3 attempts total — after 3 failures,
   remove retry option), (2) set Tracker to `none`, (3) provide a manually-created Jira key.
   Do not leave `jira:pending` in the plan file after Phase 6 completes.

Note: The card is NOT transitioned to "In Progress" at plan time. Transition happens at
swarm completion (Phase 7) — consistent with GitHub's `in-progress` label timing. See
`code-quality/references/tracker-field-spec.md` Lifecycle section.

**If Tracker is `jira:PROJ-N` (linked existing):**

a. Spawn `jira:jira-agent` to verify the issue exists (do NOT transition to "In Progress"
   — transition happens at swarm completion, Phase 7)
b. If the agent reports the key is invalid, inform the user via `AskUserQuestion`
   and ask for a corrected Jira key. Update the `**Tracker:**` field with the corrected
   key. Repeat until validation passes or user selects "set Tracker to none".

**If Tracker is `none`:**

Skip issue creation entirely.

**Tracker finalization constraint:** See `code-quality/references/tracker-field-spec.md`
Finalization Constraint section. The `**Tracker:**` field must reach a terminal state
(`github:owner/repo#N`, `jira:PROJ-N`, or `none`) before `/swarm` is invoked — no
`pending` or `linked#N` states may remain.

### Phase 6 Chat Output

After issue creation completes, include in the chat output:
- "**Tracker:** Created GH issue #N in owner/repo" (or "Linked to GH #N" / "Created Jira PROJ-N" / etc.)
- The issue URL for easy access

**Do NOT offer execution options. Do NOT ask "should I implement this?"**

## Quick Reference

### Flow
```
Phase 0: Assess depth → Phase 1: Explore (findings in chat) →
Phase 2: Clarify (min 3 questions + tracker question) → Phase 3: Consult (complex only) →
Phase 4: Write incrementally (summaries in chat, content to file) →
Phase 5: Validate → Phase 6: Complete (issue creation + completion report)
```

### What Goes Where
```
CHAT: Research findings, reasoning, questions, 1-sentence task summaries, checkpoints
FILE: Plan header, task definitions, code snippets, test commands, commit messages
NEVER IN CHAT: Full plan content, task details, code blocks from the plan
```

### Plan File Location
```
1. {memory_dir}/plans/{run-id}-<feature>.md → if memory dir exists (detect per project-memory-reference.md)
2. ~/.claude/plans/{run-id}-<feature>.md → fallback for all other cases
```
