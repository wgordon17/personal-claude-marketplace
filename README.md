# Personal Claude Marketplace

Personal Claude Code plugins: LSP servers, code quality agents, development utilities, and git tools.

## Plugins

### LSP Plugins

| Plugin | Description | Execution |
|--------|-------------|-----------|
| pyright-uvx | Python language server | `uvx` (always latest) |
| vtsls-npx | TypeScript/JavaScript language server | `npx` (always latest) |
| gopls-go | Go language server | system `gopls` |
| vscode-html-css-npx | HTML/CSS language servers | `npx` (always latest) |
| rust-analyzer-rustup | Rust language server | `rustup` (always latest) |

### Code Quality

| Plugin | Description | Components |
|--------|-------------|------------|
| code-quality | Architecture, security, QA, and performance agents with audit and orchestration skills | 4 agents, 6 skills |

**Agents:**
- `code-quality:architect` - System architecture specialist (design, technology choices, refactoring)
- `code-quality:security` - Application security specialist (OWASP, auth, vulnerability detection)
- `code-quality:qa` - Code quality & QA specialist (test strategy, maintainability, tech debt)
- `code-quality:performance` - Performance engineering specialist (profiling, optimization, bottlenecks)

**Skills:**
- `/deep-research` - Multi-hop research (40+ sources, multi-perspective analysis)
- `/business-panel` - Multi-stakeholder business impact analysis
- `/file-audit` - Deep code quality audit system
- `/bug-investigation` - PROACTIVE interactive bug hunting with background agents
- `/unfuck` - Comprehensive one-shot repo cleanup
- `/swarm` - Full agent team implementation via TeamCreate

### Development Essentials

| Plugin | Description | Components |
|--------|-------------|------------|
| dev-essentials | LSP navigation, Python tooling, test execution, planning, and session management | 1 agent, 4 skills, 4 commands |

**Agent:** `dev-essentials:test-runner` - Efficient test execution specialist

**Skills:**
- `/lsp-navigation` - PROACTIVE semantic code navigation
- `/uv-python` - PROACTIVE Python tooling enforcement (uv over pip)
- `/test-runner` - Efficient test execution patterns
- `/incremental-planning` - Incremental planning workflow (replaces native plan mode)

**Commands:**
- `/dev-essentials:session-start` - Load project context or initialize
- `/dev-essentials:session-end` - Sync project memory before ending
- `/dev-essentials:review-project` - Comprehensive TODO validation
- `/dev-essentials:lsp-status` - Check LSP server status

### Git Tools

| Plugin | Description | Components |
|--------|-------------|------------|
| git-tools | Git history, hooks, commit review, and contributing guide generation | 3 skills, 2 commands |

**Skills:**
- `/git-history` - Git history manipulation (git-branchless)
- `/git-hooks-install` & `/git-hooks-uninstall` - Defense-in-depth git hooks

**Commands:**
- `/git-tools:review-commits` - AI-assisted commit review for PRs
- `/git-tools:contributing` - Generate/update CONTRIBUTING.md

### Development Guard

| Plugin | Description | Components |
|--------|-------------|------------|
| dev-guard | Tool selection policies, commit validation, and pre-push review | 3 hooks |

**Hooks:**
- **PreToolUse: Tool Selection Guard** - Enforces native tool usage, Python/Rust tooling, git safety, URL fetch guard
- **PreToolUse: Pre-push Review** - Commit summary and suggestions when pushing 3+ commits
- **PostToolUse: Commit Validation** - Conventional Commits format enforcement

## Installation

```bash
# Add marketplace
claude plugin marketplace add wgordon17/personal-claude-marketplace

# Install non-LSP plugins
claude plugin install dev-guard@personal-claude-marketplace
claude plugin install code-quality@personal-claude-marketplace
claude plugin install dev-essentials@personal-claude-marketplace
claude plugin install git-tools@personal-claude-marketplace

# Install LSP plugins (pick what you need)
claude plugin install pyright-uvx@personal-claude-marketplace
claude plugin install vtsls-npx@personal-claude-marketplace
claude plugin install gopls-go@personal-claude-marketplace
claude plugin install vscode-html-css-npx@personal-claude-marketplace
claude plugin install rust-analyzer-rustup@personal-claude-marketplace
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

## License

MIT
