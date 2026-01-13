# Project Plugin

Project-specific agents for Project development with zero-knowledge encryption patterns.

## Agents

| Agent | Purpose | Model |
|-------|---------|-------|
| `project-dev:orchestrator` | Meta-orchestrator for complex workflows | Sonnet |
| `project-dev:architecture` | Architecture proposals and design | Sonnet |
| `project-dev:feature-writer` | End-to-end feature implementation | Sonnet |
| `project-dev:bug-fixer` | Diagnose and fix bugs with regression tests | Sonnet |
| `project-dev:test-writer` | Generate tests following TESTING.md | Haiku |
| `project-dev:code-quality` | Pre-PR quality gates | Haiku |
| `project-dev:pr-reviewer` | PR review with security checks | Sonnet |
| `project-dev:refactor` | Code improvement and consolidation | Sonnet |
| `project-dev:frontend-design` | Tailwind v4 / Alpine.js design | Haiku |
| `project-dev:migration-reviewer` | Django migration safety review | Haiku |

## Usage

### Via Orchestrator (Recommended)

For complex tasks, use the orchestrator which automatically coordinates subagents:

```python
Task(
  subagent_type="project-dev:orchestrator",
  description="Add budget tracking feature",
  prompt="Implement budget tracking with monthly limits and category breakdown"
)
```

### Direct Agent Invocation

For specific tasks, invoke agents directly:

```python
Task(
  subagent_type="project-dev:bug-fixer",
  description="Fix login timeout",
  prompt="The session expires too quickly, investigate and fix"
)
```

## Project Constraints

**No backwards compatibility requirements** — Always implement full solutions without migration shims or deprecated code paths.

## Bundled Skills

| Skill | Purpose | Trigger |
|-------|---------|---------|
| `/docs-sync` | Documentation synchronization | PROACTIVE after code changes |
| `/security-review` | Comprehensive security audit | Manual |
| `/test-guide` | Test patterns and fixture reference | PROACTIVE |
| `/url-check` | URL architecture validation | PROACTIVE |
| `/migrations` | Database migration safety | PROACTIVE |
| `/performance` | Query optimization guidance | Manual |

## Integration with Global Skills

Agents leverage global skills:
- `/lsp-navigation` — Semantic code navigation
- `/test-runner` — Efficient test execution
- `/git-history` — Git history manipulation
- `/uv-python` — Python tooling

## Documentation

See project `CLAUDE.md` for complete usage guide.
