#!/usr/bin/env bash
# shared-feedback.sh — SessionStart hook that injects cross-project behavioral feedback
# into every session by printing the contents of shared-feedback.md to stdout.
# Claude Code's SessionStart hook mechanism delivers this output as system context.
#
# Graceful degradation: exits 0 silently if CLAUDE_PLUGIN_ROOT is unset or the file
# is missing. This prevents hook failures from blocking session startup.

# Guard: CLAUDE_PLUGIN_ROOT must be set and non-empty
if [[ -z "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
    exit 0
fi

FEEDBACK_FILE="${CLAUDE_PLUGIN_ROOT}/references/shared-feedback.md"

if [[ -f "$FEEDBACK_FILE" ]]; then
    cat "$FEEDBACK_FILE"
fi

exit 0
