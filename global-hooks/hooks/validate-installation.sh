#!/bin/bash
# Validate that hooks are correctly installed and active

set -euo pipefail

HOOKS_FILE="${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json"

echo "=== Hook Installation Validation ==="

# Check hooks.json exists and is valid JSON
if ! jq empty "$HOOKS_FILE" 2>/dev/null; then
    echo "❌ FAIL: Invalid JSON in hooks.json"
    exit 1
fi
echo "✓ hooks.json is valid JSON"

# Check required structure
if ! jq -e '.hooks.PreToolUse' "$HOOKS_FILE" >/dev/null 2>&1; then
    echo "❌ FAIL: Missing PreToolUse in hooks.json"
    exit 1
fi
echo "✓ PreToolUse hooks defined"

# Check matcher syntax (no parentheses allowed)
if jq -r '.. | .matcher? // empty' "$HOOKS_FILE" | grep -q '('; then
    echo "❌ FAIL: Invalid matcher syntax (found parentheses)"
    exit 1
fi
echo "✓ Matcher syntax is correct"

# Check script references exist
jq -r '.. | .command? // empty' "$HOOKS_FILE" | while read -r cmd; do
    # Expand CLAUDE_PLUGIN_ROOT
    expanded="${cmd//\$\{CLAUDE_PLUGIN_ROOT\}/${CLAUDE_PLUGIN_ROOT}}"
    if [[ "$expanded" == *".sh"* ]] && [[ ! -x "$expanded" ]]; then
        echo "⚠ WARNING: Script not executable: $expanded"
    fi
done

echo "=== Validation Complete ==="
