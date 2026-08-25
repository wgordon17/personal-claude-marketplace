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
jq . ~/.claude/cache/vertex-logging-state.json
```

## Step 3: Interpret and report

- `models` all `unlogged` (and `audit_data_access: off`) — **no prompt/output content is logged** (the default). Report clearly that prompts are not being captured.
- any model `LOGGED` — **request/response logging is ON**: that model's prompts and outputs are written to a BigQuery table. Name the model(s). Note that whoever holds the IAM permission `aiplatform.endpoints.setPublisherModelConfig` can enable this; suggest verifying the BigQuery destination and who configured it.
- `audit_data_access: on` — Data Access audit logging is enabled (caller/model/timestamp metadata, **not** content).
- `error` field present (`no_project`, `no_gcloud_token`, `denied`) — state is unknown; explain the cause (not configured for Vertex, gcloud not authenticated, or insufficient permission) rather than implying "safe".

Report the state plainly. Do not claim prompts are unlogged unless every monitored model is `unlogged`/`config-off`.
