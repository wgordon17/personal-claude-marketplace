# Code Quality Plugin

Code quality agents and orchestration skills for architecture, security, QA, performance review, auditing, and comprehensive cleanup.

## Agents (4)

| Agent | Purpose | Model |
|-------|---------|-------|
| `code-quality:architect` | System architecture design and evaluation | Sonnet |
| `code-quality:security` | Application security review (OWASP) | Sonnet |
| `code-quality:qa` | Code quality, test strategy, tech debt | Sonnet |
| `code-quality:performance` | Performance profiling and optimization | Sonnet |

## Skills (6)

| Skill | Description | Type |
|-------|-------------|------|
| `/deep-research` | Multi-hop research (40+ sources) | Manual |
| `/business-panel` | Multi-stakeholder business analysis | Manual |
| `/file-audit` | Deep code quality audit system | Manual |
| `/bug-investigation` | Interactive bug hunting with background agents | PROACTIVE |
| `/unfuck` | Comprehensive one-shot repo cleanup | Manual |
| `/swarm` | Full agent team implementation via TeamCreate | Manual |

## Requirements

- **dev-essentials plugin** — Required. The `/swarm` and `/unfuck` skills use the `dev-essentials:test-runner` agent for verification phases.
- **Context7 MCP** — Required for `/file-audit` library validation (deprecated APIs, wrong signatures). Listed in `/file-audit` allowed-tools.
- **SuperClaude** — The `/unfuck` skill references SuperClaude skills (`sc:index-repo`, `sc:analyze`, etc.) from a separate plugin system. These are informational — the skill degrades gracefully without them.

## Installation

```bash
claude plugin install code-quality@personal-claude-marketplace
```
