---
name: guard-stats
description: Show actionable dev-guard metrics with improvement suggestions
arguments:
  - name: days
    description: "Number of days to look back (default: 7)"
    required: false
---

# Guard Stats

Analyze dev-guard metrics and suggest concrete improvements.

## Step 1: Collect Data

Run the guard-stats script:

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/guard-stats.py $DAYS
```

Where `$DAYS` is the days argument (default 7 if not provided).

## Step 2: Analyze and Recommend

After reviewing the output, provide actionable recommendations for each section:

**Guard Decisions:** For high-frequency blocks, read both `~/.claude/CLAUDE.md` (global) and the
project's `CLAUDE.md` to check whether the blocked behavior is already addressed. Most
high-frequency blocks (cat-file, grep, python, echo) are global agent behavior issues — suggest
changes to the global CLAUDE.md. Project-specific patterns go in the project CLAUDE.md. If a rule
is already addressed, suggest rewording for emphasis. If not, draft a specific addition.

**Trust Opportunities:** For rules asked 50+ times, recommend whether to trust permanently
(`/trust add <rule> --scope always`) or investigate why the rule triggers so often.

**RTK Compression:** If the expansion rate (full reads / compressions) exceeds 25%, identify which
commands are being re-read and suggest bypassing RTK for those commands. If expansion is low,
confirm RTK is delivering value.

**Stop Hook:** If error rate > 0%, diagnose why the LLM is unreachable (credentials, network,
timeout). If specific findings repeat, identify the pattern and suggest a workflow change.
If pass rate is 100% over a long period, question whether the LLM prompt is too lenient.

**Session Summaries:** If friction rate is high (>15%), suggest rule tuning. If specific projects
have disproportionate block rates, investigate project-specific issues.

## Step 3: Present

Show the raw stats output first, then your analysis with specific, implementable recommendations.
Do not give vague advice like "consider reviewing" — provide exact changes to make.
