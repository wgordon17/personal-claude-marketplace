# Finding Normalization Reference

Detailed procedures for Phase 0.5 of `/fix`: test plan discovery, common finding schema, and source-specific normalization rules.

---

## Test Plan Discovery

Before normalizing findings, discover the plan file for the current branch and load any linked
test plan. This follows the same branch-header matching algorithm as /swarm Phase 4 and
/quality-gate Step 0:

1. Get the current branch: `git branch --show-current`
2. Search `{memory_dir}/plans/` for plan files whose `**Branch:**` header field matches the
   current branch. Fall back to branch-slug prefix matching if no header match is found.
   Use the most recent by unix timestamp if multiple match. Normalize the discovered path
   (resolve `..` components) and verify it falls within `{memory_dir}/plans/`. If the
   normalized path escapes that directory, set `{plan_file_path_pr}` to empty string (skip
   counter increment silently). Otherwise store as `{plan_file_path_pr}` for counter increment.
   If no plan file is found, `{plan_file_path_pr}` is empty string.
3. If a plan file is found and it contains a `## Test Plan` section, extract the `**Test Plan:**`
   path annotation from that section.
4. Normalize the path (resolve `..` components) and verify it falls within
   `{memory_dir}/test-plans/`. If the normalized path escapes that directory, set
   `{plan_test_plan}` to empty string and log a warning: "Warning: test plan path escapes
   {memory_dir}/test-plans/ boundary — setting {plan_test_plan} to empty string."
5. If the path is valid but the file does not exist, set `{plan_test_plan}` to empty string
   (graceful fallback — no warning).
6. If the path is valid and the file exists, read the file and store its content as
   `{plan_test_plan}`.
7. If no plan file is found, or no `## Test Plan` section exists, set `{plan_test_plan}` to
   empty string.

`{plan_test_plan}` is available to all subsequent phases (Phase 1 triage and Phase 2 investigator
dispatch).

---

## Common Finding Schema

Each normalized finding tracks:

- `id` — generated identifier using pattern `{source-prefix}-{category-abbrev}-{N}`:
  - pr-review prefix: `pr` (e.g., `pr-test-1`, `pr-sec-2`, `pr-corr-3`)
  - plan-review prefix: `pl` (e.g., `pl-feas-1`, `pl-rsch-2`, `pl-arch-3`)
  - bug-investigation prefix: `BUG` (e.g., `BUG-001`, matching the BUGS.md entry number)
- `source` — one of: `pr-review` | `plan-review` | `bug-investigation`
- `classification` — always `needs-fix` (see note below on needs-input)
- `category` — the category section heading from the upstream output (e.g., `TESTING GAPS`,
  `RESEARCH GAPS`, `CORRECTNESS`)
- `location` — `file:line` for code findings, plan section name for plan findings, `BUG-NNN` for
  bug entries
- `description` — the finding text as written in the review output
- `evidence` — the reviewer's evidence line from the review output
- `suggested_fix` — `null` for pr-review and plan-review (not present in terminal output);
  the Resolution Plan checklist items for bug-investigation entries
- `is_research_gap` — `true` if (a) the finding appears under the `RESEARCH GAPS` category section
  OR has a `[Reviewer]` tag of `Unknown Unknowns` (plan-review — structural match on category +
  reviewer tag, not keyword matching), or (b) the finding description contains the literal string
  `Recommended resolution: /deep-research` (pr-review — see source-specific rule below).
- `verifier_verdict` — `"needs_context"` if from the upstream Needs Context section, otherwise `null`
- `spike_question` — the specific assumption to verify, extracted from the finding text.
  Populated only when `is_research_gap == true` AND `source == plan-review`. Null for pr-review
  research gaps (pr-review findings go to standard investigators, not spike investigators).
  Extraction rule: if the finding contains a question sentence (ends with `?`), use it verbatim.
  Otherwise, reformulate the finding's core assumption as a yes/no question.
- `plan_context` — surrounding plan text (5-10 lines around the affected section). Populated
  only when `is_research_gap == true` AND `source == plan-review`. Null for pr-review research
  gaps (no plan exists — the finding references code, not a plan section).

---

## Source-Specific Normalization

**pr-review findings:**

Categories (in display order): Testing Gaps, Correctness, Security, Architecture, Decisions Needed,
Performance, Style & Conventions. Extract all findings from each category section. Also extract
findings from the `─── Needs Context ───` section with `verifier_verdict: "needs_context"`.
For Needs Context findings, map the `Investigation: {investigation_summary}` field to `evidence`
(these use `Investigation:` instead of `Evidence:` in the upstream output).
Skip findings in the Deferred section — these were explicitly deferred by the user during the upstream review and should not be re-processed.
Set `is_research_gap: true` if the finding description contains the literal string
`Recommended resolution: /deep-research`. This format is emitted by any domain reviewer's
third-party technology gap checklist item when the gap cannot be resolved from codebase context alone.

Category abbreviations for IDs: `test`, `corr`, `sec`, `arch`, `dec`, `perf`, `style`.

**plan-review findings:**

Categories (in display order): Research Gaps, Feasibility, Scope, Dependencies, Architecture,
Security, Specification. Extract all findings from each category section. Also extract findings
from the `─── Needs Context ───` section with `verifier_verdict: "needs_context"`.
For Needs Context findings, map `Investigation: {investigation_summary}` to `evidence`.
Set `is_research_gap: true` per the structural rules above (category = RESEARCH GAPS, OR reviewer tag =
Unknown Unknowns) — not by scanning description text.
Skip findings in the Deferred section — these were explicitly deferred by the user during the upstream review and should not be re-processed.

Category abbreviations for IDs: `rsch`, `feas`, `scope`, `dep`, `arch`, `sec`, `spec`.

**bug-investigation findings:**

Read `{memory_dir}/BUGS.md` directly (do not rely on session output). Extract all `## BUG-NNN`
entries whose `**Status:**` field is `Root Cause Found` or `Fix Ready`. All bug entries are
`needs-fix` by definition — a bug at these statuses has a known root cause and is ready to fix.
The `suggested_fix` field maps to the entry's `### Resolution Plan` checklist.

---

## Note on needs-input

Upstream skills (pr-review, plan-review) resolve ALL `needs-input` findings via `AskUserQuestion`
before producing terminal output. By the time this skill runs, all findings visible in the terminal
output are actionable. Normalize all of them as `needs-fix`. Do not re-classify or re-triage based
on description text.

**Incremental workflow scoping:** When the Lead provides a `pr_boundary_files` list (during
incremental swarm execution), the Fixer processes ONLY findings whose `file` field matches
a file in the list. This is structural filtering by the Lead per the Fixer Protocol in
`[{skill_dir}/../../references/finding-classification.md](../../../references/finding-classification.md)` — the Fixer does not make scoping decisions.
When invoked standalone: existing behavior unchanged — process all findings.

