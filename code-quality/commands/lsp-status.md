---
description: Check configured LSP servers and their status
allowed-tools: Bash, Read, LSP
---

# LSP Status — Check Language Server Configuration

Verify that Language Server Protocol (LSP) servers are configured and available.

## Your Task

### Step 1: Check LSP Feature is Enabled

Verify the `ENABLE_LSP_TOOL` environment variable:

```bash
jq '.env.ENABLE_LSP_TOOL' ~/.claude/settings.json
```

**Expected:** `"1"` (enabled)

### Step 2: Check Enabled LSP Plugins

List all LSP-related plugins that are enabled:

```bash
jq '.enabledPlugins | to_entries | map(select(.value == true)) | map(.key) | map(select(test("lsp|pyright|vtsls|gopls|html|rust"; "i")))' ~/.claude/settings.json
```

### Step 3: Verify Prerequisites

Check that required tools are installed:

```bash
echo "=== Python LSP (pyright via uvx) ==="
if command -v uvx &> /dev/null; then
    echo "✓ uvx available: $(uvx --version 2>&1 | head -1)"
    echo "  Testing pyright..."
    uvx --from pyright pyright-langserver --version 2>&1 | head -1 || echo "  ⚠ pyright not cached yet (will download on first use)"
else
    echo "✗ uvx NOT installed - Python LSP unavailable"
    echo "  Install with: brew install uv"
fi

echo ""
echo "=== TypeScript/JS LSP (vtsls via npx) ==="
if command -v npx &> /dev/null; then
    echo "✓ npx available: $(npx --version)"
else
    echo "✗ npx NOT installed - TypeScript LSP unavailable"
    echo "  Install Node.js to get npx"
fi

echo ""
echo "=== Go LSP (gopls) ==="
if command -v gopls &> /dev/null; then
    echo "✓ gopls available: $(gopls version 2>&1 | head -1)"
else
    echo "✗ gopls NOT installed - Go LSP unavailable"
    echo "  Install with: go install golang.org/x/tools/gopls@latest"
fi

echo ""
echo "=== HTML/CSS LSP (vscode-langservers via npx) ==="
if command -v npx &> /dev/null; then
    echo "✓ npx available (HTML/CSS LSP will work)"
else
    echo "✗ npx NOT installed - HTML/CSS LSP unavailable"
fi

echo ""
echo "=== Rust LSP (rust-analyzer via rustup) ==="
if command -v rust-analyzer &> /dev/null; then
    echo "✓ rust-analyzer available: $(rust-analyzer --version 2>&1 | head -1)"
else
    echo "✗ rust-analyzer NOT installed - Rust LSP unavailable"
    echo "  Install with: rustup component add rust-analyzer"
fi
```

### Step 4: List LSP Plugin Configurations

Show the configuration for each LSP plugin:

```bash
echo "=== LSP Plugin Configurations ==="
for config in ~/.claude/plugins/cache/*/*/*/.lsp.json; do
    if [ -f "$config" ]; then
        plugin_name=$(echo "$config" | sed 's|.*/cache/[^/]*/\([^/]*\)/.*|\1|')
        echo ""
        echo "--- $plugin_name ---"
        cat "$config"
    fi
done
```

### Step 5: Test LSP Tool Availability

Attempt a simple LSP operation to verify the tool is accessible:

Use the LSP tool with `hover` operation on any Python file (if available):

```
LSP(operation="hover", filePath="[any .py file]", line=1, character=1)
```

### Step 6: Present Status Summary

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 LSP STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Feature Enabled: ✓/✗

Language Servers:
┌───────────────────┬─────────┬────────────────────────┐
│ Language          │ Status  │ Server                 │
├───────────────────┼─────────┼────────────────────────┤
│ Python            │ ✓/✗     │ pyright (via uvx)      │
│ TypeScript/JS     │ ✓/✗     │ vtsls (via npx)        │
│ Go                │ ✓/✗     │ gopls                  │
│ Rust              │ ✓/✗     │ rust-analyzer (rustup) │
│ HTML/CSS          │ ✓/✗     │ vscode-langservers     │
└───────────────────┴─────────┴────────────────────────┘

Available Operations:
• goToDefinition  - Find where symbol is defined
• findReferences  - Find all usages of symbol
• hover           - Get type info and documentation
• documentSymbol  - List symbols in file
• workspaceSymbol - Search symbols across workspace
• goToImplementation - Find implementations
• incomingCalls   - What calls this function
• outgoingCalls   - What this function calls

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Instructions for fixing any issues found]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Troubleshooting

### LSP Tool Not Available

1. Verify `ENABLE_LSP_TOOL` is set to `"1"` in `~/.claude/settings.json`
2. Restart Claude Code after changing settings

### Language Server Not Starting

1. Check debug logs: `tail -f ~/.claude/debug/latest`
2. Test server manually (see Step 3 commands)
3. Clear caches if needed:
   - pyright: `uv cache clean pyright`
   - npx tools: `rm -rf ~/.npm/_npx`

### Missing Plugins

If LSP plugins are not installed:

```bash
# LSP plugins should be installed from personal-claude-marketplace
# If you don't have access to this marketplace, check with the maintainer

# Install individual plugins
/plugin install pyright-uvx@personal-claude-marketplace
/plugin install vtsls-npx@personal-claude-marketplace
/plugin install gopls-go@personal-claude-marketplace
/plugin install rust-analyzer-rustup@personal-claude-marketplace
/plugin install vscode-html-css-npx@personal-claude-marketplace
```

## Documentation

- LSP navigation skill: `/lsp-navigation`
- CLAUDE.md LSP section: See "LSP Tooling — RECOMMENDED"
