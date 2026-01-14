# Global Hooks Plugin

Git safety checks, commit message validation, and pre-push review automation.

## Hooks (2 + 1 utility)

### PreToolUse Hooks

**Pre-push Review** - `Bash(git push origin*)`
- Triggers when pushing 3+ commits
- Shows commit summary and suggestions
- Warns about WIP commits or duplicate scopes
- Suggests squashing related commits
- **Non-blocking** - push proceeds after review

### PostToolUse Hooks

**Commit Message Validation** - `Bash(git commit:*)`
- Validates Conventional Commits format
- Enforces present indicative tense ("adds" not "add")
- Checks subject line length (<72 chars, warn >50)
- Blocks emoji and meta-commentary
- Warns about body quality issues
- **Exit 2** shows errors but commit already completed (PostToolUse limitation)

### Utility Scripts

**git-safety-check.sh** - Shared safety validation logic

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
claude plugin install global-hooks@private-claude-marketplace
```

## Requirements

- Bash shell
- Git repository

## Customization

Hooks use plugin-relative paths (`${CLAUDE_PLUGIN_ROOT}`) and work in any project without modification.

## Author

wgordon - January 2026
