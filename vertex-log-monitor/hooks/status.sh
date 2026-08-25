#!/usr/bin/env bash
# vertex-log-monitor: fast, network-free reader of the logging-state cache.
#
# Usage: status.sh [--plain]
#   --plain  emoji only, no ANSI (for Starship, which applies its own style;
#            also triggered by STATUSLINE_PLAIN=1 or the standard NO_COLOR).
#
# With no stdin, prints an aggregate across all monitored models. If Claude
# Code's session JSON is piped on stdin, prints the state for the active model.
# When the cache is stale it fires a throttled background refresh (self-heal)
# via the stable refresh symlink, unless VERTEX_LOG_SELFHEAL=0.
set -uo pipefail

CACHE_DIR="${VERTEX_LOG_CACHE_DIR:-${HOME}/.claude/cache}"
CACHE="${CACHE_DIR}/vertex-logging-state.json"
REFRESH_LINK="${CACHE_DIR}/vertex-log-monitor-refresh.sh"
MAX_AGE="${VERTEX_LOG_MAX_AGE:-5400}"

if [ "${1:-}" = "--plain" ] || [ -n "${STATUSLINE_PLAIN:-}" ] || [ -n "${NO_COLOR:-}" ]; then
  G=""; R=""; Y=""; O=""; D=""; X=""
else
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; O=$'\033[33m'; D=$'\033[90m'; X=$'\033[0m'
fi
emit() { printf '%s%s%s' "$2" "$1" "$X"; exit 0; }
unknown() { printf '%s⚪ vtx:? (%s)%s' "$D" "$1" "$X"; exit 0; }

[ -f "$CACHE" ] || unknown "no cache"
command -v jq >/dev/null 2>&1 || unknown "no jq"

mtime=$(stat -c %Y "$CACHE" 2>/dev/null || stat -f %m "$CACHE" 2>/dev/null)
now=$(date +%s)
if [ -n "$mtime" ] && [ $((now - mtime)) -gt "$MAX_AGE" ]; then
  if [ "${VERTEX_LOG_SELFHEAL:-1}" != "0" ] && [ -e "$REFRESH_LINK" ]; then
    nohup /opt/homebrew/bin/bash "$REFRESH_LINK" >/dev/null 2>&1 &
  fi
  unknown "stale"
fi

err=$(jq -r '.error // empty' "$CACHE")
[ -n "$err" ] && unknown "$err"

model=""
if [ ! -t 0 ]; then
  stdin=$(cat 2>/dev/null || true)
  [ -n "$stdin" ] && model=$(printf '%s' "$stdin" | jq -r '.model.id // empty' 2>/dev/null)
fi

audit=$(jq -r '.audit_data_access // "unknown"' "$CACHE")

if [ -n "$model" ]; then
  state=$(jq -r --arg m "$model" '.models[$m] // "n/a"' "$CACHE")
else
  state=$(jq -r '[.models[]] as $m
    | if   ($m | any(. == "LOGGED")) then "LOGGED"
      elif (($m | length) > 0 and ($m | all(. == "unlogged" or . == "config-off"))) then "unlogged"
      else "mixed" end' "$CACHE")
fi

case "$state" in
  LOGGED)     emit "🔴 vtx:LOGGED" "$R" ;;
  unlogged)   if [ "$audit" = "on" ]; then emit "🟡 vtx:audit-only" "$Y"; else emit "🟢 vtx:unlogged" "$G"; fi ;;
  config-off) emit "🟢 vtx:unlogged" "$G" ;;
  denied)     unknown "denied" ;;
  n/a)        unknown "no data:$model" ;;
  mixed)      emit "🟠 vtx:mixed" "$O" ;;
  *)          unknown "$state" ;;
esac
