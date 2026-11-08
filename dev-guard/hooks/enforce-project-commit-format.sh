#!/usr/bin/env bash
#
# Enforce project-level commit message format (PreToolUse hook on Bash).
#
# Activated when `claude-code.commit-trailers-required` is `true` in the target
# repository. Checks `git commit` command strings before execution for:
#   - Subject prefix matching `claude-code.commit-subject-pattern`
#     (default: `^([A-Z][A-Z0-9]+-[0-9]+|NO-ISSUE): `)
#   - `Assisted-by:` trailer
#   - `Signed-off-by:` trailer (inline, `--trailer`, or `-s`/`--signoff`)
#
# Fails closed (exit 2) if `jq` is missing or if an explicit `-C`/`--git-dir`/
# `--work-tree` path does not exist on disk.

if ! command -v jq >/dev/null 2>&1; then
    printf "ERROR: jq not found — commit format hook cannot run\n" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
# shellcheck source=dev-guard/hooks/lib/project-commit-format.sh disable=SC1091
source "${SCRIPT_DIR}/lib/project-commit-format.sh"

CMD=$(jq -r '.tool_input.command // empty' 2>/dev/null) || CMD=""
if [[ -z "$CMD" ]]; then
    exit 0
fi

if ! pcf_parse_command "$CMD"; then
    exit 0
fi

# Validate explicit target paths (exits 2 if an explicit path does not exist)
# and load target repository config (exits 0 if target is not a git repository).
if ! pcf_load_repo_config; then
    exit 0
fi

if [[ "$PCF_TRAILERS_REQUIRED" != "true" ]]; then
    exit 0
fi

# Defer to PostToolUse when:
# 1. --amend or --no-edit is used without -m/--message
# 2. -F/--file is used without -m/--message
# 3. -m uses an arbitrary command substitution other than $(cat <<...)
if (( PCF_HAS_MSG_FLAG == 0 )); then
    if (( PCF_HAS_AMEND || PCF_HAS_NO_EDIT || PCF_HAS_FILE_FLAG )); then
        exit 0
    fi
fi

if (( PCF_DEFER_TO_POST )); then
    exit 0
fi

FIRST_MSG="${PCF_MSG_VALUES[0]:-}"
SUBJECT=$(pcf_first_nonempty_line "$FIRST_MSG")

COMBINED_TEXT=""
for msg in "${PCF_MSG_VALUES[@]}"; do
    if [[ -n "$COMBINED_TEXT" ]]; then
        COMBINED_TEXT+=$'\n'"$msg"
    else
        COMBINED_TEXT="$msg"
    fi
done
for tr in "${PCF_TRAILER_VALUES[@]}"; do
    if [[ -n "$COMBINED_TEXT" ]]; then
        COMBINED_TEXT+=$'\n'"$tr"
    else
        COMBINED_TEXT="$tr"
    fi
done

if ! pcf_validate_commit_text "$SUBJECT" "$COMBINED_TEXT" "$PCF_HAS_SIGNOFF_FLAG"; then
    exit 2
fi

exit 0
