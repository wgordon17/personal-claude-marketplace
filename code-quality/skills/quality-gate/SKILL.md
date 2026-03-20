---
name: quality-gate
description: |
  Use when about to claim ANY work is complete — code, research, planning,
  config, or general answers. Also after /swarm, /spawn, subagent-driven
  development, or any significant deliverable.
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Agent, SendMessage, Skill]
---

# Quality Gate

Automated multi-pass review with rotating adversarial lenses, fresh-context subagent reviews,
and blocking memory/artifact gates.

**Core principle:** One review pass catches ~60% of issues. Each additional pass with a DIFFERENT
lens catches more. Fresh-context review catches what same-context review cannot. Memory and
artifact gates prevent work from being lost.

## When to Invoke

**Always after:** implementation tasks, /swarm, /spawn, subagent work, research, planning,
significant artifact creation.

**Skip when:** pure conversation with no deliverables, or user explicitly opts out.

**Never skip "because it's trivial."** Small changes cause real failures. Don't rationalize
your way out of the process.

---

## Process Flow

```dot
digraph quality_gate {
  rankdir=TB;
  node [shape=box];
  detect [label="Step 0: Detect work type"];
  layer1 [label="Layer 1: Self-Review Loop\n6 rounds, rotating lenses"];
  layer1_5 [label="Layer 1.5: Domain Expert Review\n4 parallel reviewers (code/mixed only)"];
  layer2 [label="Layer 2: Fresh-Context Subagents\n2 subagents x 2 passes"];
  memory [label="Memory Gate (BLOCKING)"];
  artifact [label="Artifact Gate (BLOCKING)"];
  docs [label="Documentation Gate (BLOCKING)"];
  nodataloss [label="No Data Loss Gate (BLOCKING)"];
  final [label="Final Verification"];
  detect -> layer1 -> layer1_5 -> layer2 -> memory -> artifact -> docs -> nodataloss -> final;
}
```

---

## Step 0: Detect & Classify

Determine work type from session activity:

| Signal | Work Type |
|--------|-----------|
| `git diff` has results | **Code** |
| Plan file written to `hack/plans/` | **Planning** |
| Research/analysis conversation, no file edits | **Research** |
| Edits to `.md`, `.yaml`, `.json`, `.toml` config | **Config/Artifact** |
| Short Q&A, no tool use | **Question** |
| Multiple of the above | **Mixed** (apply all relevant criteria) |

Select the lens set for the detected type (see `references/lens-rubrics.md`).

---

## Layer 1: Self-Review Loop

**6 rounds maximum (Round 6: Structural applies to Code/Mixed only). Exit early if a round produces zero findings AND the action audit is clean.**

Each round uses a different adversarial lens. Rotating lenses prevent anchoring fatigue and ensure
comprehensive coverage from different angles.

### Lens Rotation

| Round | Lens | Core Question |
|-------|------|---------------|
| 1 | **Correctness** | What inputs produce wrong results? What assumptions are untested? |
| 2 | **Completeness** | What was requested but not delivered? Read the original request word-by-word. Includes documentation completeness (see below). |
| 3 | **Robustness** | How does this fail? Bad input, missing deps, concurrent access, edge cases? |
| 4 | **Simplicity** | What's over-engineered? What could be deleted? What's AI slop? |
| 5 | **Adversarial** | You are a hostile reviewer. The author claims this is done. Prove them wrong. |
| 6 | **Structural** | What design flaws, race conditions, or failure modes exist in this system's architecture — not just in the current change, but in how it integrates? (Code/Mixed only) |

Table shows code lenses. Other work types adapt lens names — e.g., planning uses "Feasibility"
for Round 1, Q&A uses a reduced 3-round review. See `references/lens-rubrics.md` for all
work-type-specific lens prompts.

### Skill Integration Per Round

- **Round 1:** Invoke `sc:analyze` for code files. Use `sequential-thinking` MCP for decomposed reasoning.
- **Round 2:** Use `sequential-thinking` MCP. Apply first-principles: break original request into
  atomic requirements, check each independently.
- **Round 4:** Invoke `sc:improve` for dead code, unused imports, over-abstraction.
- **Round 5:** First-principles: "State the fundamental purpose in one sentence. Review against
  that purpose, not the structure you created."

**MCP tool availability:** If Serena (`think_about_*`) or `sequential-thinking` MCP tools are
unavailable, use extended thinking to perform the same metacognitive checks. The tools enforce
structured reasoning; without them, be explicit about pausing to reason through the same questions.

### Round Execution Protocol

Execute this protocol for EVERY round:

```
1. serena::think_about_task_adherence
   "Is this round's review still aligned with the original request?"

2. APPLY THE LENS
   Think through this EXHAUSTIVELY. Do not stop at the first issue.
   Check every modified file, every function, every edge case.
   Continue until you have genuinely run out of things to check —
   not until you feel like you've done enough.

   For this round's specific lens, use first-principles thinking:
   break the problem down to fundamental truths and rebuild
   your assessment from the ground up.

3. PROJECT RULES + CROSS-REFERENCE INTEGRITY (Round 2 only)
   a) Re-read CLAUDE.md and CONTRIBUTING.md (if they exist).
      Check every change against project-specific conventions:
      - Version bump rules (e.g., "always bump plugin versions in both files")
      - Commit message conventions
      - Required file updates (changelogs, manifests, registries)
      - Deployment/delivery requirements (will this change actually reach users?)
      - Any other project-specific rules that apply to this type of change

   b) Documentation completeness: check every change against the documentation
      triggers in `code-quality/references/documentation-taxonomy.md`. For each
      trigger that fires, verify the corresponding documentation surfaces were
      updated. Use the taxonomy's surface detection patterns to discover all
      surfaces, and its ecosystem-specific component discovery patterns to count
      on-disk components. Counts must match what's documented. A new component
      with no documentation entry is a completeness failure, same as a missing test.

   c) Cross-reference integrity: search the ENTIRE codebase for references
      to things you changed, renamed, or removed. Files you DIDN'T modify
      can have stale references to things you DID modify. Grep for:
      - Old names/values you replaced
      - Functions/skills/tools you renamed or removed
      - Version numbers that should match across files
      - Import paths, file references, cross-links

   This catches issues that no generic lens covers — project conventions
   and cross-file consistency are invisible to single-file review.

4. FIX ALL FINDINGS IMMEDIATELY
   Do not note issues for later. Do not say "could be improved."
   Fix them NOW. Every identified issue must result in an edit or
   a documented, specific blocker.

5. ACTION AUDIT
   Scan ALL output (yours and any subagent output) for
   identified-but-unactioned items:
   - "could be improved" / "might want to" / "consider"
   - "potential issue" / "noted for future" / "TODO"
   - "follow-up" / "out of scope" / "later"
   - "pre-existing" / "preexisting" / "known issue" / "existing failure"
   - "should be verified" / "needs to be confirmed" / "you should check"
   - "verify against your" / "please verify" / "you may want to update"
   - Any issue described without a corresponding fix

   THE "DEFERRAL-TO-USER" TRAP:
   Saying "should be verified" or "you should check" is an admission that
   work needs doing — and a confession that you didn't do it. If it should
   be verified, verify it. If it needs checking, check it. If it needs
   confirming, confirm it. The user asked you to do the work, not to
   generate a checklist of work for them to do.
   - "The field ID should be verified against your instance" → Look it up.
   - "You may want to update the config" → Update it.
   - "This should be tested with..." → Test it.
   - "Needs to be confirmed" → Confirm it.
   Every "should be" is either done or documented as a specific blocker
   with why you couldn't do it yourself.

   THE "PREEXISTING" TRAP:
   Labeling something "preexisting" is NOT permission to ignore it.
   For every issue labeled preexisting or known:
   - WHY does it exist? Investigate the root cause, don't just note it.
   - Is it within scope of the current work? If your changes touch the
     same area, fix it.
   - Is it ACTUALLY preexisting? Verify by checking the baseline, not
     by assuming.
   - Even if truly preexisting and out of scope: document it as a
     concrete follow-up task, not a hand-wave.
   "4 pre-existing test failures" repeated without investigation is
   the same as ignoring 4 bugs.

   For EACH identified-but-unactioned item:
   - Can fix now? → Fix it.
   - Genuinely blocked? → Document the SPECIFIC blocker.
   - Deferred without justification? → Fix it.
   - User explicitly deferred it? → Leave it, cite the user's decision.

   For subagent/spawn output specifically:
   - Parse every subagent's last_assistant_message
   - Extract findings/issues/concerns mentioned
   - Cross-reference against actual edits made
   - The delta is "identified-but-unactioned" — fix or justify each one

6. serena::think_about_whether_you_are_done
   "Did I genuinely address everything this lens covers?"
   If no → fix remaining items before proceeding to next round.
```

### Early Exit

**Rounds 1-3 always run.** Correctness, Completeness, and Robustness are critical lenses that
catch categorically different issues — a clean Round 1 says nothing about Round 2.

Starting from Round 4: if a round produces zero findings AND the action audit is clean, skip
remaining rounds. Do NOT exit early just because you "feel done" — the `think_about_whether_you_are_done`
tool must confirm.

**Note on Round 6 (Structural):** Only applies to Code and Mixed work types. Skip for all
other types. Structural lens focuses on integration architecture, not the current change in
isolation — it may find issues even when Rounds 1-5 were clean.

---

## Layer 1.5: Domain Expert Review

**Applies to: Code and Mixed work types only.** Skip for Research, Planning, Config, and
Question work types.

Domain reviewers catch categories of issues that self-review lenses systematically miss:
security vulnerabilities require dedicated adversarial security reasoning; performance issues
require cost-model thinking; QA issues require test coverage analysis; code review issues
require holistic style and maintainability assessment. Layer 1 cannot replicate these because
each domain has its own expert heuristics.

**Layer 1.5 is NOT optional for code work.** Do not skip it because Layer 1 "seemed thorough."

### Trigger

Work type is **Code** or **Mixed** (with a code component).

### Reviewer Spawning (4 in parallel)

```
Spawn all 4 reviewers simultaneously:

Reviewer 1 — Security (code-quality:security):
  Agent(
    description="Security domain review",
    model="sonnet",
    prompt=<see references/subagent-prompts.md, Domain Reviewer: Security>
  )

Reviewer 2 — QA (code-quality:qa):
  Agent(
    description="QA domain review",
    model="sonnet",
    prompt=<see references/subagent-prompts.md, Domain Reviewer: QA>
  )

Reviewer 3 — Performance (code-quality:performance):
  Agent(
    description="Performance domain review",
    model="sonnet",
    prompt=<see references/subagent-prompts.md, Domain Reviewer: Performance>
  )

Reviewer 4 — Code Review (superpowers:code-reviewer):
  Agent(
    description="Code style and maintainability review",
    model="sonnet",
    prompt=<see references/subagent-prompts.md, Domain Reviewer: Code-Reviewer>
  )
```

Each reviewer receives:
- `git diff` of all changes
- The original user request (verbatim)
- CLAUDE.md / CONTRIBUTING.md project rules (if they exist)

### Synthesis Protocol

After all 4 reviewers complete, synthesize findings by severity:

1. **Collect** all findings across the 4 reviewers
2. **Classify** each finding: CRITICAL / HIGH / MEDIUM / LOW
3. **Fix all CRITICAL and HIGH findings** before proceeding to Layer 2.
   These are blocking. Do not carry them forward.
4. **Record MEDIUM and LOW findings** — include them in the output report, fix if
   straightforward, document as follow-up if genuinely deferred.

**The synthesis step is not optional.** If you find yourself writing "domain review skipped"
or "findings noted for later," stop. Fix the critical/high findings now.

---

## Layer 2: Fresh-Context Subagent Reviews

Same-context review has an anchoring ceiling. Fresh-context subagents break through it by
reviewing with no knowledge of your implementation decisions.

**Layer 2 is NEVER optional and NEVER substitutable.** Do not skip it because the change "seems
trivial" or subagents feel "disproportionate." Do not substitute it with prior review work —
swarm Phase 4 reviewers, sc:analyze output, or any other earlier review does NOT replace Layer 2.

**Why Layer 2 is distinct from all prior reviews:**
- **Timing:** Layer 2 reviews the FINAL state — after Layer 1 fixes, after any swarm Phase 5
  fixes, after everything. Prior reviews examined earlier, now-stale versions of the work.
- **Lens:** Layer 2 uses holistic lenses (completeness-vs-original-request, adversarial).
  Domain-specific reviewers (security, performance, QA) cover different ground.
- **Context:** Layer 2 subagents have zero knowledge of your implementation decisions,
  rationalizations, or "good enough" compromises. Prior reviewers in the same session share
  your context ceiling.

If you find yourself writing "SUBSTITUTED by" or "already covered by" for Layer 2, you are
rationalizing. Stop. Spawn the subagents. If the change is truly trivial, the subagents
finish quickly — that's cheap insurance, not wasted effort.

### Preparation

```
serena::think_about_collected_information
→ "What context do the subagents need to review effectively?"
```

Collect:
- `git diff` (for code) or artifact content (for non-code)
- The original user request (verbatim)
- Work type classification from Step 0
- CLAUDE.md / CONTRIBUTING.md project rules (if they exist) — subagents need these
  to check project convention compliance

### Subagent Execution (2 passes each — BOTH mandatory)

Each subagent runs TWO passes. Pass 2 is NOT optional — it catches issues the subagent
missed on Pass 1 and verifies your fixes didn't introduce new problems.

Pass 2 resumes the Pass 1 agent via `SendMessage` using the **agent ID** (not the name).
This preserves the agent's full conversation history from Pass 1. You MUST use the agent
ID returned in the Pass 1 result — using the agent name routes to a team inbox instead.

**CRITICAL — Pass 2 synchronization:** `SendMessage` is asynchronous — it returns immediately
but the agent's response arrives later as a teammate notification. You MUST wait for each
Pass 2 response before proceeding. Do NOT move to Memory Gate or any subsequent gate until
BOTH Pass 2 responses are received and all findings are fixed. "Message sent" is not
"response received." If the agent does not respond within 2 minutes, re-do the Pass 2 check
yourself: re-read the files you fixed and verify the fixes are semantically correct (not just
syntactically applied). A self-performed Pass 2 is better than a skipped one.

**Subagent A: Completeness Reviewer (opus)**

```
PASS 1:
  pass1_result = Agent(
    description="Completeness review",
    model="opus",
    prompt=<see references/subagent-prompts.md, Subagent A Pass 1>
  )
  → Save pass1_result.agentId
  → Fix ALL findings

PASS 2 (MANDATORY — do not skip):
  SendMessage(
    to=<agentId from Pass 1>,    ← MUST be agentId, not agent name
    message="Here are the fixes I made: {summary_of_fixes}.
             Review them. Also: what did YOU miss on your first pass?
             You had fresh eyes but still have blind spots. Look again.",
    summary="Pass 2 completeness re-review"
  )
  → WAIT for response (do not proceed to gates)
  → Fix ALL findings from Pass 2
```

**Subagent B: Adversarial Reviewer (opus)**

```
PASS 1:
  pass1_result = Agent(
    description="Adversarial review",
    model="opus",
    prompt=<see references/subagent-prompts.md, Subagent B Pass 1>
  )
  → Save pass1_result.agentId
  → Fix ALL findings

PASS 2 (MANDATORY — do not skip):
  SendMessage(
    to=<agentId from Pass 1>,    ← MUST be agentId, not agent name
    message="Here are the fixes: {summary_of_fixes}.
             Verify they're correct. What else breaks in production?",
    summary="Pass 2 adversarial re-review"
  )
  → WAIT for response (do not proceed to gates)
  → Fix ALL findings from Pass 2
```

**Why Pass 2 matters:** Pass 1 finds issues. You fix them. But fixes can introduce NEW
issues, and the subagent's first pass always misses something (fresh eyes still have blind
spots). Pass 2 catches both. Skipping it is like running tests once, making changes, and
not re-running them.

---

## Memory Gate (BLOCKING)

Cannot proceed past this gate without completing all applicable checks.
Memory that drifts from reality is worse than no memory.

| Memory Surface | Action |
|----------------|--------|
| **hack/TODO.md** | Mark completed items `[x]` with date. Add new tasks. Remove stale items. |
| **hack/PROJECT.md** | Document decisions, gotchas, architecture changes from this work. |
| **hack/NEXT.md** | Update pointer to actual next task. |
| **hack/SESSIONS.md** | Append 3-5 bullet summary (if session-end isn't imminent). |
| **hack/plans/** | Delete completed plans. Update partial plans with progress. Delete superseded plans. |
| **claude-mem** | Search for related observations. Flag stale ones. Save new cross-session insights. |
| **Serena memories** | List, verify accuracy, update stale, write new project knowledge. |
| **Auto-memory** | Check project memory path, verify and update if relevant. |
| **hack/LESSONS.md** | Check if current work produced principle-level lessons worth capturing. If human corrections or rejected approaches occurred, extract and write lessons before proceeding. |

**Skip conditions:** hack/ doesn't exist → skip hack/ checks. No plans directory → skip plan
checks. claude-mem MCP unavailable → skip claude-mem checks. Serena MCP unavailable → skip
Serena memory checks. Pure question with no persistent insights → skip memory saves.
hack/LESSONS.md doesn't exist → skip LESSONS check.

---

## Artifact Gate (BLOCKING)

Ensures work products aren't lost AND project conventions are satisfied.

| Work Type | Gate Criteria |
|-----------|---------------|
| **Code** | Tests pass. Changes committed, pushed, and PR created (or ready to — do not commit without user request). "In the working tree" is not sufficient. |
| **Planning** | Plan file written. Tasks concrete and actionable. No hand-waving. |
| **Research** | Key findings documented persistently (claude-mem, PROJECT.md, or file). |
| **Config** | Valid syntax. Consistent with existing patterns. Validated. |
| **Question** | No gate (unless answer revealed something worth persisting). |

**Project-specific checks (all work types) — final re-verification:**
Round 2 checked project rules during review. This gate re-verifies AFTER all fixes are applied
(Layer 1 fixes + Layer 2 subagent fixes may have changed the state). Re-read CLAUDE.md:
- Version bumps in plugin/package manifests (if project requires them)
- Registry/marketplace entries updated to match
- Will the change actually reach users after merge? (cache invalidation, version detection)
- Any required companion files (changelogs, migration guides, etc.)

**Upstream state verification (if a PR exists):**
Do not assume prior pushes landed or that the PR is still open. Verify:
- `mcp__github__pull_request_read` (preferred) or `gh pr view` — is the PR still open, or was it already merged/closed?
- `git log origin/main..HEAD` — does the branch contain ALL intended commits?
- If force-pushing to an already-merged branch, commits are silently orphaned
- After merge: `git log origin/main` — confirm the merge includes your changes

---

## Documentation Gate (BLOCKING)

Ensures documentation accurately reflects the current state of the codebase after all changes.
Cannot proceed past this gate without completing all applicable checks.

**This gate catches code→docs gaps** — features that exist on disk but aren't documented.
Round 2 checks docs→code (do documented claims match reality). This gate checks the inverse.

Use `code-quality/references/documentation-taxonomy.md` for all definitions.

| Check | Action |
|-------|--------|
| **Trigger check** | Do any changes in this work match documentation triggers (taxonomy § Triggers)? If no triggers fire, skip remaining checks. |
| **Component inventory** | Use ecosystem-specific discovery patterns (taxonomy § Component Discovery) to count on-disk components. Compare against documentation surfaces (taxonomy § Surfaces). Mismatches are blocking. |
| **Feature coverage** | For each trigger that fired: is there a corresponding documentation update? New component without doc entry = fail. Removed component with stale references = fail. |
| **Cross-surface consistency** | Apply consistency rules (taxonomy § Cross-Surface Consistency). All surfaces must agree on names, counts, and descriptions. |

**Skip conditions:** No documentation triggers fire (per taxonomy § "Changes that DO NOT require
documentation updates"). When in doubt, run the check.

---

## No Data Loss Gate (BLOCKING)

Ensures work artifacts are persisted to a **durable, externally-trackable location** — not just
in the conversation context or the local working tree. Cannot proceed past this gate without
confirming each applicable item.

**THE WORKING TREE TRAP:**
"Changes are in the working tree" is NOT a durable location. Working tree changes are:
- Lost on `git checkout`, `git stash`, branch switches, or worktree cleanup
- Invisible to other sessions, agents, or future conversations
- Not findable by anyone searching git history, PRs, or project memory
- Equivalent to "I wrote it on a sticky note" — it exists, but nobody will find it

Durable means: **another agent in a fresh session can find the work without being told where
to look.** PRs show up in `gh pr list`. Commits show up in `git log`. Plan files show up in
`hack/plans/`. Project decisions show up in `hack/PROJECT.md`. Conversation-only knowledge
and working-tree-only changes fail this test.

| Work Type | Persistence Check |
|-----------|-------------------|
| **Code** | Changes are on a **pushed feature branch with a PR** (verifiable via `gh pr view`). Not just committed locally — pushed and PR-visible. Uncommitted or committed-but-not-pushed work is NOT durable. |
| **Planning** | Plan written to `hack/plans/YYYY-MM-DD-<topic>.md`. New tasks added to `hack/TODO.md`. Decisions documented in `hack/PROJECT.md`. |
| **Research** | Findings written to `hack/research/YYYY-MM-DD-<topic>.md` (if hack/ exists), or to `hack/PROJECT.md`, or saved to claude-mem. Key findings must survive session end. |
| **All types** | For each significant artifact, state WHERE it is persisted and HOW a future agent finds it. "It's in the working tree" or "it's in the conversation" fails this gate. |

**Skip conditions:** Pure question with no artifacts → skip. Work explicitly scoped as
"conversation only" by the user → skip.

---

## Final Verification

| Work Type | Verification |
|-----------|-------------|
| **Code** | Run test suite + lint. Compare against baseline. |
| **Planning** | Re-read plan end-to-end. Walk through mentally step-by-step. |
| **Research** | Re-read findings against original question. |
| **Config** | Validate syntax. Verify integration. |

Final check:
```
serena::think_about_whether_you_are_done → "Is this genuinely complete?"
```

---

## Output

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUALITY GATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Work type: [code / research / plan / config / question / mixed]

Layer 1 — Self-Review:
  Rounds completed: [N/6]
  Issues found: [count]
  Issues fixed: [count]
  Identified-but-unactioned caught: [count]
  Project rules violations caught: [count]

Layer 1.5 — Domain Expert Review: [N/A for non-code | COMPLETE]
  Security reviewer: [count] findings ([critical/high/medium/low breakdown])
  QA reviewer: [count] findings ([critical/high/medium/low breakdown])
  Performance reviewer: [count] findings ([critical/high/medium/low breakdown])
  Code-reviewer: [count] findings ([critical/high/medium/low breakdown])
  Critical/High fixed before Layer 2: [count]
  Medium/Low recorded: [count]

Layer 2 — Subagent Reviews:
  Subagent A (Completeness): [count] findings across 2 passes — agent ID: [id]
  Subagent B (Adversarial): [count] findings across 2 passes — agent ID: [id]
  (Layer 2 MUST show agent IDs. "SUBSTITUTED" or "N/A" is NEVER valid here.)

Memory Gate: [PASS / UPDATED]
  hack/ files: [updated / N/A]
  Plans: [N completed, N updated, N unchanged]
  Stale memories flagged: [count]
  New memories saved: [count]

Artifact Gate: [PASS / N/A]
  [work-type-specific status]

Documentation Gate: [PASS / SKIP / N/A]
  Components on disk: [count] | Documented: [count]
  Surfaces checked: [list]
  Gaps found: [count]

No Data Loss Gate: [PASS / N/A]
  [artifact persistence status]

Final Verification: [PASS / FAIL]

Overall: [PASS / NEEDS WORK]
[If NEEDS WORK: list what remains]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Relationship to Other Skills

| Skill | Relationship |
|-------|-------------|
| `sc:analyze` | Invoked in Round 1 (Correctness lens) for code files. |
| `sc:improve` | Invoked in Round 4 (Simplicity lens) for dead code removal. |
| `sc:reflect` | Replaced by Serena metacognitive tools (more targeted). |
| `code-quality:security` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `code-quality:qa` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `code-quality:performance` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `superpowers:code-reviewer` | Spawned as domain reviewer in Layer 1.5 (code/mixed work types). |
| `verification-before-completion` | Embedded in Final Verification step. |
| `session-end` | Complementary — quality-gate reviews accuracy; session-end does final save. |
| `swarm` Phase 7 | Invokes quality-gate as the final validation step. |

---

## Stop Hook Safety Net

A global Stop hook in `dev-guard` (`type: command`) catches premature completion claims that bypass
this skill. It uses a two-script architecture for speed: `stop-hook.py` (stdlib-only, <200ms)
performs deterministic triage — loop guard, transcript delta, signal detection (write tools, MCP
writes, completion claim regex, git diff hash change) — and fast-exits for low-signal cases.
When signals warrant deeper evaluation, it delegates to `stop-hook-llm.py` which calls
claude-sonnet-4-6 via Vertex AI with an adaptive prompt based on work type (code, research,
questions, planning) and trigger reasons. Fails open on any infrastructure error.
Uses `stop_hook_active` guard to prevent infinite loops.
