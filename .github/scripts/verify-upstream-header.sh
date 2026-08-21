#!/usr/bin/env bash
# verify-upstream-header.sh — Strips injected-instruction.md's 2-line header
# ("BENJAMIN-PLUS MODE ACTIVE" + one blank line) and verifies the result:
# the expected "# Benjamin-Plus" heading landed, no residual header text
# remains, and no Unicode bidi-control/zero-width characters are present
# (a Trojan-Source-style rendering-attack guard, CVE-2021-42574 pattern --
# these characters would not visibly surface in GitHub's diff view, the
# sole human review gate for this imperative-instruction content).
#
# Extracted from sync-token-efficiency.yml for unit-testability -- inline
# YAML `run:` steps can't be exercised outside a live CI run, and this is
# a security-relevant check that was previously only ever tested by the
# live weekly cron.
#
# Usage: verify-upstream-header.sh <raw-file> <stripped-output-file>

set -euo pipefail

RAW_FILE="${1:-}"
OUT_FILE="${2:-}"
if [[ -z "$RAW_FILE" || ! -f "$RAW_FILE" || -z "$OUT_FILE" ]]; then
  echo "::error::verify-upstream-header.sh requires <raw-file> (existing) and <stripped-output-file> arguments" >&2
  exit 1
fi

# injected-instruction.md ships without YAML frontmatter: it opens with
# "BENJAMIN-PLUS MODE ACTIVE" followed by one blank line, then
# "# Benjamin-Plus". Strip that header (the first two lines).
tail -n +3 "$RAW_FILE" > "$OUT_FILE"

if ! head -n 1 "$OUT_FILE" | grep -qx '# Benjamin-Plus'; then
  echo "::error::Stripped content does not start with '# Benjamin-Plus' -- upstream header format may have changed" >&2
  exit 1
fi
if grep -q 'BENJAMIN-PLUS MODE ACTIVE' "$OUT_FILE"; then
  echo "::error::Stripped content still contains 'BENJAMIN-PLUS MODE ACTIVE' -- header strip may have duplicated or missed a line" >&2
  exit 1
fi

# grep exit codes: 0 = match found, 1 = no match, 2+ = grep itself errored
# (e.g. BSD grep on macOS doesn't support -P at all). Only 1 means "safe to
# proceed" -- treating any other non-zero exit as safe would let this
# security check silently no-op if -P support is ever unavailable, exactly
# the fail-open failure mode this whole script exists to avoid.
set +e
grep -P '[\x{202A}-\x{202E}\x{2066}-\x{2069}\x{200B}-\x{200D}\x{FEFF}]' "$OUT_FILE" >/dev/null 2>&1
GREP_STATUS=$?
set -e
if [[ "$GREP_STATUS" -eq 0 ]]; then
  echo "::error::Fetched content contains Unicode bidi-control or zero-width characters -- refusing to sync" >&2
  exit 1
elif [[ "$GREP_STATUS" -gt 1 ]]; then
  echo "::error::Unicode bidi-control/zero-width check itself failed to run (grep exit $GREP_STATUS, -P unsupported?) -- refusing to sync as a precaution" >&2
  exit 1
fi
