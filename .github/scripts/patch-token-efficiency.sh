#!/usr/bin/env bash
# Applies the local blocked-tool-avoidance patches to a freshly-fetched,
# header-stripped copy of upstream JetBrains/benjamin-plus-skill's
# injected-instruction.md, then asserts the patch actually landed.
#
# This is the single source of truth for the three substitutions and the
# safety assertions — invoked by sync-token-efficiency.yml's diff-check and
# write steps so both share identical patch logic (see
# .github/workflows/sync-drawio-skill.yml's pre-fix duplication for the bug
# this pattern avoids).
#
# Usage: patch-token-efficiency.sh <path-to-fetched-file>
#
# The target file MUST already have the attribution comment prepended as
# line 1 (see sync-token-efficiency.yml step "Prepend attribution comment")
# — this script's negative assertion excludes line 1 because the attribution
# comment legitimately names the blocked tools in prose.
set -euo pipefail

FILE="${1:-}"
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "::error::patch-token-efficiency.sh requires a path to an existing file as its only argument" >&2
  exit 1
fi

# --- Rule 1: echo/head recon example -> ls -la; wc -l requirements.txt ---
# Matches only the backtick-quoted example, not the surrounding sentences --
# everything outside it is untouched pass-through, so unrelated upstream
# rewording elsewhere in the paragraph can't silently break this rule.
PTE_R1_OLD=$(cat <<'RULE1_OLD_EOF'
`echo == layout ==; ls -la; echo == deps ==; head -30 requirements.txt`
RULE1_OLD_EOF
)
PTE_R1_NEW=$(cat <<'RULE1_NEW_EOF'
e.g., `ls -la; wc -l requirements.txt`
RULE1_NEW_EOF
)

# --- Rule 2: head/tail keyhole examples -> Read tool with offset/limit ---
# Matches only the tool-list clause (including its embedded upstream line
# break, preserved literally). Same narrow-match rationale as Rule 1.
PTE_R2_OLD=$(cat <<'RULE2_OLD_EOF'
`| head -50`, `| tail -20`,
`grep -m 20`, `wc -l` before contents, Read with offset/limit.
RULE2_OLD_EOF
)
PTE_R2_NEW=$(cat <<'RULE2_NEW_EOF'
Read tool with offset/limit, `grep -m 20`, or `wc -l` before contents.
RULE2_NEW_EOF
)

# --- Rule 3: bare python3 -> uv run python3 ---
# Matches only the backtick-quoted example. Same narrow-match rationale.
PTE_R3_OLD=$(cat <<'RULE3_OLD_EOF'
`python3 -c "import x, y, z"`
RULE3_OLD_EOF
)
PTE_R3_NEW=$(cat <<'RULE3_NEW_EOF'
`uv run python3 -c "import x, y, z"`
RULE3_NEW_EOF
)

export PTE_R1_OLD PTE_R1_NEW PTE_R2_OLD PTE_R2_NEW PTE_R3_OLD PTE_R3_NEW

# Literal (\Q...\E) substitutions via ENV to sidestep shell-quoting pitfalls
# with the backticks/apostrophes/em-dashes embedded in the rule text.
perl -0777 -i -pe 's/\Q$ENV{PTE_R1_OLD}\E/$ENV{PTE_R1_NEW}/s' "$FILE"
perl -0777 -i -pe 's/\Q$ENV{PTE_R2_OLD}\E/$ENV{PTE_R2_NEW}/s' "$FILE"
perl -0777 -i -pe 's/\Q$ENV{PTE_R3_OLD}\E/$ENV{PTE_R3_NEW}/s' "$FILE"

# --- Safety assertions (fail loudly rather than ship unpatched/mangled content) ---

# 1. Strict negative: no blocked-tool word may remain outside line 1 (the
#    attribution comment legitimately names them in prose). Covers the full
#    tool-selection-guard.py blocked-tool list, not just the three patched
#    phrasings, as a defensive guard against future upstream additions.
BLOCKED_HITS=$(grep -nE '\b(head|tail|cat|sed|awk)\b' "$FILE" | grep -v '^1:' || true)
if [[ -n "$BLOCKED_HITS" ]]; then
  echo "::error::blocked-tool mention(s) found outside line 1 attribution comment — a substitution may have silently no-opped (upstream rewording?):" >&2
  echo "$BLOCKED_HITS" >&2
  exit 1
fi

# 2. python3-count parity: every 'python3' mention must be prefixed by
#    'uv run '. If a bare python3 slipped through, the counts diverge.
PYTHON3_COUNT=$(grep -c 'python3' "$FILE" || true)
UV_PYTHON3_COUNT=$(grep -c 'uv run python3' "$FILE" || true)
if [[ "$PYTHON3_COUNT" != "$UV_PYTHON3_COUNT" ]]; then
  echo "::error::'python3' mention count ($PYTHON3_COUNT) does not match 'uv run python3' count ($UV_PYTHON3_COUNT) — a bare python3 reference may have slipped through unpatched" >&2
  exit 1
fi

# 3. Positive check: confirms the Rule 2 replacement text actually landed
#    rather than the substitution being a silent no-op that the negative
#    check above happened not to catch.
if ! grep -q 'Read tool with' "$FILE"; then
  echo "::error::expected replacement text 'Read tool with' not found — Rule 2 patch may not have applied" >&2
  exit 1
fi

exit 0
