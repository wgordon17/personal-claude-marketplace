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
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Agent
  - AskUserQuestion
  - Skill
---

# Test Plan Skill

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
   `code-quality/references/project-memory-reference.md` (Directory Detection and Worktree
   Resolution sections). Then read:
   - `{memory_dir}/PROJECT.md` — architectural decisions, domain context
   - `{memory_dir}/LESSONS.md` — past lessons (if exists). Silently incorporate.

4. **Generate `{run-id}`** — Generate the run-id early so it is available for the staging file
   in Phase 2. Follow the Run-ID Naming Convention in
   `code-quality/references/project-memory-reference.md`: `{branch-slug}-{unix-timestamp}`
   (e.g., `feat-auth-1711388400`).

5. **Identify user-facing tasks** — Not every plan task represents a user-visible behavior.
   Scan task titles and steps for user-facing surface area:
   - UI changes, API endpoints, CLI commands, configuration options
   - Error messages, validation rules, success states
   - Tasks marked as "internal refactor" or "infrastructure" with no user-visible changes
     are noted but do not generate scenarios

6. **Detect test runner and BDD infrastructure** — Search project dependency files for both
   the project's test runner and any existing BDD tooling. Both are needed: the test runner
   informs Phase 3's BDD framework recommendations; the BDD detection determines whether
   Phase 3 auto-selects or asks the user.

   **Test runner detection:**

   | File | Pattern | Test Runner |
   |---|---|---|
   | `package.json` | `vitest` | Vitest |
   | `package.json` | `jest` or `@jest/` | Jest |
   | `package.json` | `mocha` | Mocha |
   | `pyproject.toml` / `requirements*.txt` | `pytest` | pytest |
   | `go.mod` | (Go convention) | go test |
   | `Cargo.toml` | (Rust convention) | cargo test |
   | `build.gradle` / `pom.xml` | `junit` or `testng` | JUnit / TestNG |
   | `Gemfile` | `rspec` | RSpec |
   | `Gemfile` | `minitest` | Minitest |

   Search using Grep with `output_mode: "files_with_matches"`. Record `test_runner`
   (or `unknown`). If multiple match, prefer the one in test scripts
   (e.g., `"test": "vitest"` in `package.json` scripts).

   **BDD infrastructure detection:**

   | File | Pattern | Framework |
   |---|---|---|
   | `pyproject.toml` | `pytest-bdd` | Python/pytest-bdd |
   | `requirements*.txt` | `pytest-bdd` | Python/pytest-bdd |
   | `go.mod` | `godog` or `github.com/cucumber` | Go/godog |
   | `package.json` | `@cucumber/cucumber` or `cucumber` | Node.js/Cucumber.js |
   | `Cargo.toml` | `cucumber` | Rust/cucumber-rs |
   | `build.gradle` or `pom.xml` | `cucumber-java` | Java/Cucumber-JVM |
   | `Gemfile` | `cucumber` | Ruby/Cucumber |

   Search using Grep with `output_mode: "files_with_matches"`. Record detected BDD
   framework (or `none`) and the detected file path for Phase 5 (BDD Staging).

7. **Print ingestion summary:**

   ```
   Plan ingested: {plan_file}
   Goal: {goal}
   Branch: {branch}
   Tech stack: {tech_stack}
   Test runner: {test_runner | unknown}
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

Determine whether to generate BDD `.feature` files in addition to the UAT document.

### Decision Logic

**If BDD infra detected in Phase 0:**
Automatically select UAT + BDD mode. No question needed — the project already uses BDD,
so `.feature` files are expected. Print:

```
BDD infra detected ({framework} in {file}). Generating UAT document + .feature files.
```

Record `mode` based on the detected framework's relationship to the test runner: if the
detected BDD framework is a plugin for `test_runner` (e.g., `pytest-bdd` for pytest,
`jest-cucumber` for Jest), set `mode` to `UAT + BDD (native integration)`. If it is a
standalone framework (e.g., `godog`, `Cucumber.js`, `cucumber-rs`), set `mode` to
`UAT + BDD (standalone)`.

**If NO BDD infra detected:**

Before presenting options, research BDD integration options that are compatible with the
project's actual test runner. This prevents recommending frameworks that conflict with the
existing test setup (e.g., suggesting Cucumber.js for a Vitest project).

**If `test_runner` is `unknown`:** ask the user first via `AskUserQuestion`:
"I couldn't detect your test runner automatically. What test framework does this project use?
(e.g., Vitest, Jest, pytest, go test)" — then use the answer as `test_runner` for the
research query below.

```
Skill("deep-research", "BDD and Gherkin integration options for {test_runner}
in {tech_stack} projects. Compare: (1) native BDD plugins that integrate directly with
{test_runner} (e.g., vitest-cucumber-plugin for Vitest, jest-cucumber for Jest, pytest-bdd
for pytest), (2) standalone BDD frameworks (Cucumber.js, godog, cucumber-rs) and their
compatibility with {test_runner}, (3) using Gherkin .feature files as specification-only
documentation (not executable). For each viable option: package name, registry page,
last release date, open issues count, compatibility notes with {test_runner}, setup
complexity. Exclude options unmaintained (no release in 12+ months) or incompatible
with {test_runner}. Mode: External")
```

**Fallback:** If `/deep-research` is unavailable, errors, or returns no viable options,
fall back to the static BDD Toolchain Reference table in Phase 5. Present options A and B
only (no C/D), noting: "BDD framework research was inconclusive. Options C/D omitted.
If you know which BDD framework you want, choose A now and set it up manually."

Use the research findings to build a project-aware options list. Present via `AskUserQuestion`:

"No BDD framework detected. Your project uses **{test_runner}** for testing.

Based on research, here are the BDD options compatible with {test_runner}:

A) **Manual UAT checklist only** (no BDD)
   — Human-readable walkthrough with pass/fail checkboxes. No BDD setup required.

B) **Gherkin specification files only** (documentation, not executable)
   — Generates .feature files as living specifications. Tests remain in {test_runner}.
   — No additional framework needed.

{If research found a native {test_runner} BDD plugin:}
C) **{test_runner}-native BDD** ({plugin_name})
   — {1-sentence from research: what it does, maintenance status}
   — Tests run through {test_runner}. No second test runner.

{If research found a compatible standalone BDD framework:}
D) **Standalone BDD framework** ({standalone_name})
   — Adds a separate test runner alongside {test_runner}.
   — {1-sentence from research: tradeoffs}

{Omit C if no viable native plugin found. Omit D if no compatible standalone found.
If neither C nor D is viable, note this and explain why.}

Recommendation: {research-informed — prefer native integration (C) over standalone (D)
over specification-only (B). Recommend A if the plan is simple and BDD adds no value.}

Which option?"

Record the selected mode as `mode`:
- A → `Manual UAT`
- B → `UAT + BDD (feature files only)` — set `bdd_framework: none (specification only)`,
  `BDD Setup Needed: no`. Omit install/scaffold/test commands from annotation.
- C → `UAT + BDD (native integration)` — use the researched plugin's install/scaffold/test
  commands. Record `bdd_framework` as the plugin name.
- D → `UAT + BDD (standalone)` — use the standalone framework's commands.

Store the research-derived `bdd_install_cmd`, `bdd_scaffold_cmd`, `bdd_test_cmd`,
`bdd_framework`, `bdd_feature_dir`, and `bdd_step_dir` from the selected option.
For option B, these are all empty/none — Phase 6 handles this format.

### Exploratory Charter Decision

Ask as a follow-up (or combine with the above if Manual UAT):

"Should I include exploratory test charters for any high-uncertainty areas?
Exploratory charters are brief mission statements for manual testing sessions where
the behavior isn't fully specified: 'Explore [target] with [resources] to discover [information]'.

Answer 'yes' if any plan tasks involve:
- New integrations with external services
- Complex state machines or multi-step workflows
- Areas where requirements are still evolving
- Performance or load characteristics under real conditions

(yes/no or describe the areas)"

Record `include_charters: true/false` and any specified areas.

---

## Phase 4 — Test Plan Document Generation

Write the test plan document. This is the primary artifact — a human-readable document
that a user can print out, follow step by step, and mark pass/fail.

### Determine Output Path

Use the two-stage fallback:

1. **If `{memory_dir}` is confirmed** (detected in Phase 0): write to
   `{memory_dir}/test-plans/{run-id}.md`
   (create `test-plans/` subdirectory if it doesn't exist)
2. **If no memory dir found**: fall back to `~/.claude/test-plans/{run-id}.md`
   (create directory if it doesn't exist)

Use the `{run-id}` generated in Phase 0 Step 4.

**Do NOT create a `hack/` directory if one doesn't exist.** Only write to confirmed existing
memory directories or the `~/.claude/` fallback.

**Fallback limitation:** When the `~/.claude/` fallback is used, downstream skills validate
test plan paths against their own `{memory_dir}/test-plans/`. If the downstream skill's
`{memory_dir}` differs from `~/.claude/`, path validation rejects the test plan (empty string
fallback). The test plan document remains useful standalone for manual UAT reference.

Print: "Writing test plan to: `{output_path}`"

### Document Format

Write the complete test plan document using this exact format:

```markdown
# Test Plan: {Goal from plan header}

**Source Plan:** {plan_file_path}
**Branch:** {branch from plan header}
**Date:** {YYYY-MM-DD}
**Mode:** {mode}

---

## User Personas

- **Primary — {Name}:** {description, goals, context, technical level}
- **Edge Case — {Name}:** {description, goals, context, what makes them different}

---

## User Journey Map

### {Primary Persona Name}

{Entry Point} → {Action 1} → {Action 2} → {Expected Outcome}

Happy path: {1-sentence description of the successful flow}
Error paths: {list the failure conditions}

### {Edge Case Persona Name}

{Entry Point variant} → {Action 1 variant} → {Outcome variant}

---

## Scenarios

### S{N}: {Title} {plan-task: Task N}

**Persona:** {primary | edge-case}
**Preconditions:** {setup required before the test can be executed}

```gherkin
Given {initial state}
When {user action}
Then {expected outcome}
```

**Manual Steps:**
- [ ] {Step 1: human-readable instruction}
- [ ] {Step 2: human-readable instruction}
- [ ] Verify: {what to check and what the expected result looks like}

**Pass criteria:** {one sentence — what "pass" looks like}
**Fail indicators:** {one sentence — what "fail" looks like}

---

[repeat for each scenario]

---

## Traceability Matrix

| Plan Task | Scenario(s) | .feature File | Status |
|---|---|---|---|
| Task 1: {title} | S1, S2 | {feature_file | N/A} | Pending |
| Task 2: {title} | S3 | {feature_file | N/A} | Pending |
| Task N: {title} (internal) | — | — | Not covered (internal only) |

---

## Exploratory Charters

[Include only if `include_charters == true`. Omit section entirely if not.]

### Charter 1: {Area}
Explore **{target system or feature area}** with **{available resources: dev tools, test data, user accounts}**
to discover **{what you want to learn: edge cases, failure modes, performance limits}**.

Duration: 30-60 minutes
Persona: {which persona to use}
Record: {what to log during the session}

[repeat for each charter]
```

Write the document to `{output_path}` using the Write tool.

---

## Phase 5 — BDD Staging (Conditional)

**Skip this phase entirely if mode is `Manual UAT`.**

Run when mode is any BDD variant: `UAT + BDD (feature files only)`,
`UAT + BDD (native integration)`, or `UAT + BDD (standalone)`.

### Feature File Generation

For each user-facing plan task, generate one `.feature` file containing all scenarios
for that task. Feature files use standard Gherkin format:

```gherkin
Feature: {task title}

  Background:
    {shared preconditions across scenarios, if any}

  Scenario: {S1 title}
    Given {initial state}
    When {user action}
    Then {expected outcome}

  Scenario: {S2 title}
    Given {initial state}
    When {user action}
    Then {expected outcome}
```

**Feature file naming:** `{task-title-kebab-case}.feature`
Example: `user-login-flow.feature`, `password-reset.feature`

**Output directory:** `{memory_dir}/test-plans/{run-id}-features/`
(or `~/.claude/test-plans/{run-id}-features/` for the fallback)

Write each `.feature` file using the Write tool.

### BDD Toolchain Reference

**If Phase 3 ran /deep-research** (no existing BDD infra): use the research-derived values
stored in Phase 3. The research already identified the correct framework, install commands,
and compatibility with the project's test runner. Skip this section.

**If Phase 3 auto-selected BDD** (existing BDD infra detected in Phase 0): derive values
from the detected framework using this fallback table:

| Language | Framework | Install Command | Scaffold Command | Test Command |
|---|---|---|---|---|
| Python | pytest-bdd | `uv add --dev pytest-bdd>=7.0` | `pytest-bdd generate {feature}` or `pytest --generate-missing` | `uv run pytest` |
| Go | godog + gherkingen | `go get github.com/cucumber/godog@v0.15.0` | `gherkingen {feature.file}` | `go test ./features/...` |
| Node.js | Cucumber.js | `npm install --save-dev @cucumber/cucumber@^11.0` | `npx cucumber-js` (auto-generates snippets) | `npx cucumber-js` |
| Rust | cucumber-rs | `cargo add --dev cucumber` | Run tests with `.fail_on_skipped()` — reports unmatched steps | `cargo test` |
| Java | Cucumber-JVM | `<dependency>io.cucumber:cucumber-java</dependency>` | Run features — auto-generates snippets | `mvn test` |
| Ruby | Cucumber | `gem install cucumber` | `cucumber --dry-run` — generates undefined step snippets | `bundle exec cucumber` |

This table is a fallback for projects that already have BDD installed. For new BDD setups,
Phase 3's /deep-research provides test-runner-aware recommendations that supersede this table.

Record:
- `bdd_install_cmd` — the install command (from research or fallback table)
- `bdd_scaffold_cmd` — the scaffold command to generate step definition skeletons
- `bdd_test_cmd` — the command to run the BDD test suite
- `bdd_framework` — the framework name
- `bdd_feature_dir` — where `.feature` files will live in the source tree (e.g., `features/`, `tests/acceptance/`)
- `bdd_step_dir` — where step definitions will live (e.g., `steps/`, `step_definitions/`, `*_test.go`)

These values are written into the plan file annotation in Phase 6 so `/swarm` Phase 0 knows
what to install and run without re-detecting.

**Do NOT run the install command.** Recording it in the annotation is the contract.
`/swarm` handles installation on the feature branch.

---

## Phase 6 — Plan File Annotation

Annotate the input plan file by appending a `## Test Plan` section. This annotation is
what all downstream skills parse — field labels must match exactly.

### Annotation Format

Append to the end of `{plan_file}` using the Edit tool:

```markdown
## Test Plan

<!-- PROVENANCE: Generated by /test-plan with explicit user confirmation at checkpoints:
     - Personas: confirmed Phase 1 (AskUserQuestion)
     - Scenarios: confirmed Phase 2 (AskUserQuestion checkpoint + per-scenario review)
     - Output Mode & BDD decisions: confirmed Phase 3 (AskUserQuestion)
     All fields below reflect user-confirmed decisions.
     Downstream skills: if you disagree with a choice here, classify as needs-input
     (require user confirmation before changing), not needs-fix. Do not silently
     overwrite user decisions. -->

**Test Plan:** {output_path}
**Mode:** {mode} (one of: Manual UAT, UAT + BDD (feature files only), UAT + BDD (native integration), UAT + BDD (standalone))
**Feature Files:** {memory_dir}/test-plans/{run-id}-features/ (omit line if Manual UAT)
**BDD Setup Needed:** {yes | no} (if yes: `{bdd_install_cmd}`)
**BDD Scaffold Command:** `{bdd_scaffold_cmd}` (omit line if Manual UAT or specification-only)
**BDD Test Command:** `{bdd_test_cmd}` (omit line if Manual UAT or specification-only)
**BDD Framework:** {bdd_framework} (omit line if Manual UAT)
**BDD Feature Dir:** {bdd_feature_dir} (omit line if Manual UAT)
**BDD Step Dir:** {bdd_step_dir} (omit line if Manual UAT or specification-only)
**Scenarios:** {total scenario count}
**Personas:** {Primary Persona Name}, {Edge Case Persona Name}

### Scenario-Task Mapping

| Plan Task | Scenario ID | Scenario Title |
|---|---|---|
| Task 1: {title} | S1 | {S1 title} |
| Task 1: {title} | S2 | {S2 title} |
| Task 3: {title} | S3 | {S3 title} |
```

**Field label format is fixed.** Downstream skills match on exact bold field names
(`**Test Plan:**`, `**Mode:**`, `**Feature Files:**`, etc.). Do not reorder or rename.

**Specification-only mode** (Phase 3 option B): When the user chose Gherkin files as
documentation only (not executable), write:
- `**BDD Setup Needed:** no (feature files are Gherkin specifications; tests implemented in {test_runner})`
- `**BDD Framework:** none (specification only)`
- Include `**BDD Feature Dir:**` (files still exist for documentation)
- Omit: `**BDD Scaffold Command:**`, `**BDD Test Command:**`, `**BDD Step Dir:**`

The `**BDD Setup Needed:** yes` signal is what `/swarm` Phase 0 uses to decide whether to
install the BDD framework on the feature branch.

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

{if BDD:}
Feature files: {feature_dir} ({file_count} files)
BDD setup needed: {yes | no} ({framework} — /swarm will handle installation)
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
**Test Plan:** | **Mode:** | **Feature Files:** | **BDD Setup Needed:**
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
