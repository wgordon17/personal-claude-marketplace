# Test Execution Plugin

Intelligent test execution patterns for pytest and pre-commit with sequential runs and targeted re-runs.

## ⚠️ Development Warning

**DO NOT edit files in `~/.claude/plugins/`!** Always edit the source repository. See marketplace README for details.

## Agent

**test-runner** - Efficient test execution specialist

### Capabilities

- Runs test commands **sequentially** (never in parallel)
- Observes output immediately after each command
- Parses failures to identify specific tests/hooks
- Targets re-runs to only failed tests
- Tracks progress using TodoWrite

### Usage

```python
Task(
  subagent_type="test-execution:test-runner",
  description="Run tests and fix failures",
  prompt="Run the test suite and fix any failing tests"
)
```

### Benefits

- **Faster iteration** - Only re-run what failed
- **No wasted time** - Stops immediately on first failure
- **Clear tracking** - TodoWrite shows what's fixed and what remains
- **Targeted fixes** - Knows exactly which tests to re-run

### Example Workflow

1. Run full test suite sequentially
2. Parse failures to identify specific test names
3. Fix first failure
4. Re-run ONLY that specific test with `pytest path/to/test.py::test_name`
5. Repeat for remaining failures

### Tools

- `Bash` - Sequential test execution
- `Read` - Read test files and outputs
- `Grep` - Parse failure messages
- `TodoWrite` - Track failures and fixes

### Model

Haiku - Fast and efficient for test execution workflows

## Installation

```bash
claude plugin install test-execution@private-claude-marketplace
```

## Compatible Test Frameworks

- **pytest** - Python testing
- **pre-commit** - Git hook framework

## Author

wgordon17 - January 2026
