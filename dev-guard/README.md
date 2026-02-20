# Dev Guard Plugin

Development environment policy enforcement: tool selection guard, commit validation, and pre-push review.

## Hooks

### PreToolUse: Tool Selection Guard

**tool-selection-guard.py** — Enforces tool and command best practices:
- **Native tool redirections** — Redirects `grep`/`find`/`cat`/`sed` to Grep/Glob/Read/Edit tools
- **Python tooling** — Enforces `uv run`/`uvx` over bare `python`/`pip`
- **Git safety** — Blocks force pushes, branch deletions, commits to main, and other destructive operations
- **URL fetch guard** — Blocks WebFetch/curl/wget for authenticated services (extensible via `URL_GUARD_EXTRA_RULES`)
- **Command guard** — User-defined command blocking rules (via `COMMAND_GUARD_EXTRA_RULES`)
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

Both URL and command guard rules can be extended with user-defined rules via environment variables pointing to JSON files. Rules are loaded at startup, merged with built-ins, and checked by the same pipeline (chains, pipes, subshells, env prefix stripping). Bad config files are silently ignored.

### Custom URL Guard Rules

Block additional authenticated URLs beyond the built-in rules (GitHub, GitLab, Google, Atlassian, Slack).

Set `URL_GUARD_EXTRA_RULES` to a JSON file path:

```bash
export URL_GUARD_EXTRA_RULES="$HOME/.config/claude/url-guard-rules.json"
```

JSON format — array of rule objects with `name`, `pattern` (regex matched against the full URL), and `message`:

```json
[
    {
        "name": "internal-gitlab",
        "pattern": "gitlab\\.internal\\.example\\.com",
        "message": "This URL is on internal GitLab. Use glab CLI instead."
    },
    {
        "name": "internal-jira",
        "pattern": "jira\\.mycompany\\.com",
        "message": "Internal Jira requires auth. Use the jira CLI or MCP tools."
    }
]
```

### Custom Command Guard Rules

Block specific command patterns while keeping the base command fully allowlisted in Claude's permissions. This is useful when you want Claude to use tools like `oc`, `gh`, or `kubectl` freely but need to prevent destructive subcommands.

Set `COMMAND_GUARD_EXTRA_RULES` to a JSON file path:

```bash
export COMMAND_GUARD_EXTRA_RULES="$HOME/.config/claude/command-guard-rules.json"
```

JSON format — array of rule objects with `name`, `pattern` (regex matched against the command), `message`, and optional `exception` (regex — if the command also matches this, it is allowed):

```json
[
    {
        "name": "oc-delete",
        "pattern": "^\\s*oc\\s+delete\\b",
        "message": "oc delete is blocked. Use the OpenShift console for deletions.",
        "exception": "--dry-run"
    },
    {
        "name": "gh-repo-delete",
        "pattern": "^\\s*gh\\s+repo\\s+delete\\b",
        "message": "gh repo delete is blocked. Delete repositories via the GitHub UI."
    },
    {
        "name": "kubectl-delete-namespace",
        "pattern": "^\\s*kubectl\\s+delete\\s+namespace\\b",
        "message": "Namespace deletion is blocked. Coordinate with the platform team."
    }
]
```

Custom command rules inherit all built-in processing:
- Checked across chained commands (`&&`, `||`, `;`)
- Checked in pipe segments and subshells (`$()`, backticks)
- Environment variable prefixes are stripped before matching (`KUBECONFIG=x oc delete` matches `oc delete`)
- `GUARD_BYPASS=1` prefix overrides all rules (built-in and custom)

### Bypass Mechanisms

| Prefix | Scope | Use case |
|--------|-------|----------|
| `GUARD_BYPASS=1` | All Bash rules | Override any tool selection or command guard rule |
| `ALLOW_FETCH=1` | URL rules (curl/wget only) | Fetch an authenticated URL after confirming alternatives |

## Author

wgordon17 - January 2026
