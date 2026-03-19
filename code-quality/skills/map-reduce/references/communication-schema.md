# Communication Schema

This file contains all JSON schemas for inter-agent communication within the `/map-reduce` skill.
Every message passed between agents via SendMessage and every file written to the audit trail
follows one of these schemas. The Lead uses these schemas to validate incoming messages and to
construct outgoing context for each agent.

---

## ChunkAssignment (Lead → Mapper)

Sent by the Lead to assign a chunk to a mapper agent. Written to `{run_dir}/chunks/chunk-{id}.json`
before spawning the mapper (so the mapper can read it on startup if preferred over inline prompt).

```json
{
  "schema": "ChunkAssignment",
  "chunk_id": "string — e.g. 'chunk-1'",
  "chunk_description": "string — what this chunk covers (e.g. 'auth/ directory: login, session, token files')",
  "files": ["string — file paths in this chunk"],
  "items": ["string — items to process if item-based split (empty array if file-based)"],
  "instructions": "string — what the mapper should do with this chunk (analysis, transformation, etc.)",
  "cross_reference_manifest": [
    {
      "file": "string — file path outside this chunk",
      "exported_symbols": ["string — function/class names exported from this file"]
    }
  ],
  "output_path": "string — where to write ChunkResult (e.g. 'hack/map-reduce/YYYY-MM-DD/chunks/chunk-1.json')",
  "context": {
    "project": "string — project name",
    "task": "string — original user task description",
    "branch": "string — current git branch name",
    "run_dir": "string — path to hack/map-reduce/YYYY-MM-DD",
    "tool_guard": "Use Read/Write/Edit/Glob/Grep/Bash for file ops. No raw shell for file reads."
  }
}
```

**Notes:**
- `files` and `items` are mutually exclusive (one will be populated, the other empty).
- `cross_reference_manifest` contains entries for ALL files NOT in this chunk. Mappers use
  this to check whether an "unused" symbol might be referenced outside their chunk.
- The manifest is intentionally lightweight — only exported/public symbol names, not full
  signatures. Mappers that need full type info must read the referenced file directly.

---

## ChunkResult (Mapper → Lead)

Written by each mapper to `{output_path}` from its ChunkAssignment. The mapper also sends a
summary to the Lead via SendMessage when complete.

```json
{
  "schema": "ChunkResult",
  "chunk_id": "string — matches ChunkAssignment chunk_id",
  "status": "complete | partial | failed",
  "findings": [
    {
      "id": "string — MAP-{chunk_id}-NNN (e.g. MAP-chunk-1-001)",
      "severity": "critical | high | medium | low | informational",
      "confidence": "verified | chunk-local",
      "file": "string — file path",
      "line": "integer | null — line number if applicable",
      "description": "string — clear description of the issue or finding",
      "evidence": "string — quoted code snippet or specific observation",
      "suggested_action": "string — what should be done about this finding",
      "cross_chunk_dependency": "string | null — symbol or file in another chunk this finding depends on"
    }
  ],
  "summary": "string — overview of what was found or done in this chunk",
  "files_modified": ["string — file paths modified (if implementation workload; empty for analysis)"],
  "turn_count": "integer — mapper's self-reported tool-call round count since spawn"
}
```

**Confidence field semantics:**
- `verified`: the finding is self-contained within this chunk. No cross-chunk context needed
  to confirm it. Examples: syntax errors, style violations, security vulnerabilities in internal
  logic, unused local variables.
- `chunk-local`: the finding's validity depends on context outside this chunk. The reducer
  MUST cross-validate before treating it as confirmed. Examples: exported symbol with no
  references (might be used in another chunk), import from a path not in this chunk (might
  exist in another chunk).

**Status semantics:**
- `complete`: mapper processed all assigned files/items successfully
- `partial`: mapper processed some files but encountered errors on others (list in `summary`)
- `failed`: mapper could not complete the assignment (fatal error)

---

## ReductionInput (Lead → Reducer)

Sent by the Lead to the reducer agent after all mappers complete. The reducer reads individual
ChunkResult files from `chunk_results_dir` rather than receiving them inline (to avoid message
size limits).

```json
{
  "schema": "ReductionInput",
  "total_chunks": "integer — total number of chunks assigned",
  "completed_chunks": "integer — number of chunks with status 'complete' or 'partial'",
  "failed_chunks": ["string — chunk_ids that returned status 'failed'"],
  "chunk_results_dir": "string — path to {run_dir}/chunks/ directory",
  "reduction_instructions": "string — how to synthesize: deduplicate, cross-validate, rank by severity, merge evidence",
  "output_path": "string — where to write ReductionResult (e.g. 'hack/map-reduce/YYYY-MM-DD/reduction-result.json')",
  "context": {
    "project": "string — project name",
    "task": "string — original user task description",
    "branch": "string — current git branch name",
    "run_dir": "string — path to hack/map-reduce/YYYY-MM-DD",
    "tool_guard": "Use Read/Write/Edit/Glob/Grep/Bash for file ops. No raw shell for file reads."
  }
}
```

**Note:** The reducer reads all `chunk-*.json` files from `chunk_results_dir` using Glob,
then processes them. The Lead does NOT inline all chunk data — that would exceed message limits
for large workloads.

---

## ReductionResult (Reducer → Lead)

Written by the reducer to `{output_path}` from the ReductionInput. The reducer also sends a
summary to the Lead via SendMessage when complete.

```json
{
  "schema": "ReductionResult",
  "status": "complete | partial",
  "total_findings": "integer — raw finding count across all chunks before deduplication",
  "deduplicated_findings": "integer — finding count after deduplication",
  "invalidated_findings": "integer — chunk-local findings disproven by cross-chunk validation",
  "by_severity": {
    "critical": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "informational": 0
  },
  "findings": [
    {
      "id": "string — original MAP-{chunk_id}-NNN id or MERGED-NNN for merged findings",
      "severity": "critical | high | medium | low | informational",
      "confidence": "verified",
      "file": "string — file path",
      "line": "integer | null",
      "description": "string — merged/deduplicated description",
      "evidence": "string — combined evidence from all chunks that found this issue",
      "suggested_action": "string — recommended action",
      "source_chunks": ["string — chunk_ids that originally found this issue"]
    }
  ],
  "cross_chunk_issues": [
    "string — conflicts or inconsistencies between chunks (e.g. 'chunk-1 proposes removing function X, chunk-3 adds a new call to function X')"
  ],
  "fidelity_warnings": [
    "string — any concerns about chunk boundary effects on result quality"
  ],
  "summary": "string — executive summary of all findings after synthesis"
}
```

**Note:** All findings in `ReductionResult.findings` have `confidence: "verified"`. The reducer
promotes or invalidates all `chunk-local` findings from mappers before writing output. The
`invalidated_findings` count records how many `chunk-local` findings were disproven.

---

## Audit Trail Directory Structure

```
hack/map-reduce/
└── YYYY-MM-DD/               # or YYYY-MM-DD-2, YYYY-MM-DD-3 for multiple runs
    ├── chunks/
    │   ├── chunk-1.json      # ChunkResult from mapper 1 (written by mapper)
    │   ├── chunk-2.json      # ChunkResult from mapper 2
    │   └── ...               # Up to 8 chunk files
    ├── reduction-result.json # ReductionResult from reducer
    └── map-reduce-report.md  # Final human-readable report (written by Lead)
```

The Lead resolves `{run_dir}` in Phase 0 and passes it to all agents in their context bundle.
Agents write directly to `{run_dir}/...` without needing to know the date or sequence number.
