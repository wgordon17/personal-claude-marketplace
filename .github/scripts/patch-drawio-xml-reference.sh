#!/usr/bin/env bash
# patch-drawio-xml-reference.sh — Rewrites the upstream xml-reference.md fetch-URL
# instruction text in a drawio SKILL.md file to reference the vendored sibling file
# instead of fetching it from the upstream URL at runtime, then verifies the old URL
# text is actually gone.
#
# Extracted from sync-drawio-skill.yml, where this perl substitution plus its safety
# check was previously duplicated across the "Check for changes" and "Update vendored
# files" steps. Both call sites now invoke this single script.
#
# Usage: patch-drawio-xml-reference.sh <file-path>

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "::error::Usage: patch-drawio-xml-reference.sh <file-path>"
  exit 1
fi

FILE="$1"

if [[ ! -f "$FILE" ]]; then
  echo "::error::patch-drawio-xml-reference.sh requires a path to an existing file as its only argument"
  exit 1
fi

perl -0777 -i -pe 's|fetch and follow the instructions at:\nhttps://raw\.githubusercontent\.com/jgraph/drawio-mcp/main/shared/xml-reference\.md|read and follow the instructions in the vendored sibling file `xml-reference.md` located in the same directory as this skill file.|s' "$FILE"

if grep -q 'raw.githubusercontent.com.*xml-reference' "$FILE"; then
  echo "::error::$FILE still contains upstream xml-reference URL after patching — upstream may have changed the surrounding text"
  exit 1
fi
