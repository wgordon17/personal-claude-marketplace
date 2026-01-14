#!/bin/bash
# Consolidated git safety checker for Claude Code
# Enforces safe git workflows based on user preferences

set -euo pipefail

# Color codes
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Logging
LOG_FILE="$HOME/.claude/logs/git-safety-blocks.log"
mkdir -p "$(dirname "$LOG_FILE")"

log_block() {
    local reason="$1"
    echo "[$(date)] BLOCKED: $FULL_COMMAND | Reason: $reason" >> "$LOG_FILE"
    echo -e "${RED}❌ BLOCKED: $reason${NC}" >&2
    exit 2
}

log_ask() {
    local reason="$1"
    echo "[$(date)] ASK: $FULL_COMMAND | Reason: $reason" >> "$LOG_FILE"
    echo -e "${YELLOW}⚠️  REQUIRES PERMISSION: $reason${NC}" >&2
    exit 1
}

# Read command from Claude Code hook JSON input (stdin)
# Fallback to $* for manual testing
if [ $# -gt 0 ]; then
    # Arguments provided (manual invocation: bash script.sh git commit ...)
    FULL_COMMAND="$*"
else
    # No arguments - read from stdin (hook invocation)
    INPUT=$(cat)
    FULL_COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')
fi

# EARLY EXIT: If not a git command, allow immediately (no logging, no delay)
# This is necessary because we use Bash(*) matcher to catch chained commands
if [[ ! "$FULL_COMMAND" =~ git ]]; then
    exit 0
fi

# CRITICAL SAFEGUARD: Block chained commands with git (known Claude Code hook bypass - Issue #13340)
# We use Bash(*) matcher because Bash(*git*) and Bash(*&&*git*) patterns don't work
# Chained git commands can bypass safety checks, so we block them entirely
if [[ "$FULL_COMMAND" =~ (&&|\|\||;) ]] && [[ "$FULL_COMMAND" =~ git ]]; then
    log_block "Chained git commands (using &&, ||, or ;) are FORBIDDEN - they bypass safety checks. Run commands separately."
fi

# Helper: Check if command contains --force (not --force-with-lease)
has_force_flag() {
    # Match --force as standalone, or -f in short flags (bundled or alone)
    # Patterns: --force, --force=anything, -f, -qf, -fu, etc.
    # Exclude: --force-with-lease (handled separately)
    if [[ "$FULL_COMMAND" =~ (^|[[:space:]])--force([[:space:]]|=|$) ]]; then
        return 0  # has --force
    fi
    if [[ "$FULL_COMMAND" =~ (^|[[:space:]])-[a-zA-Z]*f[a-zA-Z]*([[:space:]]|$) ]]; then
        return 0  # has -f (bundled or standalone)
    fi
    return 1  # no --force flag
}

# Helper: Check if command contains --force-with-lease
has_force_with_lease_flag() {
    # Match --force-with-lease, optionally with =refname
    if [[ "$FULL_COMMAND" =~ (^|[[:space:]])--force-with-lease(=[^[:space:]]+)?([[:space:]]|$) ]]; then
        return 0
    fi
    return 1
}

# Helper: Extract remote and branch from git push command
# Returns: "remote branch" or empty if can't determine
get_push_target() {
    # Simple extraction: git push <remote> <branch>
    # Handles: git push origin main, git push -f origin main, etc.
    local parts=($FULL_COMMAND)
    local remote=""
    local branch=""

    # Find first non-flag argument after "push"
    local found_push=false
    for part in "${parts[@]}"; do
        if [[ "$part" == "push" ]]; then
            found_push=true
            continue
        fi
        if $found_push; then
            # Skip flags
            if [[ "$part" =~ ^- ]]; then
                continue
            fi
            # First non-flag is remote
            if [[ -z "$remote" ]]; then
                remote="$part"
                continue
            fi
            # Second non-flag is branch
            if [[ -z "$branch" ]]; then
                branch="$part"
                break
            fi
        fi
    done

    echo "$remote $branch"
}

# =============================================================================
# DENY - These operations are completely blocked
# =============================================================================

# Block git reset --hard (destroys uncommitted changes)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+reset[[:space:]]+--hard ]]; then
    log_block "git reset --hard is FORBIDDEN. Use 'git reset --mixed' or 'git stash' to preserve changes."
fi

# Block --force/-f on ALL git push operations (too dangerous, use --force-with-lease)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+push ]] && has_force_flag; then
    log_block "Force push (--force/-f) is FORBIDDEN. Use --force-with-lease for safer force pushing."
fi

# Block git push upstream main/master (even non-force)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+push.*[[:space:]]upstream[[:space:]]+(main|master)([[:space:]]|$) ]]; then
    log_block "Pushing to upstream/main or upstream/master is FORBIDDEN. Push to origin or upstream feature branches."
fi

# Block --force-with-lease to main/master branches (any remote)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+push ]] && has_force_with_lease_flag; then
    target=$(get_push_target)
    if [[ "$target" =~ (main|master)$ ]]; then
        log_block "--force-with-lease to main/master branch is FORBIDDEN. Use feature branches for rebasing."
    fi
fi

# Block git branch -D (shorthand for force delete)
# Match: -D as standalone or bundled (e.g., -Dv)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+branch ]] && [[ "$FULL_COMMAND" =~ (^|[[:space:]])-[a-zA-Z]*D[a-zA-Z]*([[:space:]]|$) ]]; then
    log_block "git branch -D is FORBIDDEN. Use 'git branch -d' for safe deletion of merged branches."
fi

# Block git branch --force (any use of --force with branch)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+branch.*--force ]]; then
    log_block "git branch --force is FORBIDDEN. Force operations on branches must be done manually."
fi

# Block direct commits to main/master branch
if [[ "$FULL_COMMAND" =~ ^git[[:space:]]+commit ]]; then
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    if [[ "$CURRENT_BRANCH" == "main" ]] || [[ "$CURRENT_BRANCH" == "master" ]]; then
        log_block "Committing directly to $CURRENT_BRANCH is FORBIDDEN. Create a feature branch: git switch -c feature/name"
    fi
fi

# Block direct push to origin main/master (even non-force pushes)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+push.*origin[[:space:]]+(main|master)([[:space:]]|$) ]]; then
    log_block "Pushing directly to origin/main or origin/master is FORBIDDEN. Use feature branches and PRs."
fi

# Block --no-verify flag (skip hooks)
if [[ "$FULL_COMMAND" =~ --no-verify ]]; then
    log_block "--no-verify flag is FORBIDDEN. Git hooks must run for all commits and pushes."
fi

# Block git add --force (forcing ignored files)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+add ]] && has_force_flag; then
    log_block "git add --force is FORBIDDEN. Files are gitignored for a reason."
fi

# Block git rm (Ultra Safe: only allow --cached without --force)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+rm ]]; then
    # Check if --cached is present
    if [[ "$FULL_COMMAND" =~ --cached ]]; then
        # Allow --cached ONLY if no --force
        if has_force_flag || [[ "$FULL_COMMAND" =~ --force ]]; then
            log_block "git rm --cached --force is FORBIDDEN. Use 'git rm --cached' without --force."
        fi
        # Otherwise allow (git rm --cached without --force)
    else
        # Any git rm without --cached is blocked
        log_block "git rm is FORBIDDEN (deletes files from filesystem). Use 'git rm --cached' to unstage only."
    fi
fi

# Block git clean with -x or -X (deletes ignored/untracked files)
# Consolidates all 8 variations from deny list
if [[ "$FULL_COMMAND" =~ git[[:space:]]+clean ]] && [[ "$FULL_COMMAND" =~ -[a-zA-Z]*[xX] ]]; then
    log_block "git clean with -x or -X is FORBIDDEN. These delete ignored/untracked files irreversibly."
fi

# Block git config --global writes (set, unset, add, replace-all, etc.)
# Allow reads (--get, --list, -l)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+config[[:space:]]+--global ]]; then
    # Check if it's a read operation
    if [[ "$FULL_COMMAND" =~ (--get|--list)[[:space:]] ]] || [[ "$FULL_COMMAND" =~ [[:space:]]-l([[:space:]]|$) ]]; then
        # Allow reads
        exit 0
    else
        # All other global config operations require permission
        log_ask "git config --global modifications require permission. Read operations (--get, --list) are allowed."
    fi
fi

# =============================================================================
# ASK - These operations require explicit permission
# =============================================================================

# Ask before git stash drop (permanent deletion)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+stash[[:space:]]+drop ]]; then
    log_ask "git stash drop permanently deletes a stash. Confirm this is intentional."
fi

# Ask before git checkout -- (destructive, deprecated syntax)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+checkout[[:space:]]+-- ]]; then
    log_ask "git checkout -- is destructive and deprecated. Consider using 'git restore' instead."
fi

# Ask before git filter-branch (mass history rewriting, deprecated)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+filter-branch ]]; then
    log_ask "git filter-branch is dangerous and deprecated. Use git-filter-repo if truly needed."
fi

# Ask before git reflog delete/expire (removes recovery points)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+reflog[[:space:]]+(delete|expire) ]]; then
    log_ask "git reflog delete/expire removes recovery points for lost commits. Confirm this is intentional."
fi

# Ask before git remote remove (could break workflows)
if [[ "$FULL_COMMAND" =~ git[[:space:]]+remote[[:space:]]+(remove|rm) ]]; then
    log_ask "Removing a git remote may break workflows. Confirm this is intentional."
fi

# =============================================================================
# ALLOW - Command passed all safety checks
# =============================================================================

exit 0
