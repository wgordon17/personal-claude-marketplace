# Lens Rubrics by Work Type

Detailed prompts for each lens, adapted per work type. The main SKILL.md defines the round
structure. This file provides the specific questions and techniques for each lens.

---

## Code Lenses

### Round 1: Correctness

- What inputs would make this produce wrong results?
- What assumptions are untested?
- What edge cases aren't handled?
- What happens with empty/null/zero/negative/max values?
- Are error paths correct (not just happy path)?
- **Tool:** Spawn domain reviewers (`code-quality:security`, `code-quality:qa`, `code-quality:performance`) on all modified code files.
- **Reasoning:** Use `sequential-thinking` MCP to decompose correctness check step-by-step.
- **First-principles:** "What are the fundamental invariants this code must maintain? Derive
  them from the requirements, not from what the code currently does."

### Round 2: Completeness

- Read the original request WORD BY WORD.
- Break it into atomic requirements. Check each independently.
- What was requested but not delivered?
- What was identified by you or a subagent but not actioned?
- What was deferred without the user explicitly requesting deferral?
- If a plan file was found in Layer 1.75: cross-reference every atomic requirement from the
  original request against the plan's task list, then verify each plan task against the
  implementation. The plan is a more granular decomposition of the request — use it as the
  primary completeness checklist when available.
- Are all code paths covered? All branches implemented?
- **Project rules:** Re-read CLAUDE.md/CONTRIBUTING.md. Check version bumps, manifest
  updates, deployment readiness. Will this change actually reach users after merge?
- **Reasoning:** Use `sequential-thinking` MCP to check each atomic requirement.
- **First-principles:** "Return to the original request. What are its fundamental parts?
  Is each one fully satisfied?"

### Round 3: Robustness

- How does this fail under load?
- What happens with bad input, missing dependencies, concurrent access?
- Where are silent failures (errors swallowed, exceptions caught and ignored)?
- Are resource leaks possible (file handles, connections, memory)?
- What happens when external services are unavailable?
- **First-principles:** "What are the fundamental ways this category of system fails?"

### Round 4: Simplicity (BLOCKING)

**This round is a blocking sub-gate.** Dead code, unnecessary abstractions, and unused imports
are `needs-fix` findings that must be fixed before proceeding to Round 5. These are objectively
wasteful — not judgment calls.

- Calculate net lines delta: `git diff --stat` on all changes. Pass the delta to the
  code-simplifier as context ("This work adds +N/-M lines, net +X").
- Apply the full checklist from `code-quality/references/simplification-checklist.md`.
- What's over-engineered for the current requirement?
- What abstractions exist for a single consumer? (Rule of Three: don't abstract until third use)
- What could be deleted and nothing would break?
- What's AI slop? (narrating obvious, sycophantic names, filler docstrings, hedge comments)
- Commented-out code? Unused imports? Dead branches?
- For each new dependency or custom implementation: does a well-maintained library solve this?
  (See `code-quality/references/dependency-evaluation.md`)
- **Tool:** Spawn `code-quality:code-simplifier` for dead code, unused imports, over-abstraction.
- **Gate:** Fix all `needs-fix` simplification findings (dead code, unnecessary abstractions,
  unused imports) before proceeding. `needs-input` findings (architectural decisions) require
  user confirmation before proceeding.

### Round 5: Adversarial

- You are a senior engineer who rejected the last 3 PRs. This code has problems. Find them.
- Focus on: correctness bugs, security vulnerabilities, production failure modes.
- What would embarrass the author in code review?
- What will cause a 3am page?
- **First-principles:** "State the fundamental purpose in one sentence. Review against that
  purpose, not the structure you created."

### Round 6: Structural (Code/Mixed only)

Core question: "What design flaws, race conditions, or failure modes exist in this system's
architecture — not just in the current change, but in how it integrates?"

This lens examines the change in its system context, not as an isolated unit. Correctness
(Round 1) checks whether the code does what it says. Structural checks whether what it does
is safe and coherent when integrated into the larger system.

**Focus areas:**

- **Concurrency issues:** Are there race conditions between this change and concurrent
  callers? Shared mutable state accessed without synchronization? TOCTOU (time-of-check
  time-of-use) windows? Are locks acquired in consistent order to prevent deadlock?

- **State management gaps:** Does the change introduce state that can become inconsistent?
  Are state transitions explicit and complete (no missing transitions or illegal states)?
  Is initialization ordering guaranteed? Are cleanup paths symmetric with setup paths?

- **Error propagation paths:** When this component fails, how does the failure propagate?
  Does it fail loudly (crash, panic, exception) or silently (nil return, wrong result)?
  Are callers equipped to handle the error signals this code produces? Does the failure
  mode change under concurrent access?

- **API contract violations:** Does this change break the implicit or explicit contract
  of the interfaces it implements or calls? Are preconditions documented and enforced?
  Do postconditions still hold after the change? Are invariants maintained across the
  call boundary?

- **Dependency ordering assumptions:** Does this code assume initialization order of
  external dependencies? What happens if a dependency initializes after this code runs?
  Are there circular dependencies introduced by this change? Does the code assume
  availability of infrastructure (network, filesystem, database) without verifying?

**First-principles:** "If this component were a black box, what contracts would callers
rely on? Does this change honor those contracts under all observable conditions — including
failure, concurrency, and abnormal load?"

**Tool:** Use `sequential-thinking` MCP to trace each integration path step-by-step.

---

## Research Lenses

### Round 1: Accuracy

- What claims are stated as fact without verification?
- What sources were cited vs assumed from training data?
- Are numbers, dates, versions correct?
- Would a domain expert disagree with any conclusion?
- **First-principles:** "What are the foundational facts this analysis rests on? Are they
  verified or assumed?"

### Round 2: Completeness

- What was the actual question asked?
- Did we answer that question, or an adjacent one?
- What perspectives, alternatives, or counterarguments are missing?
- What assumptions did we make about the user's context?
- **Reasoning:** Use `sequential-thinking` MCP to decompose the original question into parts.
- **First-principles:** "What are the fundamental aspects of this question? Did we address
  each one?"

### Round 3: Depth

- Did we stop at the surface, or trace to root causes?
- Are explanations mechanistic (how it works) or just descriptive (what it does)?
- Would someone with deep domain knowledge learn anything from this?
- Are edge cases and exceptions acknowledged?

### Round 4: Clarity

- Would someone unfamiliar with this topic follow the explanation?
- Are technical terms defined or assumed?
- Is the structure logical (not just chronological)?
- Is anything redundant or padded?

### Round 5: Adversarial

- A domain expert will read this. What would embarrass us?
- What's the strongest counter-argument to our recommendation?
- If we're wrong, what's the consequence?
- **First-principles:** "What is the fundamental question being answered? Does our answer
  hold up from first principles, or are we pattern-matching?"

---

## Planning Lenses

### Round 1: Feasibility

- Can each step actually be executed as described?
- Are dependencies between steps identified?
- What happens if step N fails — is there a recovery path?
- Are resource/time estimates realistic?
- **First-principles:** "What are the fundamental constraints this plan operates within?"

### Round 2: Completeness

- Walk through the plan step-by-step mentally. Where do you get stuck?
- What was in the original request that isn't in the plan?
- Are edge cases and failure modes addressed?
- Is scope explicitly bounded (what's in, what's out)?
- If reviewing implementation of a plan: verify every task in the plan file has been addressed.
  Walk through the plan task-by-task, not just the original request.
- **Reasoning:** Use `sequential-thinking` MCP to walk through each step.
- **First-principles:** "What are the fundamental requirements? Is each one addressed by
  a concrete step?"

### Round 3: Robustness

- What if step 3 takes 3x longer than expected?
- What external dependencies could block progress?
- What if requirements change mid-execution?
- Are there single points of failure?

### Round 4: Simplicity

- What steps could be combined or eliminated?
- Is the plan over-specified where flexibility would serve better?
- Are there unnecessary phases or checkpoints?
- Could a simpler approach achieve the same outcome?

### Round 5: Adversarial

- A skeptical PM will review this plan. What's the weakest link?
- What's being hand-waved? Where are the vague verbs ("handle", "process", "manage")?
- Execute this plan mentally step-by-step. Where does it break?
- **First-principles:** "What is the fundamental goal? Does this plan achieve it, or does
  it achieve something adjacent?"

---

## Config/Artifact Lenses

### Round 1: Correctness

- Valid syntax? (parse/validate the file)
- Do values match the environment they target?
- Any typos in keys, paths, or identifiers?

### Round 2: Completeness

- All required fields present?
- No placeholder values left?
- Documentation/comments for non-obvious settings?
- **Project rules:** Re-read CLAUDE.md/CONTRIBUTING.md. Version bumped in ALL required
  files? Registry/marketplace entries updated? Will this change reach users after merge,
  or will caching/versioning prevent delivery?

### Round 3: Security

- No secrets, credentials, or tokens in the file?
- No overly permissive settings?
- Appropriate access controls?

### Round 4: Simplicity

- No commented-out blocks (dead config)?
- No redundant or conflicting settings?
- Consistent with existing project patterns?

### Round 5: Adversarial

- What breaks if this config is deployed to production as-is?
- What happens if someone copies this to a different environment?

---

## Question/Answer Lenses (Reduced: 3 Rounds)

For simple Q&A, use a reduced review:

### Round 1: Accuracy

- Is the answer correct? Verified against sources, not assumed?
- If uncertain, did we say "I don't know" rather than guessing?

### Round 2: Completeness

- Did we answer what was actually asked?
- Any important caveats or context missing?

### Round 3: Clarity

- Is the answer direct and actionable?
- Any AI slop (filler phrases, hedging, unnecessary elaboration)?

---

## Round Execution Protocol: Step 3 & Step 5 Trap Catalog

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
      triggers in `[{skill_dir}/../../references/documentation-taxonomy.md](../../../references/documentation-taxonomy.md)`. For each
      trigger that fires, verify the corresponding documentation surfaces were
      updated. Use the taxonomy's surface detection patterns to discover all
      surfaces, and its ecosystem-specific component discovery patterns to count
      on-disk components. Counts must match what's documented. A new component
      with no documentation entry is a completeness failure, same as a missing test.

   c) UAT coverage verification (Planning/Mixed only): if Step 0 loaded a
      non-empty `{plan_test_plan}`, for each scenario in the test plan: is
      there a corresponding planned task or test case in the plan? Will the
      planned work verify this scenario? A UAT scenario with no corresponding
      plan coverage is a completeness gap — treat as `needs-fix`.

   d) Cross-reference integrity: search the ENTIRE codebase for references
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
   - "v1" / "v2" / "future iteration" / "future enhancement" / "next version" / "phase N enhancement" / "deferred to future" / "out of scope for this implementation" (when used as self-scoping — legitimate software version references like "API v1" or "Pydantic v2" are not deferral)

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

   THE "SELF-SCOPING" TRAP:
   Labeling work as "v1" and deferring to "v2" is creating a permission
   gradient — the model decides what is "in scope" and uses version
   boundaries to justify skipping work. Scope decisions belong to the
   user via AskUserQuestion, not to the model via self-invented
   version labels.
   - "V2 Enhancements: [list]" → implement them or ask the user
   - "Scope: v1 vs. Deferred" → present as AskUserQuestion options
   - "Deferred (out of scope for v1)" → ask the user what to defer
   - "Future iteration: [feature]" → add it as a task or ask the user
   If the model claims "the user explicitly deferred this," verify the
   claim — cite the user's actual message. Fabricated user deferral
   is a violation.

   THE "NOT MY REVIEW" TRAP (PR review context):
   After /pr-review → /fix, the quality gate may find issues the review
   missed. Dismissing them as "out of scope because the review didn't
   flag it" is self-scoping — the review is not the scope boundary,
   the PR diff is. If the code was introduced by the PR and the quality
   gate finds a real issue in it, fix it. The review is a starting
   point, not an exhaustive inventory.

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

6. FINDING COUNT TRACKING (after step 5)
   Count the needs-fix findings identified and fixed in this round.
   Record as finding_count[round_N].
   If N >= 3 and finding_count[round_N] > finding_count[round_N-1]:
     Print: "⚠ Oscillation signal: round N produced more findings than
     round N-1. Verify late-round findings are genuine, not artifacts
     of over-review."
   This is a WARNING, not a blocker — continue the round.

7. serena::think_about_whether_you_are_done
   "Did I genuinely address everything this lens covers?"
   If no → fix remaining items before proceeding to next round.
```
