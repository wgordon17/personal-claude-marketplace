#!/usr/bin/env bash
# patch-drawio-xml-reference.sh — Rewrites the upstream xml-reference.md fetch-URL
# instruction text in a drawio SKILL.md file to reference the vendored sibling file
# instead of fetching it from the upstream URL at runtime, injects agentskills.io
# frontmatter (compatibility + allowed-tools) and the {skill_dir} note after the H1
# heading if missing, and verifies all 4 post-conditions.
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

# 1. Inject compatibility and allowed-tools into YAML frontmatter if allowed-tools is absent
perl -0777 -i -pe 's|\A(---\n(?:(?!^allowed-tools:).)*?\n)(---\n)|$1compatibility: Optional PNG/SVG/PDF export requires draw.io Desktop CLI\nallowed-tools: Read Write Bash\n$2|ms' "$FILE"

# 2. Inject {skill_dir} note immediately after # Draw.io Diagram Skill if absent
perl -0777 -i -pe 's|(# Draw\.io Diagram Skill\n\n)(?!> \*\*Note:\*\* `\{skill_dir\}`)|$1> **Note:** `{skill_dir}` refers to the absolute "Skill directory" path provided in your system instructions.\n\n|s' "$FILE"

# 3. Rewrite upstream xml-reference.md fetch URL to vendored sibling markdown link
# /g guards against the same same-line-duplicate bug patch-token-efficiency.sh's
# /g flags fix -- see that script's comment for the full rationale.
perl -0777 -i -pe 's|fetch and follow the instructions at:\nhttps://raw\.githubusercontent\.com/jgraph/drawio-mcp/main/shared/xml-reference\.md|read and follow the instructions in the vendored sibling file `[{skill_dir}/xml-reference.md](./xml-reference.md)`.|gs' "$FILE"

# Post-condition 1: no upstream xml-reference URL remains
if grep -q 'raw.githubusercontent.com.*xml-reference' "$FILE"; then
  echo "::error::$FILE still contains upstream xml-reference URL after patching — upstream may have changed the surrounding text"
  exit 1
fi

# Post-condition 2: allowed-tools frontmatter present
if ! grep -q '^allowed-tools: Read Write Bash$' "$FILE"; then
  echo "::error::$FILE missing expected 'allowed-tools: Read Write Bash' frontmatter after patching"
  exit 1
fi

# Post-condition 3: compatibility frontmatter present
if ! grep -q '^compatibility: Optional PNG/SVG/PDF export requires draw.io Desktop CLI$' "$FILE"; then
  echo "::error::$FILE missing expected 'compatibility:' frontmatter after patching"
  exit 1
fi

# Post-condition 4: {skill_dir} note and vendored sibling link present
if ! grep -q '> \*\*Note:\*\* `{skill_dir}`' "$FILE"; then
  echo "::error::$FILE missing expected '{skill_dir}' note after patching"
  exit 1
fi

if ! grep -q 'vendored sibling file `\[{skill_dir}/xml-reference\.md\](\./xml-reference\.md)`' "$FILE"; then
  echo "::error::$FILE missing expected vendored sibling link after patching"
  exit 1
fi
