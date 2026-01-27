#!/bin/bash
# post-commit hook - Set marker after successful commit
# This hook only runs if the commit succeeded (all pre-commit hooks passed)

set -euo pipefail

# Skip if running via pre-commit run (not during actual git commit)
# During a real commit, GIT_INDEX_FILE will be set to a temp file like .git/index.lock
# During pre-commit run, it will be unset or point to .git/index
if [[ -z "${GIT_INDEX_FILE:-}" ]] || [[ "${GIT_INDEX_FILE:-}" == ".git/index" ]] || [[ "${GIT_INDEX_FILE:-}" == *"/.git/index" ]]; then
    # Not running as part of a commit - don't set marker
    exit 0
fi

# Get unique repository identifier (hash of absolute path)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || exit 0)

# Cross-platform sha256 command
if command -v sha256sum &>/dev/null; then
    REPO_HASH=$(echo "$REPO_ROOT" | sha256sum | cut -d' ' -f1 | head -c 16)
elif command -v shasum &>/dev/null; then
    # macOS uses shasum
    REPO_HASH=$(echo "$REPO_ROOT" | shasum -a 256 | cut -d' ' -f1 | head -c 16)
else
    # No hash tool available, skip marker
    exit 0
fi

# Skip setting marker during rebase/merge/cherry-pick operations
# These operations skip prepare-commit-msg, so marker would never be consumed
if [[ -n "${GIT_REFLOG_ACTION:-}" ]]; then
    # Don't set marker during batch operations (rebase, merge, etc.)
    exit 0
fi

# Skip if git-branchless is managing the commit
if [[ -n "${GIT_BRANCHLESS_OPERATION:-}" ]]; then
    exit 0
fi

# Create marker directory and file
MARKER_DIR="$HOME/.cache/hook-checks"
mkdir -p "$MARKER_DIR"
MARKER_FILE="$MARKER_DIR/${REPO_HASH}.mark"

# Get the SHA of the commit that just succeeded
COMMIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

# Atomic write to prevent race conditions
# Format: timestamp|commit-sha
# This marker indicates: "Pre-commit hooks ran successfully for THIS specific commit"
MARKER_TMP="${MARKER_FILE}.$$"
echo "$(date +%s)|${COMMIT_SHA}" > "$MARKER_TMP"
mv "$MARKER_TMP" "$MARKER_FILE"

exit 0
