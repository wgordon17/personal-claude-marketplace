# git-branchless Complete Reference

This document provides exhaustive documentation for all git-branchless commands used by AI agents.

## Table of Contents
1. [Installation & Setup](#installation--setup)
2. [git reword - Rewriting Commit Messages](#git-reword---rewriting-commit-messages)
3. [git branchless move - Moving Commits](#git-branchless-move---moving-commits)
4. [git split - Splitting Commits](#git-split---splitting-commits)
5. [git branchless record - Creating Commits](#git-branchless-record---creating-commits)
6. [git branchless undo - Error Recovery](#git-branchless-undo---error-recovery)
7. [git sl - Visualization](#git-sl---visualization)
8. [git restack - Repairing Commit Graphs](#git-restack---repairing-commit-graphs)
9. [Squashing Commits](#squashing-commits)
10. [Common Workflows](#common-workflows)
11. [Troubleshooting](#troubleshooting)
12. [Commands to AVOID](#commands-to-avoid)

---

## Installation & Setup

### Install git-branchless
```bash
# macOS
brew install git-branchless

# From source
cargo install --locked git-branchless

# Verify installation
git branchless --version
```

### Initialize in Repository
```bash
# REQUIRED: Run once per repository
git branchless init

# Verify initialization
git sl  # Should show smart log
```

### Check if Initialized
```bash
# Returns 0 if initialized, non-zero otherwise
git branchless --version >/dev/null 2>&1 && echo "Installed" || echo "Not installed"

# Check if initialized in current repo
git sl >/dev/null 2>&1 && echo "Initialized" || echo "Not initialized"
```

---

## git reword - Rewriting Commit Messages

### Purpose
Change the commit message of ANY commit in history, not just HEAD.

### Syntax
```bash
git reword -m "new message" <commit-sha>
```

### Flags
| Flag | Description |
|------|-------------|
| `-m "message"` | Specify message directly (REQUIRED for non-interactive use) |
| `-m "line1" -m "line2"` | Multiple `-m` flags create multiple paragraphs |
| `--force-rewrite` | Allow rewording public commits |
| `--fixup` | Convert commit message to fixup format |
| `--discard` | Start with empty message |

### Examples

**Basic reword:**
```bash
git reword -m "feat(api): adds user authentication endpoint" abc1234
```

**Multi-paragraph message:**
```bash
git reword -m "feat(api): adds user authentication" -m "Implements JWT-based auth with refresh tokens." abc1234
```

**Reword with conventional commit format:**
```bash
git reword -m "fix(auth): prevents token expiration race condition" def5678
```

**Reword the parent commit:**
```bash
git reword -m "new message" HEAD~1
```

**Reword with body explaining why:**
```bash
git reword -m "refactor(db): extracts connection pooling" -m "Reduces connection overhead by reusing connections." ghi9012
```

**Reword HEAD:**
```bash
git reword -m "feat(cli): new message" HEAD
```

**Reword with breaking change:**
```bash
git reword -m "feat(api)!: changes response format" -m "BREAKING CHANGE: API now returns {data, meta} wrapper." abc1234
```

### Important Notes
- Always use `-m` flag for non-interactive use
- Without `-m`, opens an editor (NOT AI-compatible)
- Automatically rebases descendants
- Preserves author information
- Can be used on any commit, not just HEAD

### Automated Commit Message Rewording (Alternative: git rebase -i)

When you need to use `git rebase -i` for rewording (e.g., when `/review-commits` identifies multiple commits), use **git environment variables** to automate without creating script files:

**Key Environment Variables:**
- `GIT_SEQUENCE_EDITOR`: Marks which commits to reword in the todo list
- `GIT_EDITOR`: Rewrites the actual commit message (replaces line 1)

**Simple inline approach (NO scripts needed):**

```bash
# Reword commit #2 in last 5 commits
GIT_SEQUENCE_EDITOR='sed -i.bak "2s/^pick/reword/"' \
GIT_EDITOR='sed -i.bak "1c\\
feat(scope): new shorter message here
"' \
git rebase -i HEAD~5
```

**Key points:**
- Use sed's `c\` (change) command to replace line 1
- Newline after `c\` is required in the inline command
- This preserves the commit body (lines 2+)
- `-i.bak` works on macOS, use `-i` on Linux
- No script files created - all inline!

**For multiple commits, handle them sequentially:**

```bash
# First reword (commit 2)
GIT_SEQUENCE_EDITOR='sed -i.bak "2s/^pick/reword/"' \
GIT_EDITOR='sed -i.bak "1c\\
feat(api): new message for commit 2
"' \
git rebase -i HEAD~5

# Then reword commit 3 (now commit 2 after previous rebase)
GIT_SEQUENCE_EDITOR='sed -i.bak "3s/^pick/reword/"' \
GIT_EDITOR='sed -i.bak "1c\\
fix(db): new message for commit 3
"' \
git rebase -i HEAD~4
```

**Or reword all at once with a mapping approach:**
- Create one editor that reads current subject, replaces with new one
- But this requires a script file (violates "no scripts" principle)
- **Prefer:** Handle each commit with individual sed command above OR use `git reword -m` multiple times

---

## git branchless move - Moving Commits

### Purpose
Move, reorder, or reorganize commits in history without interactive rebase.

### Syntax
```bash
git branchless move -s <source> -d <destination>
git branchless move -x <exact-commit> -d <destination>
```

### Flags
| Flag | Description |
|------|-------------|
| `-s <commit>` / `--source` | Move commit AND its descendants |
| `-x <commit>` / `--exact` | Move ONLY the specified commit |
| `-d <commit>` / `--dest` | Destination commit to move after |
| `-b <commit>` / `--base` | Calculate merge-base |
| `-I` / `--insert` | Insert between destination and its children |
| `--fixup` | Squash source into destination (EXPERIMENTAL) |
| `--merge` | Enable on-disk conflict resolution |
| `--in-memory` | Force in-memory rebase (faster) |
| `--force` | Allow moving public commits |
| `--no-deduplicate-commits` | Skip commit deduplication |

### Examples

**Move commit and descendants:**
```bash
git branchless move -s abc1234 -d def5678
```

**Move only one specific commit:**
```bash
git branchless move -x abc1234 -d def5678
```

**Move multiple exact commits:**
```bash
git branchless move -x commit1 -x commit2 -x commit3 -d target
```

**Insert commit between target and its children:**
```bash
git branchless move -s abc1234 --insert -d main
```

**Reorder commits (move commit earlier in history):**
```bash
# Move commit3 before commit1 (assuming commit1 -> commit2 -> commit3)
git branchless move -x commit3 -d commit1^
```

**Move a branch onto main:**
```bash
git branchless move -b feature-branch -d main
```

**Reorder three commits (C B A → A B C):**
```bash
# Original order: commitA -> commitB -> commitC
git branchless move -x commitA -d base
git branchless move -x commitB -d commitA
git branchless move -x commitC -d commitB
```

### Important Notes
- Use `-x` for exact commit, `-s` for commit with descendants
- Handles descendant rebasing automatically
- In-memory by default (fast, preserves working directory)
- Use `--merge` only when conflicts need resolution
- Operations are 10x faster than `git rebase` due to in-memory processing

---

## git split - Splitting Commits

### Purpose
Split a commit into multiple commits by extracting specific files.

### Current Approach - Manual Reset Workflow

The manual reset workflow is the only non-interactive option for splitting commits:

**Complete step-by-step process:**

```bash
# Step 1: Identify the commit to split
git log --oneline -10
# Let's say we want to split commit abc1234

# Step 2: Navigate to that commit
git checkout abc1234

# Step 3: Soft reset to uncommit (keeps files staged)
git reset --soft HEAD~1

# Step 4: Unstage all files
git reset HEAD

# Step 5: Stage and commit first set of files
git add src/feature.py src/helpers.py
git commit -m "feat(api): adds feature implementation"

# Step 6: Stage and commit second set of files
git add tests/test_feature.py
git commit -m "test(api): adds feature tests"

# Step 7: Stage and commit remaining files (if any)
git add .
git commit -m "docs(api): updates API documentation"

# Step 8: Update git-branchless state
git restack

# Step 9: Return to your branch
git checkout your-branch-name

# Step 10: Verify the split
git sl
```

**Simplified version (2 splits):**

```bash
git checkout <commit-to-split>
git reset --soft HEAD~1
git reset HEAD
git add file1.py && git commit -m "Part 1: file1 changes"
git add . && git commit -m "Part 2: remaining changes"
git restack
git checkout -  # Return to previous branch
```

### File Selection Strategies

**By file type:**
```bash
# Separate feature code from tests
git add src/**/*.py && git commit -m "feat(api): implementation"
git add tests/**/*.py && git commit -m "test(api): test suite"
```

**By component:**
```bash
# Separate backend from frontend
git add src/api/*.py && git commit -m "feat(api): backend changes"
git add src/ui/*.js && git commit -m "feat(ui): frontend changes"
```

**By concern:**
```bash
# Separate feature from refactoring
git add src/new_feature.py && git commit -m "feat(api): new feature"
git add src/old_code.py && git commit -m "refactor(api): cleanup old code"
```

### Important Notes
- **Always run `git restack`** after manual split to update git-branchless state
- This workflow works for **any commit**, not just HEAD (checkout moves you there first)
- File-level only - hunk-level splitting requires interactive selection (complex)
- File-level splitting is sufficient for 90% of use cases
- Use `git status` between commits to see what files remain

### Future
A native `git split` command is planned for v0.11+ release. Revisit documentation when released.

---

## git branchless record - Creating Commits

### Purpose
Create commits with message directly, no editor.

### Syntax
```bash
git branchless record -m "message"
```

### Flags
| Flag | Description |
|------|-------------|
| `-m "message"` | Commit message (can use multiple times) |
| `-c <name>` / `--create` | Create new branch before committing |
| `-I` / `--insert` | Insert commit into current stack |
| `-d` / `--detach` | Detach current branch before commit |
| `-s` / `--stash` | Return to previous commit after committing |
| `-i` / `--interactive` | Interactive mode (NOT for AI use) |

### Examples

**Create commit with message:**
```bash
git branchless record -m "feat(api): adds rate limiting middleware"
```

**Multi-paragraph commit:**
```bash
git branchless record -m "feat(api): adds rate limiting" -m "Implements token bucket algorithm with Redis backing."
```

**Create on new branch:**
```bash
git branchless record -c feature/rate-limiting -m "Initial implementation"
```

**Insert into existing stack:**
```bash
git branchless record -I -m "Intermediate refactoring step"
```

**Create and return to previous commit:**
```bash
git branchless record -s -m "Temporary commit for testing"
```

**Create with detached branch:**
```bash
git branchless record -d -m "Experimental change"
```

### Important Notes
- Always use `-m` for non-interactive use
- `-i` flag opens TUI (NOT AI-compatible)
- Stages all tracked changes by default
- Multiple `-m` flags create multiple paragraphs

---

## git branchless undo - Error Recovery

### Purpose
Undo the last git-branchless operation.

### Syntax
```bash
git branchless undo --yes
```

### Flags
| Flag | Description |
|------|-------------|
| `-y` / `--yes` | Skip confirmation (REQUIRED for AI use) |
| `-i` / `--interactive` | Browse previous states (NOT for AI use) |

### Examples

**Undo last operation immediately:**
```bash
git branchless undo --yes
```

**Check operation history:**
```bash
git branchless op log
```

**Restore to specific operation:**
```bash
git branchless op restore <op-id>
```

**Undo multiple operations:**
```bash
git branchless undo --yes  # Undo once
git branchless undo --yes  # Undo twice
```

### What Can Be Undone
- Commits and amended commits
- Merges and rebases
- Checkouts
- Branch operations (create, delete, move)
- git-branchless move, reword, split operations
- git-branchless record operations

### Important Notes
- ALWAYS use `--yes` for non-interactive use
- Cannot undo working copy changes (only commit graph)
- Undo is safe—creates new state rather than destroying history
- Can undo multiple times to go back further

---

## git sl - Visualization

### Purpose
Smart log—visualize commit graph.

### Syntax
```bash
git sl
```

### Examples

**View commit graph:**
```bash
git sl
```

**View with more context:**
```bash
git branchless smartlog
```

**View specific branch:**
```bash
git sl -r <branch-name>
```

### Important Notes
- Useful for understanding current state before operations
- Shows commit relationships clearly
- Read-only operation—safe to run anytime
- Shows branches, HEAD, and commit relationships in a tree

---

## git restack - Repairing Commit Graphs

### Purpose
Repair broken commit graphs after manual operations.

### Syntax
```bash
git restack
```

### When to Use
- After manual `git reset` operations
- After manual cherry-picks
- When git-branchless state seems out of sync
- After split workflow on v0.10.0
- After any manual git operations that bypass git-branchless

### Examples

**Repair commit graph:**
```bash
git restack
```

**Restack with force:**
```bash
git restack --force
```

### Important Notes
- Safe to run—analyzes and repairs state
- Run after any manual git operations in branchless workflow
- Automatically rebases dependent commits
- Can be run multiple times safely

---

## Squashing Commits

### Method 1: git-branchless move --fixup (Experimental)
```bash
git branchless move --fixup -s <source-commit> -d <target-commit>
```

**Flags:**
- `--fixup` - Squashes source into destination
- `-x` - Exact commit to squash (use `-x`, not `-s`)
- `-d` - Destination commit to squash into

**Example:**
```bash
git branchless move --fixup -x abc1234 -d def5678
```

**Note:** This is marked EXPERIMENTAL. Use `git branchless undo --yes` to recover if needed.

### Examples

**Squash commit into previous:**
```bash
git branchless move --fixup -x HEAD -d HEAD~1
```

**Squash multiple commits into one:**
```bash
# Squash commit2 and commit3 into commit1
git branchless move --fixup -x commit2 -d commit1
git branchless move --fixup -x commit3 -d commit1
```

---

## Common Workflows

### Workflow 1: Reword a commit message
```bash
# Identify the commit
git log --oneline -10

# Reword it
git reword -m "better commit message" <sha>

# Verify
git sl
```

### Workflow 2: Reorder commits
```bash
# View current order
git sl

# Move commit3 to before commit1
git branchless move -x commit3 -d commit1^

# Verify
git sl
```

### Workflow 3: Split a large commit
```bash
# Manual reset workflow (only non-interactive option)
git checkout <sha-to-split>
git reset --soft HEAD~1
git reset HEAD
git add src/feature.py && git commit -m "feat(api): feature implementation"
git add tests/test_feature.py && git commit -m "test(api): feature tests"
git restack
git checkout -  # Return to previous branch
```

### Workflow 4: Squash commits
```bash
# Squash source commit into target
git branchless move --fixup -x <source-commit> -d <target-commit>

# Verify
git sl
```

### Workflow 5: Recover from mistake
```bash
# Undo last operation
git branchless undo --yes

# Check what was undone
git branchless op log
```

### Workflow 6: Clean up PR commits before merging
```bash
# View commits on branch
git log --oneline origin/main..HEAD

# Reword bad commit messages
git reword -m "better message" <sha1>
git reword -m "another better message" <sha2>

# Squash WIP commits
git branchless move --fixup -x <wip-commit> -d <target-commit>

# Split commits with mixed concerns
git checkout <mixed-commit>
git reset --soft HEAD~1
git reset HEAD
git add feature/*.py && git commit -m "feat(api): feature"
git add tests/*.py && git commit -m "test(api): tests"
git restack

# Verify final state
git sl
```

---

## Troubleshooting

### "git-branchless not installed"
```bash
brew install git-branchless
```

### "Repository not initialized"
```bash
git branchless init
```

**Verify initialization:**
```bash
git sl
```

### "Stale rebase state"
```bash
# Check for rebase state
test -d .git/rebase-merge && echo "Rebase in progress"
test -d .git/rebase-apply && echo "Am/apply in progress"

# Abort stale rebase
test -d .git/rebase-merge && git rebase --abort
test -d .git/rebase-apply && git am --abort
```

### "Merge conflicts during move"
```bash
# Option 1: Use --merge flag for on-disk resolution
git branchless move --merge -s <src> -d <dest>

# Option 2: Abort and try different approach
git branchless undo --yes

# Option 3: Reorder differently to avoid conflict
git branchless move -x <different-commit> -d <dest>
```

### "Operation log corrupted"
```bash
git branchless repair
```

### "Commit not found"
```bash
# View all commits including hidden ones
git branchless smartlog

# View reflog to find lost commits
git reflog
```

### "Working directory not clean"
```bash
# Stash changes before git-branchless operations
git stash

# Perform operation
git reword -m "message" <sha>

# Restore changes
git stash pop
```

---

## Commands to AVOID

These commands are interactive and NOT suitable for AI agents:

| Command | Problem | Use Instead |
|---------|---------|-------------|
| `git rebase -i` | Opens editor | `git branchless move` |
| `git add -p` | Interactive hunk selection | `git add <files>` or `git apply --cached` |
| `git revise` | No `-m` flag, requires editor | `git reword -m` |
| `git revise --cut` | Interactive hunk selection | Manual reset workflow |
| `git branchless record -i` | Opens TUI | `git branchless record -m` |
| `git branchless undo` (without --yes) | Prompts for confirmation | `git branchless undo --yes` |
| `jj` commands | Different workflow, not in Context7 | Stay with git-branchless |
| `sed` with GIT_EDITOR | Platform-dependent (BSD vs GNU), fragile | `git reword -m` |
| `sed` with GIT_SEQUENCE_EDITOR | Line numbers shift, macOS compatibility issues | `git branchless move` |

### Why These Are Unreliable for AI

**`git rebase -i`:**
- Opens editor (requires GIT_SEQUENCE_EDITOR hack)
- Line numbers change after each operation
- Platform-dependent editor behavior

**`git add -p`:**
- Requires answering y/n for each hunk
- No way to pre-program responses
- Hunk boundaries change based on context

**`sed` approaches:**
- BSD sed (macOS) vs GNU sed (Linux) incompatibility
- Newline handling differs (`\n` vs `$'\n'`)
- Line numbers are fragile (shift during rebase)

**git-branchless provides deterministic, platform-independent alternatives for all these operations.**

---

## Documentation Resources

- **Context7**: Query `/arxanas/git-branchless` (376 snippets indexed)
- **Wiki**: https://github.com/arxanas/git-branchless/wiki
- **GitHub**: https://github.com/arxanas/git-branchless
- **Help flags**:
  - `git branchless --help`
  - `git move --help`
  - `git reword --help`
  - `git record --help`
  - `git undo --help`
- **WebFetch**: Official wiki pages for detailed documentation
