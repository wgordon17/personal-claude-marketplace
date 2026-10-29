#!/usr/bin/env bash
# check-availability.sh — shared git-remote-gate + reachability probe for
# chai-bot's advisory nudge (session-start.sh) and the explicit /chai-bot
# command.
#
# Usage:
#   check-availability.sh               # Repo gate (Steps 1-3) + reachability probe (Step 4)
#   check-availability.sh --probe-only  # Reachability probe only (skips Steps 1-3)
#
# Exit code contract:
#   0 = available (eligible repo or --probe-only, and Chai Bot reachable)
#   1 = not an eligible OSAC repo (or invalid CLI flag) — no network call made
#   2 = Chai Bot is unusable (unreachable — likely VPN down — missing
#       CHAI_BOT_BASE_URL, or CHAI_BOT_BASE_URL not https://)

set -u

PROBE_ONLY=0
if [[ $# -eq 1 && "$1" == "--probe-only" ]]; then
    PROBE_ONLY=1
elif [[ $# -gt 0 ]]; then
    exit 1
fi

if [[ "$PROBE_ONLY" -ne 1 ]]; then
    # Step 1: must be inside a git repo.
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        exit 1
    fi

    # Step 2 & 3: check origin and upstream remotes against eligible OSAC
    # repository patterns. Both remotes are checked so fork workflows (where
    # origin points to a personal fork like <user>/managed-cluster-config.git
    # and upstream points to osac-project/managed-cluster-config.git) match on
    # upstream even when origin is non-empty.
    #
    # Eligible GitHub remote patterns:
    #   1. Any repository under the osac-project org:
    #      github.com[:/]osac-project/<repo>
    #   2. Any repository named osac, osac-<suffix>, or .osac-<suffix> under
    #      any valid GitHub owner (e.g. osac-spine, osac-dev-environment,
    #      osac-sno-provisioner, .osac-ai-skills, or a direct fork named osac):
    #      github.com[:/]<owner>/(osac(-[a-z0-9._-]+)?|\.osac-[a-z0-9._-]+)(\.git)?/*$
    #
    # Accepted remote URL schemes:
    #   scp-style SSH            git@github.com:<owner>/<repo>.git
    #   ssh:// SSH               ssh://git@github.com/<owner>/<repo>.git
    #   HTTPS                    https://github.com/<owner>/<repo>.git
    #   HTTPS + userinfo/token   https://user@github.com/<owner>/<repo>.git (including token userinfo)
    #
    # The github.com host stays ANCHORED (optional scheme + optional userinfo
    # may precede it, but nothing else), so a lookalike host (faux-github.com)
    # or a "github.com/osac-project/" substring buried in the path/query of
    # another host can never match. `[^/@]*@` for HTTPS userinfo stops at the
    # first / or @, so it can't swallow a different host. `<owner>` is
    # restricted to valid GitHub account characters (`[a-z0-9][a-z0-9-]*`) so
    # path-traversal segments (`..`, `.`) cannot match. Case-insensitive
    # (nocasematch) for bash 3.2 compatibility.
    shopt -s nocasematch
    MATCH=0
    for REMOTE_NAME in origin upstream; do
        REMOTE_URL="$(git remote get-url "$REMOTE_NAME" 2>/dev/null || true)"
        if [[ -n "$REMOTE_URL" ]] &&
            [[ "$REMOTE_URL" =~ ^((ssh://)?(git@)?github\.com[:/]|https?://([^/@]*@)?github\.com/)(osac-project/[a-z0-9][a-z0-9._-]*|[a-z0-9][a-z0-9-]*/(osac(-[a-z0-9._-]+)?|\.osac-[a-z0-9._-]+))(\.git)?/*$ ]]; then
            MATCH=1
            break
        fi
    done
    shopt -u nocasematch

    if [[ "$MATCH" -ne 1 ]]; then
        exit 1
    fi
fi

# Step 4: reachability probe. CHAI_BOT_BASE_URL is set in the user's global
# ~/.claude/settings.json env block — never hardcode the internal Red Hat
# hostname here (this file is committed to a public repo).
if [[ -z "${CHAI_BOT_BASE_URL:-}" ]]; then
    exit 2
fi

# Reject a non-https CHAI_BOT_BASE_URL before ever attempting the curl --
# CHAI_TOKEN travels as a bearer header on every MCP call (see .mcp.json),
# and a http:// misconfiguration would send it in cleartext to a remote
# host. A loopback host (localhost / 127.0.0.1 / ::1) never transits a
# network, so http:// is allowed there (used by the test suite's local
# probe server); every real, remote CHAI_BOT_BASE_URL must be https://.
# The loopback pattern is anchored to a FULL host: optional numeric port,
# then only a "/path" or end-of-string -- never "@", so a userinfo trick
# like http://localhost:1234@remote-host/ (whose real host is remote-host)
# cannot masquerade as loopback.
# A CR/LF is never a legitimate URL and would make bash ERE and metrics.py's
# Python regex diverge on newline handling -- reject outright so both agree.
if [[ "$CHAI_BOT_BASE_URL" == *$'\n'* || "$CHAI_BOT_BASE_URL" == *$'\r'* ]]; then
    exit 2
fi
if [[ ! "$CHAI_BOT_BASE_URL" =~ ^https:// ]] &&
    [[ ! "$CHAI_BOT_BASE_URL" =~ ^http://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?(/.*)?$ ]]; then
    exit 2
fi

if ! curl --connect-timeout 3 --max-time 3 -s -o /dev/null "${CHAI_BOT_BASE_URL}/"; then
    exit 2
fi

# Step 5: all checks passed.
exit 0
