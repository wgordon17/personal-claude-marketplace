# Personal Claude Marketplace

Personal Claude Code plugins: LSP servers (uvx/npx) and custom agents.

## Plugins

### LSP Plugins

| Plugin | Description | Execution |
|--------|-------------|-----------|
| pyright-uvx | Python language server | `uvx` (always latest) |
| vtsls-npx | TypeScript/JavaScript language server | `npx` (always latest) |
| gopls-go | Go language server | system `gopls` |
| vscode-html-css-npx | HTML/CSS language servers | `npx` (always latest) |
| rust-analyzer-rustup | Rust language server | `rustup` (always latest) |

### Agent Plugins

| Plugin | Description | Agents |
|--------|-------------|--------|
| project-dev | Project feature development agents | 10 agents |
| test-execution | Test execution patterns | test-runner |
| superclaude | SuperClaude specialized agents + research skills | 4 agents, 2 skills |

#### superclaude

**Agents included:**
- `superclaude:architect` - System architecture specialist (design, technology choices, refactoring)
- `superclaude:security` - Application security specialist (OWASP, auth, vulnerability detection)
- `superclaude:qa` - Code quality & QA specialist (test strategy, maintainability, tech debt)
- `superclaude:performance` - Performance engineering specialist (profiling, optimization, bottlenecks)

**Skills included:**
- `/deep-research` - 5-hop deep research mode (40+ sources, multi-perspective analysis)
- `/business-panel` - Multi-stakeholder business impact analysis (CTO, PM, Finance, Security views)

### Productivity Plugins

| Plugin | Description | Components |
|--------|-------------|------------|
| global-skills | Code quality & tooling skills | 11 skills |
| global-commands | Session & project management | 6 commands |
| global-hooks | Git safety & validation | 2 hooks + 1 utility |

#### global-skills

**Skills included:**
- `/bug-investigation` - PROACTIVE interactive bug hunting with background agents
- `/file-audit` - Deep code quality audit system
- `/git-history` - Git history manipulation (git-branchless)
- `/git-hooks-install` & `/git-hooks-uninstall` - Git hooks utilities
- `/incremental-planning` - Incremental planning workflow (replaces native plan mode)
- `/lsp-navigation` - PROACTIVE semantic code navigation
- `/test-runner` - Efficient test execution patterns
- `/swarm` - Full agent team implementation via TeamCreate
- `/unfuck` - Comprehensive one-shot repo cleanup
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
claude plugin marketplace add wgordon17/personal-claude-marketplace

# Install LSP plugins
claude plugin install pyright-uvx@personal-claude-marketplace
claude plugin install vtsls-npx@personal-claude-marketplace
claude plugin install gopls-go@personal-claude-marketplace
claude plugin install vscode-html-css-npx@personal-claude-marketplace
claude plugin install rust-analyzer-rustup@personal-claude-marketplace

# Install agent plugins
claude plugin install project-dev@personal-claude-marketplace
claude plugin install test-execution@personal-claude-marketplace
claude plugin install superclaude@personal-claude-marketplace

# Install productivity plugins
claude plugin install global-skills@personal-claude-marketplace
claude plugin install global-commands@personal-claude-marketplace
claude plugin install global-hooks@personal-claude-marketplace
```

## Prerequisites

### For LSP Plugins

- **uv**: `brew install uv` or `pip install uv`
- **Node.js/npm**: For npx-based servers
- **Go**: `brew install go` (for gopls)
- **Rust/rustup**: `brew install rustup` (for rust-analyzer)

### Verification

```bash
uvx --version          # uv version
npx --version          # npm version
go version             # go version
rustup --version       # rustup version
rust-analyzer --version # rust-analyzer version
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
rust-analyzer --version
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
