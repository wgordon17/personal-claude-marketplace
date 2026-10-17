# Phase 3: Supersession Detection & Archive Execution Protocol

Detailed supersession detection rules, archive offer options, BUGS.md exception, and archive execution commands for Phase 3 of `/summarize`.

---

## Supersession Detection by Artifact Type

Check for a newer artifact covering the same scope:

| Type | Supersession detection |
|------|----------------------|
| Plan | Glob `{memory_dir}/plans/*.md` → check `**Branch:**` field. If another plan has the same `**Branch:**` AND a newer timestamp → superseded. If Branch is absent or "not yet created", fall back to post-timestamp slug match (e.g., slug `fix` from `feat-fix-skill-1743692400-fix.md`). Only match identical post-timestamp slugs. Never match on the prefix before the timestamp. Also superseded if a roadmap document lists this plan in its `**Source plans:**` field and the containing roadmap phase shows `**Status:** Completed`. |
| Swarm | Glob `{memory_dir}/swarm/*/` → check if a newer run exists for the same branch slug. |
| Research | Glob `{memory_dir}/research/*.md` → extract topic slug (strip up to and including the first unix timestamp or date segment), check for substring overlap with other slugs. |
| Speculative | Check if `judgment.json` exists with a `winner` field AND a newer speculative run dir exists for the same branch slug. |
| Map-Reduce | Check if `reduction-result.json` has `status == "complete"` AND a newer map-reduce run dir exists for the same branch slug. |
| Unfuck | Check if a newer unfuck run directory exists (unfuck is project-wide — any newer run supersedes older ones). |
| Roadmap | Not checked — roadmap lifecycle is managed by `/roadmap` cleanup mode (`**Status:** Completed` header). `/summarize` defers to that convention. |
| Bugs | Not checked — BUGS.md is a persistent tracker with no supersession concept. |

---

## Completed & Active Criteria

- **Completed:** All audit items PASS (or PASS + SKIP-only) and no supersession detected. Type-specific shortcuts override the audit-based check (Phase 2 still runs and its results are still displayed):
  - **Roadmaps:** `**Status:** Completed` in the **first 10 lines** of the document → classify as Completed regardless of audit results.
  - **Bugs:** All `## BUG-NNN` sections have `**Status:** Fixed` → classify as Completed regardless of audit results.
- **Active:** Any FAIL or PARTIAL items remain in audit results.
- **Obsolete Declaration:** Only set via the `obsolete_flag` mechanism (user includes `obsolete` in `/summarize` invocation). Never auto-classified. Uses `**Status:** Obsolete (YYYY-MM-DD)` as the status header.

---

## Archive Offer

If status is Completed, Superseded, or Obsolete, present:

```
AskUserQuestion: "This [type] artifact appears to be [completed/superseded/obsolete].
[If superseded: 'Superseded by: [newer artifact path]']
Would you like to archive it?"

Options:
- "Yes, archive it" → Execute archive (see Archive Execution below)
- "No, keep it active" → Print "Keeping [path] in place." and stop.
- "Mark as [completed/obsolete] but don't move" → Add status header (see step 1 of Archive
  Execution) but do not move the file. Omit this option for Superseded artifacts (marking a
  superseded artifact as "completed" is contradictory).
```

If status is Active, print the summary and audit results without an archive offer. End with:
> "This artifact has unfinished work. Use `/fix` to address findings, or implement remaining tasks manually."

---

## BUGS.md Exception

BUGS.md is a persistent tracker — **never** move it to `done/` and **never** add a status
header. BUGS.md completion is determined by its entry statuses (all Fixed), not a document
header. Adding `**Status:** Archived` would permanently block future `/summarize` access if
new bugs are later added.

When BUGS.md is classified as Completed, skip the archive offer AskUserQuestion entirely.
Instead, print directly:
> "All bug entries are Fixed — BUGS.md is currently complete. File kept in place for future bug tracking. New bugs can be added at any time."

---

## Archive Execution

**Step 1 — Insert status header.**
Add the appropriate status header as the first line after the document title (or after the
frontmatter `---` if present):

| Status | Header |
|--------|--------|
| Completed or Superseded | `**Status:** Archived (YYYY-MM-DD)` |
| Obsolete | `**Status:** Obsolete (YYYY-MM-DD)` |

- **Single-file artifacts** (plans, research, roadmap): Edit the file directly using Edit.
- **Directory artifacts** (swarm, speculative, map-reduce, unfuck): Add the status header to
  the **primary report file** (`swarm-report.md`, `speculative-report.md`,
  `map-reduce-report.md`, `cleanup-report.md`). Note: archive is only offered for complete
  runs — incomplete runs (which use fallback files) are classified Active and never reach here.

**Step 2 — Create done/ subdirectory.**
```bash
uv run python -c "import os; os.makedirs('${parent}/done/', exist_ok=True)"
```

The path for `${parent}` is carried from Phase 0 validation (held in memory — no re-stat,
no TOCTOU risk between validation and move).

**Step 3 — Move the artifact.**
- Files: `uv run python -c "import shutil; shutil.move('${path}', '${parent}/done/${filename}')"`
- Directories (swarm, speculative, map-reduce, unfuck): `uv run python -c "import shutil; shutil.move('${dir}', '${parent}/done/${dirname}')"`
- **Root-level unfuck** (no run-id subdirectory — `cleanup-plan.md` at `unfuck/` root):
  Run as a single script to capture the date once and move all files atomically:
  ```bash
  uv run python -c "
  import os, shutil
  from datetime import date
  d = '${unfuck_dir}/done/root-' + date.today().strftime('%Y%m%d')
  os.makedirs(d, exist_ok=True)
  shutil.move('${unfuck_dir}/cleanup-plan.md', d)
  if os.path.isdir('${unfuck_dir}/discovery'):
      shutil.move('${unfuck_dir}/discovery', d)
  if os.path.isfile('${unfuck_dir}/cleanup-report.md'):
      shutil.move('${unfuck_dir}/cleanup-report.md', d)
  "
  ```
  `cleanup-report.md` (the file carrying the status header) moves last — if an earlier
  `shutil.move` raises an exception, the status header file remains at the root where the
  revert path can reach it. Print confirmation with all moves.

**If a move fails** (Python raises an exception): revert the status header edit using Edit
(remove the status header line inserted in step 1 from the primary report file). Print:
> "Archive failed: could not move [path] to done/. Status header reverted. Error: [error message]."

Stop. Do not leave the artifact in a half-archived state.

**Step 4 — Confirm.**
Print:
> "Archived [path] → [new path]"
