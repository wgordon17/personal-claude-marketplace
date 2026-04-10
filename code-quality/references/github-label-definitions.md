# GitHub Label Definitions

Shared label definitions for issue tracking integration. Referenced by both
`/incremental-planning` (Phase 6 issue creation) and `/swarm` (Phase 7 completion).

Centralizing definitions here avoids duplication and ensures label changes propagate
to all consumers.

## Label Table

Colors are bare hex (no `#` prefix) for direct use with `gh label create --color`.

| Label | Description | Color (bare hex) | Maps from |
|-------|-------------|------------------|-----------|
| `enhancement` | New feature or capability | `a2eeef` | `feat` branch prefix |
| `bug` | Something isn't working | `d73a4a` | `fix` branch prefix |
| `documentation` | Documentation improvement | `0075ca` | `docs` branch prefix |
| `refactor` | Code restructure, no behavior change | `d4c5f9` | `refactor` branch prefix |
| `chore` | Maintenance or config | `ededed` | `chore` branch prefix |
| `in-progress` | Work actively underway | `fbca04` | Applied at swarm completion (Phase 7) |

## Usage

**Create-if-missing pattern** (avoids overwriting existing repo label customizations):
```bash
gh label create <name> --description "<desc>" --color "<hex>" --repo <owner/repo> 2>/dev/null || true
```

**Fallback for unrecognized branch prefixes:** If the branch prefix does not match any
row in the table (e.g., `test/`, `perf/`, `ci/`, `style/`, or freeform branch names),
create the issue without a label — skip the label creation step entirely.
