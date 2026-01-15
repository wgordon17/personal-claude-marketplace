#!/bin/bash
# Set marker file to indicate pre-commit hooks ran successfully
# This script runs as the LAST hook in pre-commit stage
# If it runs, all previous pre-commit hooks passed

set -euo pipefail

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

# Write timestamp to marker file
date +%s > "$MARKER_FILE"

exit 0
