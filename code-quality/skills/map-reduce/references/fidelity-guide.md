# Fidelity Guide

This file documents the known fidelity risks of map-reduce analysis, the mitigations baked into
the skill, and guidance on when NOT to use map-reduce. Read this before running /map-reduce on
a workload where result accuracy is critical.

---

## Fidelity Risks

| Risk | Description | Severity |
|------|-------------|----------|
| Cross-chunk blindness | Mapper reports a symbol as unused because all references to it are in a different chunk. Without cross-chunk validation, this produces false-positive "remove this" recommendations. | High |
| Deduplication loss | Two mappers independently find the same issue. Naive merging keeps one and drops the other, losing evidence or nuance from the dropped instance. | Medium |
| Context fragmentation | Architectural issues that span chunk boundaries (e.g., a circular dependency between module A in chunk 1 and module B in chunk 2) are invisible to individual mappers. Each chunk sees only one side of the problem. | High |
| Chunk boundary artifacts | Tightly coupled files split across chunks produce misleading findings — one chunk lacks the context the other provides. A function call in chunk 1 that references a function defined in chunk 2 looks like a missing reference to chunk 1's mapper. | Medium |
| False confidence | A finding marked `verified` that is actually wrong because the mapper could not see the full picture. This happens when mappers incorrectly classify `chunk-local` findings as `verified`. | High |

---

## Mitigations

These mitigations are mandatory — they are baked into the skill protocol, not optional
configuration.

| Mitigation | Where Enforced | Addresses |
|------------|----------------|-----------|
| Module-aware splitting | Phase 0 (Plan & Split) | Chunk boundary artifacts |
| Cross-reference manifest | Phase 0 → ChunkAssignment schema | Cross-chunk blindness |
| `confidence` field on findings | Phase 1 mapper output (ChunkResult schema) | False confidence |
| `chunk-local` vs `verified` classification | Phase 1 mapper prompts | Cross-chunk blindness |
| 4-step cross-chunk validation | Phase 2 reducer protocol | Cross-chunk blindness, deduplication loss |
| "Resolve toward used" rule | Phase 2 reducer prompts | False positives on destructive actions |
| `invalidated_findings` count | Phase 2 reducer output (ReductionResult schema) | Fidelity monitoring |
| Fidelity report threshold | Phase 3 (>20% invalidation warns user) | Chunk boundary artifacts |

---

## When NOT to Use Map-Reduce

These scenarios are better served by a single agent or a different skill:

**Architectural analysis (circular dependencies, full data flow)**
- Requires full-codebase view simultaneously. Chunking makes cross-component relationships
  invisible to individual mappers. The reducer can partially reconstruct this, but will miss
  subtler patterns. Use a single powerful agent with the full codebase in context instead.

**Small workloads (<20 files)**
- The overhead of chunking, manifest building, parallel spawning, and reduction exceeds
  the benefit for small workloads. Direct parallel agents (3-5, no structured chunking)
  are simpler and equally effective.

**Tightly coupled codebases (>50% of files import from >50% of other files)**
- When coupling is so high that chunking is meaningless (almost every file is a cross-chunk
  dependency for every other file), the cross-reference manifest becomes enormous and the
  `chunk-local` → `verified` promotion rate approaches 100%. Use a single agent instead.

**Security audits requiring trust boundary analysis**
- Trust boundaries often span modules and directories. A mapper seeing only part of a trust
  boundary cannot assess whether the boundary is correctly enforced. Use `code-quality:security`
  directly for security audits that require holistic trust boundary analysis.

**Refactoring with semantic dependencies**
- When a rename or refactor has semantic implications that span the codebase (e.g., changing
  an interface contract), mappers working in isolation may apply the transformation incorrectly
  in their chunk. Use `/swarm` with a single Implementer for semantically-connected refactors.

---

## Fidelity Report Interpretation

After every /map-reduce run, the Lead checks the `invalidated_findings` count in the
ReductionResult and computes the invalidation rate:

```
invalidation_rate = invalidated_findings / total_findings
```

| Rate | Interpretation | Action |
|------|----------------|--------|
| 0–5% | Normal — very few cross-chunk false positives | Proceed normally |
| 5–20% | Elevated — some chunk boundaries were suboptimal | Note in report, no action required |
| >20% | High — chunk boundaries were poorly chosen | Warn user, suggest re-running with different splits or single-agent analysis |

A high invalidation rate means the mapper chunk boundaries did not respect the actual
dependency structure of the codebase. Consider:
- Switching from `by-directory` to `by-module` splitting
- Using the import graph to determine natural chunk boundaries
- Running a single-agent analysis if the codebase is too tightly coupled to chunk effectively
