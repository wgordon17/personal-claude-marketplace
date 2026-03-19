# LESSONS.md Template

Structured learning system for capturing principle-level insights across sessions.
Lives at `hack/LESSONS.md` (or equivalent memory directory). Created on first use.

## File Format

Each lesson is a single line with this format:

```
- [Category] Pattern observed → What to do differently → Why it matters (YYYY-MM-DD)
```

### Categories

| Category | Covers |
|----------|--------|
| Cross-Cutting | Patterns that apply across multiple domains |
| Architecture | System design, component boundaries, dependencies |
| Security | Auth, data protection, trust boundaries, secrets |
| Quality | Code quality, review patterns, testing strategies |
| Implementation | Coding patterns, tool usage, debugging approaches |
| Testing | Test strategy, coverage gaps, flaky test patterns |
| Planning | Estimation, scope, decomposition, prioritization |

### Rules

1. **Principle-level only.** No implementation details, no code snippets, no file paths.
   - Good: "Complex domain tasks benefit from probe-first design"
   - Bad: "Use `asyncio.gather()` for concurrent API calls in `src/api/client.py`"

2. **Session-dated.** Every lesson includes the date it was captured.

3. **Category-tagged.** Every lesson has exactly one category tag.

4. **Supersession.** When a newer lesson contradicts an older one:
   - Mark the old lesson: `[SUPERSEDED by YYYY-MM-DD]`
   - Keep both for history (the old one shows what changed)

5. **Size management.** Archive lessons that haven't been relevant in 10+ sessions:
   - Move to `## Archived` section at bottom
   - Include note: "Archived YYYY-MM-DD — not referenced in 10+ sessions"

### Example File

```markdown
# LESSONS

## Active

- [Architecture] Shared mutable state between plugins causes subtle ordering bugs → Use message passing or explicit dependency injection → Eliminates a class of hard-to-reproduce failures (2026-03-15)
- [Security] Pre-implementation security review catches design-level vulnerabilities that post-implementation review misses → Run security design review on architect plans, not just code → Cheaper to fix in design phase (2026-03-16)
- [Quality] Fresh-context reviewers catch issues that same-context reviewers miss due to anchoring → Always use Layer 2 subagent review, never skip → The cost is low, the catch rate is high (2026-03-15)
- [Planning] [SUPERSEDED by 2026-03-18] Light planning sufficient for multi-file changes with clear requirements → Use full planning for all multi-file changes → Requirements are never as clear as they seem (2026-03-10)
- [Planning] Multi-file changes benefit from full planning even when requirements seem clear → Clarifying questions reveal hidden complexity → Prevents rework from missed requirements (2026-03-18)

## Archived

- [Implementation] Archived 2026-03-15 — not referenced in 10+ sessions: Use polling instead of webhooks for external service integration → Webhooks require public endpoint and retry handling → Polling is simpler for low-frequency checks (2026-01-05)
```
