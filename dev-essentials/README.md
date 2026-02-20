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

## Installation

```bash
claude plugin install dev-essentials@personal-claude-marketplace
```
