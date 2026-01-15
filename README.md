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

### Productivity Plugins

| Plugin | Description | Components |
|--------|-------------|------------|
| global-skills | Code quality & tooling skills | 7 skills |
| global-commands | Session & project management | 6 commands |
| global-hooks | Git safety & validation | 3 hooks |

#### global-skills

**Skills included:**
- `/file-audit` - Deep code quality audit system
- `/git-history` - Git history manipulation (git-branchless)
- `/git-hooks-install` & `/git-hooks-uninstall` - Git hooks utilities
- `/lsp-navigation` - PROACTIVE semantic code navigation
- `/test-runner` - Efficient test execution patterns
- `/uv-python` - PROACTIVE Python tooling enforcement

#### global-commands

**Commands included:**
- `/contributing` - Generate/update CONTRIBUTING.md
- `/lsp-status` - Check LSP server status
- `/review-commits` - AI-assisted commit review for PRs
- `/review-project` - Comprehensive TODO validation
- `/session-end` - Sync project memory before ending
- `/session-start` - Load project context or initialize

#### global-hooks

**Hooks included:**
- **PreToolUse**: Pre-push review validation
- **PostToolUse**: Commit message validation (Conventional Commits)
- Git safety checks for destructive operations

## Installation

```bash
# Add marketplace (GitHub)
claude plugin marketplace add wgordon17/private-claude-marketplace

# Install LSP plugins
claude plugin install pyright-uvx@private-claude-marketplace
claude plugin install vtsls-npx@private-claude-marketplace
claude plugin install gopls-go@private-claude-marketplace
claude plugin install vscode-html-css-npx@private-claude-marketplace

# Install agent plugins
claude plugin install project-dev@private-claude-marketplace
claude plugin install test-execution@private-claude-marketplace

# Install productivity plugins
claude plugin install global-skills@private-claude-marketplace
claude plugin install global-commands@private-claude-marketplace
claude plugin install global-hooks@private-claude-marketplace
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

wgordon17 - January 2026
