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
#   VERTEX_LOG_GCLOUD_TIMEOUT (seconds, default 10)   GCLOUD_BIN
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
# Validate before splicing into the request URL: an unvalidated LOCATION or
# PUBLISHER containing "/" could redirect the token-bearing curl to an attacker
# host. Fall back to the safe defaults on any mismatch.
[[ "$LOCATION" =~ ^[a-z0-9-]+$ ]] || LOCATION="global"
PUBLISHER="${VERTEX_LOG_PUBLISHER:-anthropic}"
[[ "$PUBLISHER" =~ ^[a-z0-9-]+$ ]] || PUBLISHER="anthropic"

CACHE_DIR="${VERTEX_LOG_CACHE_DIR:-${HOME}/.claude/cache}"
CACHE_FILE="${CACHE_DIR}/vertex-logging-state.json"
mkdir -p "$CACHE_DIR"
chmod 700 "$CACHE_DIR" 2>/dev/null || true

# Stable entry point for the shell prompt / statusline. This is a COPY of the
# version-independent launcher (which resolves the installed status.sh at
# runtime), not a symlink into the versioned plugin dir: a symlink hardcodes the
# current version and dangles on the next plugin update until a SessionStart
# re-points it (surfacing as "Exit 127" in the statusline). rm -f first so the
# cp replaces any pre-existing symlink instead of writing THROUGH it and
# clobbering the real status.sh. The old refresh symlink is removed too --
# status.sh now finds refresh.sh as a sibling via BASH_SOURCE, so it is dead.
STATUS_ENTRY="${CACHE_DIR}/vertex-log-monitor-status.sh"
rm -f "$STATUS_ENTRY" "${CACHE_DIR}/vertex-log-monitor-refresh.sh"
if cp -f "${SELF_DIR}/status-launcher.sh" "$STATUS_ENTRY" 2>/dev/null; then
  chmod +x "$STATUS_ENTRY" 2>/dev/null || true
fi

# gcloud binary: honor an explicit GCLOUD_BIN override, else prefer gcloud on
# PATH, else search common install locations (Apple Silicon and Intel Homebrew,
# the SDK's share dir, a standard Linux install, and snap) rather than hardcode
# a single machine-specific path.
GCLOUD="${GCLOUD_BIN:-gcloud}"
if ! command -v "$GCLOUD" >/dev/null 2>&1; then
  for cand in \
    /opt/homebrew/bin/gcloud \
    /usr/local/bin/gcloud \
    /opt/homebrew/share/google-cloud-sdk/bin/gcloud \
    /usr/local/share/google-cloud-sdk/bin/gcloud \
    "${HOME}/google-cloud-sdk/bin/gcloud" \
    /snap/bin/gcloud; do
    if [ -x "$cand" ]; then GCLOUD="$cand"; break; fi
  done
fi

now=$(date +%s)

# Atomic write: build in a temp file on the same filesystem, then rename, so a
# concurrent reader never sees a half-written cache. Clean up the temp on failure.
write_cache() {
  local tmp
  tmp="$(mktemp "${CACHE_FILE}.XXXXXX" 2>/dev/null)"
  if printf '%s\n' "$1" > "$tmp" && mv -f "$tmp" "$CACHE_FILE"; then
    return 0
  fi
  rm -f "$tmp"
}

# jq is required for all parsing/assembly below. Fall back to a clean error cache
# if it is missing ($now is digits, so this static write needs no escaping).
if ! command -v jq >/dev/null 2>&1; then
  printf '{"updated":%s,"error":"no_jq","models":{}}\n' "$now" > "$CACHE_FILE"
  exit 0
fi

if [ -z "$PROJECT" ] || ! [[ "$PROJECT" =~ ^[a-z][-a-z0-9]{4,28}[a-z0-9]$ ]]; then
  write_cache "$(jq -nc --argjson updated "$now" '{updated:$updated, error:"no_project", models:{}}')"
  exit 0
fi

# Portable mtime: BSD/macOS stat requires -f, GNU/Linux stat requires -c.
# Branching on $OSTYPE (a bash builtin, no subprocess) avoids forking a doomed
# `stat -c` on macOS before falling back to `stat -f` on every call. Used for
# both files and the lock directory below. Duplicated inline in status.sh (same
# $OSTYPE branch) -- keep in sync.
path_mtime() {
  if [[ "$OSTYPE" == darwin* ]]; then
    stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null
  else
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
  fi
}

# Throttle: skip if the cache is still fresh (avoids spam on frequent events).
THROTTLE="${VERTEX_LOG_THROTTLE:-600}"
if [ -z "${VERTEX_LOG_FORCE:-}" ] && [ -f "$CACHE_FILE" ]; then
  m=$(path_mtime "$CACHE_FILE")
  if [ -n "$m" ] && [ $((now - m)) -lt "$THROTTLE" ]; then exit 0; fi
fi

# Single-run lock (atomic mkdir); reclaim if stale (>120s). Only the process
# that actually creates the lock removes it on exit.
LOCK="${CACHE_DIR}/.vertex-log-monitor.lock"
LOCK_OWNED=0
if mkdir "$LOCK" 2>/dev/null; then
  LOCK_OWNED=1
else
  lm=$(path_mtime "$LOCK")
  if [ -n "$lm" ] && [ $((now - lm)) -lt 120 ]; then
    # A non-forced run defers to the in-flight refresh. A forced run must still
    # produce a fresh write, so it reclaims the young lock immediately and
    # proceeds -- accepting a possible concurrent refresh, since the cache write
    # is atomic (last writer wins). It does NOT wait, so a user-invoked forced
    # check (/vertex-log-status) never blocks on another process's lock.
    [ -n "${VERTEX_LOG_FORCE:-}" ] || exit 0
  fi
  rmdir "$LOCK" 2>/dev/null
  if mkdir "$LOCK" 2>/dev/null; then LOCK_OWNED=1; else exit 0; fi
fi
trap '[ "$LOCK_OWNED" = "1" ] && rmdir "$LOCK" 2>/dev/null' EXIT

# Model sources: explicit override (space-separated) or the ANTHROPIC_DEFAULT_*
# env vars. Both are normalized identically below so cache keys always match
# status.sh's stripped lookup.
# CACHE-KEY CONTRACT: this normalization (strip "@version" and "[tags]") is
# duplicated inline in status.sh -- search that file for "CACHE-KEY CONTRACT".
# Keep both in sync. Kept inline rather than sourced from a shared lib.sh: the
# stable status entry point is a copy of the launcher living in CACHE_DIR, which
# cannot reliably locate a lib.sh back in the versioned plugin dir.
strip_model() { local m="$1"; m="${m%%@*}"; m="${m%%\[*}"; printf '%s' "$m"; }
if [ -n "${VERTEX_LOG_MODELS:-}" ]; then
  raw_models="$VERTEX_LOG_MODELS"
else
  raw_models="${ANTHROPIC_DEFAULT_OPUS_MODEL:-} ${ANTHROPIC_DEFAULT_SONNET_MODEL:-} ${ANTHROPIC_DEFAULT_HAIKU_MODEL:-}"
fi
MODELS=""
# Disable globbing for the split below: model env values like claude-opus-4-8[1m]
# contain glob metacharacters ([...]) that must be treated literally, not
# expanded against files in the CWD. Word-splitting stays on.
set -f
# shellcheck disable=SC2086 # intentional word-split on the space-separated list
for v in $raw_models; do
  [ -z "$v" ] && continue
  s="$(strip_model "$v")"
  # Allowlist the normalized id before it is spliced into the curl -K config URL
  # below. Word-splitting already prevents newlines from reaching a model value,
  # but this rejects quotes, slashes, and other characters that could corrupt
  # the config line -- defense in depth for the token-bearing request.
  [[ "$s" =~ ^[A-Za-z0-9._-]+$ ]] || continue
  case " $MODELS " in *" $s "*) : ;; *) MODELS="${MODELS:+$MODELS }$s" ;; esac
done
set +f
MODELS="${MODELS:-claude-sonnet-5}"

if [ "$LOCATION" = "global" ]; then
  HOST="https://aiplatform.googleapis.com"
else
  HOST="https://${LOCATION}-aiplatform.googleapis.com"
fi

# Wrap gcloud calls in a timeout where one is available -- `timeout` isn't stock
# on macOS/BSD but ships via GNU coreutils (as `timeout` or `gtimeout`) and is
# stock on Linux. Without either, gcloud runs unwrapped: the refresh is
# backgrounded so a hung call never blocks the prompt, and its mkdir lock is
# reclaimed after 120s by the next run -- bounded, not permanent.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi
GCLOUD_TIMEOUT="${VERTEX_LOG_GCLOUD_TIMEOUT:-10}"
gcloud_call() {
  if [ -n "$TIMEOUT_BIN" ]; then
    "$TIMEOUT_BIN" "$GCLOUD_TIMEOUT" "$GCLOUD" "$@"
  else
    "$GCLOUD" "$@"
  fi
}

TOKEN=$(gcloud_call auth print-access-token 2>/dev/null)
if [ -z "$TOKEN" ]; then
  write_cache "$(jq -nc --argjson updated "$now" --arg project "$PROJECT" '{updated:$updated, project:$project, error:"no_gcloud_token", models:{}}')"
  exit 0
fi

# Collect model -> state as TAB-separated lines; assembled into JSON via jq below
# (jq handles escaping, so model names never corrupt the cache).
models_tsv=""
for M in $MODELS; do
  # Pass the URL and bearer token via a curl config on stdin (-K -) so the token
  # never appears in this process's argv (visible via ps/procfs). The heredoc
  # delimiter is unquoted on purpose so the shell expands the vars into the
  # config; the body lines must stay unindented (plain heredoc).
  resp=$(curl -K - <<CURLCFG
url = "${HOST}/v1beta1/projects/${PROJECT}/locations/${LOCATION}/publishers/${PUBLISHER}/models/${M}:fetchPublisherModelConfig"
header = "Authorization: Bearer ${TOKEN}"
header = "x-goog-user-project: ${PROJECT}"
silent
max-time = 8
write-out = "\n%{http_code}"
CURLCFG
)
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
    *)   state="error:${code:-nocurl}" ;;
  esac
  models_tsv="${models_tsv}${M}"$'\t'"${state}"$'\n'
done
models_json=$(printf '%s' "$models_tsv" | jq -Rn '[inputs | select(length > 0) | split("\t") | {(.[0]): .[1]}] | add // {}')

# Data Access audit logging: "on" only if a DATA_READ/DATA_WRITE logType is
# actually enabled for aiplatform (or allServices) -- not merely that an
# auditConfigs entry exists.
audit="unknown"
pol=$(gcloud_call projects get-iam-policy --format=json -- "$PROJECT" 2>/dev/null)
if [ -n "$pol" ]; then
  has=$(printf '%s' "$pol" | jq -r '
    [ .auditConfigs[]?
      | select(.service == "aiplatform.googleapis.com" or .service == "allServices")
      | .auditLogConfigs[]?
      | select(.logType == "DATA_READ" or .logType == "DATA_WRITE") ] | length' 2>/dev/null)
  if [ -z "$has" ] || [ "$has" = "0" ]; then audit="off"; else audit="on"; fi
fi

write_cache "$(jq -nc \
  --argjson updated "$now" \
  --arg project "$PROJECT" \
  --arg location "$LOCATION" \
  --arg audit "$audit" \
  --argjson models "$models_json" \
  '{updated:$updated, project:$project, location:$location, audit_data_access:$audit, models:$models}')"
