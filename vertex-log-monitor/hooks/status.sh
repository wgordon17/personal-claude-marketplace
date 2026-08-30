#!/usr/bin/env bash
# vertex-log-monitor: fast, network-free reader of the logging-state cache.
#
# Usage: status.sh [--plain]
#   --plain  emoji only, no ANSI (for Starship, which applies its own style;
#            also triggered by STATUSLINE_PLAIN=1 or the standard NO_COLOR).
#
# With no stdin, prints an aggregate across all monitored models. If Claude
# Code's session JSON is piped on stdin, prints the state for the active model.
# When the cache is stale or missing it fires a throttled background refresh
# (self-heal) via the stable refresh symlink, unless VERTEX_LOG_SELFHEAL=0.
set -uo pipefail

CACHE_DIR="${VERTEX_LOG_CACHE_DIR:-${HOME}/.claude/cache}"
CACHE="${CACHE_DIR}/vertex-logging-state.json"
MAX_AGE="${VERTEX_LOG_MAX_AGE:-5400}"

if [ "${1:-}" = "--plain" ] || [ -n "${STATUSLINE_PLAIN:-}" ] || [ -n "${NO_COLOR:-}" ]; then
  G=""; R=""; Y=""; O=""; D=""; X=""
else
  # The base 8-color palette has no orange, so O uses a 256-color orange to stay
  # visually distinct from Y (yellow) -- "mixed" and "audit-only" are different
  # signals and must not render identically.
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; O=$'\033[38;5;208m'; D=$'\033[90m'; X=$'\033[0m'
fi
emit() { printf '%s%s%s' "$2" "$1" "$X"; exit 0; }
unknown() { printf '%s⚪ vtx:? (%s)%s' "$D" "$1" "$X"; exit 0; }
self_heal() {
  # Locate refresh.sh as a sibling of this script. The stable status entry point
  # is a launcher that execs this file by its REAL versioned path, so
  # BASH_SOURCE resolves to the plugin hooks/ dir and refresh.sh sits alongside
  # -- no hardcoded interpreter path, no second stable symlink to maintain.
  [ "${VERTEX_LOG_SELFHEAL:-1}" != "0" ] || return
  local here refresh
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || return
  refresh="${here}/refresh.sh"
  [ -n "$here" ] && [ -x "$refresh" ] || return
  # Skip only if a FRESH refresh lock is held (avoids forking a redundant bash
  # process on every render during an in-flight refresh). A stale/orphaned lock
  # (>120s, matching refresh.sh's own reclaim threshold) must NOT suppress
  # self-heal, or an ungraceful refresh death would starve it permanently --
  # refresh.sh reclaims a stale lock when it runs.
  local lock="${CACHE_DIR}/.vertex-log-monitor.lock" lm
  if [ -d "$lock" ]; then
    if [[ "$OSTYPE" == darwin* ]]; then
      lm=$(stat -f %m "$lock" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null)
    else
      lm=$(stat -c %Y "$lock" 2>/dev/null || stat -f %m "$lock" 2>/dev/null)
    fi
    [ -n "$lm" ] && [ $(( $(date +%s) - lm )) -lt 120 ] && return
  fi
  nohup "$refresh" >/dev/null 2>&1 &
}

# A missing cache self-heals too (not just a stale one): the SessionStart
# refresh is fire-and-forget, and the prompt can run outside any Claude session,
# so the cache may never have been created yet.
[ -f "$CACHE" ] || { self_heal; unknown "no cache"; }
command -v jq >/dev/null 2>&1 || unknown "no jq"

# Portable mtime (avoids a guaranteed double-fork on macOS, where `stat -c`
# always fails first): mirrors path_mtime() in refresh.sh.
if [[ "$OSTYPE" == darwin* ]]; then
  mtime=$(stat -f %m "$CACHE" 2>/dev/null || stat -c %Y "$CACHE" 2>/dev/null)
else
  mtime=$(stat -c %Y "$CACHE" 2>/dev/null || stat -f %m "$CACHE" 2>/dev/null)
fi
now=$(date +%s)
if [ -n "$mtime" ] && [ $((now - mtime)) -gt "$MAX_AGE" ]; then
  self_heal
  unknown "stale"
fi

model=""
if [ ! -t 0 ]; then
  IFS= read -r -d '' stdin || true
  if [ -n "${stdin:-}" ]; then
    model=$(jq -r '.model.id // empty' <<< "$stdin" 2>/dev/null)
    # CACHE-KEY CONTRACT: must stay identical to strip_model() in refresh.sh
    # (strip @version and [..] tags), else a tagged id like claude-opus-4-8[1m]
    # misses the claude-opus-4-8 cache key. Kept inline rather than sourced from
    # a shared lib.sh -- see strip_model()'s comment in refresh.sh for why.
    model="${model%%@*}"; model="${model%%\[*}"
  fi
fi

# Single jq pass over the cache: error, audit, and state (per-model or
# aggregate) in one fork instead of three. Comma-delimited, NOT @tsv/tab --
# bash's `read` treats tab as IFS whitespace and silently drops a leading empty
# field (the common case when .error is empty).
row=$(jq -r --arg m "$model" '
  (.error // "") as $err
  | (.audit_data_access // "unknown") as $audit
  | (if $m != "" then (.models[$m] // "n/a")
     else ([(.models // {})[]]
       | if   any(. == "LOGGED") then "LOGGED"
         elif (length > 0 and all(. == "unlogged" or . == "config-off")) then "unlogged"
         elif (length > 0 and all(. == "denied")) then "denied"
         elif (length > 0 and all(startswith("error:"))) then "error"
         else "mixed" end)
     end) as $state
  | [$err, $audit, $state] | join(",")
' "$CACHE")
IFS=',' read -r err audit state <<< "$row"

[ -n "$err" ] && unknown "$err"

case "$state" in
  LOGGED)              emit "🔴 vtx:LOGGED" "$R" ;;
  unlogged|config-off) if [ "$audit" = "on" ]; then emit "🟡 vtx:audit-only" "$Y"; else emit "🟢 vtx:unlogged" "$G"; fi ;;
  denied)              unknown "denied" ;;
  n/a)                 unknown "no data:$model" ;;
  mixed)               emit "🟠 vtx:mixed" "$O" ;;
  *)                   unknown "$state" ;;
esac
