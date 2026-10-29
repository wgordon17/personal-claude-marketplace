#!/usr/bin/env bash
# session-start.sh — SessionStart hook: if this session is in an eligible
# OSAC repo and Chai Bot is reachable, inject the advisory guidance doc into
# context. Silent (no output) otherwise — the SessionStart nudge doesn't need
# to distinguish "not an OSAC repo" from "unreachable".

# Guard: CLAUDE_PLUGIN_ROOT must be set and non-empty (matches
# inject-reference.sh's pattern).
if [[ -z "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
    exit 0
fi

"${CLAUDE_PLUGIN_ROOT}/hooks/check-availability.sh"
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
    exit 0
fi

GUIDANCE_FILE="${CLAUDE_PLUGIN_ROOT}/references/chai-guidance.md"
if [[ -f "$GUIDANCE_FILE" ]]; then
    cat "$GUIDANCE_FILE"
fi

exit 0
