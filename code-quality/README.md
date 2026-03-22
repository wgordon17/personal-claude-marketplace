# Code Quality Plugin

Code quality agents, development utilities, and orchestration skills: architecture, security, QA, performance, test execution, code review, code simplification, LSP navigation, uv-python, incremental planning, roadmap lifecycle management, session management, deep-research, business-panel, file-audit, bug-investigation, unfuck, swarm, quality-gate, pr-review, map-reduce, speculative, reflect, and index-repo.

## Agents (7)

| Agent | Purpose | Model |
|-------|---------|-------|
| `code-quality:architect` | System architecture design and evaluation | Sonnet |
| `code-quality:security` | Application security review (OWASP) | Sonnet |
| `code-quality:qa` | Code quality, test strategy, tech debt | Sonnet |
| `code-quality:performance` | Performance profiling and optimization | Sonnet |
| `code-quality:test-runner` | Efficient test execution specialist | Haiku |
| `code-quality:code-reviewer` | Plan alignment, code quality, convention compliance, doc accuracy | Sonnet |
| `code-quality:code-simplifier` | Dead code removal, unnecessary abstraction cleanup, clarity improvement | Sonnet |

## Skills (17)

| Skill | Description | Type |
|-------|-------------|------|
| `/deep-research` | Multi-hop research (40+ sources) | Manual |
| `/business-panel` | Multi-stakeholder business analysis | Manual |
| `/file-audit` | Deep code quality audit system | Manual |
| `/bug-investigation` | Interactive bug hunting with background agents | PROACTIVE |
| `/unfuck` | Comprehensive one-shot repo cleanup | Manual |
| `/swarm` | Full agent team implementation via TeamCreate | Manual |
| `/quality-gate` | Multi-pass review with adversarial lenses, fresh-context subagents, and blocking gates | PROACTIVE |
| `/pr-review` | Multi-agent PR review with confidence scoring | Manual |
| `/map-reduce` | Parallelized workload processing with chunking, mapper agents, and reducer synthesis | Manual |
| `/speculative` | Competing implementations in isolated worktrees with judge selection | Manual |
| `/incremental-planning` | Planning workflow with per-task review and assumption surfacing | Manual |
| `/roadmap` | Stateful multi-plan phase sequencing with roadmap lifecycle management: detect existing roadmaps, update, cleanup completed work, status/drift report, or fresh creation | Manual |
| `/lsp-navigation` | Semantic code navigation via LSP | PROACTIVE |
| `/uv-python` | Python tooling enforcement (uv over pip) | PROACTIVE |
| `/test-runner` | Efficient test execution patterns | Manual |
| `/reflect` | Mid-task self-reflection checkpoint via Serena metacognitive tools | Manual |
| `/index-repo` | Repository indexing for token-efficient codebase orientation | Manual |

## Commands (4)

| Command | Description |
|---------|-------------|
| `/code-quality:session-start` | Load project context or initialize new project |
| `/code-quality:session-end` | Sync project memory before ending |
| `/code-quality:review-project` | Comprehensive TODO validation |
| `/code-quality:lsp-status` | Check LSP server status |

## Requirements

- **At least one LSP plugin** — Required for `/lsp-navigation` (pyright-uvx, vtsls-npx, gopls-go, etc.).
- **Context7 MCP** — Required for `/file-audit` library validation (deprecated APIs, wrong signatures).
- **Serena MCP** — Optional. Enhances `/incremental-planning` Phase 1 with `get_symbols_overview`.
- **Sequential-Thinking MCP** — Optional. Used in `/incremental-planning` and `/roadmap` for scope boundary reasoning.
- **claude-mem MCP** — Optional. Searches past work and decisions in `/incremental-planning` Phase 1.
- **Serena reflection tools** — Optional. Enables `/reflect` skill's metacognitive checkpoints (`think_about_task_adherence`, etc.). Enable via `included_optional_tools` in `~/.serena/serena_config.yml`.

## Installation

```bash
claude plugin install code-quality@personal-claude-marketplace
```
