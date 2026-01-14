---
name: git-history
description: Non-interactive git history manipulation using git-branchless. Use when rewording commits, squashing, splitting, reordering, or recovering from mistakes. Provides reliable commands that AI agents can execute without interactive prompts. ALWAYS prefer these commands over git rebase -i, git add -p, or sed-based workarounds.
allowed-tools: Bash(git:*)
---

# Non-Interactive Git History Commands (git-branchless)

This skill provides comprehensive reference for git-branchless commands that AI agents can execute reliably without interactive prompts.

## CRITICAL: Why Use git-branchless

Traditional git history commands fail for AI agents because:
- `git rebase -i` opens an editor
- `git add -p` requires interactive hunk selection
- `sed` with GIT_SEQUENCE_EDITOR is platform-dependent and fragile
- `git revise` has no `-m` flag

git-branchless provides non-interactive alternatives for ALL these operations.

## Setup (One-Time Per Repo)

```bash
# Install (if not already)
brew install git-branchless

# Initialize in repo (required once per repo)
git branchless init
```

## Quick Reference Table

| Operation | Command | Notes |
|-----------|---------|-------|
| **Reword** | `git reword -m "message" <sha>` | Non-interactive with `-m` flag |
| **Move** | `git branchless move -s <src> -d <dest>` | Moves commit and descendants |
| **Move exact** | `git branchless move -x <sha> -d <dest>` | Moves only specified commit |
| **Squash** | `git branchless move --fixup -x <src> -d <dest>` | Experimental |
| **Split** | Manual reset workflow | See BRANCHLESS.md |
| **Create** | `git branchless record -m "message"` | No editor |
| **Undo** | `git branchless undo --yes` | No confirmation prompt |
| **View** | `git sl` | Smart log (commit graph) |
| **Restack** | `git restack` | Repair commit graph after manual ops |

For detailed documentation, see [BRANCHLESS.md](BRANCHLESS.md)
