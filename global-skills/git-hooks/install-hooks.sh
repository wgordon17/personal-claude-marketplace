#!/bin/bash
# Install defense-in-depth git hooks for a project
# Usage: cd /path/to/project && ~/.claude/scripts/git-hooks/install-hooks.sh

set -euo pipefail

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "Installing defense-in-depth git hooks..."

# Check if we're in a git repository
if [ ! -d .git ]; then
    echo -e "${RED}❌ Error: Not a git repository${NC}"
    echo "Run this script from the root of your git project"
    exit 1
fi

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOKS_DIR="$REPO_ROOT/.git/hooks"

# Verify hooks directory exists
if [ ! -d "$HOOKS_DIR" ]; then
    echo -e "${RED}❌ Error: .git/hooks/ directory not found${NC}"
    exit 1
fi

# Source scripts location
SCRIPTS_DIR="$HOME/.claude/scripts/git-hooks"

if [ ! -f "$SCRIPTS_DIR/prepare-commit-msg.sh" ]; then
    echo -e "${RED}❌ Error: Hook scripts not found at $SCRIPTS_DIR${NC}"
    echo "Expected: prepare-commit-msg.sh, set-precommit-marker.sh"
    exit 1
fi

# ============================================================================
# Install prepare-commit-msg hook
# ============================================================================

PREPARE_HOOK="$HOOKS_DIR/prepare-commit-msg"

# Check if hook already exists (git-branchless or other)
if [ -f "$PREPARE_HOOK" ] && [ ! -L "$PREPARE_HOOK" ]; then
    echo -e "${YELLOW}⚠️  Existing prepare-commit-msg hook found${NC}"
    echo "Backing up to: prepare-commit-msg.backup"
    cp "$PREPARE_HOOK" "$PREPARE_HOOK.backup"
fi

# Create symlink to our script
ln -sf "$SCRIPTS_DIR/prepare-commit-msg.sh" "$PREPARE_HOOK"
chmod +x "$PREPARE_HOOK"

echo -e "${GREEN}✓${NC} Installed prepare-commit-msg hook"

# ============================================================================
# Verify installation
# ============================================================================

echo ""
echo "Verifying installation..."

# Check prepare-commit-msg
if [ -x "$PREPARE_HOOK" ]; then
    echo -e "${GREEN}✓${NC} prepare-commit-msg is executable"
else
    echo -e "${RED}✗${NC} prepare-commit-msg is not executable"
    exit 1
fi

# Check if pre-commit framework is configured
if [ -f ".pre-commit-config.yaml" ]; then
    echo -e "${GREEN}✓${NC} pre-commit framework detected"

    # Check if marker-setter hook is configured
    if grep -q "set-precommit-marker" .pre-commit-config.yaml; then
        echo -e "${GREEN}✓${NC} Marker-setter hook configured in .pre-commit-config.yaml"
    else
        echo -e "${YELLOW}⚠️  Marker-setter hook NOT configured in .pre-commit-config.yaml${NC}"
        echo ""
        echo "Add this to your .pre-commit-config.yaml (as the LAST hook):"
        echo ""
        echo "  - repo: local"
        echo "    hooks:
      - id: set-precommit-marker"
        echo "        name: Set pre-commit success marker"
        echo "        entry: ~/.claude/scripts/git-hooks/set-precommit-marker.sh"
        echo "        language: system"
        echo "        always_run: true"
        echo "        pass_filenames: false"
        echo "        stages: [pre-commit]"
        echo ""
    fi
else
    echo -e "${YELLOW}⚠️  No .pre-commit-config.yaml found${NC}"
    echo "The prepare-commit-msg hook will still work, but marker detection won't function"
fi

echo ""
echo -e "${GREEN}✓ Installation complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Add marker-setter hook to .pre-commit-config.yaml (see above)"
echo "  2. Run: pre-commit install (or: uv run pre-commit install)"
echo "  3. Test: git commit --allow-empty -m \"test: verify hooks\""
