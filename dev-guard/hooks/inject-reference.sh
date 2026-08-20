#!/usr/bin/env bash
# inject-reference.sh — SessionStart hook that injects a named reference file
# into every session by printing the contents of references/$1 to stdout.
# Claude Code's SessionStart hook mechanism delivers this output as system context.
#
# Generalizes the single-purpose shared-feedback.sh into a parameterized injector:
# $1 is the target filename (relative to ${CLAUDE_PLUGIN_ROOT}/references/), e.g.
# "shared-feedback.md" or "token-efficiency.md".
#
# Graceful degradation: exits 0 silently if CLAUDE_PLUGIN_ROOT is unset, $1 is
# empty, or the resolved file is missing. This prevents hook failures from
# blocking session startup.

# Guard: CLAUDE_PLUGIN_ROOT must be set and non-empty
if [[ -z "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
    exit 0
fi

# Guard: target filename argument must be provided and non-empty
if [[ -z "${1:-}" ]]; then
    exit 0
fi

REFERENCE_FILE="${CLAUDE_PLUGIN_ROOT}/references/$1"

if [[ -f "$REFERENCE_FILE" ]]; then
    cat "$REFERENCE_FILE"
fi

exit 0
