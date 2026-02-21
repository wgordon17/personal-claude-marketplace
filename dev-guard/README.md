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

JSON format — array of rule objects with `name`, `pattern` (regex matched against the full URL), `message`, and optional `action` (`"block"` or `"ask"`, defaults to `"block"`):

```json
[
    {
        "name": "internal-gitlab",
        "pattern": "gitlab\\.internal\\.example\\.com",
        "message": "This URL is on internal GitLab. Use glab CLI instead."
    },
    {
        "name": "staging-api",
        "pattern": "staging\\.api\\.example\\.com",
        "message": "Staging API — confirm this is intentional.",
        "action": "ask"
    }
]
```

### Custom Command Guard Rules

Block specific command patterns while keeping the base command fully allowlisted in Claude's permissions. This is useful when you want Claude to use tools like `oc`, `gh`, or `kubectl` freely but need to prevent destructive subcommands.

Set `COMMAND_GUARD_EXTRA_RULES` to a JSON file path:

```bash
export COMMAND_GUARD_EXTRA_RULES="$HOME/.config/claude/command-guard-rules.json"
```

JSON format — array of rule objects with `name`, `pattern` (regex matched against the command), `message`, optional `exception` (regex — if the command also matches this, it is allowed), and optional `action` (`"block"` or `"ask"`, defaults to `"block"`):

```json
[
    {
        "name": "oc-delete",
        "pattern": "^\\s*oc\\s+delete\\b",
        "message": "oc delete is blocked. Use the OpenShift console for deletions.",
        "exception": "--dry-run"
    },
    {
        "name": "oc-scale-zero",
        "pattern": "^\\s*oc\\s+scale\\b.*--replicas=0",
        "message": "Scaling to zero — confirm this is intentional.",
        "action": "ask"
    },
    {
        "name": "gh-repo-delete",
        "pattern": "^\\s*gh\\s+repo\\s+delete\\b",
        "message": "gh repo delete is blocked. Delete repositories via the GitHub UI."
    }
]
```

Custom command rules inherit all built-in processing:
- Checked across chained commands (`&&`, `||`, `;`)
- Checked in pipe segments and subshells (`$()`, backticks)
- Environment variable prefixes are stripped before matching (`KUBECONFIG=x oc delete` matches `oc delete`)
- `GUARD_BYPASS=1` prefix overrides all rules (built-in and custom)

### Action Field

Both URL and command rules support an optional `action` field:

| Value | Mechanism | Behavior | Default |
|-------|-----------|----------|---------|
| `"block"` | Exit 2 + stderr | Hard deny — Claude cannot proceed | Yes |
| `"ask"` | JSON `hookSpecificOutput` + exit 0 | Prompts the user for confirmation | No |
| `"allow"` | JSON `hookSpecificOutput` + exit 0 | Explicitly allows the command via the hook, bypassing Claude's permission system. Exit code 0 with `permissionDecision: "allow"` JSON. | No |

Rules are matched in order of specificity: `"allow"` rules first, then `"ask"`, then `"block"`. The first matching rule wins.

The `"ask"` action outputs `permissionDecision: "ask"` via Claude Code's [PreToolUse hook JSON protocol](https://code.claude.com/docs/en/hooks#pretooluse-decision-control). Claude Code displays a permission prompt with the rule's `message` as the reason. This overrides `permissions.allow` auto-approve rules — even commands matching `Bash(git:*)` or `Bash(oc:*)` will prompt when a hook returns `"ask"`.

The `"allow"` action explicitly permits the command via `permissionDecision: "allow"` in the JSON response, bypassing Claude's default permission system. This is useful for commands that would normally be blocked but are safe in specific contexts.

> **⚠️ Important: `"ask"` and `"allow"` require the JSON `hookSpecificOutput` protocol**
>
> Both actions use `permissionDecision: "ask"` or `permissionDecision: "allow"` in a JSON object on stdout with exit code 0.
> They do **not** use `sys.exit(1)` — exit code 1 is a non-blocking error in Claude Code and the
> command will proceed silently. If you're writing custom hooks, use the JSON protocol:
>
> ```json
> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "reason"}}
> ```
>
> **Known issue (VS Code):** The VS Code extension ignores `permissionDecision: "ask"` and `permissionDecision: "allow"` and falls back
> to permission rules ([#13339](https://github.com/anthropics/claude-code/issues/13339)). The CLI
> works correctly. Use `"block"` as a workaround if VS Code support is needed.

### Config Validation

Run `--validate` to check your config files for issues:

```bash
uv run dev-guard/hooks/tool-selection-guard.py --validate
```

This checks both `URL_GUARD_EXTRA_RULES` and `COMMAND_GUARD_EXTRA_RULES` files for:
- Missing files, invalid JSON, wrong types
- Missing required fields (`name`, `pattern`, `message`)
- Invalid regex patterns and exceptions
- Empty patterns (matches everything) or empty exceptions (disables rule)
- Invalid `action` values

Validation runs automatically on **session start** via the SessionStart hook. If your config has issues, you'll see them immediately when a new session begins. Built-in rules are always enforced regardless of config file state.

## Unified Audit Log

All hook decisions (blocked, asked, allowed, trusted) are logged to a SQLite database at `~/.claude/logs/dev-guard.db` with Write-Ahead Logging (WAL) for concurrent multi-session access.

**Database location:** `~/.claude/logs/dev-guard.db` (override with `GUARD_DB_PATH` environment variable)

**Logging verbosity** is controlled via the `GUARD_LOG_LEVEL` environment variable:
- `off` — No logging
- `actions` — Log only blocked, asked, trusted, and allowed decisions (default)
- `all` — Log all hook invocations including skipped rules and non-matching patterns

**Schema overview:** The audit log stores category, action, rule name, matched command, and timestamp for each entry.

**Security note:** The audit log database file is created with `0o600` permissions (owner read/write only) because it may contain sensitive command-line data, including credentials, file paths, or API tokens passed on the command line.

## Trust Management

Users can trust ask rules to auto-approve them without manual confirmation:

```bash
/dev-guard trust <rule-name>                    # Add to session trust list
/dev-guard trust <rule-name> --always           # Add to persistent trust (all sessions)
/dev-guard trust <rule-name> --match <pattern>  # Trust only commands matching substring
/dev-guard untrust <rule-name>                  # Remove from trust
/dev-guard trust list                           # Show trusted rules for this session
```

**Important:**
- Block rules are never trustable (only ask rules can be trusted)
- Trust entries are stored in SQLite alongside the audit log
- Session-scoped trust requires a prior hook invocation (reads `session_id` from the database)
- Users receive a trust hint when prompted: "To trust this rule for future sessions, run: `/dev-guard trust <rule> --always`"

**Workflow example:**
1. Run a command that triggers an ask rule
2. Receive permission prompt with trust hint
3. Approve the command
4. Run `/dev-guard trust git-stash-drop --always` to auto-approve in the future
5. Next session: same command auto-approves without prompting

## oc/kubectl Introspection

Mutating `oc` and `kubectl` commands receive automatic risk assessment based on the resource types being modified, with enhanced prompts showing detected security risks.

**Risk tiers and resource types:**

| Risk Level | Resource Types |
|------------|----------------|
| **Critical** | secret, clusterrole, clusterrolebinding, webhookconfigurations |
| **High** | namespace, project, node, persistentvolume, CRD, serviceaccount, networkpolicy |
| **Medium** | deployment, statefulset, daemonset, service, ingress, route, configmap, role, rolebinding, cronjob, job |
| **Low** | pod, event, resourcequota, limitrange, HPA, PDB |

**YAML manifest inspection:**
- Parses YAML files passed via stdin or files to detect resource kinds and security fields
- Uses regex-based parsing (no external dependencies)
- Detects security risk fields: `privileged`, `hostNetwork`, `hostPID`, `hostPath`, `runAsRoot`
- Handles pipe patterns: `cat file.yaml | oc apply -f -`

**Automatic allowances:**
- Commands with `--dry-run` are always allowed without prompting (safe preview mode)
- User-defined command rules take priority over built-in introspection

## Trustable Rule Names

Users can trust these built-in rules with `/dev-guard trust <rule-name>`:

**Git safety rules (ask actions):**
- `config-global-write` — Global git config modifications
- `stash-drop` — Destructive stash operations
- `branch-delete` — Branch deletion
- `branch-rename` — Branch renaming
- `tag-operations` — Tag creation/deletion/modification
- `rebase-interactive` — Interactive rebase (`git rebase -i`)
- `remote-modify` — Remote add/remove/set-url
- `branch-needs-fetch` — Requires fetch before rebase (dynamic, matches current branch)

**oc/kubectl introspection rules (ask actions):**
- `oc-critical` — Critical resource modifications
- `oc-high` — High-risk resource modifications
- `oc-medium` — Medium-risk resource modifications

### Bypass Mechanisms

| Prefix | Scope | Use case |
|--------|-------|----------|
| `GUARD_BYPASS=1` | All Bash rules | Override any tool selection or command guard rule |
| `ALLOW_FETCH=1` | URL rules (curl/wget only) | Fetch an authenticated URL after confirming alternatives |

## Author

wgordon17 - January 2026
