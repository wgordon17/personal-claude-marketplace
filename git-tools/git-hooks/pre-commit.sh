#!/bin/bash
# pre-commit marker setter - Set marker when pre-commit stage completes
# This runs as the LAST hook in the pre-commit stage (via pre-commit framework)

set -euo pipefail

# FAST EXIT: Skip batch operations that don't use the marker system
# These operations (cherry-pick, rebase, merge, revert) don't run pre-commit hooks,
# so there's no marker to create. They're validated separately by prepare-commit-msg.
if [[ -f ".git/CHERRY_PICK_HEAD" ]] || \
   [[ -f ".git/REVERT_HEAD" ]] || \
   [[ -f ".git/MERGE_HEAD" ]] || \
   [[ -d ".git/rebase-merge" ]] || \
   [[ -d ".git/rebase-apply" ]] || \
   [[ -f ".git/BISECT_LOG" ]] || \
   [[ -d ".git/sequencer" ]] || \
   [[ "${GIT_REFLOG_ACTION:-}" =~ "rebase" ]] || \
   [[ "${GIT_REFLOG_ACTION:-}" =~ "cherry-pick" ]] || \
   [[ -n "${GIT_BRANCHLESS_OPERATION:-}" ]]; then
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

# Create marker directory and file
MARKER_DIR="$HOME/.cache/hook-checks"
mkdir -p "$MARKER_DIR"
MARKER_FILE="$MARKER_DIR/${REPO_HASH}.mark"

# Get the SHA of the current HEAD (about to be amended/replaced)
COMMIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

# Atomic write to prevent race conditions
# Format: timestamp|commit-sha
# This marker indicates: "Pre-commit hooks completed successfully, commit can proceed"
MARKER_TMP="${MARKER_FILE}.$$"
echo "$(date +%s)|${COMMIT_SHA}" > "$MARKER_TMP"
mv "$MARKER_TMP" "$MARKER_FILE"

exit 0
