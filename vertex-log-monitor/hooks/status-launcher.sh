#!/usr/bin/env bash
# vertex-log-monitor: version-independent entry point for the status widget.
#
# refresh.sh installs this at $CACHE_DIR/vertex-log-monitor-status.sh (the stable
# path a shell prompt or statusline points at), prepending a
# `VERTEX_LOG_PLUGIN_ROOT=<...>` line that pins resolution to THIS installed
# plugin instance (.../plugins/cache/<marketplace>/vertex-log-monitor). Resolving
# the status.sh to run at RUNTIME (rather than baking in a versioned path) means a
# plugin version bump never leaves the stable path dangling -- the previous
# symlink approach broke on every update until the next SessionStart re-pointed
# it, surfacing as "Exit 127" in the statusline.
#
# It execs the resolved real path (not a symlink), so status.sh sees its own true
# location in BASH_SOURCE and can find refresh.sh as a sibling for self-heal.
set -uo pipefail
shopt -s nullglob

# _ver_gt A B: true if dotted-numeric version A is strictly greater than B.
# Portable (stock macOS `sort` has no -V): compare component by component
# numerically, treating missing or non-numeric components as 0.
_ver_gt() {
  local a="$1" b="$2" i max x y
  local IFS=.
  # shellcheck disable=SC2206  # intentional split of dotted versions into fields
  local -a aa=($a) bb=($b)
  max=${#aa[@]}
  [ "${#bb[@]}" -gt "$max" ] && max=${#bb[@]}
  for ((i = 0; i < max; i++)); do
    x=${aa[i]:-0}; x=${x%%[!0-9]*}; x=${x:-0}
    y=${bb[i]:-0}; y=${y%%[!0-9]*}; y=${y:-0}
    ((10#$x > 10#$y)) && return 0
    ((10#$x < 10#$y)) && return 1
  done
  return 1
}

# Pin to the installed plugin instance (VERTEX_LOG_PLUGIN_ROOT is set by the
# `refresh.sh`-installed copy). When absent -- only for a launcher run directly
# from the repo or a degraded/uninstalled copy, never a normal install -- fall
# back to a broad glob across all marketplaces.
root="${VERTEX_LOG_PLUGIN_ROOT:-}"
if [ -n "$root" ]; then
  roots=("$root")
else
  roots=("${HOME}"/.claude/plugins/cache/*/vertex-log-monitor)
fi
# Nothing to search (empty fallback glob): render nothing, exit 0 -- never 127.
# Guarded before the loop because on bash < 4.4 (macOS system bash 3.2)
# `"${roots[@]}"` under `set -u` errors on an empty array instead of expanding
# to nothing.
[ "${#roots[@]}" -gt 0 ] || exit 0

# Pick the highest installed version's status.sh (semver, not mtime: robust to
# timestamps preserved by tar/rsync and to clock skew).
best="" best_ver=""
for r in "${roots[@]}"; do
  for f in "$r"/*/hooks/status.sh; do
    [ -x "$f" ] || continue
    ver="${f%/hooks/status.sh}"; ver="${ver##*/}"
    if [ -z "$best" ] || _ver_gt "$ver" "$best_ver"; then
      best="$f"; best_ver="$ver"
    fi
  done
done

# Plugin not installed (or cache pruned): render nothing and exit 0 rather than
# letting the shell return 127 -- an empty widget is correct, an error is not.
[ -n "$best" ] || exit 0

# stdin (the session JSON) and args (e.g. --plain) pass straight through.
exec "$best" "$@"
