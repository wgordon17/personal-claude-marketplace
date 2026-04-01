# Dependency Evaluation Criteria

Use this checklist before implementing custom code. If a well-maintained library exists that
solves the problem, prefer it over custom implementation unless the library introduces more
complexity than it removes.

## When to Search

Search for existing libraries when:
- Building functionality that is not core product differentiation
- Implementing well-known algorithms, protocols, or patterns
- The implementation would exceed ~100 lines of non-trivial logic
- The domain has known edge cases that mature libraries handle (crypto, date/time, parsing)

## Evaluation Criteria

**CRITICAL: Use the actual current date for all recency checks.** The current date is available
from system context. Do NOT rely on training data for date comparisons -- training data creates
a false sense of recency. A library last updated in 2024 is NOT "recent" if today is 2026.
Always compare against today's actual date, not your training cutoff.

### Must-Have (all required)

| Criterion | Threshold | How to Check |
|---|---|---|
| **Recent commits** | Within last 6 months from TODAY | GitHub API / repo page, compare commit dates against current date |
| **Recent release** | Within last 12 months from TODAY | GitHub releases / package registry, compare against current date |
| **Compatible license** | MIT, Apache 2.0, BSD, ISC, or project-compatible | Package metadata, LICENSE file |
| **No critical CVEs** | Zero unpatched critical/high CVEs | `npm audit` / `pip audit` / GitHub security advisories |

### Should-Have (strong preference, not absolute)

| Criterion | Threshold | How to Check |
|---|---|---|
| **Popularity** | >500 GitHub stars OR >10K weekly downloads | GitHub stars, npm/PyPI download stats |
| **Maintenance signals** | Issues responded to within 30 days, PRs reviewed | GitHub Issues/PRs activity |
| **Documentation** | README with usage examples, API reference exists | Repo inspection |
| **Test coverage** | CI passing, test suite exists | GitHub Actions / CI badges |
| **Minimal dependencies** | Fewer transitive deps is better | Dependency tree inspection |

### Red Flags (prefer custom code if present)

- Single maintainer with no recent activity
- Last release >2 years ago (abandoned)
- >100 open issues with no maintainer response
- Heavy transitive dependency tree (>20 deps for a focused library)
- Requires significant wrapper code to integrate (negates the benefit)
- Actively hostile to downstream users (frequent breaking changes without migration path)

## Decision Framework

```
1. Does a library exist?
   NO  → Build custom (document why in commit message)
   YES → Continue

2. Does it pass all Must-Have criteria?
   NO  → Build custom (document which criterion failed)
   YES → Continue

3. Does integrating it add more complexity than building custom?
   YES → Build custom (document the complexity trade-off)
   NO  → Use the library

4. Document the decision in the architect plan or commit message:
   "Using [library] because [reason]. Evaluated [N] alternatives."
   OR
   "Building custom because [reason]. No library met criteria: [specifics]."
```

## Date Verification Protocol

When evaluating a library's recency, follow this exact sequence:

1. Determine today's date from system context (e.g., `date +%Y-%m-%d`)
2. Find the library's most recent commit date and most recent release date
3. Calculate the actual difference in months/days
4. Compare against thresholds: commits within 6 months, releases within 12 months
5. State the comparison explicitly: "Last commit: [date]. Today: [date]. Gap: [N] months.
   Threshold: 6 months. Result: [PASS/FAIL]."

Do NOT say "recently updated" or "actively maintained" without performing this calculation.
