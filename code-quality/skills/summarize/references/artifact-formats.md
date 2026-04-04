# Artifact Formats Reference

Detection signatures, parsing rules, audit checklists, completion criteria, and supersession
signals for the 8 artifact types managed by the `/summarize` skill. An LLM reading this file
should be able to detect, summarize, and audit any of these artifact types without consulting
other files.

---

## Legacy Naming

Older artifacts use date-based names instead of the current run-id convention.

| Era | Directory example | File example |
|-----|------------------|--------------|
| Legacy (date-based) | `swarm/2026-03-16/` | `research/2026-03-17-the-swarm-evaluation.md` |
| Current (run-id) | `swarm/feat-fix-skill-1775231207/` | `research/feat-plan-adherence-1775072271-code-simplification-enforcement.md` |

**Detection rule:** Any file or directory at the expected location pattern that matches the
structural markers is a valid artifact regardless of name format. Timestamp extraction falls
back to the file's mtime when the name does not contain a unix timestamp.

---

## 1. Plans

### Detection Signature

- **Glob:** `{memory_dir}/plans/*.md`
- **Structural markers (both required):**
  - `**Goal:**` line in the file header
  - At least one `## Task N:` heading (`N` is any integer)
- **Exclude:** Files matching `*roadmap*.md` — those are Roadmap artifacts (type 4).

### Location Pattern

```
{memory_dir}/plans/{run-id}-<feature>.md
{memory_dir}/plans/<date>-<feature>.md   # legacy
```

### Key Fields to Extract

| Field | Source | Notes |
|-------|--------|-------|
| Goal | `**Goal:**` header line | 1-sentence summary of the feature |
| Branch | `**Branch:**` header line | Git branch this plan targets |
| Cynefin Domain | `**Cynefin Domain:**` header line | Complexity classification |
| Architecture Summary | `**Architecture Summary:**` header lines | 2-3 sentence overview |
| Task list | All `## Task N: <title>` headings | Extract title and checkbox completion state |
| Task progress | Count `- [x]` vs `- [ ]` step lines | Summarize as N/M steps complete |

### Audit Checklist

For each `## Task N` section:
1. Read the `**Files:**` block — verify each listed file exists in the codebase.
2. For files that exist: verify the code content matches the task's described behavior
   (grep for key function names or patterns mentioned in the task description).
3. For `- [x]` checked steps: confirm the described change is present in code.
4. For `- [ ]` unchecked steps: note as incomplete work.

### Completion Criteria

A plan is **complete** when every `- [ ]` step line across all tasks has been checked off
(`- [x]`). A plan with all tasks complete but no steps checked is ambiguous — report the
task count and note that step-level completion is unknown.

### Supersession Signals

A plan is superseded when any of the following is true:
- A **newer plan file** (later mtime or higher unix timestamp in the filename) has the same
  `**Branch:**` value.
- A **newer plan file** has the same post-timestamp slug in the filename.
  - Slug extraction: strip everything up to and including the first unix timestamp or date
    segment (e.g., `feat-fix-skill-1743692400-fix.md` → slug `fix`).
  - Use substring matching for overlap — do NOT match on the prefix before the timestamp
    (e.g., `feat-fix-skill-*` is a branch namespace, not a feature identifier).
- A **roadmap document** lists this plan in its `**Source plans:**` field and the roadmap
  phase containing this plan shows `**Status:** Completed`.

---

## 2. Swarm Reports

### Detection Signature

- **Glob:** `{memory_dir}/swarm/*/`
- **Complete run** (primary detection): directory contains `swarm-report.md`
- **Incomplete run** (fallback detection): directory contains `architect-plan.json` but no
  `swarm-report.md`
- Both detection paths are valid — document both in any status report.

### Location Pattern

```
{memory_dir}/swarm/{run-id}/         # current
{memory_dir}/swarm/<date>/           # legacy
```

### Swarm Report Format Eras

Swarm reports span three format eras. Identify the era before extracting fields.

| Era | Identifier | Directory naming | Report characteristics |
|-----|-----------|-----------------|----------------------|
| Era 1 | No `**Run ID:**` field; generic title (e.g., `# Swarm Report`) | Date-only (`2026-03-16/`) | Prose headers; no structured tables; pipeline summary in prose bullets |
| Era 2 | No `**Run ID:**` field; bold `**Key:**` pairs present | Date-only dirs | `**Key:**` bold pairs for metadata; structured tables in some sections |
| Era 3 | `**Run ID:**` field present | Run-id dirs (e.g., `feat-fix-skill-1775231207/`) | `## Scope Accountability` table; `## Agents Spawned` section; `## Commits` table |

### Key Fields to Extract

**Common across all eras:**

| Field | Location | Notes |
|-------|----------|-------|
| Branch | `**Branch:**` field in report header | Git branch this swarm targeted |
| Phase execution | Table labeled `## Pipeline Execution`, `## Pipeline Summary`, or `## Phase Summary` (era-dependent) | Extract phase count and final phase status |
| Finding counts | Era-adaptive — see below | Prose bullets (Era 1), `### Fixed`/`### Deferred` subsections (Era 2), or findings disposition table (Era 3) |

**Era 3 only (identified by `**Run ID:**`):**

| Field | Location | Notes |
|-------|----------|-------|
| Run ID | `**Run ID:**` header field | Unique run identifier |
| Agent count | `## Agents Spawned` section | Report "not recorded" for Era 1/2 |
| Key commits | `## Commits` table | SHA + description pairs |
| Scope accountability | `## Scope Accountability` table | Blocked items indicate incomplete work |

### Audit Checklist

**All eras (use fields present in the actual report):**
1. **Branch verification:** Extract the `**Branch:**` value → verify the branch exists in
   `git log --all` or was merged to main.
2. **Phase 7 completion:** Find the phase execution table → check if Phase 7 row is present
   and shows Complete/Done/PASS. If Phase 7 row is absent, skip this check (Era 1 reports
   predate Phase 7).
3. **Test non-regression:** Look for test counts in the verification section → confirm
   `net_new_failures` is 0 or `regression: false`.

**Era 3 additional checks (only when `**Run ID:**` is present):**
4. **Commit SHAs:** Extract SHAs from `## Commits` table → verify each exists in `git log`.
5. **Scope accountability:** Check `## Scope Accountability` → confirm no rows show
   `Blocked` status.

### Completion Criteria

A swarm run is **complete** when:
- `swarm-report.md` exists in the run directory, AND
- The phase execution table shows Phase 7 with status Complete/Done/PASS (or Phase 7 is
  absent and all present phases show completion — Era 1 tolerance), AND
- Test counts show zero net new failures (regression: false or net_new_failures: 0).

An incomplete run (has `architect-plan.json` but no `swarm-report.md`) is **in-progress**
or **abandoned** — report whichever is more recent based on file mtime.

### Supersession Signals

A swarm run is superseded when a **newer run directory** (later mtime or higher unix
timestamp in the run-id) targets the same `**Branch:**` value.

---

## 3. Research Documents

### Detection Signature

- **Glob:** `{memory_dir}/research/*.md`
- **Structural markers (both required):**
  - `# ` first-level heading (the report title)
  - `## Executive Summary` section
- Distinguish from plans: research docs have `## Executive Summary`, not `**Goal:**`.

### Location Pattern

```
{memory_dir}/research/{run-id}-<topic>.md
{memory_dir}/research/<date>-<topic>.md   # legacy
```

### Key Fields to Extract

| Field | Source | Notes |
|-------|--------|-------|
| Title | `# ` first heading | Research topic |
| Executive Summary | `## Executive Summary` section | 2-3 paragraph TL;DR |
| Methodology | `## Methodology` section | Sources consulted, date range, hop depth |
| Primary recommendation | Under `## Recommendations` → `### Primary Recommendation` | The chosen option |
| Next Steps | `## Next Steps` numbered list | Actions following from the research |

### Audit Checklist

1. **Recommendation adoption:** For each item in `## Recommendations`, grep the codebase
   for patterns or identifiers mentioned. Note whether the recommendation was adopted,
   partially adopted, or not yet implemented.
2. **Configuration changes:** For recommendations involving config files (e.g., settings,
   tooling), check the relevant config files directly.
3. **Next Steps state:** Read `## Next Steps` — if items are still framed as future actions
   rather than completed work, the research is done but its recommendations may be unactioned.

### Completion Criteria

A research document is **complete** when it has been written and delivered — the document
exists with findings, recommendations, and next steps. Research docs do not have a
"checkbox" model; presence of `## Next Steps` with recommendations (not incomplete tasks)
signals a complete deliverable. The research being "done" does not mean recommendations
were adopted — those are tracked separately.

### Supersession Signals

A research document is superseded when a **newer research file** (later mtime or higher
unix timestamp) has an overlapping topic slug.

**Slug extraction:** Strip everything up to and including the first unix timestamp or date
segment in the filename. Examples:
- `feat-plan-adherence-1775072271-code-simplification-enforcement.md` → slug: `code-simplification-enforcement`
- `2026-03-17-the-swarm-evaluation.md` → slug: `the-swarm-evaluation`

Use **substring matching** for overlap detection (e.g., `swarm-evaluation` overlaps
`the-swarm-evaluation`).

---

## 4. Roadmap Documents

### Detection Signature

- **Glob:** `{memory_dir}/plans/*roadmap*.md`
- **Structural markers (both required):**
  - `# Roadmap:` first-level heading (heading text starts with "Roadmap:")
  - `**Source plans:**` field in the document header block

### Location Pattern

```
{memory_dir}/plans/{run-id}-roadmap-<name>.md
{memory_dir}/plans/<date>-roadmap-<name>.md   # legacy
```

### Key Fields to Extract

| Field | Source | Notes |
|-------|--------|-------|
| Title | `# Roadmap:` heading | Roadmap name |
| Source plans | `**Source plans:**` field | List of plan files this roadmap coordinates |
| Total phases | `**Total phases:**` field | Phase count |
| Critical path | `**Critical path:**` field | Longest dependency chain |
| Per-phase status | `**Status:**` within each phase block | not-started / in-progress / Completed |
| Last updated | `**Last updated:**` field | ISO timestamp of last update (if present) |

### Audit Checklist

1. **Source plan existence:** For each path in `**Source plans:**`, verify the file still
   exists or was archived to `{memory_dir}/plans/done/`.
2. **Branch merge status:** For each phase track, check if `roadmap/phase-N/plan-name`
   branch was merged: `gh pr list --state merged --head roadmap/phase-N/plan-name`.
3. **Phase status accuracy:** Cross-reference `**Status:**` fields in phase blocks against
   actual branch merge status from git. Flag discrepancies.

### Completion Criteria

A roadmap is **complete** when `**Status:** Completed` appears in the first 10 lines of the
document header (indicating all phases are done). Individual phase blocks showing
`**Status:** Completed` indicate per-phase completion, not the overall roadmap.

### Supersession Signals

Roadmaps are not typically superseded — they are updated in-place (via Update mode) or
archived (via Cleanup mode). A roadmap with `**Status:** Archived` in the header has been
explicitly retired.

---

## 5. Bug Tracker (BUGS.md)

### Detection Signature

- **Glob:** `{memory_dir}/BUGS.md`
- **Structural markers:**
  - `# Bug Investigation` in the first heading (or similar — exact text varies by creation)
  - At least one `## BUG-NNN:` heading (NNN is a zero-padded integer)
- This is a **single file** artifact, not a directory.

### Location Pattern

```
{memory_dir}/BUGS.md
```

### Key Fields to Extract

Per `## BUG-NNN: <Title>` section:

| Field | Source | Notes |
|-------|--------|-------|
| Bug ID | `## BUG-NNN:` heading | Sequential identifier |
| Title | Text after `## BUG-NNN:` | Short description |
| Status | `**Status:**` field | Investigating / Root Cause Found / Fix Ready / Fixed |
| Reported date | `**Reported:**` field | YYYY-MM-DD |
| Impact | `**Impact:**` field | Critical / High / Medium / Low |
| Root Cause | `### Root Cause` section | Code-level explanation with file:line refs |
| Resolution Plan | `### Resolution Plan` checkboxes | `- [ ]` / `- [x]` steps |

**Summary extraction:** Count bugs by status and impact. List all non-Fixed bugs with their
impact and status.

### Audit Checklist

For each `## BUG-NNN` with `**Status:** Fixed`:
1. Read `### Files Involved` — open each listed file and verify the described fix is present.
2. Read `### Resolution Plan` — confirm all `- [ ]` steps were completed (look for `- [x]`
   or check the code directly if steps aren't checked off).

For each `## BUG-NNN` with `**Status:** Fix Ready`:
1. Read `### Resolution Plan` — verify the fix was actually applied by checking the code,
   not just the plan.

### Completion Criteria

BUGS.md is **fully resolved** when every `## BUG-NNN` section shows `**Status:** Fixed`.
Individual bugs are complete per the template: `**Status:** Fixed` in their section.

### Supersession Signals

BUGS.md is a living document — it is not superseded but updated in place. There is at most
one BUGS.md per memory directory.

---

## 6. Speculative Reports

> **Format derived from skill definition — not validated against actual artifacts.**
> No speculative artifacts have been created in this project. Detection signatures and
> field names are derived from `code-quality/skills/speculative/SKILL.md` and
> `code-quality/skills/speculative/references/communication-schema.md`.

### Detection Signature

- **Glob:** `{memory_dir}/speculative/*/`
- **Complete run** (primary detection): directory contains `judgment.json`
- **Incomplete run** (fallback): directory contains `implementations/competitor-*.json`
  but no `judgment.json`

### Location Pattern

```
{memory_dir}/speculative/{run-id}/
{memory_dir}/speculative/{run-id}/implementations/competitor-{id}.json
{memory_dir}/speculative/{run-id}/judgment.json
{memory_dir}/speculative/{run-id}/speculative-report.md
```

### Key Fields to Extract

| File | Field | Notes |
|------|-------|-------|
| `judgment.json` | `winner` | Competitor ID that was selected |
| `judgment.json` | `hybrid_recommended` | Whether a hybrid was recommended |
| `speculative-report.md` | Title | What was being compared |
| `implementations/competitor-*.json` | `status` per file | `complete` or `failed` |

### Audit Checklist

1. **Winner field:** Read `judgment.json` → extract `winner` field to identify the chosen competitor.
2. **Branch existence:** Verify the winning implementation was merged — check git log for
   the winning worktree's branch or commits.
3. **Loser cleanup:** Loser worktrees are deleted after merge, so absence of a branch is
   expected for non-winners. Use run dir timestamps for recency comparison, not branch existence.

### Completion Criteria

A speculative run is **complete** when `judgment.json` exists AND `winner` field is populated
AND `speculative-report.md` exists in the run directory.

### Supersession Signals

A speculative run is superseded when a **newer run directory** (later mtime or higher unix
timestamp in the run-id) targets the same branch slug. Compare run-id timestamps, not
branch existence (loser branches are deleted).

---

## 7. Map-Reduce Reports

> **Format derived from skill definition — not validated against actual artifacts.**
> No map-reduce artifacts have been created in this project. Detection signatures and
> field names are derived from `code-quality/skills/map-reduce/SKILL.md` and
> `code-quality/skills/map-reduce/references/communication-schema.md`.

### Detection Signature

- **Glob:** `{memory_dir}/map-reduce/*/`
- **Complete run** (primary detection): directory contains `reduction-result.json`
- **Incomplete run** (fallback): directory contains `chunks/chunk-*.json` but no
  `reduction-result.json`

### Location Pattern

```
{memory_dir}/map-reduce/{run-id}/
{memory_dir}/map-reduce/{run-id}/chunks/chunk-{id}.json
{memory_dir}/map-reduce/{run-id}/reduction-result.json
{memory_dir}/map-reduce/{run-id}/map-reduce-report.md
```

### Key Fields to Extract

| File | Field | Notes |
|------|-------|-------|
| `reduction-result.json` | `status` | `complete` or partial |
| `reduction-result.json` | `needs_fix_count` | Number of verified findings requiring fixes |
| `reduction-result.json` | `fidelity_warnings` | Invalidation rate warnings (>20% threshold) |
| `map-reduce-report.md` | Summary statistics section | Files analyzed, total / deduped / invalidated findings |

### Audit Checklist

1. **Status field:** Read `reduction-result.json` → verify `status == "complete"`.
2. **Fidelity warnings:** Check `fidelity_warnings` — if present, note that chunk boundaries
   may have been poorly chosen and findings may be less reliable.
3. **Findings addressed:** For each `needs-fix` finding in the reduction result, check
   whether the issue was addressed in the codebase (grep for the relevant symbol/pattern).

### Completion Criteria

A map-reduce run is **complete** when `reduction-result.json` exists AND
`status == "complete"` AND `map-reduce-report.md` exists.

### Supersession Signals

A map-reduce run is superseded when a **newer run directory** (later mtime or higher unix
timestamp in the run-id) targets the same branch slug.

---

## 8. Unfuck Reports

### Detection Signature

**Two detection paths — check both:**

**Path A — run-id subdirectory:**
- **Glob:** `{memory_dir}/unfuck/*/`
- Directory contains `cleanup-plan.md` or `cleanup-report.md`

**Path B — root-level (no run-id subdirectory):**
- **Glob:** `{memory_dir}/unfuck/cleanup-plan.md` or `{memory_dir}/unfuck/cleanup-report.md`
- Root-level files without a run-id directory are valid artifacts (confirmed in this project:
  `hack/unfuck/cleanup-plan.md` exists alongside `hack/unfuck/2026-02-18/` subdirectory)

### Location Pattern

```
{memory_dir}/unfuck/{run-id}/cleanup-plan.md       # current, run-id dir
{memory_dir}/unfuck/{run-id}/cleanup-report.md     # current, run-id dir
{memory_dir}/unfuck/<date>/cleanup-plan.md         # legacy date dir
{memory_dir}/unfuck/cleanup-plan.md                # root-level (no run-id)
{memory_dir}/unfuck/cleanup-report.md              # root-level (no run-id)
{memory_dir}/unfuck/done/root-{YYYYMMDD}/          # archived root-level
{memory_dir}/unfuck/done/{run-id}/                 # archived run-id
```

### Key Fields to Extract

| File | Field | Notes |
|------|-------|-------|
| `cleanup-plan.md` | Header status line | If present, indicates archived/complete state |
| `cleanup-plan.md` | Per-category findings | Security, dead code, duplicates, AI slop, complexity, architecture, docs |
| `cleanup-report.md` | Summary stats | Files modified, lines added/removed, issues fixed by category |
| `cleanup-report.md` | `## Blocked items` section | Findings that could not be auto-fixed |

### Audit Checklist

1. **Fix application:** For each completed category in `cleanup-report.md`, verify a commit
   exists with a matching category description (`git log --oneline | grep <category>`).
2. **Blocked items:** Read `## Blocked items` section — note any findings that require
   manual intervention and verify if they have since been addressed.
3. **Test passage:** Check that the cleanup run committed passing tests (look for test
   result summary in `cleanup-report.md`).

### Completion Criteria

An unfuck run is **complete** when:
- `cleanup-report.md` exists (Phase 4 writes this), AND
- All non-blocked categories show committed changes in git.

A run with only `cleanup-plan.md` and no `cleanup-report.md` is **planned but not implemented**
(or a `--dry-run` was used).

A root-level artifact that has been archived to `{memory_dir}/unfuck/done/root-{YYYYMMDD}/`
is **archived** (complete and moved).

### Supersession Signals

An unfuck run is superseded when a **newer cleanup run** (later mtime or higher unix
timestamp) exists for the same project scope. Root-level artifacts are superseded by any
newer run-id subdirectory run.
