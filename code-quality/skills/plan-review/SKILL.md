---
name: plan-review
description: |
  Multi-agent plan review with independent fresh-context reviewers. Use when asked to
  "review plan", "review this plan", "plan review", or given a plan file path to review.
  Spawns 6 parallel specialized reviewers (feasibility, scope, dependencies, unknown unknowns,
  architect, security), verifies findings by re-reading the plan, and prints a structured
  terminal report. Designed for cross-session use: write a plan in session A, review in session B.
allowed-tools: [Read, Glob, Grep, Bash, Agent, AskUserQuestion]
---

# Plan Review Skill

Multi-agent plan review. Spawns 6 parallel reviewers (4 plan-specific + 2 domain) — feasibility,
scope & completeness, dependency ordering, unknown unknowns/spike detection, architect, and security
— each required to read and analyze the plan independently. A verification agent then cross-checks
findings against plan content. Results are categorized and printed as a structured terminal report.

**Never modifies plan files.** Output is terminal-only.

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
   `code-quality/references/project-memory-reference.md` (Directory Detection and Worktree
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

Read `references/reviewer-prompts.md`. For each applicable reviewer, locate the corresponding
prompt template, substitute all placeholders with actual values, and spawn an agent. Most
reviewers use `model="sonnet"`; the Unknown Unknowns Reviewer uses `model="opus"`.

Spawn all applicable reviewers simultaneously (parallel Agent calls).

### Plan-Specific Reviewers (4 parallel)

```
Agent(
  description="Feasibility review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Feasibility Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Scope & completeness review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Scope & Completeness Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Dependency & ordering review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Dependency & Ordering Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Unknown unknowns review of plan: {plan_file_path}",
  model="opus",
  prompt=<Unknown Unknowns Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)
```

Each plan-specific reviewer receives: `{plan_file_path}`, `{plan_content}`, `{plan_goal}`,
`{plan_files}`, `{claude_md_rules}`, `{contributing_md_rules}`, `{project_context}`.

The Unknown Unknowns Reviewer additionally receives: `{plan_open_questions}`, `{plan_trade_offs}`,
`{plan_decisions}`.

### Domain Reviewers (2 parallel, with plan-specific reviewers above)

```
Agent(
  description="Architect review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Architect Reviewer template from references/reviewer-prompts.md, placeholders substituted>
)

Agent(
  description="Security review of plan: {plan_file_path}",
  model="sonnet",
  prompt=<Security Reviewer template from references/reviewer-prompts.md, placeholders substituted>
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

Preserve: description, location reference (plan section or task number), severity, evidence,
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
    "severity": "HIGH",
    "evidence": "..."
  },
  ...
]
```

```
Agent(
  description="Finding verification for plan: {plan_file_path}",
  model="sonnet",
  prompt=<Finding Verifier template from references/reviewer-prompts.md, placeholders substituted>
)
```

The verifier receives: `{findings_json}` (the array above), `{plan_content}`,
`{plan_file_path}`.

The verifier **re-reads the plan** to check each finding against plan content. It returns a
JSON array with a verdict for each finding:
`[{finding_id, verdict, investigation_summary, category}, ...]`

Verdicts: `verified` (finding accurately references plan content), `false_positive` (finding
misread or misrepresents the plan), `needs_context` (cannot confirm or deny — requires human
judgment).

Parse the verifier's response as JSON. If parsing fails, extract JSON from between the first
`[` and last `]` markers. If that also fails, include all findings with verdict `unverified`
and set `{verification_note}` to `"⚠ Verification failed — all findings shown unverified"`.
If verification succeeded, set `{verification_note}` to empty string.

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

## Phase 4 — Terminal Output

Print the structured report. Use `━` (U+2501) for the divider lines.

### Findings Exist

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLAN REVIEW — {plan_file_path}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Goal: {plan_goal}
Domain: {plan_domain}
Tasks: {plan_tasks} | Files: {plan_files_count}

Findings: {verified_count} verified, {needs_context_count} needs context
  (from {total_raw} raw findings — {false_positive_count} false positives removed)

RESEARCH GAPS
  1. [{Reviewer}] {description} [{severity}]
     {location}
     Evidence: {evidence}

FEASIBILITY
  ...

SCOPE
  ...

DEPENDENCIES
  ...

ARCHITECTURE
  ...

SECURITY
  ...

SPECIFICATION
  ...

─── Needs Context ({needs_context_count}) ───
  1. [{Reviewer}] {description} [{severity}]
     {location}
     Investigation: {investigation_summary}

{verification_note}
{skipped_note}
Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: {verified_count} | False positives removed: {false_positive_count} | Needs context: {needs_context_count}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

`{verification_note}` — if verification JSON parsing failed, print the warning line. Otherwise omit.
`{skipped_note}` — if any reviewers were skipped, print a line such as "Skipped: Dependency & Ordering (single-task plan)" or "Skipped: Security (no auth/security paths detected)". Otherwise omit.

Category sections contain only `verified` findings. Group by category in order: RESEARCH GAPS
(first — highest leverage to fix before implementation) → FEASIBILITY → SCOPE → DEPENDENCIES →
ARCHITECTURE → SECURITY → SPECIFICATION. Within each category, sort by severity (CRITICAL →
HIGH → MEDIUM → LOW). For the `[Reviewer]` tag, use the short reviewer name: Feasibility,
Scope, Dependencies, Unknown Unknowns, Architect, Security.

Omit category sections with zero verified findings.

`needs_context` and `unverified` findings appear ONLY in the dedicated "Needs Context" section
at the bottom — they do NOT appear in category sections above. These are items the verifier
could not confirm or deny and require human judgment.

### No Findings After Verification

Use this path only when `verified_count == 0` AND `needs_context_count == 0`. If
`needs_context_count > 0`, use the "Findings Exist" path.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLAN REVIEW — {plan_file_path}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Goal: {plan_goal}
Domain: {plan_domain}
Tasks: {plan_tasks} | Files: {plan_files_count}

No verified issues found.
Checked for: {checked_areas}

{skipped_note}
Reviewed by: {reviewer_list}
Total raw: {total_raw} | Verified: 0 | False positives removed: {false_positive_count} | Needs context: 0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Edge Cases

| Condition | Action |
|-----------|--------|
| No plan file path given and no memory dir | Error: "No memory directory found. Pass a plan file path explicitly: `/plan-review <path>`" |
| No `.md` files in `{memory_dir}/plans/` | Error: "No plan files found in `{memory_dir}/plans/`. Pass a plan file path explicitly: `/plan-review <path>`" |
| Plan file path given but file does not exist | Error: "Plan file not found: {path}" |
| Plan file exists but is empty | Error: "Plan file is empty: {path}" |
| Single plan file found | Auto-select with confirmation: "Using plan: {filename}" |
| Multiple plan files found | Present via AskUserQuestion with filename, goal line, relative age |
| `{plan_tasks}` < 2 | Skip Dependency & Ordering reviewer; include in `{skipped_note}` |
| Security reviewer skipped | Include in `{skipped_note}`: "Security (no auth/security paths detected)" |
| All findings false positive | Output "no findings" report format (not an error) |
| Verification JSON parse fails | All findings get `unverified` verdict, routed to Needs Context section; `{verification_note}` warns in output |

---

## Reviewer Prompt Templates

Prompt templates are in `references/reviewer-prompts.md`. Read that file and substitute
placeholders before passing to each Agent call. The templates are not executable — they are
documentation that Claude reads and fills in.

### Placeholder Reference

| Placeholder | Value | Used by |
|-------------|-------|---------|
| `{plan_content}` | Full plan file content | All reviewers + Verifier |
| `{plan_file_path}` | Absolute path to plan file | All reviewers + Verifier |
| `{plan_goal}` | Extracted goal string | All reviewers |
| `{plan_tasks}` | Integer task count | Phase 1 skip logic + Phase 4 terminal output |
| `{plan_files}` | Newline-separated file paths from the plan | All reviewers |
| `{plan_files_count}` | Count of unique paths in `{plan_files}` | Phase 4 terminal output |
| `{plan_domain}` | Extracted domain or `"Unknown"` | Phase 4 terminal output |
| `{claude_md_rules}` | CLAUDE.md content or "No CLAUDE.md found." | All reviewers |
| `{contributing_md_rules}` | CONTRIBUTING.md content or "No CONTRIBUTING.md found." | All reviewers |
| `{project_context}` | PROJECT.md content or "No PROJECT.md found." | All reviewers |
| `{plan_open_questions}` | Extracted open questions or empty string | Unknown Unknowns only |
| `{plan_trade_offs}` | Extracted trade-offs or empty string | Unknown Unknowns only |
| `{plan_decisions}` | Extracted decisions (not trade-offs) or empty string | Unknown Unknowns only |
| `{findings_json}` | JSON array of all findings | Finding Verifier only |
| `{verification_note}` | Warning when verification JSON parse fails, or empty string | Phase 4 terminal output |
| `{skipped_note}` | List of skipped reviewers with reasons, or empty string | Phase 4 terminal output |
| `{reviewer_list}` | Comma-separated names of reviewers that ran | Phase 4 terminal output |
| `{total_raw}` | Total findings reported by all reviewers before verification | Phase 4 terminal output |
| `{verified_count}` | Count of findings with verdict `verified` | Phase 4 terminal output |
| `{false_positive_count}` | Count of findings with verdict `false_positive` (removed) | Phase 4 terminal output |
| `{needs_context_count}` | Count of findings with verdict `needs_context` or `unverified` | Phase 4 terminal output |
| `{checked_areas}` | Comma-separated list of reviewer areas that ran (excludes skipped) | Phase 4 "No Findings" output |

---

## Relationship to Other Skills

| Skill | Relationship |
|-------|-------------|
| `incremental-planning` | Creates plans that plan-review reviews. Run plan-review after incremental-planning produces a plan file. |
| `quality-gate` | Complementary: plan-review is pre-implementation (plan quality), quality-gate is post-implementation (code quality). |
| `plan-adherence` (agent) | Complementary: plan-review checks plan quality before coding; plan-adherence agent checks implementation fidelity after coding. |
| `swarm` | plan-review should run before `/swarm` — catch gaps in the plan before spawning parallel implementers. |
| `roadmap` | plan-review can review individual milestone plans that roadmap generates. |
