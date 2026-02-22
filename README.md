# Personal Claude Marketplace

Personal Claude Code plugins: LSP servers, code quality agents, development utilities, and git tools.

## Plugins

### LSP Plugins

| Plugin | Description | Execution | Docs |
|--------|-------------|-----------|------|
| pyright-uvx | Python language server | `uvx` (always latest) | [README](pyright-uvx/README.md) |
| vtsls-npx | TypeScript/JavaScript language server | `npx` (always latest) | [README](vtsls-npx/README.md) |
| gopls-go | Go language server | system `gopls` | [README](gopls-go/README.md) |
| vscode-html-css-npx | HTML/CSS language servers | `npx` (always latest) | [README](vscode-html-css-npx/README.md) |
| rust-analyzer-rustup | Rust language server | `rustup` (always latest) | [README](rust-analyzer-rustup/README.md) |

### Code Quality

| Plugin | Description | Components | Docs |
|--------|-------------|------------|------|
| code-quality | Architecture, security, QA, and performance agents with audit and orchestration skills | 4 agents, 6 skills | [README](code-quality/README.md) |

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

| Plugin | Description | Components | Docs |
|--------|-------------|------------|------|
| dev-essentials | LSP navigation, Python tooling, test execution, planning, and session management | 1 agent, 4 skills, 4 commands | [README](dev-essentials/README.md) |

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

| Plugin | Description | Components | Docs |
|--------|-------------|------------|------|
| git-tools | Git history, hooks, commit review, and contributing guide generation | 3 skills, 2 commands | [README](git-tools/README.md) |

**Skills:**
- `/git-history` - Git history manipulation (git-branchless)
- `/git-hooks-install` & `/git-hooks-uninstall` - Defense-in-depth git hooks

**Commands:**
- `/git-tools:review-commits` - AI-assisted commit review for PRs
- `/git-tools:contributing` - Generate/update CONTRIBUTING.md

### Development Guard

| Plugin | Description | Components | Docs |
|--------|-------------|------------|------|
| dev-guard | Tool selection policies, commit validation, and pre-push review | 3 hooks | [README](dev-guard/README.md) |

> **⚠️ Important:** Dev-guard's `"ask"` action uses Claude Code's JSON `hookSpecificOutput` protocol. This correctly overrides `permissions.allow` auto-approve rules in the CLI but is [not supported in VS Code](https://github.com/anthropics/claude-code/issues/13339). See the [dev-guard README](dev-guard/README.md#action-field) for details.

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

### For All Non-LSP Plugins

- **uv**: `brew install uv` or `pip install uv` — Required by dev-guard (all hooks use `uv run`), dev-essentials (test-runner, uv-python), and git-tools (hook installation)

### For LSP Plugins

- **uv**: Required by pyright-uvx (`uvx`)
- **Node.js/npm**: For npx-based servers (vtsls-npx, vscode-html-css-npx)
- **Go**: `brew install go` (for gopls)
- **Rust/rustup**: `brew install rustup` (for rust-analyzer)

### For Git Tools

- **[git-branchless](https://github.com/arxanas/git-branchless)**: `brew install git-branchless` — Required by `/git-history` skill and `/git-tools:review-commits` command
- **[pre-commit](https://pre-commit.com/)**: `uv tool install pre-commit` — Required by `/git-hooks-install` and `/git-hooks-uninstall` skills

### Verification

```bash
uv --version           # uv (required for all non-LSP plugins)
uvx --version          # uvx (for pyright-uvx)
npx --version          # npm (for npx-based LSP servers)
go version             # go (for gopls)
rustup --version       # rustup (for rust-analyzer)
git branchless --help  # git-branchless (for git-tools)
pre-commit --version   # pre-commit (for git-tools hooks)
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

## Ecosystem & Dependencies

Plugins in this marketplace have cross-dependencies, external tool requirements, and optional MCP server integrations. This section documents what each plugin needs for full functionality.

### Plugin Dependencies

**code-quality + dev-essentials** are tightly coupled and should be installed together:

- **code-quality** requires **dev-essentials** — The `/swarm` and `/unfuck` skills reference the `dev-essentials:test-runner` agent for verification phases. Without dev-essentials, final verification cannot spawn the correct agent type.
- **dev-essentials** requires **code-quality** — The `/incremental-planning` skill references `code-quality:architect`, `code-quality:security`, and `code-quality:qa` agents for expert consultation. Without code-quality, Phase 3 (Consult) cannot spawn these agents.
- **dev-essentials** requires at least one **LSP plugin** — The `/lsp-navigation` skill depends on an installed LSP plugin (pyright-uvx, vtsls-npx, gopls-go, etc.) to function.

**dev-guard** and **git-tools** are self-contained — no cross-plugin dependencies.

### External Tool Requirements

| Tool | Required By | Purpose |
|------|------------|---------|
| [`uv`](https://docs.astral.sh/uv/) | **All non-LSP plugins** | dev-guard hooks (`uv run`), test-runner, uv-python enforcement, hook installation |
| [`uv`](https://docs.astral.sh/uv/) | pyright-uvx | Runs Pyright via `uvx` |
| [`git-branchless`](https://github.com/arxanas/git-branchless) | git-tools | `/git-history` skill, `/git-tools:review-commits` command |
| [`pre-commit`](https://pre-commit.com/) | git-tools | `/git-hooks-install` and `/git-hooks-uninstall` skills |
| [Node.js/npm](https://nodejs.org/) | vtsls-npx, vscode-html-css-npx | Runs LSP servers via `npx` |
| [Go](https://go.dev/) | gopls-go | Runs gopls language server |
| [Rust/rustup](https://rustup.rs/) | rust-analyzer-rustup | Runs rust-analyzer |

### MCP Server Integrations

These MCP servers enhance functionality but are not required for core operation (except where noted):

| MCP Server | Plugin | Dependency | Purpose |
|------------|--------|------------|---------|
| **[Context7](https://github.com/upstash/context7)** | code-quality | **Hard** (for `/file-audit` library validation) | Library usage validation — deprecated APIs, wrong signatures. Listed in `/file-audit` allowed-tools header. |
| **[Context7](https://github.com/upstash/context7)** | git-tools | Soft | Informational reference for git-branchless documentation in `/git-history` and `/git-tools:review-commits`. |
| **[Serena](https://github.com/Agentic-Coding/serena)** | dev-essentials | Soft | `get_symbols_overview` for component-level understanding in `/incremental-planning` Phase 1. Alternative tools work. |
| **[Sequential-Thinking](https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking)** | dev-essentials | Soft | Reasoning about scope boundaries in `/incremental-planning` Phases 1 and 5. Reasoning works without it. |
| **[claude-mem](https://github.com/pchaganti/gx-claude-mem)** | dev-essentials | Soft | Search past work, decisions, and learnings in `/incremental-planning` Phase 1. Enhanced context, not required. |

### SuperClaude / Superpowers References

Some skills in code-quality (`/unfuck`) and dev-essentials (`/incremental-planning`) reference SuperClaude skills (`sc:index-repo`, `sc:analyze`, `sc:cleanup`, `sc:improve`, `sc:reflect`) and Superpowers patterns (`superpowers:verification-before-completion`, `superpowers:subagent-driven-development`). These are from a separate plugin system not distributed in this marketplace. The skills degrade gracefully without them — the references are informational and the skills use alternative approaches when SuperClaude is unavailable.

### Dependency Matrix

Rows = plugins, columns = dependencies. **HARD** = breaks without it. **soft** = degraded without it.

| Plugin | code-quality | dev-essentials | LSP plugins | Context7 | Serena | seq-thinking | claude-mem | uv | pre-commit | git-branchless | SuperClaude |
|--------|-------------|---------------|-------------|----------|--------|-------------|-----------|-----|-----------|----------------|-------------|
| **code-quality** | -- | HARD | soft | HARD | -- | -- | -- | soft | -- | -- | HARD |
| **dev-essentials** | HARD | -- | HARD | -- | soft | soft | soft | soft | soft | -- | soft |
| **git-tools** | -- | -- | -- | soft | -- | -- | -- | HARD | HARD | HARD | -- |
| **dev-guard** | -- | -- | -- | -- | -- | -- | -- | HARD | -- | -- | -- |
| **LSP plugins** | -- | -- | -- | -- | -- | -- | -- | HARD* | -- | -- | -- |

*pyright-uvx requires uv/uvx; other LSP plugins require npm/npx, Go, or Rust toolchains.

## License

MIT
