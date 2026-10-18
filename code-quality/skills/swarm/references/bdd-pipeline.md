# Phase 3.5: BDD Step Writing Pipeline

(Not to be confused with `/speculative`'s internal Phase 3.5 synthesis within Phase 2.7.)

**Skip when:** The plan file has no `## Test Plan` section, OR the `## Test Plan` section has no
`**Feature Files:**` path, OR the `**Feature Files:**` path fails boundary validation (path
escapes `{memory_dir}/test-plans/`). If `{TEST_PLAN}` is empty string (failed path validation),
skip entirely.

When triggered, run the following steps after Phase 3 completes and before Phase 4 begins:

**Step 3.5.1 — Determine .feature target directory:** Check for an existing `features/` directory
in the repo root. If not found, check for `tests/acceptance/`. If neither exists, create
`tests/acceptance/`. Record the resolved target as `{feature_target_dir}`.

**Step 3.5.2 — Promote .feature files:** Copy each `.feature` file from the staging path
(`**Feature Files:**` directory in the test plan annotation) into `{feature_target_dir}`.
Before copying each file, verify it is a regular file and not a symlink (SEC-004). If a
`.feature` file with the same name already exists in `{feature_target_dir}`, use
`AskUserQuestion` to resolve the collision: offer "Overwrite", "Keep existing", or "Rename
incoming" for each conflict. Do not overwrite silently.

**Step 3.5.3 — BDD dependency installation (if needed):** If `**BDD Setup Needed:** yes`,
run the recorded install command (from `**BDD Setup Needed:**` annotation value, e.g.,
`uv add --dev pytest-bdd==7.x.x`) on the current feature branch. Use the version specifier
as recorded — do not strip versions.

**Step 3.5.4 — Build BDD context and spawn BDD-Step-Writer:** Construct `{bdd_framework_info}`:

```json
{
  "framework": "<value of **BDD Framework:** from test plan annotation>",
  "feature_dir": "<{feature_target_dir}>",
  "step_dir": "<value of **BDD Step Dir:** from test plan annotation>",
  "scaffold_cmd": "<value of **BDD Scaffold Command:** from test plan annotation>",
  "test_cmd": "<value of **BDD Test Command:** from test plan annotation>",
  "feature_files": ["<list of promoted .feature files>"]
}
```

Spawn the BDD-Step-Writer (general-purpose, sonnet, bypassPermissions) with the full context
bundle, `{TEST_PLAN}` content, `{bdd_framework_info}`, and the promoted `.feature` files.
The BDD-Step-Writer generates step definition skeletons for all unimplemented steps in the
`.feature` files. Inject `{TEST_PLAN}` into the BDD-Step-Writer prompt the same way it is
injected into the Implementer — the BDD-Step-Writer must understand user personas and scenario
intent to name steps correctly.

**Step 3.5.5 — Collect BDDStepHandoff:** Receive the BDD-Step-Writer's completion message
(`BDDStepHandoff` type) listing files created, step count, and any steps it could not scaffold.
Store `{bdd_framework_info}` for Phase 7 (Verifier scope). Shut down the BDD-Step-Writer.

**Phase 3.5 does NOT run BDD tests.** BDD test execution is deferred to Phase 7 — the Verifier
runs both the unit test suite and the BDD acceptance suite when `.feature` files are present.
