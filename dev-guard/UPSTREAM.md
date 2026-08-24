# Upstream Tracking

| Field | Value |
|-------|-------|
| **Source repo** | [JetBrains/benjamin-plus-skill](https://github.com/JetBrains/benjamin-plus-skill) |
| **Source path** | `injected-instruction.md` |
| **License** | MIT |
| **Vendored SHA** | `b9c4ba62df2e1d946218932ef8016bced0d972b1` |
| **Last synced** | `2026-08-24` |

This file is updated automatically by the `sync-token-efficiency` GitHub Actions workflow.

Rules 1-3's examples are rewritten on every sync to avoid recommending shell tools (`head`/`tail`/`cat`/bare `python`) this repo's own `dev-guard/hooks/tool-selection-guard.py` blocks, in favor of the Read tool / `uv run` — see `.github/scripts/patch-token-efficiency.sh`.

Future `dev-guard` version bumps triggered purely by upstream content syncs are content-only, not guard-logic changes — the commit-type prefix (`chore(dev-guard): syncs...` vs. `feat(dev-guard): ...`) is the reader-facing signal distinguishing the two.
