#!/usr/bin/env bash
# check-availability.sh — shared git-remote-gate + reachability probe for
# chai-bot's advisory nudge (session-start.sh) and the explicit /chai-bot
# command. Kept as one script so both callers share identical gate/probe
# logic (avoids drift between the automatic nudge and the explicit command).
#
# Exit code contract:
#   0 = available (osac-project repo, Chai Bot reachable)
#   1 = not an eligible (osac-project) repo — no network call made
#   2 = eligible repo, but Chai Bot is unreachable (likely VPN down)

set -u

# Step 1: must be inside a git repo.
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    exit 1
fi

# Step 2: resolve a remote URL — try origin, fall back to upstream.
REMOTE_URL="$(git remote get-url origin 2>/dev/null)"
if [[ -z "$REMOTE_URL" ]]; then
    REMOTE_URL="$(git remote get-url upstream 2>/dev/null)"
fi
if [[ -z "$REMOTE_URL" ]]; then
    exit 1
fi

# Step 3: match against the osac-project GitHub org. Accepts the real remote
# forms git produces/accepts:
#   scp-style SSH   git@github.com:osac-project/x.git
#   ssh:// SSH       ssh://git@github.com/osac-project/x.git
#   HTTPS           https://github.com/osac-project/x.git
#   HTTPS + userinfo/token   https://user@github.com/osac-project/x.git
#                            https://x-access-token:TOKEN@github.com/osac-project/x.git
# The github.com host stays ANCHORED (optional scheme + optional userinfo may
# precede it, but nothing else), so a lookalike host (faux-github.com) or a
# "github.com/osac-project/" substring buried in the path/query of another host
# can never match (host-boundary bypass — SEC-1). `[^/@]*@` for HTTPS userinfo
# stops at the first / or @, so it can't swallow a different host. Case-insensitive
# (nocasematch) so a differently cased real remote (OSAC-Project) still matches --
# bash 3.2 compatible (shopt -s nocasematch since bash 3.1; no ${VAR,,}).
shopt -s nocasematch
MATCH=0
if [[ "$REMOTE_URL" =~ ^((ssh://)?(git@)?github\.com[:/]|https?://([^/@]*@)?github\.com/)osac-project/ ]]; then
    MATCH=1
fi
shopt -u nocasematch

if [[ "$MATCH" -ne 1 ]]; then
    exit 1
fi

# Step 4: reachability probe. CHAI_BOT_BASE_URL is set in the user's global
# ~/.claude/settings.json env block — never hardcode the internal Red Hat
# hostname here (this file is committed to a public repo).
if [[ -z "${CHAI_BOT_BASE_URL:-}" ]]; then
    exit 2
fi

if ! curl --connect-timeout 3 --max-time 3 -s -o /dev/null "${CHAI_BOT_BASE_URL}/"; then
    exit 2
fi

# Step 5: all checks passed.
exit 0
