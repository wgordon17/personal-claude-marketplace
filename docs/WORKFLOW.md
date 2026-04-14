# Recommended Skill Workflow

## Primary Workflow

```
deep-research ──► incremental-planning ──► test-plan ──► plan-review ──► fix
                                                                          │
                  ┌───────────────────────────────────────────────────────┘
                  ▼
              roadmap (if multi-plan) ──► swarm ──► pr-review ──► fix
```

| Step | Type | What it does |
|------|------|-------------|
| deep-research | skill | Background agents + web search for thorough investigation |
| incremental-planning | skill | Interview-driven plan file in `hack/plans/` |
| test-plan | skill | UAT scenarios and acceptance criteria |
| plan-review | skill | 6 parallel reviewers evaluate the plan |
| fix | skill | Addresses findings from any review skill |
| roadmap | skill | Sequences multiple plans into phases |
| swarm | skill | 21+ agent pipeline: architect through verification |
| pr-review | skill | 6 parallel reviewers on a PR |
| quality-gate | skill | Multi-pass verification — runs in swarm Phase 7 and standalone |

## Side Workflows

- **bug-investigation ──► fix** — Report bugs interactively; background agents investigate in parallel
- **unfuck** — One-shot repo cleanup swarm (dead code, AI slop, duplication)
- **speculative** — Competing implementations in isolated worktrees; judge picks winner
- **file-audit ──► map-reduce** — Parallel codebase analysis for 20+ file workloads
