# Dev Guard Plugin

Development environment policy enforcement: tool selection guard, commit validation, and pre-push review.

## Hooks

### PreToolUse: Tool Selection Guard

**tool-selection-guard.py** — Enforces tool and command best practices:
- **Native tool redirections** — Redirects `grep`/`find`/`cat`/`sed` to Grep/Glob/Read/Edit tools
- **Python tooling** — Enforces `uv run`/`uvx` over bare `python`/`pip`
- **Git safety** — Blocks force pushes, branch deletions, commits to main, and other destructive operations
- **URL fetch guard** — Blocks WebFetch/WebSearch for authenticated services (configurable via `URL_GUARD_EXTRA_RULES`)
- **Interactive command blocking** — Prevents `git rebase -i`, `git add -p`, and other interactive commands
- **Plan mode redirect** — Redirects `EnterPlanMode` to incremental-planning skill

### PreToolUse: Pre-push Review

**pre-push-review.sh** — `Bash(git push origin*)`
- Triggers when pushing 3+ commits
- Shows commit summary and suggestions
- Warns about WIP commits or duplicate scopes
- **Non-blocking** — push proceeds after review

### PostToolUse: Commit Message Validation

**validate-commit-message.sh** — `Bash(git commit:*)`
- Validates Conventional Commits format
- Enforces present indicative tense ("adds" not "add")
- Checks subject line length (<72 chars, warn >50)
- Blocks emoji and meta-commentary
- **Exit 2** shows errors but commit already completed (PostToolUse limitation)

## How Hooks Work

Hooks execute automatically when enabled:

1. **Install plugin** → hooks activate automatically
2. **No configuration needed** → works out of the box
3. **Merge with project hooks** → runs alongside local hooks
4. **Parallel execution** → doesn't block other hooks

## Validation Examples

### Good Commit Messages

```
feat(auth): adds password reset flow
fix(api): prevents null pointer in handler
docs: updates API documentation
```

### Bad Commit Messages (Blocked)

```
❌ "add feature"           → Use "adds feature"
❌ "Added cool stuff"      → No type/scope
❌ "feat: Add feature 🎉"  → No emoji
❌ "Very long subject..."  → >72 chars blocked
```

## Installation

```bash
claude plugin install dev-guard@personal-claude-marketplace
```

## Requirements

- Bash shell
- Git repository

## Customization

Hooks use plugin-relative paths (`${CLAUDE_PLUGIN_ROOT}`) and work in any project without modification.

### Custom URL Guard Rules

Set `URL_GUARD_EXTRA_RULES` to a JSON file path to add organization-specific URL blocking rules:

```bash
export URL_GUARD_EXTRA_RULES="/path/to/rules.json"
```

See `examples/url-guard-extra-rules.example.json` for the format.

## Author

wgordon17 - January 2026
