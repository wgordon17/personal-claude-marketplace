#!/usr/bin/env bash
# vertex-log-monitor: version-independent entry point for the status widget.
#
# refresh.sh copies this file (a real file, NOT a symlink) to
# $CACHE_DIR/vertex-log-monitor-status.sh, which is the stable path a shell
# prompt (Starship) or Claude Code statusline (ccstatusline) points at. Because
# its body resolves the installed status.sh at RUNTIME rather than baking in a
# versioned path, a plugin version bump can never leave the stable path dangling
# -- the previous symlink approach broke on every update until the next
# SessionStart re-pointed it, surfacing as an "Exit 127" in the statusline.
#
# It execs the resolved real path (not a symlink), so status.sh sees its own
# true location in BASH_SOURCE and can find refresh.sh as a sibling for
# self-heal -- no second stable symlink needed.
set -uo pipefail
shopt -s nullglob

# Pick the newest installed status.sh across any marketplace/version. mtime, not
# semver-sort, so a reinstall of the same version still wins; ties are
# irrelevant (identical content).
best=""
for f in "${HOME}"/.claude/plugins/cache/*/vertex-log-monitor/*/hooks/status.sh; do
  [ -x "$f" ] || continue
  { [ -z "$best" ] || [ "$f" -nt "$best" ]; } && best="$f"
done

# Plugin not installed (or cache pruned): render nothing and exit 0 rather than
# letting the shell return 127 -- an empty widget is correct, an error is not.
[ -n "$best" ] || exit 0

# stdin (the session JSON) and args (e.g. --plain) pass straight through.
exec "$best" "$@"
