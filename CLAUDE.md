# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
make all          # Lint + test (CI runs this)
make lint         # uv run ruff check . && uv run ruff format --check .
make format       # Auto-fix: uv run ruff format . && uv run ruff check --fix .
make test         # uv run pytest (runs global-hooks/tests/)
make prek         # uvx prek run --all-files

# Single test file or test
uv run pytest global-hooks/tests/test_tool_selection_guard.py -v
uv run pytest global-hooks/tests/test_tool_selection_guard.py::TestGitBranchEnforcement::test_branch_enforcement -k "deny-switch-c-no-start"
```

Python 3.10+. Ruff line-length 100, select `E,W,F,I,UP,B,SIM`. Tests only exist in `global-hooks/tests/`.

## Critical Rule

**Never edit `~/.claude/plugins/`** — always edit this source repository. After changes:
1. Commit and push
2. `claude plugin marketplace update private-claude-marketplace`
3. `claude plugin update <plugin-name>@private-claude-marketplace`

## Repository Architecture

This is a **private Claude Code plugin marketplace** containing 11 plugins. The master registry is `.claude-plugin/marketplace.json` — it lists all plugins with their versions and source paths. Each plugin has its own `.claude-plugin/plugin.json` with independent versioning.

### Plugin Categories

**LSP Plugins (5)** — Language servers via `.lsp.json`:
`pyright-uvx`, `vtsls-npx`, `gopls-go`, `vscode-html-css-npx`, `rust-analyzer-rustup`
Minimal structure: `.claude-plugin/plugin.json` + `.lsp.json`. No code to maintain.

**Agent Plugins (3)** — `.md` files with YAML frontmatter (name, tools, model, color):
- `project-dev/` — 10 agents (orchestrator, feature-writer, bug-fixer, etc.) + 6 skills
- `test-execution/` — 1 agent (test-runner)
- `superclaude/` — 4 agents (architect, security, qa, performance) + 2 skills

**Productivity Plugins (3)**:
- `global-hooks/` — Hook scripts + `hooks/hooks.json` registration. **Only plugin with tests.**
- `global-skills/` — 8 skills in `skills/*/SKILL.md`
- `global-commands/` — 6 commands in `commands/*.md`

### Plugin Anatomy

```
plugin-name/
  .claude-plugin/plugin.json    # Always present: name, version, description
  .lsp.json                     # LSP only: server command, extensions, settings
  agents/*.md                   # Agent definitions (YAML frontmatter + system prompt)
  skills/*/SKILL.md             # Skill definitions (YAML frontmatter + instructions)
  commands/*.md                 # Slash command definitions
  hooks/hooks.json              # Hook registration (PreToolUse/PostToolUse matchers)
  hooks/*.py, hooks/*.sh        # Hook implementations
```

### global-hooks: tool-selection-guard.py

The most complex component. A PreToolUse hook (~860 lines) that runs on every Bash/Read/Write/Edit/Grep/Glob invocation. Three rule systems:

1. **RULES** — Tool selection (28 regex rules). 4-tuple: `(name, pattern, exception, message)`. Blocks suboptimal patterns like `cat file` (use Read), `grep` (use Grep), `ls` (use Glob), `python` (use `uv run`).

2. **GIT_DENY_RULES** — Hard blocks (exit 2). 3-tuple: `(name, check_fn, message)`. Prevents force push, push to upstream, reset --hard, branch -D, branch creation without start-point, etc.

3. **GIT_ASK_RULES** — User prompts (exit 1). Same 3-tuple format. Warns about stash drop, local main staleness, non-upstream branching, etc.

Additional special cases in `check_git_safety()`: commit-to-main detection (subprocess), branch-needs-fetch (chain-aware fetch tracking via `fetch_seen` parameter from `main()`).

Key helpers: `_parse_branch_creation(cmd)`, `_is_safe_start_point(ref)`, `_get_push_target(cmd)`, `_has_force_flag(cmd)`. Command parsing: `split_commands()` (&&/||/;), `extract_bash_c()`, `extract_subshells()`. Preprocessing: `strip_env_prefix()` (KEY=val), `strip_shell_keyword()` (do/then/else from for/if/while splits).

### Version Bumping

When modifying a plugin, bump its version in **both** files:
- `<plugin>/.claude-plugin/plugin.json` (the plugin's own version)
- `.claude-plugin/marketplace.json` (the marketplace entry's version)

## CI

GitHub Actions on PRs to main (Python/config file changes only). Runs `make all`. Uses `uv sync --group dev` for dependencies.
