---
name: vertex-log-status
description: Check whether Vertex AI is logging your prompts/outputs for this project
---

# Vertex AI Logging Status

Report whether request/response (prompt+output) logging or Data Access audit
logging is enabled for the current Vertex AI project.

## Step 1: Force a fresh check

```bash
VERTEX_LOG_FORCE=1 ${CLAUDE_PLUGIN_ROOT}/hooks/refresh.sh
```

## Step 2: Read the state

```bash
jq . "${VERTEX_LOG_CACHE_DIR:-$HOME/.claude/cache}/vertex-logging-state.json"
```

## Step 3: Interpret and report

- `models` all `unlogged`/`config-off` (and `audit_data_access: off`) — **no prompt/output content is logged** (the default). Report clearly that prompts are not being captured.
- any model `LOGGED` — **request/response logging is ON**: that model's prompts and outputs are written to a BigQuery table. Name the model(s). Note that whoever holds the IAM permission `aiplatform.endpoints.setPublisherModelConfig` can enable this; suggest verifying the BigQuery destination and who configured it.
- `audit_data_access: on` — Data Access audit logging is enabled (caller/model/timestamp metadata, **not** content).
- `audit_data_access: unknown` — audit status could not be verified (the `gcloud projects get-iam-policy` call failed or returned nothing, commonly because the caller lacks IAM-policy read permission). Report it as unknown/unverifiable — do not report it as "off".
- top-level `error` field present (`no_project`, `no_gcloud_token`, `no_jq`) — state is unknown; explain the cause: `no_project` (Vertex project not configured), `no_gcloud_token` (gcloud not authenticated), or `no_jq` (jq not installed, so the cache could not be built) — rather than implying "safe".
- any model `denied` inside `models` — that model returned HTTP 403 (a permission problem, not proof logging is off); report it as unknown/unverifiable for that model even when no top-level `error` is set.

Report the state plainly. Do not claim prompts are unlogged unless every monitored model is `unlogged`/`config-off`.
