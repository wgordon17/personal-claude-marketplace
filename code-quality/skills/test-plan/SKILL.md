---
name: test-plan
description: >
  Use when user requests user-guided test plans, UAT validation, acceptance criteria definition,
  or user journey testing. Triggers on: "test plan", "user journey test", "UAT", "acceptance
  criteria", "manual test plan", "user-guided test", "validate from user perspective",
  "walk me through testing", "define what to test".
  Takes an implementation plan file as input and produces a test plan document with user
  personas, Given/When/Then scenarios, manual UAT steps, traceability matrix, and optional
  BDD .feature files. Annotates the input plan file so downstream skills (/swarm, /plan-review,
  /pr-review, /fix, /quality-gate) discover and consume the test plan automatically.
allowed-tools: Read Write Edit Bash Agent AskUserQuestion Skill
---

# Test Plan Skill

> **Note:** `{skill_dir}` refers to the absolute "Skill directory" path provided in your system instructions.

Produces a user-facing test plan from an existing implementation plan file. The test plan is
written as a human-walkthrough document (personas, Given/When/Then scenarios, manual UAT steps,
traceability matrix) and annotated back into the plan file so all downstream skills discover it
automatically.

**Standalone mode is not supported.** This skill requires an implementation plan file as input.
The canonical workflow is:

```
/incremental-planning → produces plan file (hack/plans/{run-id}-<feature>.md)
        ↓
/test-plan <plan-file-path> → enriches plan file + writes test plan doc
        ↓
/swarm (reads plan file as always) → discovers test plan via ## Test Plan annotation
```

---

## Usage

```
/test-plan hack/plans/{run-id}-<feature>.md
```

The plan file path is required. If not provided, ask via `AskUserQuestion`:
"Which plan file should I generate a test plan for? Provide the path to a plan file
(e.g., hack/plans/feat-auth-1711388400-session-auth.md)."

---

## Phase 0 — Plan File Ingestion

Read and parse the input plan file before doing anything else.

### Actions

1. **Validate plan file path** — Before reading the plan file, normalize the provided path
   (resolve `..` segments, collapse `./` sequences). Verify the normalized path falls within
   the current working directory. If the path escapes the CWD boundary, stop with an error:
   "Plan file path is outside the project boundary. Provide a path within the current
   working directory."

2. **Read the plan file** — Extract:
   - **Branch header** (`**Branch:**` field) for downstream cross-session discovery
   - **Goal** (`**Goal:**` field) — 1-sentence feature description
   - **Tech Stack** (`**Tech Stack:**` field) — language/framework context
   - **All task titles and steps** — the user-facing behavior changes to test
   - **Existing `## Test Plan` section** (if present — stop and report if already annotated):
     "This plan file already has a test plan. Run `/test-plan` on a fresh plan or delete
     the `## Test Plan` section from `{plan_file}` to regenerate."

3. **Read project memory** — Detect `{memory_dir}` per
   `[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)` (Directory Detection and Worktree
   Resolution sections). Then read:
   - `{memory_dir}/PROJECT.md` — architectural decisions, domain context
   - `{memory_dir}/LESSONS.md` — past lessons (if exists). Silently incorporate.

4. **Generate `{run-id}`** — Generate the run-id early so it is available for the staging file
   in Phase 2. Follow the Run-ID Naming Convention in
   `[{skill_dir}/../../references/project-memory-reference.md](../../references/project-memory-reference.md)`: `{branch-slug}-{unix-timestamp}`
   (e.g., `feat-auth-1711388400`).

5. **Identify user-facing tasks** — Not every plan task represents a user-visible behavior.
   Scan task titles and steps for user-facing surface area:
   - UI changes, API endpoints, CLI commands, configuration options
   - Error messages, validation rules, success states
   - Tasks marked as "internal refactor" or "infrastructure" with no user-visible changes
     are noted but do not generate scenarios

6. **Detect test runner and BDD infrastructure** — Search project dependency manifests (`package.json`, `pyproject.toml`, `requirements*.txt`, `go.mod`, `Cargo.toml`, `build.gradle`, `pom.xml`, `Gemfile`) using `Bash` (`rg -l` / `test -f`) or `Read` per the detection tables and priority ladder in `[{skill_dir}/references/bdd-integration.md](./references/bdd-integration.md)` (Phase 0 Step 6). Record `test_runner` (or `unknown`) and detected BDD framework (or `none`).

7. **Resolve unknown test runner** — If `test_runner` is `unknown`, ask via `AskUserQuestion` ("What test framework does this project use?") and sanitize the answer before storing (strip newlines, `**`, backticks, `<!--`, `-->`, and `|`).

8. **Print ingestion summary:**

   ```
   Plan ingested: {plan_file}
   Goal: {goal}
   Branch: {branch}
   Tech stack: {tech_stack}
   Test runner: {test_runner}
   User-facing tasks: {N} of {total_tasks}
   BDD infra: {framework | none detected}
   ```

---

## Phase 1 — User Journey Mapping

Map the user journeys before writing acceptance criteria. Understanding WHO the users are and
WHAT they're trying to accomplish shapes every scenario.

### Step 1a: Persona Discovery

Ask via `AskUserQuestion`:

"I've identified {N} user-facing tasks in this plan. Before I write scenarios, I need to
understand the users:

1. Who is the **primary user** of this feature? (role, goal, technical level)
2. Are there any **edge-case users** who interact differently? (e.g., admin vs. regular user,
   mobile vs. desktop user, first-time vs. returning user, accessibility needs)
3. What is the user's **main goal** when using this feature? (not what the feature does —
   what the user is trying to accomplish)
4. Are there any **known failure modes** or frustrations you want to ensure are handled?

You can answer as briefly as you like. I'll derive the personas from your answers."

Derive at least 2 personas from the answers:
- **Primary persona** — the main user path
- **Edge-case persona** — at least one secondary user with different context or constraints

If the domain is unfamiliar (e.g., the plan involves specialized business logic, compliance
requirements, or domain-specific workflows not obvious from the tech stack), invoke
`/deep-research` in Bridged mode before defining personas:

```
Skill("deep-research", "Research {domain} user journeys and acceptance criteria
patterns to inform user personas for {goal}. Mode: Bridged")
```

Feed research findings into persona definitions.

### Step 1b: Journey Map Construction

For each persona, construct the user journey:

```
Entry Point → Action 1 → Action 2 → ... → Expected Outcome
```

Identify:
- **Happy path** — the successful end-to-end flow
- **Error paths** — what happens when input is invalid, resources are unavailable, or permissions are denied
- **Edge cases** — boundary conditions, unusual sequences, concurrent access

Map each journey step to the plan tasks identified in Phase 0. This forms the traceability backbone.

### Step 1c: Journey Summary

Present the journey map in chat (not to a file yet):

> "I've mapped the user journeys. Here's what I see:
>
> **Primary persona**: [Name] — [description]
> Journey: [Entry] → [A1] → [A2] → [Outcome]
> Happy path covers Tasks 1, 3. Error paths touch Task 2.
>
> **Edge-case persona**: [Name] — [description]
> Journey: [Entry] → [A1 variant] → [Outcome variant]
> Additional coverage for Task 2 edge case.
>
> Proceeding to write acceptance criteria."

---

## Phase 2 — Acceptance Criteria Definition

Write Given/When/Then acceptance criteria for each user-facing behavior identified in Phase 0.

### Scenario Writing Rules

- **1-3 criteria per scenario** — if a scenario needs more than 3, it should be split
- **User-language, not implementation-language** — "the user sees an error message" not
  "the server returns HTTP 422"
- **Concrete, not abstract** — "the login button is disabled" not "validation prevents submission"
- **Include negative scenarios** — what should NOT happen is as important as what should
- **Reference plan task** — every scenario annotated with `{plan-task: Task N}`

### Scenario ID Format

Assign IDs sequentially: `S1`, `S2`, `S3`, etc. IDs are stable — do not renumber.

### Scenario Structure

```
### S{N}: {Title} {plan-task: Task N}

**Persona:** {primary | edge-case}

Given {initial state or precondition}
When {user performs this action}
Then {expected outcome observable to the user}

[And {additional condition} (optional, max 1)]
```

Write scenarios to a working list in memory (not to a file yet). Scenarios will be written
to the test plan document in Phase 4.

### Checkpoint: User Review

After drafting all scenarios, present them in chat as a numbered list with titles only.
Then ask via `AskUserQuestion`:

"I've drafted {N} scenarios across {M} plan tasks. Here's the list:

{S1: title (Task N) — primary}
{S2: title (Task N) — primary}
{S3: title (Task N) — edge-case}
...

Questions:
1. Are there any user behaviors I missed?
2. Are there any scenarios that don't match how users actually interact with this feature?
3. Should any scenarios be split or merged?

(Answer 'looks good' to proceed, or describe adjustments.)"

Incorporate feedback before moving to Phase 3.

### Per-Scenario Quality Review

After incorporating user feedback, batch the finalized scenarios and spawn parallel Sonnet agents
to review them for testability and completeness:

Batch scenarios into groups of 5 and spawn one Agent (sonnet model) per batch (max 8 agents).
Each reviewer receives the full scenario list for cross-reference but reviews only its assigned
batch. Output per reviewer: for each scenario, `testable: yes|no`,
`completeness: [missing conditions]`, `specificity: [vague terms]`. Present scenarios flagged
as not testable or incomplete to the user for revision.

### Scenario Persistence

Write the finalized scenario list to a staging file before proceeding to Phase 3. Use the
memory directory detected in Phase 0: `{memory_dir}/test-plans/{run-id}-scenarios-draft.md`
(fallback: `~/.claude/test-plans/{run-id}-scenarios-draft.md`). The `{run-id}` was generated
in Phase 0 Step 4. This protects against context recycling loss during the multi-step Phase 3-4
interval and ensures concurrent `/test-plan` runs do not collide. Phase 4 reads from this file
instead of memory. After Phase 4 writes the full test plan document, delete the staging file.

---

## Phase 3 — Output Mode Selection

Determine whether to generate BDD `.feature` files in addition to the UAT document by following `[{skill_dir}/references/bdd-integration.md](./references/bdd-integration.md)` (Phase 3):
- **If BDD infra was detected in Phase 0:** Auto-select `UAT + BDD (native integration)` or `UAT + BDD (standalone)` with `BDD Setup Needed: no`, and resolve `bdd_feature_dir` / `bdd_step_dir`.
- **If NO BDD infra was detected:** Run `/deep-research` (External mode via `Skill`) to identify BDD options compatible with `{test_runner}` and `{tech_stack}`, then present Options A (`Manual UAT`), B (`UAT + BDD (feature files only)`), C (`UAT + BDD (native integration)`), and D (`UAT + BDD (standalone)`) via `AskUserQuestion` (falling back to A/B if research is inconclusive).

### Exploratory Charter Decision

Ask via `AskUserQuestion` whether to include exploratory test charters (`Explore [target] with [resources] to discover [information]`) for high-uncertainty areas (external integrations, complex state machines, evolving requirements, load characteristics) and record `include_charters: true/false`.

---

## Phase 4 — Test Plan Document Generation

Write the primary human-readable UAT test plan document:
1. **Determine output path:** Write to `{memory_dir}/test-plans/{run-id}.md` if `{memory_dir}` is confirmed (fallback: `~/.claude/test-plans/{run-id}.md`; never create `hack/` if none exists).
2. **Document format:** Read scenarios from the Phase 2 staging file, write the complete test plan document (`# Test Plan: {Goal}`, `## User Personas`, `## User Journey Map`, `## Scenarios` with Gherkin blocks and manual steps, `## Traceability Matrix`, and optional `## Exploratory Charters`) to `{output_path}` using the exact template in `[{skill_dir}/references/templates.md](./references/templates.md)` (Phase 4), then delete the Phase 2 staging file.

---

## Phase 5 — BDD Staging (Conditional)

**Skip this phase entirely if mode is `Manual UAT`.**
For any BDD mode (`UAT + BDD (feature files only)`, `UAT + BDD (native integration)`, or `UAT + BDD (standalone)`), follow `[{skill_dir}/references/bdd-integration.md](./references/bdd-integration.md)` (Phase 5) to generate one `.feature` file per user-facing task in `{memory_dir}/test-plans/{run-id}-features/` (or `~/.claude/test-plans/{run-id}-features/`) and record `bdd_install_cmd`, `bdd_scaffold_cmd`, `bdd_test_cmd`, `bdd_framework`, `bdd_feature_dir`, and `bdd_step_dir` for Phase 6 (do NOT run the install command — `/swarm` installs on the feature branch).

---

## Phase 6 — Plan File Annotation

Annotate the input plan file by appending the `## Test Plan` section at the end of `{plan_file}` using the exact field labels (`**Test Plan:**`, `**Mode:**`, `**Test Runner:**`, `**Feature Files:**`, `**BDD Setup Needed:**`, `**BDD Scaffold Command:**`, `**BDD Test Command:**`, `**BDD Framework:**`, `**BDD Feature Dir:**`, `**BDD Step Dir:**`, `**Scenarios:**`, `**Personas:**`, and `### Scenario-Task Mapping`) and specification-only overrides in `[{skill_dir}/references/templates.md](./references/templates.md)` (Phase 6).

The appended `## Test Plan` section MUST include the `<!-- PROVENANCE: Generated by /test-plan with explicit user confirmation at checkpoints: ... -->` HTML comment header (from `[{skill_dir}/references/templates.md](./references/templates.md)`) so downstream skills (`/swarm`, `/plan-review`, `/pr-review`, `/fix`, `/quality-gate`) classify any disagreement with user-confirmed test plan choices as `needs-input` rather than `needs-fix`.

---

## Completion Report

After Phase 6, print the completion report:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEST PLAN COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Plan file annotated: {plan_file}
Test plan document: {output_path}
Mode: {mode}
Scenarios: {N} ({primary_count} primary, {edge_case_count} edge case)
Tasks covered: {M} of {total_user_facing_tasks} user-facing tasks

{if BDD (native integration or standalone):}
Feature files: {feature_dir} ({file_count} files)
BDD setup needed: {yes | no} ({framework}{if yes: — /swarm will handle installation}{if no: — already installed})
{end if}

{if BDD (feature files only — specification-only):}
Feature files: {feature_dir} ({file_count} files, specification only)
BDD setup needed: no (Gherkin specifications — tests in {test_runner})
{end if}

{if exploratory charters:}
Exploratory charters: {charter_count}
{end if}

Next: Run /swarm to implement the plan. /swarm will automatically discover
this test plan from the plan file annotation.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Quick Reference

```
### Phase Flow
Phase 0: Ingest plan file (detect test runner + BDD infra) →
Phase 1: Map user journeys →
Phase 2: Write scenarios + user checkpoint →
Phase 3: Select output mode (/deep-research for BDD compatibility) →
Phase 4: Write test plan document →
Phase 5: Generate .feature files (BDD only) →
Phase 6: Annotate plan file (with provenance markers) → Completion report

### Output Paths (two-stage fallback)
1. {memory_dir}/test-plans/{run-id}.md          — test plan document
   {memory_dir}/test-plans/{run-id}-features/   — .feature files (BDD only)
2. ~/.claude/test-plans/{run-id}.md             — fallback
   ~/.claude/test-plans/{run-id}-features/      — fallback (BDD only)

### Plan File Annotation Fields (exact labels — never rename)
**Test Plan:** | **Mode:** | **Test Runner:** | **Feature Files:** | **BDD Setup Needed:**
**BDD Scaffold Command:** | **BDD Test Command:** | **BDD Framework:** | **BDD Feature Dir:** | **BDD Step Dir:**
**Scenarios:** | **Personas:** | ### Scenario-Task Mapping

### BDD Toolchain
Research-driven (Phase 3 /deep-research) when no existing BDD infra.
Fallback table (Phase 5) when BDD already installed:
Python: pytest-bdd | Go: godog+gherkingen | Node.js: Cucumber.js
Rust: cucumber-rs | Java: Cucumber-JVM | Ruby: Cucumber

### Standalone Mode
Not supported. A plan file path is required.
Run /incremental-planning first to produce the plan file.
```
