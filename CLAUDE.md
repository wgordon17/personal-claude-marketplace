# CLAUDE.md

## Development Commands

```bash
make all          # Lint + test (CI runs this)
make format       # Auto-fix: uv run ruff format . && uv run ruff check --fix .
make test         # uv run pytest (runs global-hooks/tests/)
```

Python 3.10+. Ruff line-length 100, select `E,W,F,I,UP,B,SIM`. Tests only exist in `global-hooks/tests/`.

## Critical Rules

- **Never edit `~/.claude/plugins/`** — always edit this source repository.
- **Always bump versions in both files** when modifying a plugin:
  - `<plugin>/.claude-plugin/plugin.json`
  - `.claude-plugin/marketplace.json`

## Change Workflow

**CLAUDE.md-only changes** do not need a PR. Edit in place and leave uncommitted.

**All other changes** follow this workflow:

1. **Develop and test locally** — Run `make all` before committing.
2. **Branch, commit, push, PR** — Branch from `origin/main`. Conventional commits. Show the PR link to the user.
3. **Wait for CI** — `gh pr checks <number> --watch`. Do not merge on red. CI only triggers on `*.py`, `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, and `.github/workflows/ci.yml` changes. If no checks appear (JSON-only, markdown-only, or skill-only PRs), merge after local `make all` passes.
4. **Merge** — `gh pr merge <number> --merge`.
5. **Update local plugin** — After merge, run these commands (do not hand them to the user).
   Claude Code blocks nested `claude` CLI invocations (since v2.1.39). Prefix with `CLAUDECODE=""` to bypass:
   ```bash
   git switch main && git pull origin main
   CLAUDECODE="" claude plugin marketplace update private-claude-marketplace
   CLAUDECODE="" claude plugin update <plugin-name>@private-claude-marketplace
   ```
6. **Restart and E2E verify** — Tell user to restart their session. E2E verification happens in the new session.

## Repository Structure

Private Claude Code plugin marketplace with 11 plugins. Master registry: `.claude-plugin/marketplace.json`.

- **LSP plugins (5):** `pyright-uvx`, `vtsls-npx`, `gopls-go`, `vscode-html-css-npx`, `rust-analyzer-rustup`
- **Agent plugins (3):** `project-dev/`, `test-execution/`, `superclaude/`
- **Productivity plugins (3):** `global-hooks/` (only plugin with tests), `global-skills/`, `global-commands/`

Each plugin has `.claude-plugin/plugin.json`. Hooks register in `hooks/hooks.json`. Skills live in `skills/*/SKILL.md`.

## CI

GitHub Actions on PRs to main. Runs `make all`. Uses `uv sync --group dev`.
