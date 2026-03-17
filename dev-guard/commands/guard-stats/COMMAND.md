---
name: guard-stats
description: Show actionable metrics from dev-guard audit database
arguments:
  - name: days
    description: "Number of days to look back (default: 7)"
    required: false
---

# Guard Stats

Show actionable metrics from the dev-guard audit database.

## Your Task

Run the guard-stats script and present the output to the user:

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/guard-stats.py $DAYS
```

Where `$DAYS` is the days argument (default 7 if not provided).

Present the output as-is — it's already formatted. If any section shows warnings or actionable suggestions, highlight them.
