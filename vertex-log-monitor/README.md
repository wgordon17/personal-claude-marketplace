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
  `~/.claude/cache/vertex-logging-state.json`. It also installs a stable entry
  point at `~/.claude/cache/vertex-log-monitor-status.sh` (a copy of
  `hooks/status-launcher.sh`) that the shell prompt / statusline points at.
- `hooks/status.sh` reads that cache instantly (no network) and prints a compact
  indicator. With no stdin it prints an **aggregate** across all monitored models;
  when the Claude session JSON is piped on stdin (a ccstatusline custom-command
  widget or a Claude Code `statusLine`), it shows the state for the **active
  model**, normalizing `@version`/`[..]` tags so a tagged id like
  `claude-opus-4-8[1m]` matches its cache key. It self-heals a stale or missing
  cache by firing a throttled background refresh (it finds `refresh.sh` as a
  sibling of its own real path).
- The stable entry point is a **version-independent launcher**: it resolves the
  installed `status.sh` at runtime, so a plugin version bump never leaves it
  dangling. (The earlier symlink hardcoded the version and returned `Exit 127`
  in the statusline after each update until the next SessionStart.) `refresh.sh`
  pins the installed launcher to *this* plugin instance's marketplace root, so a
  same-named plugin from another marketplace can't win resolution; among that
  instance's installed versions it picks the **highest version** (semver, not
  mtime). Two gaps it cannot close, both one-time and inherent (the statusline
  points at a file only `refresh.sh` writes): the very first render on a machine
  where no Claude session has ever run (nothing has installed the launcher yet),
  and, when upgrading from the old symlink scheme, the first render after that
  upgrade until the next SessionStart replaces the stale symlink with the
  launcher. After that first session, updates never dangle it again.

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
`VERTEX_LOG_MODELS`, `VERTEX_LOG_PUBLISHER`, `VERTEX_LOG_CACHE_DIR` (cache
directory, default `~/.claude/cache`, shared by `refresh.sh` and `status.sh`),
`VERTEX_LOG_THROTTLE`, `VERTEX_LOG_FORCE` (skip the throttle and force a fresh
check — set by `/vertex-log-monitor:vertex-log-status`), `VERTEX_LOG_MAX_AGE`,
`VERTEX_LOG_GCLOUD_TIMEOUT` (per-`gcloud`-call timeout in seconds, default 10,
applied when `timeout`/`gtimeout` is available), and `VERTEX_LOG_SELFHEAL=0`.

Requires `gcloud` (authenticated), `jq`, and `curl`. `GCLOUD_BIN` overrides the
`gcloud` binary `refresh.sh` invokes (default: `gcloud` on `PATH`, else a search
of common install locations). The config check calls `fetchPublisherModelConfig`
on the project (available to any `roles/aiplatform.user`); the audit-config check
reads the project IAM policy (`getIamPolicy`).

## On-demand check

`/vertex-log-monitor:vertex-log-status` forces a fresh check and reports the state.

## Shell prompt (Starship)

After the plugin has run once (any Claude session), a stable reader exists at
`~/.claude/cache/vertex-log-monitor-status.sh`. Add to `~/.config/starship.toml`:

```toml
[custom.vertexlog]
command = '~/.claude/cache/vertex-log-monitor-status.sh --plain'
when = true
format = '[$output ]($style)'
style = 'bold'
```

The reader is an executable launcher (with an `env bash` shebang), so no
interpreter path is hardcoded — Starship runs it via the default shell. It
resolves the installed `status.sh` at runtime, so plugin updates never break the
prompt (it does not need to be re-pointed on each session start).

## Claude Code statusline (ccstatusline)

Add a Custom Command widget to `~/.config/ccstatusline/settings.json` (append to a
`lines[]` entry):

```json
{
  "id": "vertexlog",
  "type": "custom-command",
  "commandPath": "~/.claude/cache/vertex-log-monitor-status.sh --plain",
  "timeout": 2000
}
```

ccstatusline pipes the Claude session JSON on stdin, so the widget shows the state
for the model you're currently using (not just the aggregate). `--plain` emits the
emoji indicator without ANSI so ccstatusline applies its own styling.
