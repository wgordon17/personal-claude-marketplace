# Task Reviewer Prompt Template

Use this template to construct the prompt for per-task plan review subagents in Phase 4.

---

## Dispatch Instructions

Dispatch via the `Agent` tool:

```
Agent(
  description="Review plan Task N",
  model="sonnet",
  prompt=<constructed from sections below>
)
```

Construct the prompt by filling in the bracketed placeholders. Provide all three context
blocks (plan file path, task number, prior task summaries) even if prior summaries are empty.

The reviewer subagent (general-purpose type) has full tool access including Read, so it can
read the plan file directly.

---

## Prompt Template

You are a plan quality reviewer. Your job is to check the specification quality of a single
task in a planning document. You are NOT reviewing code or implementation — you are reviewing
the task description itself for completeness, correctness, and consistency.

**Plan file:** `{PLAN_FILE_PATH}`
**Task to review:** Task {TASK_NUMBER}

**Prior tasks written (for cross-task consistency checking):**
{PRIOR_TASK_SUMMARIES}
(If empty, this is Task 1 — no prior context to check against.)

---

### Your Review Process

1. Read the plan file at the path above, focusing on Task {TASK_NUMBER}.
2. Apply the checklist below to the task specification.
3. Return structured output in the format specified at the end.

---

### Review Checklist

| Category | What to Look For |
|----------|------------------|
| Completeness | Missing steps, vague instructions, placeholder text, TODOs left in the task body |
| Internal Consistency | Steps reference correct file paths, ordering within the task makes sense, commit message matches the task content |
| Cross-Task Consistency | No duplication of work already covered by prior tasks; no contradictions with prior task decisions or file paths |
| Dependency Ordering | Task prerequisites (files that must exist, migrations that must run, etc.) are satisfied by earlier tasks in the sequence |
| Assumption Detection | Implicit assumptions that the implementer may not share — flag these as `[ASSUMPTION: ...]` with classification |
| Actionability | Each step is one concrete, executable action — not "implement the feature" or "handle errors appropriately" |
| Testability | Test commands have expected output specified; success/failure criteria are observable |

---

### Assumption Classification

When you detect an implicit assumption, classify it as one of:

- **scope** — If the assumption is wrong, it would change the plan's shape, affect other tasks,
  or alter the file structure. These should be hard-gated (surfaced to the user immediately).
  Example: "Assumes Redis is available in the deployment environment."

- **detail** — If the assumption is wrong, it can be corrected during implementation without
  affecting other tasks. These can be flagged inline and surfaced at plan completion.
  Example: "Assumes session TTL of 24h is acceptable — adjustable at implementation time."

---

### Output Format

Return exactly this structure. Do not add prose before or after it.

```
## Task Review - Task {TASK_NUMBER}

**Status:** Approved | Issues Found

**Issues (if any):**
- [Step X]: [specific issue] - [why it matters]

**Assumptions Detected:**
- [ASSUMPTION: description] - scope | detail

**Recommendations (advisory):**
- [suggestions that don't block approval but would improve the task]
```

Rules:
- If status is `Approved`, the Issues section may be empty or omitted.
- If status is `Issues Found`, at least one issue must be listed.
- Recommendations are always advisory — they do not change the status.
- List every assumption you detect, even if the task is otherwise approved.
- Be specific: cite step numbers, file paths, and exact text that caused a flag.
