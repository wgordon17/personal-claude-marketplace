# BDD & Test Runner Detection, Mode Selection, and Staging Reference

Detailed procedures for Phase 0 Step 6 (test runner & BDD detection), Phase 3 (BDD output mode selection), and Phase 5 (`.feature` file staging and BDD toolchain fallback) in `/test-plan`.

---

## Phase 0 Step 6 — Test Runner & BDD Infrastructure Detection

Search project dependency files for both the project's test runner and any existing BDD tooling. Both are needed: the test runner informs Phase 3's BDD framework recommendations; the BDD detection determines whether Phase 3 auto-selects or asks the user.

### Test Runner Detection

| File | Pattern | Test Runner |
|---|---|---|
| `package.json` | `vitest` | Vitest |
| `package.json` | `jest` or `@jest/` | Jest |
| `package.json` | `mocha` | Mocha |
| `pyproject.toml` / `requirements*.txt` | `pytest` | pytest |
| `go.mod` | file exists (`test -f`) | go test |
| `Cargo.toml` | file exists (`test -f`) | cargo test |
| `build.gradle` / `pom.xml` | `junit` or `testng` | JUnit / TestNG |
| `Gemfile` | `rspec` | RSpec |
| `Gemfile` | `minitest` | Minitest |

For rows with a pattern: use `Bash` (`rg -l`) or `Read` on the manifest file.
For rows marked "file exists (`test -f`)": use `Bash` (`test -f go.mod`, `test -f Cargo.toml`) or `Read` to check whether the file exists in the project root. Record `test_runner` (or `unknown`). If multiple match, resolve by this priority ladder:
1. **scripts.test contains a runner name** (e.g., `"test": "vitest run"`) → that runner wins, even if the scripts value uses a prefix or flags (substring match is sufficient).
2. **No scripts match or scripts key absent** → fall back to the order of appearance in the detection table above (earlier rows win — `package.json` patterns before file-existence rows).
3. **Still ambiguous** → record `unknown`; Phase 0 Step 7 will ask the user.

### BDD Infrastructure Detection

| File | Pattern | Framework |
|---|---|---|
| `pyproject.toml` | `pytest-bdd` | Python/pytest-bdd |
| `requirements*.txt` | `pytest-bdd` | Python/pytest-bdd |
| `go.mod` | `godog` or `github.com/cucumber` | Go/godog |
| `package.json` | `@cucumber/cucumber` or `cucumber` | Node.js/Cucumber.js |
| `Cargo.toml` | `cucumber` | Rust/cucumber-rs |
| `build.gradle` or `pom.xml` | `cucumber-java` | Java/Cucumber-JVM |
| `Gemfile` | `cucumber` | Ruby/Cucumber |

Search using `Bash` (`rg -l`) or `Read` on the dependency manifests. Record detected BDD framework (or `none`) and the detected file path for Phase 5 (BDD Staging).

---

## Phase 3 — Output Mode Selection (BDD Decision Logic)

Determine whether to generate BDD `.feature` files in addition to the UAT document.

**If BDD infra detected in Phase 0:**
Automatically select UAT + BDD mode. No question needed — the project already uses BDD,
so `.feature` files are expected. Print:

```
BDD infra detected ({framework} in {file}). Generating UAT document + .feature files.
```

Record `mode` based on the detected framework's relationship to the test runner: if the
detected BDD framework is a plugin for `test_runner` (e.g., `pytest-bdd` for pytest,
`jest-cucumber` for Jest), set `mode` to `UAT + BDD (native integration)`. If it is a
standalone framework (e.g., `godog`, `Cucumber.js`, `cucumber-rs`), set `mode` to
`UAT + BDD (standalone)`.
Set `BDD Setup Needed: no` — the framework is already installed. Scan the project for
existing `.feature` files and step definitions to determine `bdd_feature_dir` and `bdd_step_dir`.
If none found, use the framework's conventional defaults: Python/pytest-bdd → `tests/features/`,
`tests/step_defs/`; Go/godog → `features/`, `features/`; Node.js/Cucumber.js → `features/`,
`features/step_definitions/`; Rust/cucumber-rs → `tests/features/`, `tests/`; Java/Cucumber-JVM
→ `src/test/resources/`, `src/test/java/`; Ruby/Cucumber → `features/`, `features/step_definitions/`.

**If NO BDD infra detected:**

Before presenting options, research BDD integration options that are compatible with the
project's actual test runner. This prevents recommending frameworks that conflict with the
existing test setup (e.g., suggesting Cucumber.js for a Vitest project).

(`test_runner` is always known at this point — unknown was resolved in Phase 0 Step 7.)

```
Skill("deep-research", "BDD and Gherkin integration options for {test_runner}
in {tech_stack} projects. Compare: (1) native BDD plugins that integrate directly with
{test_runner} (e.g., vitest-cucumber-plugin for Vitest, jest-cucumber for Jest, pytest-bdd
for pytest), (2) standalone BDD frameworks (Cucumber.js, godog, cucumber-rs) and their
compatibility with {test_runner}, (3) using Gherkin .feature files as specification-only
documentation (not executable). For each viable option: package name, registry page,
last release date, open issues count, compatibility notes with {test_runner}, setup
complexity. Exclude options unmaintained (no release in 12+ months) or incompatible
with {test_runner}. Mode: External")
```

**Fallback:** If `/deep-research` is unavailable, errors, or returns no viable options,
present options A and B only (no C/D), with this note:
"BDD framework research was inconclusive — viable framework and install commands could not be
determined for {test_runner}. Options C/D require confirmed package compatibility and have been
omitted. Choose A (no BDD) or B (specification-only .feature files). If you know which BDD
framework you want, set it up manually after the plan is complete."
Do NOT fall back to the Phase 5 BDD Toolchain Reference table — that table applies only to
projects that already have BDD installed; it does not provide install commands suitable for
fresh BDD setup on arbitrary test runners.

**If research succeeds:** Use the research findings to build a project-aware options list.
Present via `AskUserQuestion`:

"No BDD framework detected. Your project uses **{test_runner}** for testing.

Based on research, here are the BDD options compatible with {test_runner}:

A) **Manual UAT checklist only** (no BDD)
   — Human-readable walkthrough with pass/fail checkboxes. No BDD setup required.

B) **Gherkin specification files only** (documentation, not executable)
   — Generates .feature files as living specifications. Tests remain in {test_runner}.
   — No additional framework needed.

{If research found a native {test_runner} BDD plugin:}
C) **{test_runner}-native BDD** ({plugin_name})
   — {1-sentence from research: what it does, maintenance status}
   — Tests run through {test_runner}. No second test runner.

{If research found a compatible standalone BDD framework:}
D) **Standalone BDD framework** ({standalone_name})
   — Adds a separate test runner alongside {test_runner}.
   — {1-sentence from research: tradeoffs}

{Omit C if no viable native plugin found. Omit D if no compatible standalone found.
If neither C nor D is viable, note this and explain why.}

Recommendation: {research-informed — prefer native integration (C) over standalone (D)
over specification-only (B). Recommend A if the plan is simple and BDD adds no value.}

Which option?"

Record the selected mode as `mode`:
- A → `Manual UAT`
- B → `UAT + BDD (feature files only)` — set `bdd_framework: none (specification only)`,
  `BDD Setup Needed: no`. Omit install/scaffold/test commands from annotation.
- C → `UAT + BDD (native integration)` — use the researched plugin's install/scaffold/test
  commands. Record `bdd_framework` as the plugin name.
- D → `UAT + BDD (standalone)` — use the standalone framework's commands.

Store the research-derived `bdd_install_cmd`, `bdd_scaffold_cmd`, `bdd_test_cmd`,
`bdd_framework`, `bdd_feature_dir`, and `bdd_step_dir` from the selected option.
For option B, these are all empty/none — Phase 6 handles this format.

---

## Phase 5 — BDD Staging & Toolchain Reference

**Skip this phase entirely if mode is `Manual UAT`.**

Run when mode is any BDD variant: `UAT + BDD (feature files only)`,
`UAT + BDD (native integration)`, or `UAT + BDD (standalone)`.

### Feature File Generation

For each user-facing plan task, generate one `.feature` file containing all scenarios
for that task. Feature files use standard Gherkin format:

```gherkin
Feature: {task title}

  Background:
    {shared preconditions across scenarios, if any}

  Scenario: {S1 title}
    Given {initial state}
    When {user action}
    Then {expected outcome}

  Scenario: {S2 title}
    Given {initial state}
    When {user action}
    Then {expected outcome}
```

**Feature file naming:** `{task-title-kebab-case}.feature`
Example: `user-login-flow.feature`, `password-reset.feature`

**Output directory:** `{memory_dir}/test-plans/{run-id}-features/`
(or `~/.claude/test-plans/{run-id}-features/` for the fallback)

Write each `.feature` file using the Write tool.

### BDD Toolchain Reference

**If Phase 3 ran /deep-research** (no existing BDD infra): use the research-derived values
stored in Phase 3. The research already identified the correct framework, install commands,
and compatibility with the project's test runner. Skip this section.

**If Phase 3 auto-selected BDD** (existing BDD infra detected in Phase 0): derive values
from the detected framework using this fallback table:

| Language | Framework | Install Command | Scaffold Command | Test Command |
|---|---|---|---|---|
| Python | pytest-bdd | `uv add --dev pytest-bdd>=7.0` | `pytest-bdd generate {feature}` or `pytest --generate-missing` | `uv run pytest` |
| Go | godog + gherkingen | `go get github.com/cucumber/godog@v0.15.0` | `gherkingen {feature.file}` | `go test ./features/...` |
| Node.js | Cucumber.js | `npm install --save-dev @cucumber/cucumber@^11.0` | `npx cucumber-js` (auto-generates snippets) | `npx cucumber-js` |
| Rust | cucumber-rs | `cargo add --dev cucumber` | Run tests with `.fail_on_skipped()` — reports unmatched steps | `cargo test` |
| Java | Cucumber-JVM | `<dependency>io.cucumber:cucumber-java</dependency>` | Run features — auto-generates snippets | `mvn test` |
| Ruby | Cucumber | `gem install cucumber` | `cucumber --dry-run` — generates undefined step snippets | `bundle exec cucumber` |

This table is a fallback for projects that already have BDD installed. For new BDD setups,
Phase 3's /deep-research provides test-runner-aware recommendations that supersede this table.

Record:
- `bdd_install_cmd` — the install command (from research or fallback table)
- `bdd_scaffold_cmd` — the scaffold command to generate step definition skeletons
- `bdd_test_cmd` — the command to run the BDD test suite
- `bdd_framework` — the framework name
- `bdd_feature_dir` — where `.feature` files will live (from Phase 3 project scan or research; not in the fallback table)
- `bdd_step_dir` — where step definitions will live (from Phase 3 project scan or research; not in the fallback table)

These values are written into the plan file annotation in Phase 6 so `/swarm` Phase 0 knows
what to install and run without re-detecting.

**Do NOT run the install command.** Recording it in the annotation is the contract.
`/swarm` handles installation on the feature branch.
