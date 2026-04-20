# Skill-Eval Fixtures

External Markdown files containing realistic test data (code diffs, plans,
findings, task descriptions) referenced by test cases via `{fixture:KEY}`
placeholders.

## Directory Structure

```
fixtures/
  {skill-name}/
    {case-id}-{description}.md
```

The `{case-id}` portion matches the test case `id` field in the corresponding
JSON config under `test_cases/`.

Example:
```
fixtures/
  quality-gate/
    1-clean-plan.md
    2-deferred-items.md
  fix/
    1-security-findings.md
  pr-review/
    1-diff-with-injection.md
```

## Fixture Categories by Skill Type

| Skill type       | Typical fixture content                         |
|------------------|-------------------------------------------------|
| Review skills    | Code diffs, implementation plans, PR summaries  |
| Fix skill        | Finding lists, bug reports, review output       |
| Output skills    | Task descriptions, scenario contexts            |
| Gate skills      | Completed work artifacts, phase summaries       |

## Fixture Metadata (Optional YAML Frontmatter)

Fixtures may include YAML frontmatter to document planted issues or expected
findings. The `load_fixture()` function **strips frontmatter before returning
content** -- callers receive only the body text.

```markdown
---
planted_issues:
  - type: sql_injection
    line: 42
  - type: path_traversal
    line: 87
expected_findings: 2
---

# Actual fixture content starts here

diff --git a/app.py b/app.py
...
```

## Delimiter Safety

Lines that exactly match `execute_skill` delimiter strings are stripped from
fixture content to prevent prompt injection:

- `=== SKILL INSTRUCTIONS ===`
- `=== END SKILL INSTRUCTIONS ===`
- `=== BEHAVIORAL CONTEXT (CLAUDE.md) ===`
- `=== END BEHAVIORAL CONTEXT ===`
