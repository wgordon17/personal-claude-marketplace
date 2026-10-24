---
name: plan-review
description: |
  Multi-agent plan review with independent fresh-context reviewers. Use when asked to
  "review plan", "review this plan", "plan review", or given a plan file path to review.
  Spawns 6 parallel specialized reviewers (feasibility, scope, dependencies, unknown unknowns,
  architect, security), verifies findings by re-reading the plan, and prints a structured
  terminal report. Designed for cross-session use: write a plan in session A, review in session B.
allowed-tools: Read Edit Bash Agent AskUserQuestion
---

# Plan Review Skill

> **Note:** `{skill_dir}` refers to the absolute "Skill directory" path provided in your system instructions.

Multi-agent plan review. Spawns 6 parallel reviewers (4 plan-specific + 2 domain) — feasibility,
scope & completeness, dependency ordering, unknown unknowns/spike detection, architect, and security
— each required to read and analyze the plan independently. A verification agent then cross-checks
findings against plan content. Results are categorized and printed as a structured terminal report.

**Never modifies plan content.** Exception: increments the `review-cycle` lifecycle counter after review completes.

## Usage

```
/plan-review [<plan-file-path>]
```

- `<plan-file-path>` — optional. Absolute or relative path to a plan `.md` file. If omitted, the
  skill discovers the most recent plan in `{memory_dir}/plans/`.

---

## Phase 0 — Setup & Plan Discovery

### Parse Arguments

Extract from `$ARGUMENTS`:
- Plan file path (optional; first token if present)

### Plan Discovery

If a plan file path was provided in `$ARGUMENTS`, use it directly. Skip discovery.

If no path was given:

1. Detect the memory directory using the convention in
   `[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)` (Directory Detection and Worktree
   Resolution sections). If no validated memory directory is found, stop with:
   "No memory directory found. Pass a plan file path explicitly: `/plan-review <path>`"

2. Scan `{memory_dir}/plans/` for `.md` files. If the directory does not exist or contains no
   `.md` files, stop with:
   "No plan files found in `{memory_dir}/plans/`. Pass a plan file path explicitly: `/plan-review <path>`"

3. **Primary: Branch-header matching** — Parse each plan file's `**Branch:**` header field.
   Match against the current git branch (`git branch --show-current`). If exactly one plan
   matches, use it. If multiple match, use the most recent by unix timestamp in the filename.
   This matches the discovery convention used by pr-review, swarm, and quality-gate.

4. **Fallback: mtime sorting with user selection** — If no Branch-header match is found (e.g.,
   reviewing from `main` or a different branch), sort all plan files by modification time
   (most recent first):
   - If exactly one file exists, use it. Print: "Using plan: {filename}"
   - If multiple files exist, present them via `AskUserQuestion` with each file's filename,
     the value of its first `**Goal:**` or `## Goal` line (if found), and its relative age.

### Read Plan File

Read the selected plan file. Store as `{plan_content}` and `{plan_file_path}`.

Extract from the plan content:
- `{plan_goal}` — value of `**Goal:**` header or first H2 that describes the objective
- `{plan_domain}` — inferred from file paths, tech stack mentions, or explicit `**Cynefin Domain:**` statement. If domain cannot be determined, use `"Unknown"`.
- `{plan_decisions}` — content of any `## Decisions` or `## Key Decisions` section (not `## Trade-offs` — that is captured separately by `{plan_trade_offs}`)
- `{plan_tasks}` — count of `## Task N:` headings (or `- [ ]` top-level task items)
- `{plan_files}` — file paths from `## File Structure` sections and task `Files:` blocks
- `{plan_open_questions}` — content of any `## Open Questions` section
- `{plan_trade_offs}` — content of any `## Trade-offs` section

> **Note:** These extractions assume a structured plan format. If the plan lacks these headings,
> reviewers will work with the full `{plan_content}` and report on what they can infer.

### Read Test Plan (if linked)

If the plan file contains a `## Test Plan` section, extract the `**Test Plan:**` path annotation from it.
Normalize the path (resolve `..` components) and verify it falls within `{memory_dir}/test-plans/`.
If the normalized path escapes that directory, set `{plan_test_plan}` to empty string and log a
warning: "Warning: test plan path escapes {memory_dir}/test-plans/ boundary — setting {plan_test_plan} to empty string."
If the path is valid but the file does not exist, set `{plan_test_plan}` to empty string (graceful fallback — no warning).
If valid and readable, read the file and store its content as `{plan_test_plan}`.
If no `## Test Plan` section exists in the plan file, set `{plan_test_plan}` to empty string.

### Read Project Context

From the repo root:
- Read `CLAUDE.md`. If missing, use: `"No CLAUDE.md found."`
- Read `CONTRIBUTING.md`. If missing, use: `"No CONTRIBUTING.md found."`
- Read `{memory_dir}/PROJECT.md`. If missing, use: `"No PROJECT.md found."`

Store as `{claude_md_rules}`, `{contributing_md_rules}`, and `{project_context}`.

### Build Input Context

Assemble these values — passed to reviewers in Phase 2:
- `{plan_content}` = full plan file content
- `{plan_file_path}` = absolute path to the plan file
- `{plan_goal}` = extracted goal or empty string
- `{plan_domain}` = extracted domain or `"Unknown"`
- `{plan_tasks}` = task count integer
- `{plan_files}` = newline-separated list of file paths from the plan
- `{plan_files_count}` = count of unique file paths in `{plan_files}` (derived, not extracted)
- `{claude_md_rules}` = CLAUDE.md content or placeholder
- `{contributing_md_rules}` = CONTRIBUTING.md content or placeholder
- `{project_context}` = PROJECT.md content or placeholder

Additional context for Unknown Unknowns reviewer:
- `{plan_open_questions}` = extracted open questions or empty string
- `{plan_trade_offs}` = extracted trade-offs or empty string
- `{plan_decisions}` = extracted decisions or empty string

Additional context for Scope & Completeness and Feasibility reviewers:
- `{plan_test_plan}` = full test plan document content, or empty string if no test plan is linked

---

## Phase 1 — Reviewer Applicability

Determine which of the 6 reviewers apply based on plan content.

Default: all 6 reviewers run. Skip a reviewer only if its domain has zero applicability:

| Reviewer | Skip condition |
|----------|----------------|
| Feasibility | never skip |
| Scope & Completeness | never skip |
| Dependency & Ordering | skip if `{plan_tasks}` < 2 (no ordering to verify with a single task) |
| Unknown Unknowns | never skip (most critical reviewer) |
| Architect | never skip |
| Security | skip if `{plan_files}` contains no paths referencing auth, security, crypto, secrets, permissions, or API endpoints (case-insensitive check on file path strings and plan content keywords) |

Record which reviewers will run.

---

## Phase 2 — Parallel Review

Read `[{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md)`. For each applicable reviewer, locate the corresponding
prompt template, substitute all placeholders with actual values, and spawn an agent. Most
reviewers use `model="sonnet"`; the Unknown Unknowns Reviewer uses `model="opus"`.

**Incremental workflow awareness:** When reviewing a plan with `**Workflow:** incremental`,
include the following guidance in each reviewer's prompt: treat PR boundary markers
(`**PR:** N`, `**PR Boundaries:**`) as expected structure, not scope gaps. The scope reviewer
should validate that each PR boundary is coherent (files form a logical unit). The dependency
reviewer should verify cross-boundary dependency ordering (tasks in PR N+1 can depend on tasks
in PR N, but not vice versa). The feasibility reviewer should assess whether 200-400 line
targets per boundary are realistic given the file structure.

Spawn all applicable reviewers simultaneously (parallel Agent calls).

### Plan-Specific Reviewers (4 parallel)

```
Agent(
  description="Feasibility review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Feasibility Reviewer template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)

Agent(
  description="Scope & completeness review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Scope & Completeness Reviewer template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)

Agent(
  description="Dependency & ordering review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Dependency & Ordering Reviewer template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)

Agent(
  description="Unknown unknowns review of plan: {plan_file_path}",
  model="opus",
  prompt=<Unknown Unknowns Reviewer template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)
```

Each plan-specific reviewer receives: `{plan_file_path}`, `{plan_content}`, `{plan_goal}`,
`{plan_files}`, `{claude_md_rules}`, `{contributing_md_rules}`, `{project_context}`.

The Unknown Unknowns Reviewer additionally receives: `{plan_open_questions}`, `{plan_trade_offs}`,
`{plan_decisions}`.

The Scope & Completeness Reviewer and Feasibility Reviewer additionally receive: `{plan_test_plan}`.
When `{plan_test_plan}` is empty string, omit the `## UAT Context` section entirely from the
Feasibility and Scope & Completeness reviewer prompts — do not render the heading, `TEST PLAN:`
label, or empty placeholder.

### Domain Reviewers (2 parallel, with plan-specific reviewers above)

```
Agent(
  description="Architect review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Architect Reviewer template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)

Agent(
  description="Security review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Security Reviewer template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)
```

Domain reviewers receive the same inputs as plan-specific reviewers (minus the Unknown Unknowns
extras).

### Collect Findings

After all agents complete, collect all findings into a consolidated list. Assign each finding a
unique ID using the prefix for its reviewer:

| Reviewer | Finding prefix |
|----------|---------------|
| Feasibility | `feas-` |
| Scope & Completeness | `scope-` |
| Dependency & Ordering | `dep-` |
| Unknown Unknowns | `unk-` |
| Architect | `arch-` |
| Security | `sec-` |

Preserve: description, location reference (plan section or task number), classification, evidence,
source reviewer.

---

## Phase 3 — Verification (batched)

If no findings were reported by any reviewer, skip verification and proceed directly to
Phase 4 with the 'no findings' output path.

Spawn a **single** Sonnet agent with ALL findings in one call. Do NOT spawn one agent per
finding — that pattern is catastrophically slow. A single batched call takes ~15 seconds;
per-finding agents with 15–20 findings would take 2–5 minutes.

Build the findings JSON array:
```json
[
  {
    "id": "feas-1",
    "reviewer": "Feasibility",
    "description": "...",
    "location": "Task 3, step 2",
    "classification": "needs-fix | needs-input",
    "evidence": "..."
  },
  ...
]
```

```
Agent(
  description="Finding verification for plan: {plan_file_path}",
  model="opus",
  prompt=<Finding Verifier template from [{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md), placeholders substituted>
)
```

The verifier receives: `{findings_json}` (the array above), `{plan_content}`,
`{plan_file_path}`.

The verifier **re-reads the plan** to check each finding against plan content. It returns a
JSON array with a verdict for each finding:
`[{finding_id, verdict, investigation_summary, category, classification, options}, ...]`

Verdicts: `verified` (finding accurately references plan content), `false_positive` (finding
misread or misrepresents the plan), `needs_context` (cannot confirm or deny — requires human
judgment).

Parse the verifier's response as JSON. If parsing fails, extract JSON from between the first
`[` and last `]` markers. If that also fails, include all findings with verdict `unverified`
and set `{verification_note}` to `"⚠ Verification failed — all findings shown unverified"`.
If verification succeeded, set `{verification_note}` to empty string.

### Reconcile

**Finding fidelity check:** Before categorizing, verify the verifier returned a verdict for
every submitted finding. For each finding ID in the original `{findings_json}`, check if a
matching `finding_id` exists in the verifier's response. Any finding without a returned verdict
is assigned verdict `unverified` with `investigation_summary`: "Verifier did not return a
verdict for this finding." This prevents silent finding loss during verification - the same
principle as the Fixer verification protocol in `[{skill_dir}/../../references/finding-classification.md](../../references/finding-classification.md)`.

### Categorize

The verifier assigns each finding to a category based on its nature:

| Category | Examples |
|----------|----------|
| **Research Gaps** | Unvalidated assumptions, missing spikes, external dependencies not investigated |
| **Feasibility** | Steps that cannot be implemented as described, missing prerequisites |
| **Scope** | Missing requirements, scope creep, goal not fully addressed |
| **Dependencies** | Wrong task ordering, implicit dependencies, circular dependencies |
| **Architecture** | Design issues, pattern violations, unnecessary abstractions |
| **Security** | Missing security considerations, auth gaps, sensitive data handling |
| **Specification** | Ambiguous steps, missing detail, unclear success criteria |

### Filter

Remove findings with verdict `false_positive`. Keep `verified` findings for the category
sections. Keep `needs_context` and `unverified` findings for the Needs Context section only
(they do NOT appear in category sections). Treat `unverified` as `needs_context` for display.

---

## Phase 3.5 — Needs-Input Resolution

If any surviving findings (after filtering false positives) have classification `needs-input`,
present them to the user before producing the report. Do NOT skip this step - the skill must
not exit with unresolved `needs-input` items.

If zero `needs-input` findings remain after Phase 3, skip to Phase 4.

### Present to User

Present each `needs-input` finding individually via AskUserQuestion. Each finding gets its own
question with full context so the user can make an informed decision. Batch up to 4 findings
per AskUserQuestion call (the tool's question limit):

```
AskUserQuestion(questions=[
  {
    "question": "[{id}] [{Reviewer}] {description}\n\nLocation: {location}\nDecision needed: {input_needed}\n▸dp:file={plan_file},line=0,cat={Reviewer},skill=plan-review",
    "header": "{id}",
    "options": [
      ... (map each element from the finding's `options` array to {label, description}),
      {"label": "Defer", "description": "Skip for now — user-deferred"}
    ],
    "multiSelect": false
  },
  ... (one question per finding, up to 4 per call)
])
```

If more than 4 `needs-input` findings exist, make multiple AskUserQuestion calls.

### Record Decisions

For each `needs-input` finding:
- **Option selected (any non-Defer option):** Promote to `needs-fix` with verdict `verified`.
  Record the selected option label in the finding's `suggested_fix` field (this tells the
  Fixer which approach to implement). Place the finding in its normal category section.
- **Defer selected:** Update the finding's classification to `user-deferred` in the output.

Every `needs-input` item gets a recorded user decision, not silent deferral.

---

## Phase 4 — Terminal Output

Print the structured `PLAN REVIEW — {plan_file_path}` report following the `Findings Exist` or `No Findings After Verification` template and edge-case rules in `[{skill_dir}/references/output-and-edge-cases.md](./references/output-and-edge-cases.md)`. Use `━` (U+2501) for divider lines, group verified `needs-fix` findings by category (RESEARCH GAPS → FEASIBILITY → SCOPE → DEPENDENCIES → ARCHITECTURE → SECURITY → SPECIFICATION), omit empty categories, and place `needs_context` and `user-deferred` items in their dedicated bottom sections.

### Counter Increment

After producing the Phase 4 terminal output, increment the `review-cycle` lifecycle counter
on the plan file. This is the only plan file modification plan-review makes.

1. Re-read the plan file using `{plan_file_path}` stored in Phase 0 (do not re-discover)
2. Find the `**Iterations:**` block
3. Read the current `review-cycle` value N from the line matching `- review-cycle: {N}`
4. Verify N is a non-negative integer (digits only, e.g. `0`, `1`, `12`). If N is not a valid
   integer, skip the increment silently
5. Increment `review-cycle` by 1: use Edit to replace `- review-cycle: {N}` with
   `- review-cycle: {N+1}`, where `{N}` is the actual integer read in step 3
6. If no plan file or no `**Iterations:**` block found, skip silently

---

## Reviewer Prompt Templates

Prompt templates and placeholder definitions are in `[{skill_dir}/references/reviewer-prompts.md](./references/reviewer-prompts.md)`. Read that file and substitute
placeholders before passing to each Agent call. The templates are not executable — they are
documentation that Claude reads and fills in.
