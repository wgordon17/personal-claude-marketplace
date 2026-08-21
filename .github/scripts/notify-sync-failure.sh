#!/usr/bin/env bash
# notify-sync-failure.sh — Opens a GitHub Issue on sync workflow failure, or
# comments on an existing open one with a matching title, to avoid opening a
# duplicate issue on repeat weekly failures.
#
# Both sync-token-efficiency.yml and sync-drawio-skill.yml run their patch
# script (or an earlier fetch/safety check) with no continue-on-error, so a
# failure skips every downstream step including PR creation -- no PR ever
# exists in that case, so there is no PR check to fail. This script is the
# only durable, notification-triggering signal for that failure mode.
#
# Shared by both workflows rather than inlined separately in each: the
# dedup-search-then-create-or-comment branching is identical in both, and
# a standalone script is unit-testable the way inline YAML `run:` steps
# are not (see patch-drawio-xml-reference.sh, extracted from this repo's
# own sync-drawio-skill.yml for the same reason).
#
# Usage: notify-sync-failure.sh <title> <body>

set -euo pipefail

TITLE="${1:-}"
BODY="${2:-}"
if [[ -z "$TITLE" || -z "$BODY" ]]; then
  echo "::error::notify-sync-failure.sh requires <title> and <body> arguments" >&2
  exit 1
fi

# Strip embedded double quotes for the search query only (the issue's own
# --title stays untouched below) -- an unescaped " in $TITLE would break
# out of GitHub's in:title "..." quoted-phrase syntax and corrupt the dedup
# search. Not reachable with today's hardcoded caller titles, but this
# script is shared/reusable, so harden it defensively.
SEARCH_TITLE="${TITLE//\"/}"
EXISTING=$(gh issue list --state open --search "in:title \"$SEARCH_TITLE\"" --json number --jq '.[0].number // empty')
if [[ -n "$EXISTING" ]]; then
  gh issue comment "$EXISTING" --body "Failed again.

$BODY"
else
  gh issue create --title "$TITLE" --body "$BODY"
fi
