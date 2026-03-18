#!/usr/bin/env bash
# git-instructions.sh — Dynamic git workflow instructions for Claude Code
# Runs at SessionStart via git-tools plugin hook.
# Detects repo configuration and outputs tailored git instructions.
# REPLACES Claude's built-in git instructions (set includeGitInstructions: false).

# Early exit: not a git repo
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    exit 0
fi

# Temp file for network fallback in detect_mainline; cleaned up on any exit
TMPFILE=$(mktemp /tmp/.git-instructions-lsremote.XXXXXX)
trap 'rm -f "$TMPFILE" 2>/dev/null' EXIT

# ============================================================================
# Detection functions (macOS bash 3.2 compatible — no associative arrays,
# no mapfile, no GNU timeout)
# ============================================================================

detect_mainline() {
    local mainline=""

    # Try symbolic ref for origin HEAD (no network call)
    mainline=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')

    if [ -z "$mainline" ]; then
        # Try upstream HEAD (fork repos)
        mainline=$(git symbolic-ref refs/remotes/upstream/HEAD 2>/dev/null | sed 's|refs/remotes/upstream/||')
    fi

    if [ -z "$mainline" ]; then
        # Network fallback: git ls-remote with 5s timeout
        # Uses polling loop (bash 3.2 compatible — no wait -n, no GNU timeout)
        # TMPFILE created at script level with mktemp; cleaned up by EXIT trap
        git ls-remote --symref origin HEAD >"$TMPFILE" 2>/dev/null &
        LS_PID=$!
        TIMEOUT=5
        while [ $TIMEOUT -gt 0 ]; do
            if ! kill -0 $LS_PID 2>/dev/null; then break; fi
            sleep 1
            TIMEOUT=$((TIMEOUT - 1))
        done
        kill $LS_PID 2>/dev/null || true
        wait $LS_PID 2>/dev/null || true
        if [ -s "$TMPFILE" ]; then
            mainline=$(grep "^ref: refs/heads/" "$TMPFILE" 2>/dev/null | head -1 | sed 's|ref: refs/heads/||' | awk '{print $1}')
        fi
    fi

    if [ -z "$mainline" ]; then
        # Local branch existence fallback
        if git show-ref --verify --quiet refs/heads/main 2>/dev/null; then
            mainline="main"
        elif git show-ref --verify --quiet refs/heads/master 2>/dev/null; then
            mainline="master"
        else
            mainline="main"
        fi
    fi

    echo "$mainline"
}

detect_fork() {
    # Returns owner of upstream remote if fork, empty string otherwise
    local upstream_url
    upstream_url=$(git remote get-url upstream 2>/dev/null || echo "")
    if [ -z "$upstream_url" ]; then
        echo ""
        return
    fi

    # Extract owner from SSH (SCP-style or ssh://), HTTPS URL
    local owner=""
    if echo "$upstream_url" | grep -q "git@.*:"; then
        # SCP-style SSH: git@github.com:owner/repo.git
        owner=$(echo "$upstream_url" | sed 's|.*:\([^/]*\)/.*|\1|')
    else
        # HTTPS or ssh:// style: https://github.com/owner/repo.git
        # Also handles ssh://git@github.com/owner/repo.git
        owner=$(echo "$upstream_url" | sed 's|.*/\([^/]*\)/[^/]*$|\1|')
    fi
    echo "$owner"
}

detect_conventions() {
    # Returns "yes", "maybe", or "no"
    # First check for commitlint config files
    local config_found=0
    for f in .commitlintrc .commitlintrc.js .commitlintrc.ts .commitlintrc.json .commitlintrc.yaml .commitlintrc.yml commitlint.config.js commitlint.config.ts commitlint.config.mjs; do
        if [ -f "$f" ]; then
            config_found=1
            break
        fi
    done

    if [ "$config_found" -eq 1 ]; then
        echo "yes"
        return
    fi

    # Analyze last 10 commit messages for conventional commit pattern
    local total=0
    local matching=0
    while IFS= read -r msg; do
        total=$((total + 1))
        if echo "$msg" | grep -qE "^(feat|fix|docs|chore|refactor|test|perf|style|build|ci)(\(.+\))?!?:[[:space:]].+"; then
            matching=$((matching + 1))
        fi
    done < <(git log -10 --format="%s" 2>/dev/null || true)

    if [ "$total" -eq 0 ]; then
        echo "no"
        return
    fi

    # 7/10 or more = yes, 3/10 or more = maybe
    local threshold_yes=7
    local threshold_maybe=3
    # Scale thresholds to actual total
    if [ "$total" -lt 10 ]; then
        threshold_yes=$(( (total * 7 + 9) / 10 ))
        threshold_maybe=$(( (total * 3 + 9) / 10 ))
    fi

    if [ "$matching" -ge "$threshold_yes" ]; then
        echo "yes"
    elif [ "$matching" -ge "$threshold_maybe" ]; then
        echo "maybe"
    else
        echo "no"
    fi
}

# ============================================================================
# Gather detection results
# ============================================================================

MAINLINE=$(detect_mainline)
UPSTREAM_OWNER=$(detect_fork)
CONVENTIONS=$(detect_conventions)

# Determine if this is a fork
IS_FORK=0
if [ -n "$UPSTREAM_OWNER" ]; then
    IS_FORK=1
fi

# ============================================================================
# Output: Git Instructions for Claude
# ============================================================================

cat <<'HEADER'
# Git Workflow Instructions

These instructions replace Claude Code's built-in git instructions.
If you see duplicate built-in git instructions in this session, warn the user:
"Built-in git instructions may be duplicating these — set includeGitInstructions: false in settings.json."

HEADER

DETECTED_CONFIG="mainline=\`${MAINLINE}\`"
if [ "$IS_FORK" -eq 1 ]; then
    DETECTED_CONFIG="${DETECTED_CONFIG}, fork (upstream owner: \`${UPSTREAM_OWNER}\`)"
fi
if [ "$CONVENTIONS" = "yes" ]; then
    DETECTED_CONFIG="${DETECTED_CONFIG}, conventional commits"
elif [ "$CONVENTIONS" = "maybe" ]; then
    DETECTED_CONFIG="${DETECTED_CONFIG}, conventional commits (likely)"
fi
echo "**Detected configuration:** ${DETECTED_CONFIG}"
echo ""

cat <<'GIT_SAFETY'

## Git Safety Protocol

- NEVER force push to the mainline branch
- NEVER skip hooks (--no-verify, --no-gpg-sign)
- NEVER commit directly to mainline — always use feature branches
- NEVER update git config
- NEVER add Signed-off-by, Assisted-by, or Co-Authored-By trailers to commits
- Prefer staging specific files by name over `git add -A` or `git add .`
- Create new commits rather than amending existing ones
- Use HEREDOC format for commit messages (see Commit Workflow below)

GIT_SAFETY

# Branch workflow section with dynamic mainline
cat <<BRANCH_WORKFLOW

## Branch Workflow

Create all feature branches from \`${MAINLINE}\`:

\`\`\`bash
git fetch origin ${MAINLINE}
git switch -c <branch-name> origin/${MAINLINE}
\`\`\`

BRANCH_WORKFLOW

if [ "$IS_FORK" -eq 1 ]; then
    cat <<FORK_BRANCH
In this fork repo, sync from upstream before branching:

\`\`\`bash
git fetch upstream ${MAINLINE}
git switch -c <branch-name> upstream/${MAINLINE}
\`\`\`

FORK_BRANCH
fi

cat <<'BRANCH_RULES'
Branch naming: kebab-case descriptive names. Never stack branches — every feature branch is independent of other feature branches.

BRANCH_RULES

if [ "$CONVENTIONS" = "yes" ] || [ "$CONVENTIONS" = "maybe" ]; then
    cat <<'BRANCH_CONVENTIONAL'
Conventional commit prefixes for branch names: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`.

BRANCH_CONVENTIONAL
fi

cat <<'COMMIT_WORKFLOW'

## Commit Workflow

Follow these steps in order:

1. **Status** — see what changed:
   ```bash
   git status
   ```

2. **Diff** — review staged and unstaged changes:
   ```bash
   git diff
   git diff --staged
   ```

3. **Log** — check recent commit messages for style reference:
   ```bash
   git log --oneline -10
   ```

4. **Draft the commit message** — write it mentally before staging.

5. **Stage specific files** — never use `git add -A` or `git add .`:
   ```bash
   git add path/to/file1 path/to/file2
   ```

6. **Commit with HEREDOC** — always use this format (never inline -m for multi-word messages):
   ```bash
   git commit -m "$(cat <<'EOF'
   type(scope): short present-tense description
   EOF
   )"
   ```

**Commit message rules:**
- Use present indicative tense: "adds feature" not "add feature" or "added feature"
- Keep the subject line under 72 characters
- No trailing period
- NEVER include Signed-off-by, Assisted-by, or Co-Authored-By trailers

COMMIT_WORKFLOW

if [ "$CONVENTIONS" = "yes" ] || [ "$CONVENTIONS" = "maybe" ]; then
    cat <<'CONVENTIONAL_TABLE'

### Conventional Commits

This repository uses Conventional Commits format: `type(scope): description`

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `chore` | Maintenance, dependencies, config |
| `refactor` | Code restructure (no behavior change) |
| `test` | Tests only |
| `perf` | Performance improvement |
| `style` | Formatting only (no logic change) |
| `build` | Build system, CI |
| `ci` | CI/CD config only |

Breaking changes: append `!` after type/scope — `feat(api)!: renames endpoint`

CONVENTIONAL_TABLE
fi

# PR workflow with dynamic fork/non-fork behavior
cat <<PR_WORKFLOW

## PR Workflow

1. **Analyze changes** — review all commits since branching from \`${MAINLINE}\`:
   \`\`\`bash
   git log --oneline origin/${MAINLINE}..HEAD
   git diff origin/${MAINLINE}...HEAD
   \`\`\`

2. **Push to origin**:
   \`\`\`bash
   git push -u origin <branch-name>
   \`\`\`

3. **Create PR** — prefer GitHub MCP tools over \`gh\` CLI:

PR_WORKFLOW

if [ "$IS_FORK" -eq 1 ]; then
    cat <<FORK_PR
   **Fork repo — PR targets upstream:**
   Use \`mcp__github__create_pull_request\` with \`owner="${UPSTREAM_OWNER}"\` to target the upstream repo.
   Fallback if MCP unavailable: \`gh pr create --repo ${UPSTREAM_OWNER}/<repo>\`

FORK_PR
else
    cat <<'NONFORK_PR'
   Use `mcp__github__create_pull_request` (GitHub MCP tool).
   Fallback if MCP unavailable: `gh pr create`

NONFORK_PR
fi

cat <<'PR_FORMAT'
**PR body format — strictly follow this structure:**
```
## Summary
- What changed and why (1-3 bullets)
- Second bullet if needed
- Third bullet if needed
```

**NEVER include in PR descriptions:**
- "Test plan" sections
- TODO lists or checkbox checklists (`- [ ]`)
- File lists ("Files changed: ...")
- Meta-commentary ("This PR is a work in progress...")
- More than 3 bullet points under Summary

PR_FORMAT

# Before PR: rebase instructions
cat <<REBASE
**Before opening the PR**, rebase onto the latest \`${MAINLINE}\`:
\`\`\`bash
git fetch origin ${MAINLINE}
git rebase origin/${MAINLINE}
\`\`\`

REBASE

if [ "$IS_FORK" -eq 1 ]; then
    cat <<FORK_REBASE
In this fork repo, rebase onto upstream instead:
\`\`\`bash
git fetch upstream ${MAINLINE}
git rebase upstream/${MAINLINE}
\`\`\`

FORK_REBASE
fi

cat <<AFTER_MERGE
After PR merge, sync and rebase any other active branches:
\`\`\`bash
git fetch --all
git switch <other-branch>
git rebase origin/${MAINLINE}
\`\`\`

AFTER_MERGE

# ============================================================================
# Project-specific overrides
# ============================================================================

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/hack/git-instructions.md" ]; then
    echo ""
    echo "# Project-Specific Git Instructions"
    echo ""
    cat "$REPO_ROOT/hack/git-instructions.md"
fi
