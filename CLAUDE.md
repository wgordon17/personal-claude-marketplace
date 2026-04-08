# CLAUDE.md

## Development Commands

```bash
make all          # Lint + test (CI runs this)
make format       # Auto-fix: uv run ruff format . && uv run ruff check --fix .
make test         # uv run pytest (runs dev-guard/tests/)
```

Python 3.13+. Ruff line-length 100, select `E,W,F,I,UP,B,SIM`. Tests in `dev-guard/tests/`.

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
4. **Wait for user to merge** — Do NOT merge automatically. Tell the user the PR is ready for review and wait for them to confirm the merge. Only proceed to step 5 after the user says it's merged.
5. **Update local plugin** — After merge, run these commands (do not hand them to the user).
   Claude Code blocks nested `claude` CLI invocations (since v2.1.39). Prefix with `CLAUDECODE=""` to bypass:
   ```bash
   git switch main && git pull origin main
   CLAUDECODE="" claude plugin marketplace update personal-claude-marketplace
   CLAUDECODE="" claude plugin update <plugin-name>@personal-claude-marketplace
   ```
6. **Reload and E2E verify** — Tell user to run `/reload-plugins` to pick up the updated plugin without restarting. E2E verification happens after reload.

## Repository Structure

Personal Claude Code plugin marketplace with 10 plugins. Master registry: `.claude-plugin/marketplace.json`.

- **LSP plugins (5):** `pyright-uvx`, `vtsls-npx`, `gopls-go`, `vscode-html-css-npx`, `rust-analyzer-rustup`
- **dev-guard/** — Tool selection guard, commit validation, pre-push review, subagent completion verification (only plugin with tests)
- **code-quality/** — Agents (architect, security, QA, performance, test-runner, code-reviewer, code-simplifier), skills (20), commands (4), and orchestration
- **git-tools/** — Git history, hooks, commit review, contributing guide; SessionStart git instructions
- **github-mcp/** — GitHub MCP server (HTTP, api.githubcopilot.com); full toolsets for PRs, issues, actions, code security
- **jira/** — Jira integration via Atlassian Rovo MCP; OSAC defaults (project=MGMT, component=OSAC); skill (`/jira:jira`) and spawnable agent (`jira:jira-agent`)

Each plugin has `.claude-plugin/plugin.json`. Hooks register in `hooks/hooks.json`. Skills live in `skills/*/SKILL.md`.

## ATLAS.md

`ATLAS.md` is a fully generated plugin inventory and health report. Never edit it manually.

- **Generate:** `uv run .claude/commands/generate-atlas.py`
- **Staleness check:** Pre-commit hook validates on plugin file changes
- **Semantic health:** Pre-push hook runs Vertex AI analysis (fail-open)
- **Workflow guide:** `docs/WORKFLOW.md` is hand-authored and included verbatim at the top

## CI

GitHub Actions on PRs to main. Two workflows: `ci.yml` (Python checks via `make all`, `uv sync --group dev`) and `plugin-lint.yml` (plugin structure via `uvx claudelint --strict`, ATLAS.md staleness check).
