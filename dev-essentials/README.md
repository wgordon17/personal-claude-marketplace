# Dev Essentials Plugin

Essential development utilities: LSP navigation, Python tooling enforcement, test execution, incremental planning, and session management.

## Agent (1)

| Agent | Purpose | Model |
|-------|---------|-------|
| `dev-essentials:test-runner` | Efficient test execution specialist | Haiku |

## Skills (4)

| Skill | Description | Type |
|-------|-------------|------|
| `/lsp-navigation` | Semantic code navigation via LSP | PROACTIVE |
| `/uv-python` | Python tooling enforcement (uv over pip) | PROACTIVE |
| `/test-runner` | Efficient test execution patterns | Manual |
| `/incremental-planning` | Planning workflow (replaces native plan mode) | Manual |

## Commands (4)

| Command | Description |
|---------|-------------|
| `/dev-essentials:session-start` | Load project context or initialize new project |
| `/dev-essentials:session-end` | Sync project memory before ending |
| `/dev-essentials:review-project` | Comprehensive TODO validation |
| `/dev-essentials:lsp-status` | Check LSP server status |

## Requirements

- **code-quality plugin** — Required. The `/incremental-planning` skill uses `code-quality:architect`, `code-quality:security`, and `code-quality:qa` agents for expert consultation.
- **At least one LSP plugin** — Required for `/lsp-navigation` (pyright-uvx, vtsls-npx, gopls-go, etc.).
- **Serena MCP** — Optional. Enhances `/incremental-planning` Phase 1 with `get_symbols_overview` for component-level understanding. Alternative tools work.
- **Sequential-Thinking MCP** — Optional. Used in `/incremental-planning` for scope boundary reasoning. Reasoning works without it.
- **claude-mem MCP** — Optional. Searches past work and decisions in `/incremental-planning` Phase 1 for enhanced context.

## Installation

```bash
claude plugin install dev-essentials@personal-claude-marketplace
```
