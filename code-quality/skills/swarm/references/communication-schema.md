# Communication Schema

This file contains all JSON schemas for inter-agent communication within the `/swarm` skill.
Every message passed between agents via `SendMessage` and every file written to the audit trail
follows one of these schemas. The Lead uses these schemas to validate incoming messages and to
construct outgoing context for each agent. Agents that deviate from schema should have their
output flagged and the Lead should request a corrected response before proceeding.

---

## Context Bundle Schema

Every agent receives this bundle when spawned. The Lead constructs it in Phase 0 and updates
the `key_files` list after the Architect completes.

```json
{
  "project": "string — project name from repo root",
  "task": "string — original user task description, verbatim",
  "branch": "string — current git branch name",
  "run_dir": "string — absolute or repo-relative path to hack/swarm/YYYY-MM-DD",
  "key_files": [
    "string — file paths identified by architect as central to this task"
  ],
  "tool_guard": "Use Read/Write/Edit/Glob/Grep/Bash for file ops. No raw shell for file reads."
}
```

**Usage:** Embed the full context bundle as a JSON block at the top of every agent prompt.
The `run_dir` field enables agents to write their outputs to the correct audit trail location
without needing to know the date or sequence number.

---

## Architect Plan Schema

The Architect writes this to `{run_dir}/architect-plan.json` and sends a summary to the Lead. The Lead
reads the file and uses `components` to drive Phase 3 pipeline routing.

```json
{
  "goal": "string — one-sentence description of what this implementation achieves",
  "components": [
    {
      "id": "string — short identifier, e.g. 'auth-middleware'",
      "name": "string — human-readable component name",
      "description": "string — what this component does and why",
      "files_to_create": ["string — paths of new files to create"],
      "files_to_modify": ["string — paths of existing files to change"],
      "dependencies": ["string — component IDs this component depends on"],
      "implementation_order": "integer — 1-based sequence within independent groups",
      "testing_strategy": "string — what to test and how (unit/integration/e2e)",
      "risks": ["string — risks specific to this component"],
      "estimated_complexity": "low | medium | high"
    }
  ],
  "component_dependency_graph": {
    "description": "string — text description of dependency ordering",
    "independent_groups": [
      ["string — component IDs that can be implemented in parallel"]
    ],
    "sequential_chains": [
      ["string — component IDs that must be sequential, in order"]
    ]
  },
  "pipeline_feasible": "boolean — true if 2+ independent components exist",
  "pipeline_notes": "string — explains why pipeline is or is not feasible",
  "global_risks": ["string — risks affecting the whole implementation"],
  "data_model_changes": "string | null — description of schema/model changes, or null",
  "api_changes": "string | null — description of public API surface changes, or null"
}
```

**Note:** If `pipeline_feasible` is `false`, the Lead runs Phase 3 in sequential mode using the
same agents and protocol — just no parallelism. See `references/pipeline-model.md`.

---

## Pipeline Handoff Schemas

These schemas cover every message exchanged during Phase 3 pipeline execution.

### ComponentAssignment (Lead → Implementer)

Sent by the Lead to assign a component to the Implementer (new component or revision).

```json
{
  "$schema": "component-assignment",
  "component_id": "C1",
  "component_name": "Human-readable name",
  "component_spec": "Full component spec from architect plan",
  "files_to_create": ["path/to/new.py"],
  "files_to_modify": ["path/to/existing.py"],
  "dependencies_completed": ["C0"],
  "testing_strategy": "From architect's plan",
  "attempt": 1
}
```

For revisions, add `"revision": true` and `"previous_feedback": [...]` with the Reviewer's issues array.
For test fix cycles, add `"fix_tests": true` and `"failures": [...]` from the TestResult.

### ComponentHandoff (Implementer → Lead)

Sent when the Implementer completes a component and is ready for review.

```json
{
  "schema": "ComponentHandoff",
  "component_id": "string — matches id from architect plan",
  "status": "complete | blocked",
  "files_created": ["string — paths of newly created files"],
  "files_modified": ["string — paths of files that were changed"],
  "summary": "string — what was implemented and any notable decisions",
  "notes": "string | null — anything the Reviewer should pay attention to",
  "attempt": "integer — 1 for first attempt, 2+ for retries after rejection",
  "turn_count": "integer — agent's tool-call round count since spawn (for context health tracking)"
}
```

### ReviewResult (Reviewer → Lead)

Sent when the Reviewer completes assessment of a component.

```json
{
  "schema": "ReviewResult",
  "component_id": "string — matches ComponentHandoff component_id",
  "verdict": "approved | rejected",
  "issues": [
    {
      "severity": "critical | high | medium | low",
      "file": "string — file path",
      "line": "integer | null — line number if known",
      "description": "string — what is wrong",
      "suggested_fix": "string — specific, actionable fix recommendation"
    }
  ],
  "notes": "string | null — overall assessment or context for the Implementer",
  "turn_count": "integer — agent's tool-call round count since spawn (for context health tracking)"
}
```

**Lead routing:** If `verdict` is `approved`, forward to Test-Writer. If `rejected`, add context
about the attempt number and route all `issues` back to the Implementer. If attempt reaches 3,
stash and escalate to user.

### TestRequest (Lead → Test-Writer)

Sent after Reviewer approves a component, requesting test authorship.

```json
{
  "schema": "TestRequest",
  "component_id": "string — matches approved component",
  "component_name": "string — human-readable name for context",
  "files": ["string — all files created or modified for this component"],
  "behavior_to_test": "string — from architect's testing_strategy for this component",
  "testing_strategy": "unit | integration | e2e | mixed"
}
```

### TestHandoff (Test-Writer → Lead)

Sent when the Test-Writer completes writing tests for a component.

```json
{
  "schema": "TestHandoff",
  "component_id": "string — matches TestRequest component_id",
  "test_files": ["string — paths to the test files written"],
  "test_count": "integer — number of test cases written",
  "coverage": "string — brief description of what scenarios are covered",
  "run_command": "string — exact command to execute the tests",
  "turn_count": "integer — agent's tool-call round count since spawn (for context health tracking)"
}
```

### TestExecution (Lead → Test-Runner)

Sent to the Test-Runner to execute tests for a completed component.

```json
{
  "schema": "TestExecution",
  "component_id": "string — matches TestHandoff component_id",
  "test_files": ["string — test files to run"],
  "run_command": "string — exact command to execute"
}
```

### TestResult (Test-Runner → Lead)

Sent after the Test-Runner executes tests for a component.

```json
{
  "schema": "TestResult",
  "component_id": "string — matches TestExecution component_id",
  "status": "pass | fail",
  "tests_run": "integer — total tests executed",
  "tests_passed": "integer",
  "tests_failed": "integer",
  "failures": [
    {
      "test_name": "string — test function or case name",
      "file": "string — test file path",
      "error": "string — failure message or exception"
    }
  ],
  "run_command": "string — the command that was executed",
  "turn_count": "integer — agent's tool-call round count since spawn (for context health tracking)"
}
```

**Lead routing:** If `status` is `pass`, mark component complete and move to next. If `fail`,
route `failures` back to the Implementer with the original component context for targeted fixes.
After fixes, re-submit through the full Review → Test cycle.

---

## Review Finding Schema

Used by all Phase 4 reviewers (Security, QA, Code-Reviewer, Performance, and optional domain
reviewers). Each reviewer writes a `ReviewFindings` object to `{run_dir}/reviews/<name>.json`
and sends a summary to the Lead.

```json
{
  "schema": "ReviewFindings",
  "reviewer": "string — reviewer role name (security, qa, code-reviewer, performance, ui, api, db)",
  "timestamp": "string — ISO 8601 datetime",
  "summary": {
    "total_findings": "integer",
    "by_severity": {
      "critical": "integer",
      "high": "integer",
      "medium": "integer",
      "low": "integer",
      "informational": "integer"
    },
    "verdict": "clean | findings"
  },
  "findings": [
    {
      "id": "string — unique ID with prefix (see table below)",
      "severity": "critical | high | medium | low | informational",
      "category": "string — issue category (e.g. injection, auth, complexity, naming)",
      "file": "string — file path",
      "line": "integer | null — line number if applicable",
      "description": "string — clear description of the issue",
      "evidence": "string — quoted code snippet or specific observation",
      "suggested_fix": "string — specific, actionable recommendation",
      "risk": "string — what could go wrong if this is not addressed"
    }
  ]
}
```

### Finding ID Prefix Convention

| Reviewer | ID Prefix | Example |
|----------|-----------|---------|
| Security | SEC | SEC-001 |
| QA | QA | QA-001 |
| Code-Reviewer | CR | CR-001 |
| Performance | PERF | PERF-001 |
| UI Reviewer | UI | UI-001 |
| API Reviewer | API | API-001 |
| DB Reviewer | DB | DB-001 |

IDs are sequential within each reviewer's output (SEC-001, SEC-002, etc.). The Lead uses these
IDs when routing findings to the Fixer and when recording disposition in the audit trail.

---

## Fix Summary Schema

The Fixer sends this to the Lead after completing Phase 5 work. Written to
`{run_dir}/fix-summary.json`.

```json
{
  "schema": "FixSummary",
  "findings_fixed": ["string — finding IDs that were fully resolved"],
  "findings_deferred": [
    {
      "id": "string — finding ID",
      "reason": "string — why it was deferred (e.g. requires architectural change, out of scope)"
    }
  ],
  "files_modified": ["string — paths of files changed during fixing"],
  "fixes": [
    {
      "finding_id": "string",
      "description": "string — what was changed to address this finding",
      "file": "string",
      "line_range": "string | null — e.g. '42-48' for changed range"
    }
  ],
  "deferred_items": [
    {
      "finding_id": "string",
      "recommended_action": "string — what the team should do with this later"
    }
  ]
}
```

---

## Verification Result Schema

The Verifier sends this to the Lead after Phase 7 test execution. Written to
`{run_dir}/verification-result.json`.

```json
{
  "schema": "VerificationResult",
  "lint": "pass | fail | skipped",
  "lint_issues": [
    {
      "file": "string",
      "line": "integer | null",
      "rule": "string — lint rule identifier",
      "message": "string"
    }
  ],
  "tests_run": "integer",
  "tests_passed": "integer",
  "tests_failed": "integer",
  "failures": [
    {
      "test_name": "string",
      "file": "string",
      "error": "string"
    }
  ],
  "status": "pass | fail",
  "baseline_comparison": {
    "baseline_passed": "integer — from Phase 0 baseline",
    "baseline_failed": "integer — pre-existing failures from Phase 0",
    "net_new_failures": "integer — failures not present in baseline",
    "regression": "boolean — true if net_new_failures > 0"
  }
}
```

---

## Agent Health Monitoring Schemas

These schemas support context health tracking and agent recycling during long-running phases.

### Turn Count Field (embedded in all message types)

Every agent includes a `turn_count` field in its structured SendMessage payloads. For agents that
send multiple messages during their lifecycle (Implementer, Test-Writer, Reviewer in Phase 3),
each message includes the agent's current turn count so the Lead can track context accumulation.

The `turn_count` is the agent's self-reported count of tool-call rounds it has completed since
spawning. Agents increment this by counting each time they make a tool call and receive a result.

This field is added to all existing message types — `ComponentHandoff`, `ReviewResult`,
`TestHandoff`, and `TestResult` all include it:

```json
{
  "turn_count": "integer — agent's self-reported tool-call round count since spawn"
}
```

Example: a `ComponentHandoff` with turn tracking:
```json
{
  "schema": "ComponentHandoff",
  "component_id": "auth-middleware",
  "status": "complete",
  "files_created": ["src/auth/middleware.py"],
  "files_modified": ["src/auth/__init__.py"],
  "summary": "Implemented auth middleware with JWT validation",
  "notes": null,
  "attempt": 1,
  "turn_count": 18
}
```

### HandoffRequest (Lead → Agent)

Sent by the Lead when an agent's `turn_count` exceeds the recycling threshold (default: 25
turns). The agent must respond with a `HandoffSummary` before being shut down.

```json
{
  "schema": "HandoffRequest",
  "reason": "context_rotation",
  "message": "You are approaching context limits. Please send a HandoffSummary with your current state so a replacement agent can continue your work."
}
```

### HandoffSummary (Agent → Lead)

Sent by an agent in response to a `HandoffRequest`. Contains everything a replacement agent
needs to continue the work.

```json
{
  "schema": "HandoffSummary",
  "agent_role": "string — implementer | reviewer | test-writer",
  "turn_count": "integer — final turn count",
  "completed_work": [
    {
      "component_id": "string",
      "status": "complete | in_progress",
      "files_modified": ["string"],
      "summary": "string — what was done"
    }
  ],
  "in_progress": {
    "component_id": "string | null — component currently being worked on",
    "status": "string — what has been done so far on this component",
    "files_modified": ["string — files already changed"],
    "remaining": "string — what still needs to be done",
    "key_decisions": ["string — important design decisions that must be preserved"]
  },
  "context_notes": "string — any important state, gotchas, or reasoning the replacement needs"
}
```

**Lead protocol:** After receiving a `HandoffSummary`, the Lead shuts down the agent and spawns
a replacement with the original prompt plus the `HandoffSummary` content appended as a
`=== CONTINUATION FROM PREVIOUS AGENT ===` block.

---

## Agent Silent Failure Detection

When a teammate goes idle without having sent a completion message or marked their task as done,
the Lead treats this as a silent failure. The Lead does NOT receive the specific error message
(e.g., "Context limit reached") — it only sees silence.

### Detection Heuristic

The Lead maintains a simple tracking table:

```
| Agent | Last Message Type | Task Complete? | Action |
|-------|-------------------|----------------|--------|
| implementer | ComponentHandoff | No | Monitor |
| reviewer | ReviewResult | No | Monitor |
| test-writer | TestHandoff | Yes | OK |
```

**Silent failure = teammate idle + task not completed + no final message.**

### Recovery Protocol

1. **Check what the agent accomplished:** Run `git diff` and `Glob` to see file changes
2. **Check the audit trail:** Read any files the agent wrote to `{run_dir}/`
3. **Spawn replacement agent** with:
   - Original agent prompt
   - `=== RECOVERY FROM FAILED AGENT ===` block describing what was accomplished
   - File state summary from `git diff`
4. **Log the failure** to `{run_dir}/errors.log`

If the same agent role fails twice, escalate to the user via `AskUserQuestion`.

---

## Audit Trail Directory Structure

All swarm run artifacts are written under a date-scoped directory. Multiple runs on the same
date append a sequence number.

```
hack/swarm/
└── YYYY-MM-DD/               # or YYYY-MM-DD-2, YYYY-MM-DD-3 for multiple runs
    ├── architect-plan.json         # Architect's component decomposition plan
    ├── fix-summary.json            # Fixer's summary (Phase 5, if run)
    ├── verification-result.json    # Verifier's final test results (Phase 7)
    ├── swarm-report.md             # Human-readable completion report
    └── reviews/
        ├── security.json           # Security reviewer findings (ReviewFindings)
        ├── qa.json                 # QA reviewer findings (ReviewFindings)
        ├── code-reviewer.json      # Code-Reviewer findings (ReviewFindings)
        ├── performance.json        # Performance reviewer findings (ReviewFindings)
        ├── ui.json                 # UI reviewer findings (if triggered)
        ├── api.json                # API reviewer findings (if triggered)
        └── db.json                 # DB reviewer findings (if triggered)
```

The Lead resolves `{run_dir}` once in Phase 0 and passes it to all agents in their context
bundle. Agents write directly to `{run_dir}/<filename>` without needing to know the date.
