# Pipeline Model

This file documents the pipelined execution model used in Phase 3 of the `/swarm` skill. It
covers how the Architect decomposes work into components, how those components flow through the
Implementer → Reviewer → Test-Writer → Test-Runner pipeline, and how the Lead manages parallelism,
backpressure, and failure recovery. Read this alongside `communication-schema.md` which defines
the JSON messages exchanged at each pipeline stage.

---

## Component Decomposition

The Architect's primary job is to break the implementation task into components that can be
pipelined. A well-decomposed component has these properties:

- **Logically cohesive:** All files in the component serve a single purpose (e.g., one feature,
  one service layer, one data model).
- **Bounded scope:** 1 to 5 files. Larger groups should be split. Single-file components are
  fine.
- **Independently testable:** The component can be tested in isolation, without depending on
  another component being complete first.
- **Clear interface:** If other components depend on this one, the interface (function signatures,
  exported types, API shape) is fully specified in the plan before implementation begins.

### Dependency Detection Rules

The Architect identifies dependencies between components using these rules:

| Pattern | Dependency Type |
|---------|----------------|
| Component B imports from Component A's files | B depends on A |
| Component B calls functions defined in Component A | B depends on A |
| Component B uses a data model defined in Component A | B depends on A |
| Components A and B both modify the same file | They must be sequential |
| Component B's tests fixture relies on Component A | B's tests depend on A |

**Independent = no dependency in either direction.** Two components are independent if neither
imports, calls, or shares files with the other. Independent components can be pipelined.

### Size Guidelines

| Estimated Complexity | Target Size |
|---------------------|-------------|
| low | 1-2 files, focused changes |
| medium | 2-4 files, new functionality |
| high | 3-5 files, architectural impact |

If a logical unit exceeds 5 files, split it at a natural boundary (e.g., split data layer from
business logic, split API handler from service, split model from migration).

---

## Pipeline Stage Definitions

The pipeline has four stages. The Lead acts as the routing layer between stages, never storing
intermediate state beyond what is needed to construct the next message.

```
+-------------------+     ComponentHandoff     +------------------+
|                   | -----------------------> |                  |
|   Implementer     |     (if rejected)        |    Reviewer      |
|   (sonnet)        | <----------------------- |    (opus)        |
|                   |   ReviewResult           |                  |
+-------------------+                          +------------------+
                                                       |
                                                       | ReviewResult (approved)
                                                       | --> Lead constructs TestRequest
                                                       v
+-------------------+     TestHandoff          +------------------+
|                   | <----------------------- |                  |
|   Test-Runner     |                          |   Test-Writer    |
|   (haiku)         |   TestExecution          |   (sonnet)       |
|                   | <-- Lead routes          |                  |
+-------------------+                          +------------------+
         |
         | TestResult
         v
      Lead routes:
        pass --> component complete
        fail --> back to Implementer with failure details
```

### Stage Transition Table

| From | To | Trigger | Message Type |
|------|----|---------|-------------|
| Lead | Implementer | Component assigned | Context bundle + component spec |
| Implementer | Lead | Component finished | `ComponentHandoff` |
| Lead | Reviewer | Handoff received | Routes `ComponentHandoff` to Reviewer |
| Reviewer | Lead | Review complete | `ReviewResult` |
| Lead | Implementer | Reviewer rejected | Routes `ReviewResult` issues back |
| Lead | Test-Writer | Reviewer approved | `TestRequest` constructed from `ReviewResult` |
| Test-Writer | Lead | Tests written | `TestHandoff` |
| Lead | Test-Runner | Tests ready | `TestExecution` constructed from `TestHandoff` |
| Test-Runner | Lead | Tests executed | `TestResult` |
| Lead | Implementer | Tests failed | Routes `TestResult` failures back |

---

## Pipeline Execution Modes

The Lead selects the execution mode based on the Architect's `component_dependency_graph`.

### Full Pipeline Mode (2+ Independent Components)

When components can run in parallel, the Implementer starts the next component while the
Reviewer is still processing the previous one. This is the most efficient mode.

```
Time -->

Component A:  [Implement A ]-->[Review A    ]-->[Write A Tests]-->[Run A Tests]
Component B:            [Implement B ]-->[Review B    ]-->[Write B Tests]-->[Run B Tests]
Component C:                      [Implement C ]-->[Review C    ]-->[Write C Tests]-->[Run C Tests]

Lead:         Route A --> Route A --> Route B --> Route A+ --> Route B --> Route C --> ...
```

The Implementer pipeline advances as soon as the Reviewer picks up a component. The Implementer
does not wait for Reviewer to finish — it immediately starts the next independent component.

**Lead throttle rule:** Never let the Implementer start a component if the Reviewer already has
2 components in queue (see Backpressure section). The Implementer waits until the Reviewer's
queue drops to 1.

### Sequential Mode (All Components Dependent)

When every component depends on the previous one completing, the pipeline cannot parallelize.
The same 4-agent team runs each component to completion before starting the next.

```
Time -->

Component A:  [Implement A ]-->[Review A]-->[Write A Tests]-->[Run A Tests]
                                                                      |
                                                                 (A complete)
                                                                      |
Component B:                                                    [Implement B ]-->...
```

Same message types, same handoff schemas — just no overlap between components.

### Mixed Mode (Partial Dependencies)

When some components are independent and others form chains, the Lead reorders for maximum
parallelism: schedule independent components first, chain-dependent components after their
predecessors complete.

```
Time -->

Group 1 (independent):
  Component A:  [Implement A ]-->[Review A]-->[Tests A]
  Component B:          [Implement B ]-->[Review B]-->[Tests B]

Group 2 (depends on A):
  Component C:                                   (wait for A)-->[Implement C]-->...

Group 3 (depends on B):
  Component D:                                          (wait for B)-->[Implement D]-->...
```

The Lead tracks which components have completed and gates each dependent component on its
prerequisites. A component in Group 2 does not start until its Group 1 dependency sends a
passing `TestResult`.

---

## Backpressure Handling

**Problem:** In Full Pipeline Mode, the Implementer is faster than the Reviewer (sonnet vs
opus). If the Implementer submits components faster than the Reviewer can review them, the
Reviewer accumulates a growing queue of unreviewed work, which creates confusion and context
overload for the Reviewer agent.

**Solution:** The Lead tracks a queue count for each downstream stage and throttles the
Implementer when queues are full.

### Queue Limits

| Stage | Max Queue Depth | Action When Full |
|-------|----------------|-----------------|
| Review | 2 components | Stop Implementer; wait for Reviewer to clear to 1 |
| Test-Write | 2 components | Hold Test-Writer assignments until queue drops |
| Test-Run | 3 components | Hold Test-Runner assignments; process FIFO |

### Throttling Protocol

When the Reviewer has 2 components in queue:
1. Lead does NOT send another `ComponentHandoff` to the Reviewer.
2. Lead sends a hold message to the Implementer: "Hold on Component X until Reviewer clears."
3. Implementer acknowledges and waits.
4. When the Reviewer sends a `ReviewResult` (reducing its queue to 1), Lead resumes.
5. Lead routes the held `ComponentHandoff` and signals the Implementer to proceed.

The Lead maintains a simple in-memory count of in-flight messages per stage:

```
reviewer_queue: integer (increments on each ComponentHandoff sent, decrements on each ReviewResult received)
test_writer_queue: integer (increments on each TestRequest, decrements on each TestHandoff)
test_runner_queue: integer (increments on each TestExecution, decrements on each TestResult)
```

---

## Fallback to Sequential

The pipeline falls back to sequential mode when any of these conditions are true:

| Condition | Reason |
|-----------|--------|
| `pipeline_feasible: false` in architect plan | All components are dependent |
| Only 1 component in the plan | Nothing to parallelize |
| `--sequential` flag passed to `/swarm` | User explicitly requested sequential |
| Multiple components share a file they both modify | Concurrent edits would conflict |

In sequential mode:
- The same 4-agent team (Implementer, Reviewer, Test-Writer, Test-Runner) is used.
- The same JSON schemas and handoff protocol apply.
- Components are processed one at a time in `implementation_order` from the architect plan.
- No throttling is needed since there is no parallelism.

Sequential mode produces the same quality as pipeline mode — it is not a degraded fallback, just
a different scheduling strategy.

---

## Team Lifecycle

### Phase 3: Pipeline Team Spawn

Spawn all 4 pipeline agents at the start of Phase 3, before assigning any components. Agents
should be idle and waiting for the Lead's first message. The Architect (from Phase 2) remains
active and on standby for clarification questions.

```
Lead spawns (pipeline team):
  implementer  (general-purpose, sonnet)   -- waits for first component assignment
  reviewer     (general-purpose, opus)     -- waits for first ComponentHandoff
  test-writer  (general-purpose, sonnet)   -- waits for first TestRequest
  test-runner  (dev-essentials:test-runner, haiku) -- waits for first TestExecution

Active from Phase 2 (remains available):
  architect    (code-quality:architect, opus) -- on standby for clarification questions
```

These 4 pipeline agents persist through ALL components in Phase 3. Do not shut them down between
components — maintaining the agents' context across the full implementation reduces re-explanation
overhead and keeps the Reviewer aware of earlier decisions.

The Architect persists through Phase 3 to answer clarification questions from the Implementer
and Reviewer. Implementers and Reviewers may message the Architect directly for clarifications
without routing through the Lead.

### Context Health & Agent Recycling

Pipeline agents are persistent and accumulate context across all components. The Lead monitors
`turn_count` from each agent's structured messages and proactively recycles agents approaching
context limits. See `orchestration-playbook.md` Step 3.6 for the full protocol.

Key points for the pipeline:
- Recycling only happens **between components**, never mid-implementation or mid-review
- The replacement agent receives the original prompt plus a `HandoffSummary` from the outgoing
  agent, ensuring continuity of design decisions and file state awareness
- The pipeline flow is paused during recycling — no new `ComponentAssignment` is sent until the
  replacement agent is ready
- Recycling is transparent to other pipeline agents: the Reviewer doesn't know (or care) if it's
  reviewing work from the original or replacement Implementer

If an agent goes silent (idle without completion message), the Lead follows the silent failure
detection protocol: status check → recovery spawn → escalate if repeated. See
`communication-schema.md` for the detection heuristic and recovery schemas.

### Phase 3 Completion

The pipeline team is done when all components have passed the Test-Runner and all `TestResult`
messages show `status: pass`. The Lead sends shutdown requests to all 4 pipeline agents AND the
Architect before spawning the Phase 4 review team. The Architect's clarification role ends at
Phase 3 completion.

### Phase 4: Review Team Spawn

Spawn all Phase 4 reviewers simultaneously at the start of Phase 4:

```
Lead spawns (all at once):
  security    (code-quality:security, opus)        -- reads all modified files
  qa          (code-quality:qa, opus)              -- reads all modified files
  code-reviewer (superpowers:code-reviewer, sonnet) -- reads all modified files
  performance (code-quality:performance, sonnet)   -- reads all modified files
  [optional] ui-reviewer, api-reviewer, db-reviewer
```

All Phase 4 reviewers are read-only. They operate on the final state of all files after Phase 3
completes. They do not communicate with each other — all communication goes through the Lead.

### Phase 4 Completion

The Lead waits for `ReviewFindings` from every spawned reviewer. After all findings are received,
the Lead synthesizes them and shuts down all reviewers before proceeding to Phase 5.

### Phase 5: Fix Team Spawn (conditional)

If any reviewer reported critical or high findings, spawn the Fixer and Code-Simplifier:

```
Lead spawns sequentially:
  fixer            (general-purpose, sonnet)                   -- addresses findings
  code-simplifier  (code-simplifier:code-simplifier, sonnet)   -- post-fix pass
```

These two run sequentially: Fixer first, Code-Simplifier after Fixer completes. Shut down both
before Phase 6.

### Phase 6: Docs Agent

```
Lead spawns:
  docs  (general-purpose, haiku)  -- updates docs and hack/ memory
```

Single agent, shut down after it reports completion.

### Phase 7: Verifier

```
Lead spawns:
  verifier  (dev-essentials:test-runner, haiku)  -- full test suite + lint
```

Single agent, shut down after it sends `VerificationResult`.

### Resource Management Summary

| Phase | Active Teammates | Max Count |
|-------|-----------------|-----------|
| 0 | Lead only | 1 |
| 1 | Lead only | 1 |
| 2 | Architect | 2 |
| 3 | Architect (standby) + Implementer, Reviewer, Test-Writer, Test-Runner | 6 (5 + Lead) |
| 4 | Security, QA, Code-Reviewer, Performance (+ optionals) | 5-10 + Lead |
| 5 | Fixer, then Code-Simplifier | 2 + Lead |
| 6 | Docs | 2 |
| 7 | Verifier | 2 |

Phases never overlap. The Lead shuts down each phase's agents before spawning the next phase.
This keeps the active teammate count bounded and prevents context cross-contamination between
phases. The maximum active count at any point is 8 (Lead + up to 7 Phase 4 reviewers with all
optional domain reviewers).
