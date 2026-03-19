# Cynefin Domain Classification Reference

This file defines the Cynefin framework as adapted for software engineering task decomposition
within the `/swarm` skill. Agents use this reference to classify tasks, select response strategies,
and decide which pipeline phases are mandatory versus optional. Classification is **advisory** —
it informs decisions but never restricts mandatory pipeline phases.

---

## Overview

Cynefin (pronounced "ku-NEV-in") provides five domains that describe the relationship between
cause and effect in a problem. In /swarm, domain classification happens during Phase 2 (Architect decomposition)
and can be revised at any point during execution.

| Domain | Cause→Effect Relationship | Core Response |
|--------|--------------------------|---------------|
| Clear | Obvious, known | Apply best practice |
| Complicated | Discoverable through analysis | Analyze, then act |
| Complex | Visible only in retrospect | Probe, sense, respond |
| Chaotic | No discernible pattern | Act, stabilize, then assess |
| Disorder | Unknown which domain applies | Probe as Complex |

---

## Domain Definitions

### Clear (formerly Simple/Obvious)

**Cause→Effect:** Known and documented. There is one right answer.

**Heuristics:**
- A well-established best practice or standard exists
- Any competent engineer would reach the same solution
- Changes are scoped, bounded, and reversible
- No meaningful uncertainty about outcomes
- Similar tasks have been done many times before

**Examples in software engineering:**
- Bumping a dependency version (non-breaking)
- Fixing a typo or formatting error
- Adding a config flag with documented behavior
- Simple bug fix where root cause is clear and isolated
- Adding a test for a known untested code path
- Renaming a variable or function with no callers outside one file

**Response strategy:** Standard pipeline. Phase 2.5 Security Design Review is optional and can
be skipped unless the task touches auth, data handling, or external APIs.

---

### Complicated

**Cause→Effect:** Discoverable through expert analysis. Multiple valid approaches exist.

**Heuristics:**
- Experts may disagree on the best approach, but analysis can determine the right answer
- The solution space is bounded — there are wrong answers and better/worse right answers
- Dependencies are traceable with investigation
- The problem is solvable with sufficient knowledge and time
- Outcomes are predictable once the right approach is identified

**Examples in software engineering:**
- Performance optimization with profiling data
- Refactoring a module with multiple callers and test coverage
- Multi-file feature implementation with known requirements
- Database schema migration with understood data model
- Debugging a race condition with reproducible steps
- Integrating a third-party API with complete documentation
- Designing a caching layer with known access patterns

**Response strategy:** Full pipeline. Architect decomposes with expert analysis. Phase 2.5
Security Design Review is mandatory if the feature involves auth, sessions, data persistence,
or external communication. Component decomposition should be thorough — use dependency graphs.

---

### Complex

**Cause→Effect:** Only visible in retrospect. Emergent behavior. No predictable outcome.

**Heuristics:**
- Multiple interdependent variables with non-linear interactions
- Previous similar attempts had unexpected outcomes
- Stakeholder requirements are unclear or evolving
- The solution space is unbounded or poorly understood
- Small changes may have large, unpredictable effects
- Experts disagree not on approach but on whether the problem is solvable as stated

**Examples in software engineering:**
- Distributed system issues (timing-dependent, environment-specific failures)
- UX redesign with unknown user impact
- Architectural changes with cascading effects across many services
- Performance regression with no obvious cause after profiling
- Migrating to a new framework or language across a large codebase
- Fixing flaky tests that fail non-deterministically
- Resolving emergent security vulnerabilities in complex data flows

**Response strategy:** Probe design. The Architect's primary output is a set of experiments or
signals rather than a full implementation plan. Prefer smaller components with explicit checkpoints.
More check-ins with Lead. Phase 2.5 Security Design Review is mandatory regardless of surface area.

Probe design means:
- Identify what information is missing that prevents confident planning
- Design the smallest implementation that produces that information
- Define success/failure criteria before probing
- Re-classify after probe results are known

---

### Chaotic

**Cause→Effect:** No discernible pattern. The situation is unstable and needs immediate action.

**Heuristics:**
- The system is in active failure mode
- Waiting for analysis will make things worse
- Any action is better than inaction
- Stabilization is the first priority; root cause comes later
- Time pressure prevents normal investigation cycles

**Examples in software engineering:**
- Production outage with active user impact
- Security breach or active exploit in progress
- Cascading test failures blocking all CI with no clear root cause
- Data corruption spreading across systems
- Build system entirely broken with no rollback available
- Critical dependency yanked or compromised

**Response strategy:** Stabilization brief. Architect produces an immediate action plan focused
on stopping the bleeding, not solving the root cause. Phase 2.5 is deferred until stabilization.
Normal decomposition is suspended — use a single Implementer for the stabilization action.

Stabilization sequence:
1. Identify the minimum action that stops the spread/impact
2. Execute that action immediately
3. Verify stabilization
4. Only then begin root cause analysis (re-classify to Complicated or Complex)

---

### Disorder

**Cause→Effect:** Unknown — cannot determine which domain applies yet.

**Heuristics:**
- Insufficient information to classify
- The problem statement is ambiguous
- Multiple team members would place this in different domains
- Initial investigation has not narrowed the solution space

**Examples in software engineering:**
- Bug report with no reproduction steps
- Performance complaint with no metrics
- "Something is wrong" reports from users with no specifics
- Unfamiliar codebase with no documentation
- Inherited system with unknown behavior

**Response strategy:** Gather information before committing to an approach. Treat as Complex
by default — use probe design. The Architect's first output should be a clarification request
or investigation plan rather than an implementation plan. Do not assign implementers until
the domain is determined.

---

## Classification Decision Tree

Use this tree at the start of Phase 2 (Architect) to classify the task:

```
START: What is the task?
       |
       v
Are there documented, established solutions or best practices that
clearly apply, and any competent engineer would reach the same answer?
       |
      YES --> [Clear] Apply best practice. Standard pipeline.
       |
      NO
       |
       v
Is the situation in active failure/crisis mode requiring immediate
action to prevent further damage (time pressure eliminates analysis)?
       |
      YES --> [Chaotic] Stabilize first. Defer analysis.
       |
      NO
       |
       v
Do you have enough information to begin analysis at all?
Can you define the problem space clearly?
       |
      NO --> [Disorder] Gather information. Probe as Complex.
       |
      YES
       |
       v
Can expert analysis determine the correct approach, even if multiple
valid approaches exist and investigation is required?
       |
      YES --> [Complicated] Full analysis pipeline. Expert decomposition.
       |
      NO
       |
       v
Are outcomes uncertain even after analysis? Does the solution space
feel unbounded, or do experts disagree on whether the problem is
solvable as stated?
       |
      YES --> [Complex] Probe design. Smaller iterations. More checkpoints.
       |
      NO --> [Complex] (default — classify higher when uncertain).
```

---

## Domain-to-Phase Mapping

### Clear Domain

| Phase | Mandatory | Notes |
|-------|-----------|-------|
| Phase 0: Pre-flight & Setup | Yes | Brief. |
| Phase 1: Clarify & Checkpoint | Yes | Confirm scope is truly bounded. |
| Phase 2: Architect | Yes | Can be short. Single-pass decomposition. |
| Phase 2.5: Security Design Review | Optional | Skip unless auth/data/external API is involved. |
| Phase 2.7: Speculative Fork | Skip | Overhead exceeds value for Clear-domain tasks. |
| Phase 3: Pipelined Implementation | Yes | Standard pipeline. |
| Phase 4: Parallel Review | Yes | Standard. |
| Phase 4.5: Structural Design Review | Yes | Always runs. |
| Phase 5: Fix & Simplify | Conditional | Only if Phase 4/4.5 finds critical/high issues. |
| Phase 6: Docs & Memory | Yes | Standard. |
| Phase 7: Verification & Completion | Yes | Standard. Quality-gate invoked here. |

### Complicated Domain

| Phase | Mandatory | Notes |
|-------|-----------|-------|
| Phase 0: Pre-flight & Setup | Yes | Standard. |
| Phase 1: Clarify & Checkpoint | Yes | Confirm scope and identify expert needs. |
| Phase 2: Architect | Yes | Full decomposition with dependency graph. |
| Phase 2.5: Security Design Review | Conditional | Mandatory if auth/data/external API involved. |
| Phase 2.7: Speculative Fork | Conditional | Recommended when architect identifies competing approaches. |
| Phase 3: Pipelined Implementation | Yes | Standard pipeline. Parallel where possible. |
| Phase 4: Parallel Review | Yes | Standard. May require domain expert reviewer. |
| Phase 4.5: Structural Design Review | Yes | Always runs. |
| Phase 5: Fix & Simplify | Conditional | Only if Phase 4/4.5 finds critical/high issues. |
| Phase 6: Docs & Memory | Yes | Standard. |
| Phase 7: Verification & Completion | Yes | Standard. Quality-gate invoked here. |

### Complex Domain

| Phase | Mandatory | Notes |
|-------|-----------|-------|
| Phase 0: Pre-flight & Setup | Yes | Standard. |
| Phase 1: Clarify & Checkpoint | Yes | Flag complexity. Set probe objectives. |
| Phase 2: Architect | Yes | Probe design output, not full plan. Define signals. |
| Phase 2.5: Security Design Review | Yes | Always mandatory for Complex tasks. |
| Phase 2.7: Speculative Fork | Recommended | Complex-domain tasks benefit most from speculative execution. |
| Phase 3: Pipelined Implementation | Yes | Smaller components. Explicit checkpoints between. |
| Phase 4: Parallel Review | Yes | Reviewer assesses probe results, not just correctness. |
| Phase 4.5: Structural Design Review | Yes | Always runs. Probe results may reveal structural issues. |
| Phase 5: Fix & Simplify | Conditional | Only if Phase 4/4.5 finds critical/high issues. |
| Phase 6: Docs & Memory | Yes | Standard. |
| Phase 7: Verification & Completion | Yes | Quality-gate invoked here. Include re-classification assessment. |

### Chaotic Domain

| Phase | Mandatory | Notes |
|-------|-----------|-------|
| Phase 0: Pre-flight & Setup | Yes | Emergency triage only. |
| Phase 1: Clarify & Checkpoint | Yes | Identify stabilization target. |
| Phase 2: Architect | Yes | Stabilization brief only. No full decomposition. |
| Phase 2.5: Security Design Review | Deferred | Resume after stabilization. |
| Phase 2.7: Speculative Fork | Skip | Crisis mode — no time for competing implementations. |
| Phase 3: Pipelined Implementation | Yes | Single implementer. Stabilization action only. |
| Phase 4: Parallel Review | Yes | Lightweight. Verify stabilization works. |
| Phase 4.5: Structural Design Review | Yes | Always runs. Stabilization may introduce structural issues. |
| Phase 5: Fix & Simplify | Conditional | Standard. |
| Phase 6: Docs & Memory | Yes | Standard. |
| Phase 7: Verification & Completion | Yes | Quality-gate invoked here. Must re-classify domain after stabilization. |

### Disorder Domain

| Phase | Mandatory | Notes |
|-------|-----------|-------|
| Phase 0: Pre-flight & Setup | Yes | Standard. |
| Phase 1: Clarify & Checkpoint | Yes | Identify what information is missing. |
| Phase 2: Architect | Yes | Investigation plan, not implementation plan. |
| Phase 2.5: Security Design Review | Deferred | Re-evaluate after classification. |
| Phase 2.7: Speculative Fork | Deferred | Re-evaluate after classification. |
| Phase 3-7 (incl. 4.5) | Conditional | Only after domain is determined. Follow phase map for determined domain. |

---

## Misclassification Traps

These are the most common errors. Recognizing them mid-task is normal — reclassify and continue.

### Clear → Chaotic Cliff
**Pattern:** A task classified as Clear suddenly cascades — the "simple" change has unexpected
side effects, breaks assumptions, or requires touching more of the system than expected.

**Warning signs during implementation:**
- Implementer finds unexpected callers or dependents
- A "simple" change requires touching 5+ files
- Tests fail in modules apparently unrelated to the change
- The fix for one thing breaks another

**Action:** Immediately escalate to Lead. Reclassify to Complicated or Complex. Do not proceed
with the original Clear-scoped plan.

---

### Complex → Complicated (Premature Analysis)
**Pattern:** Treating an emergent problem as analyzable. Spending analysis time on a problem
that requires experimentation to understand.

**Warning signs:**
- The Architect produces a confident plan for a system with unknown behavior
- The plan assumes cause→effect relationships that haven't been verified
- Multiple "obvious" approaches have already failed unexpectedly

**Action:** Back up. Design a probe. Validate assumptions before committing to implementation.

---

### Complicated → Clear (Skipping Expert Analysis)
**Pattern:** Treating a problem as having an obvious solution when it actually requires expert
analysis. "This is simple, I know how to do this" when the problem has hidden complexity.

**Warning signs:**
- The implementer immediately knows the solution without investigation
- No dependency analysis was done
- The plan has no contingencies

**Action:** Pause. Ask: "Have I actually verified this is the right approach, or does it just
feel familiar?" If the answer is "it feels familiar," invest 5 minutes in validation.

---

### Chaotic → Complex (Probing During Crisis)
**Pattern:** Attempting to understand root cause when immediate action is needed. Running
experiments when the system is in active failure.

**Warning signs:**
- Investigation time is increasing impact duration
- The team is analyzing while users are affected
- There's a known stabilization action available but it hasn't been taken

**Action:** Stabilize first. Stop the bleeding. Analyze after.

---

## Reclassification

Reclassification is expected and healthy. It is not a failure of initial classification.

### When to Reclassify

| Trigger | Likely Reclassification |
|---------|------------------------|
| Implementation reveals unexpected dependencies | Clear → Complicated or Complex |
| Probe results are inconclusive after one iteration | Complex (continue probing) |
| Escalation counter hit (e.g., reviewer returns work 3x) | Complicated → Complex |
| Stabilization reveals deeper systemic issues | Chaotic → Complex |
| Information gathering reveals clear solution | Disorder → Clear or Complicated |
| "Simple" task cascades to 5+ files | Clear → Complicated |
| Analysis produces confident plan | Complex → Complicated |

### Reclassification Process

1. **Any agent** can flag a potential misclassification to Lead via SendMessage
2. **Lead** decides whether to re-run Architect with the new domain classification
3. **Architect re-run** produces a revised plan appropriate to the new domain
4. **Work in progress** is preserved where compatible with the new plan
5. **Document the reclassification** in the quality gate output

Reclassification does not require abandoning completed work. Completed components remain valid
unless the new domain classification reveals they were built on wrong assumptions.

---

## Advisory Principles

**Classification is advisory, not restrictive.** These rules hold regardless of domain:

1. Any agent can escalate to a higher-complexity domain at any time without Lead approval.
   Escalating is always safe; under-classifying is the risk.

2. Domain classification never restricts the depth of mandatory phases. Architecture, Implementation,
   Review, and Quality Gate always run. Classification affects *how* they run, not *whether*.

3. Classification CAN inform skip decisions for optional phases. Phase 2.5 Security Design Review
   is the primary example — it can be skipped for Clear-domain tasks that don't touch sensitive
   surfaces, but it is mandatory for Complex and always recommended for Complicated.

4. A Clear-domain task that touches auth, sessions, data handling, user input, or external APIs
   should receive Phase 2.5 review regardless of domain classification. Surface area matters more
   than task complexity for security review decisions.

5. When in doubt, classify higher. Complex is a safer default than Complicated. Complicated is
   safer than Clear. The cost of over-caution is more iterations. The cost of under-caution is
   unexpected failures in production.
