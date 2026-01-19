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

# Detect rebase/cherry-pick/revert by checking for working directories
# These are the most reliable indicators across all git versions
if [ -d ".git/rebase-merge" ] || [ -d ".git/rebase-apply" ] || [ -f ".git/CHERRY_PICK_HEAD" ] || [ -f ".git/REVERT_HEAD" ]; then
    # Skip during batch operations
    exit 0
fi

# Also skip for merge and squash commits
if [[ "$COMMIT_SOURCE" == "merge" ]] || [[ "$COMMIT_SOURCE" == "squash" ]]; then
    exit 0
fi

# Additional safety: skip if GIT_REFLOG_ACTION is set
if [[ -n "${GIT_REFLOG_ACTION:-}" ]]; then
    exit 0
fi

# NOTE: We DO check amend commits (COMMIT_SOURCE="commit") to catch --no-verify on amends

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

# Validate marker: check existence, age, and commit SHA
PRE_COMMIT_BYPASSED=true
MARKER_VALID=false

if [ -f "$MARKER_FILE" ]; then
    # Read marker contents (format: timestamp|commit-sha)
    MARKER_CONTENTS=$(cat "$MARKER_FILE")
    MARKER_TIMESTAMP=$(echo "$MARKER_CONTENTS" | cut -d'|' -f1)
    MARKER_SHA=$(echo "$MARKER_CONTENTS" | cut -d'|' -f2)
    CURRENT_TIME=$(date +%s)
    MARKER_AGE=$((CURRENT_TIME - MARKER_TIMESTAMP))

    # Validation 1: Check marker age (must be < 30 seconds)
    if [ "$MARKER_AGE" -gt 30 ]; then
        echo -e "${YELLOW}⚠️  Stale marker detected (${MARKER_AGE}s old)${NC}" >&2
        echo -e "${YELLOW}This likely means an earlier commit was interrupted (Ctrl+C) or failed.${NC}" >&2
        echo "" >&2
        echo "To clear stale marker:" >&2
        echo "  rm -f ${MARKER_FILE}" >&2
        echo "" >&2
        # Treat as bypassed - run critical checks
    else
        # Validation 2: Check if marker SHA is parent of current commit
        # (Only applicable for amend - for new commits this check doesn't apply)
        if git merge-base --is-ancestor "$MARKER_SHA" HEAD 2>/dev/null || [ "$MARKER_SHA" = "unknown" ]; then
            # Marker is valid - pre-commit ran successfully
            MARKER_VALID=true
            PRE_COMMIT_BYPASSED=false
        else
            echo -e "${YELLOW}⚠️  Marker SHA mismatch (expected ancestor of HEAD)${NC}" >&2
        fi
    fi

    # Delete marker regardless of validity (one-time use)
    rm -f "$MARKER_FILE"
fi

# If marker was invalid or missing, block the commit
if [ "$MARKER_VALID" = false ]; then
    echo -e "${RED}❌ BLOCKED: --no-verify flag is FORBIDDEN${NC}" >&2
    echo "" >&2
    echo "Pre-commit hooks must run for all commits." >&2
    echo "This prevents accidental bypass of linting, tests, and security checks." >&2
    echo "" >&2
    echo "If you absolutely must commit without pre-commit:" >&2
    echo "  1. Fix the issue pre-commit is catching" >&2
    echo "  2. Or temporarily disable this hook:" >&2
    echo "     mv .git/hooks/prepare-commit-msg .git/hooks/prepare-commit-msg.disabled" >&2
    exit 1
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

exit 0
