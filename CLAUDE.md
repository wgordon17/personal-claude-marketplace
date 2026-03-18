# CLAUDE.md

## Development Commands

```bash
make all          # Lint + test (CI runs this)
make format       # Auto-fix: uv run ruff format . && uv run ruff check --fix .
make test         # uv run pytest (runs dev-guard/tests/)
```

Python 3.10+. Ruff line-length 100, select `E,W,F,I,UP,B,SIM`. Tests in `dev-guard/tests/` and `cmux-integration/tests/`.

## Critical Rules

- **Never edit `~/.claude/plugins/` or `~/.claude/plugins/cache/`** — those directories contain installed/cached versions. Always edit this source repository. Changes in `~/.claude/plugins/` will be lost on update and are never committed.
- **Always bump plugin versions in both files** when modifying a plugin:
  - `<plugin>/.claude-plugin/plugin.json`
  - `.claude-plugin/marketplace.json` (the plugin's entry, NOT `metadata.version`)
- The marketplace `metadata.version` is **informational only** — it does not control update detection. Bump it only for structural marketplace changes (adding/removing plugins, schema changes), not for individual plugin version bumps.

## Change Workflow

**CLAUDE.md-only changes** do not need a PR. Edit in place and leave uncommitted.

**All other changes** follow this workflow:

1. **Develop and test locally** — Run `make all` before committing.
2. **Branch, commit, push, PR** — Branch from `origin/main`. Conventional commits. Show the PR link to the user.
3. **Wait for CI** — `gh pr checks <number> --watch`. Do not merge on red. Two CI workflows run: `ci.yml` (triggers on `*.py`, `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`) and `plugin-lint.yml` (triggers on `**.json`, `**.md`, plugin files). If no checks appear, merge after local `make all` passes.
   For PR creation, prefer GitHub MCP tools (`mcp__github__create_pull_request`) if available. Use `gh pr create` as fallback.
4. **Merge** — `gh pr merge <number> --merge`.
5. **Update local plugin** — After merge, run these commands (do not hand them to the user).
   Claude Code blocks nested `claude` CLI invocations (since v2.1.39). Prefix with `CLAUDECODE=""` to bypass:
   ```bash
   git switch main && git pull origin main
   CLAUDECODE="" claude plugin marketplace update personal-claude-marketplace
   CLAUDECODE="" claude plugin update <plugin-name>@personal-claude-marketplace
   ```
6. **Reload and E2E verify** — Tell user to run `/reload-plugins` to pick up the updated plugin without restarting. E2E verification happens after reload.

## Repository Structure

Personal Claude Code plugin marketplace with 11 plugins. Master registry: `.claude-plugin/marketplace.json`.

- **LSP plugins (5):** `pyright-uvx`, `vtsls-npx`, `gopls-go`, `vscode-html-css-npx`, `rust-analyzer-rustup`
- **dev-guard/** — Tool selection guard, commit validation, pre-push review (only plugin with tests)
- **code-quality/** — Architecture, security, QA, performance agents + audit and orchestration skills
- **dev-essentials/** — LSP navigation, uv-python, test execution, planning, session management
- **git-tools/** — Git history, hooks, commit review, contributing guide; SessionStart git instructions
- **github-mcp/** — GitHub MCP server (HTTP, api.githubcopilot.com); full toolsets for PRs, issues, actions, code security
- **cmux-integration/** — Bridges Claude Code hook events to CMUX terminal (notifications, sidebar, activity log)

Each plugin has `.claude-plugin/plugin.json`. Hooks register in `hooks/hooks.json`. Skills live in `skills/*/SKILL.md`.

## CI

GitHub Actions on PRs to main. Runs `make all`. Uses `uv sync --group dev`.
