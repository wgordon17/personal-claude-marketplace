#!/bin/bash
# prepare-commit-msg hook - Defense-in-depth git safety
# This hook CANNOT be bypassed with --no-verify
#
# Purpose: Detect when pre-commit hooks were bypassed and enforce critical safety checks
# Location: Should be installed to .git/hooks/prepare-commit-msg in each project
#
# Args: $1 = commit message file, $2 = commit source, $3 = commit SHA (for amend)

set -euo pipefail

# Exit early if not an interactive commit
# This hook should only validate regular commits, not merges/squashes/rebases
COMMIT_SOURCE="${2:-}"
if [[ "$COMMIT_SOURCE" == "merge" ]] || [[ "$COMMIT_SOURCE" == "squash" ]] || [[ "$COMMIT_SOURCE" == "commit" ]] || [[ -n "${GIT_REFLOG_ACTION:-}" ]]; then
    # Skip for: merge commits, squash commits, amend commits, and any rebase operations
    # GIT_REFLOG_ACTION is set during rebase/cherry-pick/revert
    exit 0
fi

# Color codes for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Get unique repository identifier (hash of absolute path)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || exit 0)

# Cross-platform sha256 command
if command -v sha256sum &>/dev/null; then
    REPO_HASH=$(echo "$REPO_ROOT" | sha256sum | cut -d' ' -f1 | head -c 16)
elif command -v shasum &>/dev/null; then
    # macOS uses shasum
    REPO_HASH=$(echo "$REPO_ROOT" | shasum -a 256 | cut -d' ' -f1 | head -c 16)
else
    echo -e "${YELLOW}⚠️  Warning: No sha256 tool found, defense-in-depth checks disabled${NC}" >&2
    exit 0
fi

# Marker file location
MARKER_DIR="$HOME/.cache/hook-checks"
MARKER_FILE="$MARKER_DIR/${REPO_HASH}.mark"

# Track if pre-commit was bypassed
PRE_COMMIT_BYPASSED=false
if [ ! -f "$MARKER_FILE" ]; then
    PRE_COMMIT_BYPASSED=true
    echo -e "${YELLOW}⚠️  Pre-commit hooks were bypassed (--no-verify detected)${NC}" >&2
    echo -e "${YELLOW}Running critical safety checks...${NC}" >&2
fi

# ============================================================================
# CRITICAL SAFETY CHECKS (unbypassable)
# ============================================================================

# Check 1: Block commits directly to main/master branches
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
if [[ "$CURRENT_BRANCH" == "main" ]] || [[ "$CURRENT_BRANCH" == "master" ]]; then
    echo -e "${RED}❌ BLOCKED: Direct commits to $CURRENT_BRANCH are FORBIDDEN${NC}" >&2
    echo "" >&2
    echo "Use feature branch workflow:" >&2
    echo "  git switch -c feature/descriptive-name" >&2
    echo "  # make your changes" >&2
    echo "  git commit -m \"feat(scope): description\"" >&2
    echo "  git push origin feature/descriptive-name" >&2
    echo "  gh pr create" >&2
    echo "" >&2
    echo "Emergency bypass: git checkout -b temp-branch && git commit" >&2
    exit 1
fi

# Check 2: Block commits with unresolved merge conflicts
if git diff --cached --name-only | xargs grep -l "^<<<<<<< HEAD" 2>/dev/null | head -1 | grep -q .; then
    CONFLICT_FILES=$(git diff --cached --name-only | xargs grep -l "^<<<<<<< HEAD" 2>/dev/null || true)
    echo -e "${RED}❌ BLOCKED: Unresolved merge conflicts detected${NC}" >&2
    echo "" >&2
    echo "Files with conflict markers:" >&2
    echo "$CONFLICT_FILES" | while read -r file; do
        echo "  - $file" >&2
    done
    echo "" >&2
    echo "Resolve conflicts and remove <<<<<<< HEAD markers before committing" >&2
    exit 1
fi

# Check 3: Validate commit message format (only if pre-commit was bypassed)
# If pre-commit ran, commit-msg hook would have validated this
COMMIT_MSG_FILE="$1"
if [ "$PRE_COMMIT_BYPASSED" = true ]; then
    # Read first line of commit message
    FIRST_LINE=$(head -1 "$COMMIT_MSG_FILE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    # Check Conventional Commits format: type(scope): description
    # Allow: feat, fix, docs, chore, refactor, test, perf, style, build, ci
    if ! echo "$FIRST_LINE" | grep -qE "^(feat|fix|docs|chore|refactor|test|perf|style|build|ci)(\(.+\))?!?:[[:space:]].+"; then
        echo -e "${RED}❌ BLOCKED: Invalid commit message format${NC}" >&2
        echo "" >&2
        echo "Conventional Commits format required:" >&2
        echo "  type(scope): description" >&2
        echo "" >&2
        echo "Valid types: feat, fix, docs, chore, refactor, test, perf, style, build, ci" >&2
        echo "Scope is optional but recommended" >&2
        echo "" >&2
        echo "Examples:" >&2
        echo "  feat(auth): adds password reset flow" >&2
        echo "  fix(api): prevents null pointer in handler" >&2
        echo "  docs: updates installation guide" >&2
        echo "" >&2
        echo "Your message:" >&2
        echo "  $FIRST_LINE" >&2
        exit 1
    fi
fi

# ============================================================================
# CLEANUP
# ============================================================================

# Always delete marker file after checking (one-time use)
# This ensures next commit will re-check if pre-commit ran
rm -f "$MARKER_FILE"

exit 0
