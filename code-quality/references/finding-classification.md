# Finding Classification

Canonical reference for finding classification, LoE estimation, and Fixer protocol. Referenced by
the quality-gate (Rounds 2–4), swarm Fixer phase, unfuck implementation agents, and orchestration
verification.

## Anti-Deferral Principle

Every finding is work that needs doing. You do not self-prioritize. When given findings, you process
ALL of them. Your assessment of a finding's importance is not a valid reason to defer. The only
valid reasons to not address a finding are: (1) a specific, documented technical blocker you cannot
resolve, or (2) the user explicitly excluded it via AskUserQuestion.

## Anti-Fabrication Principle

No fabricated findings. Reporting no findings after thorough review is not deferral — it is the
correct outcome when nothing is wrong. Inventing findings to appear thorough violates review
integrity. Anti-deferral means do the work that exists, not invent work that doesn't.

## Finding Classification

Two-tier taxonomy:

- `needs-fix`: The reviewer has sufficient context to describe the problem AND the fix is within the
  agent's capability to resolve independently. No user decision required.
- `needs-input`: The fix requires a decision the agent cannot make alone — architectural choice,
  scope decision, external dependency, user preference, or the fix would change behavior in ways the
  user should approve. The agent reports the finding with an LoE estimate; the user decides.

**Classification guidance for reviewers:**

- Default to `needs-fix`. Only use `needs-input` when you genuinely cannot determine the correct
  resolution without human judgment on a specific decision point.
- When classifying as `needs-input`, you MUST include an `input_needed` field explaining what
  specific decision the user must make and why the agent cannot make it.
- `needs-input` is NOT a way to defer work. Using `needs-input` to avoid implementing a fix you
  could make yourself is deferral-by-classification — a violation of the Anti-Deferral Principle.
- **Provenance override:** Findings that propose changing fields in a section marked with a
  `<!-- PROVENANCE: ... -->` comment MUST be classified as `needs-input`. The comment marks all
  fields that follow it as user-confirmed decisions made via explicit checkpoints (e.g.,
  AskUserQuestion in `/test-plan`).
  Overwriting them without re-confirming with the user violates the decision chain. If you believe
  a user-confirmed decision is wrong (e.g., incompatible framework choice), surface the conflict
  as `needs-input` with your evidence — let the user reconcile, don't auto-fix.

**What IS `needs-input`:**
- "Two valid architectures exist (X vs Y) with different tradeoffs" — genuine design decision
- "This changes user-facing behavior — should the API return 404 or 200 with empty body?" — UX decision
- "This requires choosing between backward compatibility and correctness" — scope decision
- A downstream skill proposes changing a field in a PROVENANCE-marked section (e.g., "the annotation
  says Cucumber.js but the project uses Vitest") — user-confirmed decision, must escalate not auto-fix

**What is NOT `needs-input`:**
- "I'm not sure if this is worth fixing" → `needs-fix`. Your opinion of worth is irrelevant.
- "This is a documented accepted risk" → `needs-fix` if it has a clear resolution, or don't report it.
- "This could go either way" → `needs-fix`. Pick the better option and explain why.
- "This is stylistic" → `needs-fix`. Apply the project's conventions.
- "The risk is low" → `needs-fix` if there's a fix, or don't report it. Risk assessment is not input.

## Verifier De-escalation

The Finding Verifier is the classification enforcement gate. When a reviewer classifies a
finding as `needs-input`, the verifier MUST investigate and apply this decision tree:

1. **Does the finding have exactly one correct resolution?** (e.g., missing dependency,
   wrong task ordering, incorrect prerequisite) → Reclassify to `needs-fix`. Set
   `investigation_summary` to explain why no decision exists.
2. **Does the finding have multiple valid resolutions with meaningfully different
   consequences?** (e.g., two architectures with different tradeoffs, API design choice
   affecting consumers) → Keep `needs-input`. Generate 2-4 concrete options in the
   `options` array.
3. **Is the "decision" actually about effort, priority, or risk tolerance — not about
   what to do?** (e.g., "should we add this test?", "is this dependency worth adding?")
   → Reclassify to `needs-fix`. The answer is always "yes, do the work."

**De-escalation test:** "If I gave this to two competent engineers independently, would
they make different choices?" If no → `needs-fix`. If yes → `needs-input` with options.

## Option Quality

When the verifier generates options for `needs-input` findings:

- Each option must be a concrete, actionable approach — not "fix it" or "investigate further"
- Options must be mutually exclusive — selecting one rules out the others
- Options must have meaningfully different consequences — if two options lead to the same
  outcome, merge them
- Labels should be short (3-7 words): "Extract shared module", "Use dependency injection"
- Descriptions should include the key tradeoff: "Simpler but couples A to B"
- 2 options minimum, 4 maximum (excluding Defer)

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
      "input_needed": "string | null — what decision the user must make (required when classification is needs-input)",
      "options": "array | null — concrete fix approaches when classification=needs-input. Each element: {label: string, description: string}. Required when classification=needs-input (verifier generates these). null when needs-fix. 2-4 options (excluding Defer, which is always appended by the orchestrator)."
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
      "suggested_action": "string — what the Fixer recommends",
      "options": "array | null — verifier-generated options passed through from ReviewFindings. Fixer MUST preserve this field when collecting needs-input items. null if the upstream finding had no options."
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
   - Present each needs-input item individually via AskUserQuestion (one question per finding,
     batch up to 4 per call). Each question includes full context:
     ```
     question: "[{id}] {description}\n\nLoE: {loe}\nDecision needed: {input_needed}\n▸dp:file={file},line={line},cat={reviewer},skill={skill_name}"
     header: "{id}"
     options: [
       {options[0].label}: {options[0].description},
       {options[1].label}: {options[1].description},
       ... (all verifier-generated options),
       {"label": "Defer", "description": "Skip for now — user-deferred"}
     ]
     multiSelect: false
     ```
   The orchestrator (Lead) appends Defer as the final option. The verifier does NOT include
   Defer in its `options` array — that's the orchestrator's responsibility.
   When `options` is `null` (needs-fix findings, or findings from pipelines without a verifier
   like the swarm Fixer), fall back to the original binary: `[{"label": "Fix"}, {"label": "Defer"}]`.
   The `▸dp:` metadata suffix MUST be present — it's consumed by the decision-persistence hook.
   - **Option selected (any non-Defer option):** Lead sends back to Fixer (or fixes inline if trivial), records selected option label in suggested_fix, adds to `findings_fixed`
   - Defer: recorded in `user_deferred` with reason "User declined via triage"
5. After user triage complete: Lead updates the final FixSummary with `user_deferred` entries

## Verification Protocol

Structural enforcement after Fixer completes and user triage is done:

- Count findings-in vs (findings_fixed + user_deferred). Note: user-approved needs-input items
  appear in `findings_fixed` after the Fixer processes them, so this formula covers all outcomes.
- Delta > 0 → findings were silently dropped → escalate via AskUserQuestion
- Delta == 0 → all findings accounted for → proceed

> **Note:** Pipeline-specific skills may extend this protocol with additional outcome buckets
> (e.g., /fix uses 7 outcome buckets to account for out-of-scope, blocked, and needs-plan
> outcomes). The 2-bucket formula above is the canonical minimum; extensions must be supersets.
