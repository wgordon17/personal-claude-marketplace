# Git Tools Plugin

Git workflow tools: history manipulation, defense-in-depth hooks, commit review, and contributing guide generation.

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

Defense-in-depth hook scripts for use with pre-commit framework:
- `git-hooks/pre-commit.sh` — Sets success marker after all pre-commit hooks pass
- `git-hooks/prepare-commit-msg.sh` — Unbypassable safety validator (branch protection, conflict detection)

## Requirements

- **git-branchless**: `brew install git-branchless` — Required by `/git-history` skill and `/git-tools:review-commits` command. Without it, history manipulation and commit review are non-functional.
- **pre-commit**: `uv tool install pre-commit` — Required by the defense-in-depth git hook scripts.
- **uv**: Required for pre-commit installation and hook management.

## Installation

```bash
claude plugin install git-tools@personal-claude-marketplace
```

## OMP Compatibility

Also ships an `omp-extension.ts` bridge (`package.json` declares it via `omp.extensions`) so the SessionStart hook works under [OMP](https://omp.sh) too: it shells out to the same `scripts/git-instructions.sh`, unchanged, and injects its output into context. No mapping table needed — see `dev-guard/OMP-COMPAT.md` for the marketplace's full OMP compatibility reference.
