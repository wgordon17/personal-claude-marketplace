#!/usr/bin/env bash
# vertex-log-monitor: refresh the Vertex AI logging-state cache.
#
# Runs OUT OF BAND (SessionStart hook, backgrounded) and via self-heal from the
# shell prompt. Writes a small JSON cache that status.sh reads instantly. Never
# call this synchronously from a prompt/statusline -- it makes network calls.
#
# Zero-config: derives project / region / models from the ANTHROPIC_* and
# CLOUD_ML_REGION env vars Claude Code already exports. Overrides:
#   VERTEX_LOG_PROJECT   VERTEX_LOG_LOCATION   VERTEX_LOG_MODELS
#   VERTEX_LOG_PUBLISHER VERTEX_LOG_CACHE_DIR  VERTEX_LOG_THROTTLE  VERTEX_LOG_FORCE
#
# Logging-state semantics (empirically verified against the Vertex API):
#   HTTP 200 + loggingConfig.enabled==true -> LOGGED (content -> BigQuery)
#   HTTP 404                               -> unlogged (no config for this exact model ref)
#   HTTP 403                               -> denied (permission problem, NOT proof of "off")
# A 404 is only meaningful for the exact model+location+publisher in use; a
# wrong model name returns the same 404. The model list is derived from the
# ANTHROPIC_DEFAULT_*_MODEL env vars, which are exactly what this client calls.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT="${VERTEX_LOG_PROJECT:-${ANTHROPIC_VERTEX_PROJECT_ID:-}}"
LOCATION="${VERTEX_LOG_LOCATION:-${CLOUD_ML_REGION:-global}}"
PUBLISHER="${VERTEX_LOG_PUBLISHER:-anthropic}"

CACHE_DIR="${VERTEX_LOG_CACHE_DIR:-${HOME}/.claude/cache}"
CACHE_FILE="${CACHE_DIR}/vertex-logging-state.json"
mkdir -p "$CACHE_DIR"

# Stable, version-independent symlinks so the shell prompt never references the
# versioned plugin cache dir (survives plugin updates; refreshed every run).
ln -sf "${SELF_DIR}/refresh.sh" "${CACHE_DIR}/vertex-log-monitor-refresh.sh" 2>/dev/null || true
ln -sf "${SELF_DIR}/status.sh"  "${CACHE_DIR}/vertex-log-monitor-status.sh"  2>/dev/null || true

GCLOUD="${GCLOUD_BIN:-gcloud}"
command -v "$GCLOUD" >/dev/null 2>&1 || GCLOUD="/opt/homebrew/share/google-cloud-sdk/bin/gcloud"

now=$(date +%s)

write_cache() { printf '%s\n' "$1" > "$CACHE_FILE"; }

if [ -z "$PROJECT" ]; then
  write_cache "$(printf '{"updated":%s,"error":"no_project","models":{}}' "$now")"
  exit 0
fi

# Throttle: skip if the cache is still fresh (avoids spam on frequent events).
THROTTLE="${VERTEX_LOG_THROTTLE:-600}"
if [ -z "${VERTEX_LOG_FORCE:-}" ] && [ -f "$CACHE_FILE" ]; then
  m=$(stat -c %Y "$CACHE_FILE" 2>/dev/null || stat -f %m "$CACHE_FILE" 2>/dev/null)
  if [ -n "$m" ] && [ $((now - m)) -lt "$THROTTLE" ]; then exit 0; fi
fi

# Single-run lock (atomic mkdir); reclaim if stale (>120s).
LOCK="${CACHE_DIR}/.vertex-log-monitor.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  lm=$(stat -c %Y "$LOCK" 2>/dev/null || stat -f %m "$LOCK" 2>/dev/null)
  if [ -n "$lm" ] && [ $((now - lm)) -lt 120 ]; then exit 0; fi
  rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Derive models from ANTHROPIC_DEFAULT_*_MODEL, stripping @version and [..] tags.
strip_model() { local m="$1"; m="${m%%@*}"; m="${m%%\[*}"; printf '%s' "$m"; }
if [ -n "${VERTEX_LOG_MODELS:-}" ]; then
  MODELS="$VERTEX_LOG_MODELS"
else
  MODELS=""
  for v in "${ANTHROPIC_DEFAULT_OPUS_MODEL:-}" "${ANTHROPIC_DEFAULT_SONNET_MODEL:-}" "${ANTHROPIC_DEFAULT_HAIKU_MODEL:-}"; do
    [ -z "$v" ] && continue
    s="$(strip_model "$v")"
    case " $MODELS " in *" $s "*) : ;; *) MODELS="${MODELS:+$MODELS }$s" ;; esac
  done
fi
MODELS="${MODELS:-claude-sonnet-5}"

if [ "$LOCATION" = "global" ]; then
  HOST="https://aiplatform.googleapis.com"
else
  HOST="https://${LOCATION}-aiplatform.googleapis.com"
fi

TOKEN=$("$GCLOUD" auth print-access-token 2>/dev/null)
if [ -z "$TOKEN" ]; then
  write_cache "$(printf '{"updated":%s,"project":"%s","error":"no_gcloud_token","models":{}}' "$now" "$PROJECT")"
  exit 0
fi

models_json=""
for M in $MODELS; do
  resp=$(curl -s -m 8 -w $'\n%{http_code}' \
    "${HOST}/v1beta1/projects/${PROJECT}/locations/${LOCATION}/publishers/${PUBLISHER}/models/${M}:fetchPublisherModelConfig" \
    -H "Authorization: Bearer ${TOKEN}" -H "x-goog-user-project: ${PROJECT}")
  code=$(printf '%s' "$resp" | tail -n1)
  payload=$(printf '%s' "$resp" | sed '$d')
  case "$code" in
    200)
      if [ "$(printf '%s' "$payload" | jq -r '.loggingConfig.enabled // false' 2>/dev/null)" = "true" ]; then
        state="LOGGED"
      else
        state="config-off"
      fi
      ;;
    404) state="unlogged" ;;
    403) state="denied" ;;
    *)   state="error:${code}" ;;
  esac
  models_json="${models_json}$(printf '"%s":"%s",' "$M" "$state")"
done
models_json="{${models_json%,}}"

audit="unknown"
pol=$("$GCLOUD" projects get-iam-policy "$PROJECT" --format=json 2>/dev/null)
if [ -n "$pol" ]; then
  has=$(printf '%s' "$pol" | jq -r '(.auditConfigs // []) | map(select(.service=="aiplatform.googleapis.com" or .service=="allServices")) | length' 2>/dev/null)
  if [ "$has" = "0" ] || [ -z "$has" ]; then audit="off"; else audit="on"; fi
fi

write_cache "$(printf '{"updated":%s,"project":"%s","location":"%s","audit_data_access":"%s","models":%s}' \
  "$now" "$PROJECT" "$LOCATION" "$audit" "$models_json")"
