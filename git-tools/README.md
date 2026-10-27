# Git Tools Plugin

Git workflow tools: dynamic SessionStart git instructions, history manipulation, commit review, and CONTRIBUTING.md generation.

## Skills (1)

| Skill | Description |
|-------|-------------|
| `/git-history` | Non-interactive git history manipulation (git-branchless) |

## Commands (2)

| Command | Description |
|---------|-------------|
| `/git-tools:review-commits` | AI-assisted commit review for PR readiness |
| `/git-tools:contributing` | Generate or update CONTRIBUTING.md |

## Git Hook Scripts

Defense-in-depth hook scripts for use with pre-commit framework and Git 2.54+ additive global hooks:
- `git-hooks/pre-commit.sh` — Sets success marker after all pre-commit hooks pass
- `git-hooks/prepare-commit-msg.sh` — Unbypassable safety validator (branch protection, conflict detection)
- `git-hooks/betterleaks-hook.sh` — Unbypassable (`prepare-commit-msg`) Betterleaks v1/v2 staged secret scanner with `--confidence medium`, auto-recompilation across `v1.9` → `v2.0` upgrades, and 7-day non-blocking background rule sync
- `scripts/sync-betterleaks-overlay.py` — PEP-723 `uv run` compiler that merges curated LeakTK (`leaktk/patterns`) and Google OSV-SCALIBR Veles rules with `# notsecret` line filtering into `~/.config/betterleaks/{overlay-v2,betterleaks-unified}.toml`

### Global Betterleaks Hook Setup (Git 2.54+)

```bash
uv run git-tools/scripts/sync-betterleaks-overlay.py
git config --global hook.betterleaks.event prepare-commit-msg
git config --global hook.betterleaks.parallel true
git config --global hook.betterleaks.command /path/to/git-tools/git-hooks/betterleaks-hook.sh
```

## Requirements

- **git-branchless**: `brew install git-branchless` — Required by `/git-history` skill and `/git-tools:review-commits` command. Without it, history manipulation and commit review are non-functional.
- **betterleaks**: `brew install betterleaks` — Required by `git-hooks/betterleaks-hook.sh` and `scripts/sync-betterleaks-overlay.py` for staged secret scanning and rule-overlay validation.
- **Git 2.54+**: `brew install git` — Required for additive global `hook.betterleaks.*` configuration.
- **pre-commit**: `uv tool install pre-commit` — Required by the defense-in-depth git hook scripts.
- **uv**: Required for pre-commit installation, hook management, and running `scripts/sync-betterleaks-overlay.py`.

## Installation

```bash
claude plugin install git-tools@personal-claude-marketplace
```

## OMP Compatibility

Also ships an `omp-extension.ts` bridge (`package.json` declares it via `omp.extensions`) so the SessionStart hook works under [OMP](https://omp.sh) too: it shells out to the same `scripts/git-instructions.sh`, unchanged, and injects its output into context. No mapping table needed — see `dev-guard/OMP-COMPAT.md` for the marketplace's full OMP compatibility reference.
