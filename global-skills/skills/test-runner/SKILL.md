---
name: test-runner
description: Efficient test execution patterns for pytest and pre-commit - sequential execution, targeted re-runs, and smart failure handling
allowed-tools: [Bash, Read, Grep, Glob]
---

# Test Execution Best Practices

This skill provides comprehensive guidance for running tests efficiently. Use this when executing pytest, pre-commit, or other test workflows.

## Core Principles

### 1. Sequential Execution, Never Parallel

**CRITICAL**: Run test commands ONE AT A TIME, observe output completely before proceeding.

```bash
# ✅ CORRECT: Sequential execution
uv run pre-commit run --all-files 2>&1
# [wait for output, analyze results]
uv run pytest --tb=short -v 2>&1

# ❌ WRONG: Parallel execution
uv run pre-commit run --all-files & uv run pytest  # NEVER DO THIS
```

**Why**: Parallel execution means you can't observe failures immediately, leading to wasted time running subsequent commands that may be invalidated by earlier failures.

### 2. Targeted Re-runs, Never Full Suite

After fixing a failure, re-run ONLY the tests that failed, not the entire test suite.

```bash
# ✅ CORRECT: Targeted re-run
uv run pytest tests/test_auth.py::test_login_flow -vv

# ❌ WRONG: Full suite re-run
uv run pytest  # wastes minutes re-running tests that already passed
```

### 3. Smart Failure Analysis

Parse test output immediately to identify:
- Which specific tests failed
- What files/modules are affected
- Error types and root causes

Use this information to make targeted fixes and targeted re-runs.

## Pytest Execution Patterns

### Initial Test Run

```bash
# Run tests with concise tracebacks
uv run pytest --tb=short -v 2>&1

# Alternative: Stop at first failure (faster feedback)
uv run pytest --tb=short -x 2>&1
```

### Smart Re-run Options

After identifying failures, use these targeted re-run strategies:

```bash
# Re-run only tests that failed in the last run
uv run pytest --lf -vv

# Re-run failed tests, stop at first new failure
uv run pytest --lf --sw -vv

# Re-run specific test file
uv run pytest path/to/test_file.py -vv

# Re-run specific test function/method
uv run pytest path/to/test_file.py::TestClass::test_method -vv

# Re-run tests matching a pattern
uv run pytest -k "test_authentication" -vv

# Re-run tests in a specific module
uv run pytest tests/auth/ -vv
```

### Common Pytest Flags

| Flag | Purpose | When to Use |
|------|---------|-------------|
| `--lf` | Last failed - re-run only failures | After fixing failures |
| `--sw` | Stepwise - stop at first failure | Iterative debugging |
| `-x` | Exit on first failure | Fast feedback loops |
| `--tb=short` | Concise tracebacks | Initial runs, less noise |
| `--tb=long` | Full tracebacks | Debugging specific failures |
| `-v` | Verbose output | See test names |
| `-vv` | Very verbose | Detailed failure analysis |
| `-q` | Quiet mode | Final verification runs |
| `--durations=10` | Show slowest tests | Performance analysis |
| `-k "pattern"` | Filter by test name | Run related tests |
| `-m "marker"` | Run tests with marker | Skip slow/flaky tests |

### Test Failure Workflow

1. **Initial run** - Capture all failures:
   ```bash
   uv run pytest --tb=short -v 2>&1 | tee test_output.txt
   ```

2. **Parse failures** - Extract test names:
   ```
   FAILED tests/auth/test_login.py::test_login_flow - AssertionError
   FAILED tests/auth/test_logout.py::test_logout_redirect - KeyError
   ```

3. **Fix the issues** - Make targeted code changes

4. **Targeted re-run** - Only the failed tests:
   ```bash
   uv run pytest tests/auth/test_login.py::test_login_flow tests/auth/test_logout.py::test_logout_redirect -vv
   ```

5. **Full verification** - After all fixes pass:
   ```bash
   uv run pytest --tb=no -q
   ```

## Pre-Commit Execution Patterns

### Initial Pre-Commit Run

```bash
# Run all hooks on all files
uv run pre-commit run --all-files 2>&1

# Alternative: Run on staged files only
uv run pre-commit run 2>&1
```

### Targeted Pre-Commit Re-runs

After identifying failures, use these targeted strategies:

```bash
# Re-run specific hook
uv run pre-commit run <hook-name>
# Examples:
uv run pre-commit run ruff-format
uv run pre-commit run mypy
uv run pre-commit run trailing-whitespace

# Re-run on specific files
uv run pre-commit run --files path/to/file1.py path/to/file2.py

# Re-run specific hook on specific files
uv run pre-commit run --files path/to/file.py ruff-format

# Skip slow hooks during iteration
SKIP=mypy,pylint uv run pre-commit run --all-files
```

### Common Pre-Commit Hooks

Identify hook names from `.pre-commit-config.yaml` or output:

```bash
# List all configured hooks
uv run pre-commit run --all-files --verbose | grep "^- hook id:"

# Common hooks:
# - ruff-format (code formatting)
# - ruff (linting)
# - mypy (type checking)
# - trailing-whitespace (whitespace cleanup)
# - end-of-file-fixer (newline at EOF)
# - check-yaml (YAML syntax)
# - check-json (JSON syntax)
```

### Pre-Commit Failure Workflow

1. **Initial run** - Capture all hook failures:
   ```bash
   uv run pre-commit run --all-files 2>&1 | tee precommit_output.txt
   ```

2. **Parse failures** - Identify which hooks failed on which files:
   ```
   ruff-format....................Failed
   - hook id: ruff-format
   - files were modified by this hook

   Files were modified by this hook. Additional output:

   3 files reformatted, 45 files left unchanged
   ```

3. **Auto-fix if possible** - Many hooks auto-fix:
   ```bash
   # Re-run the hook to apply fixes
   uv run pre-commit run ruff-format --all-files
   ```

4. **Manual fixes** - For hooks that require manual intervention (e.g., mypy)

5. **Targeted re-check** - Only the previously failing hook:
   ```bash
   uv run pre-commit run mypy --all-files
   ```

## Combined Workflow: Pre-Commit + Pytest

The correct sequence when both are needed:

```bash
# Step 1: Run pre-commit first (fixes formatting/linting)
uv run pre-commit run --all-files 2>&1

# [Analyze output, fix any issues, re-run specific hooks if needed]

# Step 2: After pre-commit passes, run pytest
uv run pytest --tb=short -v 2>&1

# [Analyze output, fix any issues, re-run specific tests if needed]

# ❌ NEVER run in parallel:
# uv run pre-commit run --all-files & uv run pytest  # WRONG!
```

**Why this order**:
1. Pre-commit fixes formatting/linting issues
2. Those fixes might affect test outcomes
3. Running tests before pre-commit passes wastes time if formatting changes break tests

## Command Validation Checklist

Before running ANY test command, verify:

- [ ] **Not running in parallel** - No `&` operators, no multiple simultaneous Bash calls
- [ ] **Observing output** - Will wait for and read complete output before next step
- [ ] **Targeted re-run** - If fixing failures, only re-running what failed
- [ ] **Correct flags** - Using appropriate pytest/pre-commit flags for the situation

## Advanced Techniques

### Pytest: Running Related Tests

When you fix a function used by multiple tests:

```bash
# Run all tests in related modules
uv run pytest tests/auth/ tests/api/ -v

# Run tests matching a pattern
uv run pytest -k "authentication or login" -v
```

### Pytest: Skipping Slow Tests During Iteration

```bash
# Skip tests marked as slow
uv run pytest -m "not slow" -v

# Skip integration tests, run unit tests only
uv run pytest -m "not integration" -v
```

### Pre-Commit: Manual Hook Staging

```bash
# Run fast hooks first
uv run pre-commit run trailing-whitespace end-of-file-fixer check-yaml

# Then run slower hooks after fast ones pass
uv run pre-commit run ruff mypy
```

### Capturing Test Output for Analysis

```bash
# Save output for later analysis
uv run pytest --tb=short -v 2>&1 | tee test_results.txt

# Search for specific failures
grep "FAILED" test_results.txt

# Count failures
grep -c "FAILED" test_results.txt
```

## Integration with UV

All test commands use `uv run` for project environment execution:

```bash
# ✅ Correct: Using uv run
uv run pytest
uv run pre-commit run --all-files

# ❌ Wrong: Direct execution (bypasses project environment)
pytest  # NEVER
pre-commit run  # NEVER

# ✅ Alternative: Ephemeral execution for tools
uvx pytest  # if not in a project context
```

See `/uv-python skill` for comprehensive Python tooling guidance.

## Quick Reference

### Most Common Patterns

```bash
# Initial test run
uv run pytest --tb=short -v 2>&1

# Re-run only failures
uv run pytest --lf -vv

# Re-run specific test
uv run pytest path/to/test.py::test_function -vv

# Initial pre-commit run
uv run pre-commit run --all-files 2>&1

# Re-run specific hook
uv run pre-commit run hook-name

# Re-run on specific files
uv run pre-commit run --files file1.py file2.py
```

### Decision Tree

```
Need to run tests?
├── First run?
│   ├── Yes → uv run pytest --tb=short -v
│   └── No → Did tests fail before?
│       ├── Yes → uv run pytest --lf -vv (re-run failures)
│       └── No → uv run pytest --tb=no -q (quick verification)
│
Need to run pre-commit?
├── First run?
│   ├── Yes → uv run pre-commit run --all-files
│   └── No → Did hooks fail before?
│       ├── Yes (know which hook) → uv run pre-commit run <hook-name>
│       └── Yes (know which files) → uv run pre-commit run --files <files>
```

## When to Use Test-Runner Agent

For complex test workflows, consider launching the test-runner agent via Task tool:

```python
# Launch agent for test execution and failure reporting
Task(
  subagent_type="test-execution:test-runner",
  prompt="Run pre-commit and pytest, report any failures with specific test paths"
)
```

The agent will:
- Execute commands sequentially
- Parse failures intelligently
- Report failures with specific test paths and error details
- Track progress with TodoWrite
- Return verification commands for fixing agents

**Note:** The test-runner agent does NOT fix code. For fixes, spawn `project-dev:bug-fixer` with the failure report, then have bug-fixer spawn test-runner for verification.

