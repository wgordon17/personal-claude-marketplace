#!/bin/bash
#
# Pre-push review script
# Suggests reviewing commits before pushing
#
# Exit codes:
#   0 = Allow push (non-blocking)
#

set -uo pipefail

# Get the current branch
CURRENT_BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null)
if [[ -z "$CURRENT_BRANCH" ]]; then
    # Detached HEAD or other issue, allow push
    exit 0
fi

# Get tracking branch (what this branch tracks remotely)
TRACKING_BRANCH=$(git rev-parse --abbrev-ref @{upstream} 2>/dev/null)

# Determine base branch for comparison
if [[ -n "$TRACKING_BRANCH" ]]; then
    BASE_BRANCH="$TRACKING_BRANCH"
else
    # No tracking branch set, try common defaults
    if git rev-parse origin/main >/dev/null 2>&1; then
        BASE_BRANCH="origin/main"
    elif git rev-parse origin/master >/dev/null 2>&1; then
        BASE_BRANCH="origin/master"
    else
        # Can't determine base, allow push
        exit 0
    fi
fi

# Count commits ahead of base
COMMITS_AHEAD=$(git rev-list --count "$BASE_BRANCH".."$CURRENT_BRANCH" 2>/dev/null || echo 0)

# If only 1-2 commits, no review needed
if [[ $COMMITS_AHEAD -le 2 ]]; then
    exit 0
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 PRE-PUSH REVIEW"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "You have $COMMITS_AHEAD commits on branch '$CURRENT_BRANCH':"
echo ""
git log --oneline --color=always "$BASE_BRANCH".."$CURRENT_BRANCH"
echo ""

# Look for WIP-style messages
WIP_COMMITS=$(git log --format=%s "$BASE_BRANCH".."$CURRENT_BRANCH" | grep -ciE '\b(wip|try|maybe|testing|debug|temp|experiment|fixup|squash)\b' || true)
if [[ $WIP_COMMITS -gt 0 ]]; then
    echo "⚠️  Found $WIP_COMMITS commit(s) with WIP/debugging language"
    echo ""
fi

# Look for multiple commits with same scope
SCOPES=$(git log --format=%s "$BASE_BRANCH".."$CURRENT_BRANCH" | grep -oE '\([a-z0-9_-]+\)' | sort | uniq -c | sort -rn)
DUPLICATE_SCOPES=$(echo "$SCOPES" | awk '$1 > 2 {print}')

if [[ -n "$DUPLICATE_SCOPES" ]]; then
    echo "⚠️  Multiple commits with same scope detected:"
    echo "$DUPLICATE_SCOPES" | while read count scope; do
        echo "   $scope appears $count times"
    done
    echo ""
fi

# Provide suggestion
if [[ $WIP_COMMITS -gt 0 ]] || [[ -n "$DUPLICATE_SCOPES" ]]; then
    echo "💡 Consider squashing related commits before pushing:"
    echo "   git rebase -i $BASE_BRANCH"
    echo ""
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Always allow push (non-blocking)
exit 0
