# Private Claude Marketplace

Private Claude Code plugins: LSP servers (uvx/npx) and custom agents.

## Plugins

### LSP Plugins

| Plugin | Description | Execution |
|--------|-------------|-----------|
| pyright-uvx | Python language server | `uvx` (always latest) |
| vtsls-npx | TypeScript/JavaScript language server | `npx` (always latest) |
| gopls-go | Go language server | system `gopls` |
| vscode-html-css-npx | HTML/CSS language servers | `npx` (always latest) |

### Agent Plugins

| Plugin | Description | Agents |
|--------|-------------|--------|
| project-dev | Project feature development agents | 10 agents |
| test-execution | Test execution patterns | test-runner |

## Installation

```bash
# Add marketplace
claude plugin marketplace add ~/Projects/personal/private-claude-marketplace

# Install all plugins
claude plugin install pyright-uvx@private-claude-marketplace
claude plugin install vtsls-npx@private-claude-marketplace
claude plugin install gopls-go@private-claude-marketplace
claude plugin install vscode-html-css-npx@private-claude-marketplace
claude plugin install project-dev@private-claude-marketplace
claude plugin install test-execution@private-claude-marketplace
```

## Prerequisites

### For LSP Plugins

- **uv**: `brew install uv` or `pip install uv`
- **Node.js/npm**: For npx-based servers
- **Go**: `brew install go` (for gopls)

### Verification

```bash
uvx --version          # uv version
npx --version          # npm version
go version             # go version
```

## LSP Benefits

These plugins use `uvx` and `npx` to run language servers on-demand:

- **Always up-to-date**: Gets latest version on each run
- **No global pollution**: No `npm install -g` or `pip install --user`
- **Reproducible**: Same command works everywhere
- **Clean environment**: Tools cached locally, not installed globally

## Troubleshooting

### LSP Server Not Starting

```bash
# Check debug logs
tail -f ~/.claude/debug/latest

# Test servers manually
uvx --from pyright pyright-langserver --version
npx -y @vtsls/language-server --version
gopls version
npx -y vscode-langservers-extracted vscode-html-language-server --version
```

### Clear Caches

```bash
# uvx cache
uv cache clean

# npx cache
rm -rf ~/.npm/_npx
```

## Author

wgordon - January 2026
