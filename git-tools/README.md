# Git Tools Plugin

Git workflow tools: history manipulation, defense-in-depth hooks, commit review, and contributing guide generation.

## Skills (3)

| Skill | Description |
|-------|-------------|
| `/git-history` | Non-interactive git history manipulation (git-branchless) |
| `/git-hooks-install` | Install defense-in-depth git hooks using pre-commit |
| `/git-hooks-uninstall` | Safely remove defense-in-depth git hooks |

## Commands (2)

| Command | Description |
|---------|-------------|
| `/git-tools:review-commits` | AI-assisted commit review for PR readiness |
| `/git-tools:contributing` | Generate or update CONTRIBUTING.md |

## Git Hook Scripts

Defense-in-depth hook scripts for use with pre-commit framework:
- `git-hooks/pre-commit.sh` — Sets success marker after all pre-commit hooks pass
- `git-hooks/prepare-commit-msg.sh` — Unbypassable safety validator (branch protection, conflict detection)

## Dependencies

- **git-branchless**: `brew install git-branchless` (for git-history skill)
- **pre-commit**: Installed via uv in Python projects (for git-hooks skills)

## Installation

```bash
claude plugin install git-tools@personal-claude-marketplace
```
