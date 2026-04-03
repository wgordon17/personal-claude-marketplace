# Finding Classification

Canonical reference for finding classification, LoE estimation, and Fixer protocol. Referenced by
the quality-gate (Rounds 2–4), swarm Fixer phase, unfuck implementation agents, and orchestration
verification.

## Anti-Deferral Principle

Every finding is work that needs doing. You do not self-prioritize. When given findings, you process
ALL of them. Your assessment of a finding's importance is not a valid reason to defer. The only
valid reasons to not address a finding are: (1) a specific, documented technical blocker you cannot
resolve, or (2) the user explicitly excluded it via AskUserQuestion.

## Finding Classification

Two-tier taxonomy:

- `needs-fix`: The reviewer has sufficient context to describe the problem AND the fix is within the
  agent's capability to resolve independently. No user decision required.
- `needs-input`: The fix requires a decision the agent cannot make alone — architectural choice,
  scope decision, external dependency, user preference, or the fix would change behavior in ways the
  user should approve. The agent reports the finding with an LoE estimate; the user decides.

**Classification guidance for reviewers:**

- Default to `needs-fix`. Only use `needs-input` when you genuinely cannot determine the correct
  resolution.
- "I'm not sure if this is worth fixing" is NOT `needs-input` — it's `needs-fix`. Your opinion of
  worth is irrelevant.
- "This requires an architectural decision" IS `needs-input`.
- "This is stylistic" is `needs-fix` — apply the project's conventions.

**Post-triage states** (assigned after user interaction via AskUserQuestion, not by reviewers):

- `user-confirmed`: User selected the finding as needing work. Promoted to `needs-fix` and placed
  in its normal category section alongside other verified findings.
- `user-deferred`: User explicitly chose not to act on the finding now. Placed in the Deferred
  section of the output.

## Level of Effort (LoE) Scale

Required for Fixer-pipeline skills (swarm, quality-gate, unfuck). Optional for advisory skills.

- `trivial`: One-liner or mechanical change. No judgment required. (e.g., rename, add import, fix
  typo)
- `moderate`: Multi-file or requires reading context. Some judgment. (e.g., refactor function, add
  error handling, write test)
- `significant`: Architectural impact or cross-cutting concern. Substantial judgment. (e.g.,
  redesign interface, add new module, change data flow)

## ReviewFindings Schema

Canonical JSON schema for reviewer output:

```json
{
  "schema": "ReviewFindings",
  "reviewer": "string",
  "timestamp": "string — ISO 8601",
  "summary": {
    "total_findings": "integer",
    "needs_fix_count": "integer",
    "needs_input_count": "integer",
    "verdict": "clean | findings"
  },
  "findings": [
    {
      "id": "string — unique ID with reviewer prefix",
      "classification": "needs-fix | needs-input",
      "loe": "trivial | moderate | significant — required for Fixer-pipeline, optional for advisory",
      "category": "string",
      "file": "string",
      "line": "integer | null",
      "description": "string",
      "evidence": "string",
      "suggested_fix": "string",
      "risk": "string — what could go wrong if not addressed",
      "input_needed": "string | null — what decision the user must make (required when classification is needs-input)"
    }
  ]
}
```

## FixSummary Schema

Canonical JSON schema for Fixer output:

```json
{
  "schema": "FixSummary",
  "findings_fixed": ["string — finding IDs"],
  "needs_input_items": [
    {
      "id": "string",
      "loe": "trivial | moderate | significant",
      "description": "string",
      "input_needed": "string — what decision the user must make",
      "suggested_action": "string — what the Fixer recommends"
    }
  ],
  "user_deferred": [
    {
      "id": "string",
      "reason": "string — user's stated reason for deferral"
    }
  ],
  "fixes": [
    {
      "finding_id": "string",
      "description": "string",
      "file": "string",
      "line_range": "string | null"
    }
  ],
  "files_modified": ["string"]
}
```

## Fixer Protocol

How the Fixer processes findings:

1. Process all `needs-fix` findings (in file order to minimize context switching)
2. Collect all `needs-input` findings into `needs_input_items` in the FixSummary
3. Emit FixSummary via SendMessage to the Lead (the Fixer does NOT have AskUserQuestion — only
   Read, Write, Edit, Glob, Grep, Bash)
4. The **Lead** (not the Fixer) handles user triage:
   - Present needs-input items via multiSelect AskUserQuestion with one option per finding
   - Each option: label = Finding ID, description = "LoE: [X]. [Description]. Suggested: [action]"
   - User checks items to fix, leaves unchecked items to skip
   - Checked items: Lead sends back to Fixer (or fixes inline if trivial), adds to `findings_fixed`
   - Unchecked items: recorded in `user_deferred` with reason "User declined via triage"
5. After user triage complete: Lead updates the final FixSummary with `user_deferred` entries

## Verification Protocol

Structural enforcement after Fixer completes and user triage is done:

- Count findings-in vs (findings_fixed + user_deferred). Note: user-approved needs-input items
  appear in `findings_fixed` after the Fixer processes them, so this formula covers all outcomes.
- Delta > 0 → findings were silently dropped → escalate via AskUserQuestion
- Delta == 0 → all findings accounted for → proceed
