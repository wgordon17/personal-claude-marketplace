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
# Commits whose message cannot be read from the command string (-F, -C/-c,
# --fixup, --amend/--no-edit without -m, non-heredoc command substitution, no -m
# at all) are left to the PostToolUse validator, which checks the commit that
# landed. Forms the static parser cannot see (`bash -c`, `eval`) are covered the same
# way when the command text mentions `git ... commit`; git aliases are not covered.
#
# Fails closed (exit 2) if the shared helper cannot be loaded, if `jq` is
# missing and the input looks like a git commit, or if an explicit
# `-C`/`--git-dir`/`--work-tree` path does not exist on disk.

# Builtin read: must work even when PATH has no external tools.
INPUT=$(</dev/stdin)

# Cheap O(n) pre-filter: this hook runs on every Bash call, and the tokenizer is
# far slower on large commands (multi-KB heredocs) than on a commit line.
if [[ "$INPUT" != *git* || "$INPUT" != *commit* ]]; then
    exit 0
fi

SCRIPT_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
# shellcheck source=dev-guard/hooks/lib/project-commit-format.sh disable=SC1091
if ! source "${SCRIPT_DIR}/lib/project-commit-format.sh"; then
    printf "ERROR: cannot load project-commit-format.sh — commit format hook cannot run\n" >&2
    exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
    # Without jq the command cannot be extracted; block only input that mentions a commit.
    if [[ "$INPUT" =~ git.*commit ]]; then
        printf "ERROR: jq not found — commit format hook cannot run\n" >&2
        exit 2
    fi
    exit 0
fi

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null) || CMD=""
if [[ -z "$CMD" ]]; then
    exit 0
fi

# Check the commit currently described by the PCF_* state. Exits 2 to block; returns 0 when
# the commit passes or is left to PostToolUse.
enforce_current_commit() {
    # A target that depends on shell expansion ($VAR, $(...)) cannot be resolved here.
    if (( PCF_TARGET_UNRESOLVED )); then
        return 0
    fi

    # pcf_load_repo_config returns 1 for a non-repository target (nothing to enforce)
    # and 2 when an explicit target path is missing (fail closed; message already printed).
    pcf_load_repo_config
    case $? in
        0) ;;
        2) exit 2 ;;
        *) return 0 ;;
    esac

    if [[ "$PCF_TRAILERS_REQUIRED" != "true" ]]; then
        return 0
    fi

    # Defer to PostToolUse when the message cannot be read statically.
    if (( PCF_HAS_MSG_FLAG == 0 || PCF_DEFER_TO_POST )); then
        return 0
    fi

    local subject combined
    subject=$(pcf_first_nonempty_line "${PCF_MSG_VALUES[0]:-}")
    combined=$(printf '%s\n' "${PCF_MSG_VALUES[@]}" ${PCF_TRAILER_VALUES[@]+"${PCF_TRAILER_VALUES[@]}"})

    if ! pcf_validate_commit_text "$subject" "$combined" "$PCF_HAS_SIGNOFF_FLAG"; then
        exit 2
    fi
}

if ! pcf_parse_command "$CMD"; then
    exit 0
fi

# A single command can contain several commits (`git commit ... && git commit ...`).
while true; do
    enforce_current_commit
    pcf_parse_next || break
done

exit 0
