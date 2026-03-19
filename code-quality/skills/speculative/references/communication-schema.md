# Communication Schema

This file contains all JSON schemas for inter-agent communication within the `/speculative` skill.
Every message passed between agents via `SendMessage` and every file written to the audit trail
follows one of these schemas. The Lead uses these schemas to construct outgoing context and to
validate incoming results.

---

## Context Bundle

Every agent receives this bundle when spawned. The Lead constructs it in Phase 0.

```json
{
  "project": "string — project name from repo root",
  "task": "string — original user task description, verbatim",
  "branch": "string — current git branch name",
  "run_dir": "string — absolute or repo-relative path to hack/speculative/YYYY-MM-DD",
  "tool_guard": "Use Read/Write/Edit/Glob/Grep/Bash for file ops. No raw shell for file reads."
}
```

---

## SpeculativeSpec (Lead → Competitor)

Sent by the Lead to assign a problem to a competitor agent. Each competitor receives the same
problem and criteria but may receive a different `approach_hint`.

```json
{
  "schema": "SpeculativeSpec",
  "problem": "string — what to implement, with full context",
  "success_criteria": [
    {
      "criterion": "string — e.g. 'Correctness', 'Readability', 'Performance'",
      "weight": "number 0-1 — relative importance (all weights must sum to 1.0)",
      "description": "string — what 'success' means for this criterion"
    }
  ],
  "approach_hint": "string | null — specific approach for this competitor to try, or null to let the competitor choose",
  "competitor_id": "string — unique identifier, e.g. 'competitor-1'",
  "worktree_path": "string — absolute path to this competitor's git worktree",
  "output_path": "string — absolute path where competitor must write its ImplementationResult JSON",
  "context": {
    "project": "string",
    "task": "string",
    "branch": "string",
    "run_dir": "string",
    "tool_guard": "string"
  }
}
```

**Notes:**
- If `approach_hint` is null, the competitor is free to choose any valid approach.
- `worktree_path` is the competitor's isolated working directory — all file changes go here.
- `output_path` is where the competitor writes its `ImplementationResult` JSON before signaling completion.

---

## ImplementationResult (Competitor → Lead)

Written by the competitor to `{run_dir}/implementations/competitor-{id}.json` and sent as a
SendMessage summary to the Lead when implementation is complete.

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "string — matches the SpeculativeSpec competitor_id",
  "status": "complete | partial | failed",
  "approach": "string — clear description of the approach taken and the key design decisions",
  "files_created": ["string — paths of newly created files (relative to worktree root)"],
  "files_modified": ["string — paths of files modified (relative to worktree root)"],
  "test_results": {
    "tests_run": "integer — total test cases executed",
    "tests_passed": "integer",
    "tests_failed": "integer",
    "command": "string — exact command used to run tests"
  },
  "self_assessment": [
    {
      "criterion": "string — must match a criterion from SpeculativeSpec.success_criteria",
      "score": "number 1-10 — honest self-assessment (not inflated)",
      "rationale": "string — specific reasoning for this score, not generic praise"
    }
  ],
  "trade_offs": "string — what this approach gives up (be honest — judges penalize inflated self-reports)",
  "failure_reason": "string | null — if status is 'failed', why it failed",
  "turn_count": "integer — number of tool-call rounds completed since spawning"
}
```

**Notes:**
- `self_assessment` must cover EVERY criterion in the SpeculativeSpec. Missing criteria will
  be flagged by the lead and the judge.
- `trade_offs` is required. Approaches with no stated trade-offs will be treated as incomplete
  self-assessments by the judge.
- If `status` is `partial`, the competitor must describe what was completed and what was not
  in the `approach` field.

---

## JudgmentRequest (Lead → Judge)

Sent by the Lead to the judge agent with all competitor results and the original spec.

```json
{
  "schema": "JudgmentRequest",
  "problem": "string — original problem statement",
  "success_criteria": [
    {
      "criterion": "string",
      "weight": "number 0-1",
      "description": "string"
    }
  ],
  "competitor_results": [
    {
      "competitor_id": "string",
      "worktree_path": "string — path to this competitor's worktree for code inspection",
      "result_path": "string — path to this competitor's ImplementationResult JSON"
    }
  ],
  "output_path": "string — where to write JudgmentResult JSON"
}
```

---

## JudgmentResult (Judge → Lead)

Written by the judge to `{run_dir}/judgment.json` and sent as a SendMessage summary to the Lead.

```json
{
  "schema": "JudgmentResult",
  "winner": "string — competitor_id of the best implementation, OR 'hybrid' if combining is recommended",
  "scoring_matrix": [
    {
      "competitor_id": "string",
      "scores": [
        {
          "criterion": "string — matches success_criteria criterion name",
          "score": "number 1-10 — judge's independent assessment",
          "rationale": "string — specific evidence from the implementation"
        }
      ],
      "weighted_total": "number — sum of (score * weight) across all criteria"
    }
  ],
  "rationale": "string — narrative explanation of why the winner was chosen over alternatives",
  "code_inspected": ["string — worktree paths/files the judge read directly (empty if none)"],
  "hybrid_recommended": "boolean — true if combining elements from multiple competitors would be better than either alone",
  "hybrid_elements": [
    "string — specific element to combine, with source (e.g., 'competitor-1 error handling approach: its use of Result types prevents unchecked exceptions')"
  ]
}
```

**Notes:**
- `winner` must be a valid `competitor_id` from the JudgmentRequest, OR the string `"hybrid"`.
- If `winner` is `"hybrid"`, `hybrid_recommended` must be `true` and `hybrid_elements` must
  be non-empty with specific, actionable descriptions.
- `hybrid_elements` entries must name the source competitor and describe the specific element
  precisely enough for a synthesis agent to implement it without seeing the original code.
- The judge's `scores` are independent of the competitors' self-assessments. Disagreement is
  expected — the judge has full context that competitors did not.

---

## Audit Trail Directory Structure

All speculative run artifacts are written under a date-scoped directory. Multiple runs on the
same date append a sequence number.

```
hack/speculative/
└── YYYY-MM-DD/                    # or YYYY-MM-DD-2, YYYY-MM-DD-3 for multiple runs
    ├── implementations/
    │   ├── competitor-1.json          # competitor-1's ImplementationResult
    │   ├── competitor-2.json          # competitor-2's ImplementationResult
    │   └── competitor-N.json          # (up to competitor-4)
    ├── judgment.json                  # Judge's JudgmentResult
    └── speculative-report.md          # Human-readable completion report
```

The Lead resolves `{run_dir}` once in Phase 0 and passes it to all agents in their context
bundle. Agents write directly to their assigned `output_path` without needing to resolve the
date themselves.
