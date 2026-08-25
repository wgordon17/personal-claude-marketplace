# vertex-log-monitor

Surfaces whether **Vertex AI is logging your prompts and outputs** for the
project Claude Code is running against — so "is anything capturing what I type?"
is a glance, not an investigation.

## Why

Claude Code on Vertex AI (`CLAUDE_CODE_USE_VERTEX=1`) sends every prompt through
a GCP project. Two independent, opt-in mechanisms can capture that traffic:

- **Request/response logging** (`fetchPublisherModelConfig` / `setPublisherModelConfig`)
  — writes full prompt + output JSON to a BigQuery table. Per-model, off by default.
- **Data Access audit logs** — caller/model/timestamp metadata (no content),
  off by default.

Neither is visible from a normal user seat without deliberately checking. This
plugin checks on every session and caches the result for a shell prompt.

## How it works

- A **SessionStart hook** runs `hooks/refresh.sh` (backgrounded, throttled to
  once per 10 min). It calls `fetchPublisherModelConfig` for each model in use
  and inspects the project IAM policy's `auditConfigs`, writing a small cache at
  `~/.claude/cache/vertex-logging-state.json`.
- `hooks/status.sh` reads that cache instantly (no network) and prints a compact
  indicator. It self-heals: if the cache is stale it fires a throttled
  background refresh via a stable symlink, so it survives plugin updates.

Indicator states:

| Output | Meaning |
|--------|---------|
| `🟢 vtx:unlogged` | No prompt/output content logged (default) |
| `🔴 vtx:LOGGED` | Request/response logging ON for ≥1 model — content → BigQuery |
| `🟡 vtx:audit-only` | Data Access audit logging on (metadata only) |
| `🟠 vtx:mixed` | Mixed / partial state across models |
| `⚪ vtx:? (…)` | Unknown — no project, no gcloud token, permission denied, or stale |

## Zero-config

`refresh.sh` derives everything from env Claude Code already sets:
`ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`, and the
`ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` vars (stripping `@version` and
`[..]` tags). Overrides: `VERTEX_LOG_PROJECT`, `VERTEX_LOG_LOCATION`,
`VERTEX_LOG_MODELS`, `VERTEX_LOG_PUBLISHER`, `VERTEX_LOG_THROTTLE`,
`VERTEX_LOG_MAX_AGE`, `VERTEX_LOG_SELFHEAL=0`.

Requires `gcloud` (authenticated), `jq`, and `curl`. The config check calls
`fetchPublisherModelConfig` on the project (available to any `roles/aiplatform.user`);
the audit-config check reads the project IAM policy (`getIamPolicy`).

## On-demand check

`/vertex-log-monitor:vertex-log-status` forces a fresh check and reports the state.

## Shell prompt (Starship)

After the plugin has run once (any Claude session), a stable reader exists at
`~/.claude/cache/vertex-log-monitor-status.sh`. Add to `~/.config/starship.toml`:

```toml
[custom.vertexlog]
command = '/opt/homebrew/bin/bash ~/.claude/cache/vertex-log-monitor-status.sh --plain'
shell = '/opt/homebrew/bin/bash'
when = true
format = '[$output ]($style)'
style = 'bold'
```

The stable symlink is re-pointed on every session start, so plugin updates never
break the prompt.
