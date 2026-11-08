#!/usr/bin/env bash
# shellcheck disable=SC2034
#
# Shared helper for project-level commit format enforcement.
# Sourced by both:
#   - dev-guard/hooks/validate-commit-message.sh (PostToolUse)
#   - dev-guard/hooks/enforce-project-commit-format.sh (PreToolUse)

PCF_DEFAULT_SUBJECT_PATTERN='^([A-Z][A-Z0-9]+-[0-9]+|NO-ISSUE): '

PCF_ERR_PREFIX="Missing Jira key or NO-ISSUE prefix (e.g., OSAC-123: or NO-ISSUE:)"
PCF_ERR_ASSISTED_BY="Missing Assisted-by trailer (e.g., Assisted-by: Codex <noreply@openai.com>)"
PCF_ERR_SIGNED_OFF_BY="Missing Signed-off-by trailer (use -s flag: git commit -s -m ...)"

PCF_IS_GIT_COMMIT=0
PCF_TARGET_DIR=""
PCF_TARGET_GIT_DIR=""
PCF_TARGET_WORK_TREE=""
PCF_EXPLICIT_PATHS=()
PCF_HAS_AMEND=0
PCF_HAS_NO_EDIT=0
PCF_HAS_SIGNOFF_FLAG=0
PCF_HAS_FILE_FLAG=0
PCF_HAS_MSG_FLAG=0
PCF_DEFER_TO_POST=0
PCF_MSG_VALUES=()
PCF_TRAILER_VALUES=()
PCF_TRAILERS_REQUIRED="false"
PCF_SUBJECT_PATTERN="$PCF_DEFAULT_SUBJECT_PATTERN"

pcf_reset_state() {
    PCF_IS_GIT_COMMIT=0
    PCF_TARGET_DIR=""
    PCF_TARGET_GIT_DIR=""
    PCF_TARGET_WORK_TREE=""
    PCF_EXPLICIT_PATHS=()
    PCF_HAS_AMEND=0
    PCF_HAS_NO_EDIT=0
    PCF_HAS_SIGNOFF_FLAG=0
    PCF_HAS_FILE_FLAG=0
    PCF_HAS_MSG_FLAG=0
    PCF_DEFER_TO_POST=0
    PCF_MSG_VALUES=()
    PCF_TRAILER_VALUES=()
    PCF_TRAILERS_REQUIRED="false"
    PCF_SUBJECT_PATTERN="$PCF_DEFAULT_SUBJECT_PATTERN"
}

pcf_expand_tilde() {
    local p="$1"
    # shellcheck disable=SC2088
    if [[ "$p" == "~" ]]; then
        printf '%s' "$HOME"
    elif [[ "$p" == "~/"* ]]; then
        printf '%s/%s' "${HOME%/}" "${p#\~/}"
    else
        printf '%s' "$p"
    fi
}

pcf_join_dir() {
    local base="$1"
    local next
    next=$(pcf_expand_tilde "$2")
    if [[ -z "$base" || "$next" == /* ]]; then
        printf '%s' "$next"
    else
        printf '%s/%s' "${base%/}" "$next"
    fi
}

# Tokenize a shell command string into tokens separated by NUL bytes,
# emitting "\x1f" tokens at unquoted command separators (;, &&, ||)
# and stripping unquoted # comments through end-of-line.
# Preserves inner $(cat <<...) blocks inside double-quoted tokens so
# heredoc bodies containing quotes or hashes are kept intact.
PCF_TOKENS=()

pcf_tokenize_command() {
    local cmd="$1"
    local len=${#cmd}
    local i=0
    local cur=""
    local has_cur=0
    local in_sq=0
    local in_dq=0
    local subshell_depth=0
    local ch next_ch

    PCF_TOKENS=()

    while (( i < len )); do
        ch="${cmd:i:1}"
        next_ch="${cmd:i+1:1}"

        if (( in_sq )); then
            if [[ "$ch" == "'" ]]; then
                in_sq=0
            else
                cur+="$ch"
            fi
            (( i++ ))
            continue
        fi

        if (( in_dq )); then
            if (( subshell_depth > 0 )); then
                if [[ "$ch" == "\\" && $(( i + 1 )) -lt $len ]]; then
                    cur+="$ch$next_ch"
                    (( i += 2 ))
                    continue
                elif [[ "$ch" == "(" ]]; then
                    (( subshell_depth++ ))
                elif [[ "$ch" == ")" ]]; then
                    (( subshell_depth-- ))
                fi
                cur+="$ch"
                (( i++ ))
                continue
            fi

            if [[ "$ch" == "\\" && $(( i + 1 )) -lt $len ]]; then
                if [[ "$next_ch" == '"' || "$next_ch" == "\\" || "$next_ch" == '$' || "$next_ch" == '`' ]]; then
                    cur+="$next_ch"
                else
                    cur+="$ch$next_ch"
                fi
                (( i += 2 ))
                continue
            elif [[ "$ch" == '$' && "$next_ch" == "(" ]]; then
                cur+="\$("
                subshell_depth=1
                (( i += 2 ))
                continue
            elif [[ "$ch" == '"' ]]; then
                in_dq=0
                (( i++ ))
                continue
            else
                cur+="$ch"
                (( i++ ))
                continue
            fi
        fi

        # Unquoted context
        if [[ "$ch" == "\\" && $(( i + 1 )) -lt $len ]]; then
            cur+="$next_ch"
            has_cur=1
            (( i += 2 ))
            continue
        fi

        if [[ "$ch" == "'" ]]; then
            in_sq=1
            has_cur=1
            (( i++ ))
            continue
        fi

        if [[ "$ch" == '"' ]]; then
            in_dq=1
            has_cur=1
            (( i++ ))
            continue
        fi

        if [[ "$ch" == '$' && "$next_ch" == "(" ]]; then
            cur+="\$("
            has_cur=1
            subshell_depth=1
            (( i += 2 ))
            while (( i < len && subshell_depth > 0 )); do
                ch="${cmd:i:1}"
                next_ch="${cmd:i+1:1}"
                if [[ "$ch" == "\\" && $(( i + 1 )) -lt $len ]]; then
                    cur+="$ch$next_ch"
                    (( i += 2 ))
                    continue
                elif [[ "$ch" == "(" ]]; then
                    (( subshell_depth++ ))
                elif [[ "$ch" == ")" ]]; then
                    (( subshell_depth-- ))
                fi
                cur+="$ch"
                (( i++ ))
            done
            continue
        fi

        # Unquoted comment: '#' at start of token skips until newline
        if [[ "$ch" == "#" && $has_cur -eq 0 ]]; then
            while (( i < len )) && [[ "${cmd:i:1}" != $'\n' ]]; do
                (( i++ ))
            done
            continue
        fi

        # Command separators: ;, &&, ||, or newline
        if [[ "$ch" == ";" || "$ch" == $'\n' ]]; then
            if (( has_cur )); then
                PCF_TOKENS+=("$cur")
                cur=""
                has_cur=0
            fi
            PCF_TOKENS+=($'\x1f')
            (( i++ ))
            continue
        fi

        if [[ ( "$ch" == "&" && "$next_ch" == "&" ) || ( "$ch" == "|" && "$next_ch" == "|" ) ]]; then
            if (( has_cur )); then
                PCF_TOKENS+=("$cur")
                cur=""
                has_cur=0
            fi
            PCF_TOKENS+=($'\x1f')
            (( i += 2 ))
            continue
        fi

        # Whitespace
        if [[ "$ch" == " " || "$ch" == $'\t' || "$ch" == $'\r' ]]; then
            if (( has_cur )); then
                PCF_TOKENS+=("$cur")
                cur=""
                has_cur=0
            fi
            (( i++ ))
            continue
        fi

        cur+="$ch"
        has_cur=1
        (( i++ ))
    done

    if (( has_cur )); then
        PCF_TOKENS+=("$cur")
    fi
}

# Unwrap a -m / --message argument value.
# If it uses $(cat <<'EOF' ... EOF), extracts the heredoc body.
# If it uses any other $(...) or `...` command substitution, sets PCF_DEFER_TO_POST=1.
PCF_UNWRAPPED_MSG=""

pcf_unwrap_message_arg() {
    local raw="$1"
    PCF_UNWRAPPED_MSG="$raw"

    if [[ "$raw" =~ ^\$\([[:space:]]*cat[[:space:]]+\<\<(-?)[[:space:]]*[\'\"]?([A-Za-z0-9_]+)[\'\"]?[[:space:]]*$'\n'(.*)$ ]]; then
        local delim="${BASH_REMATCH[2]}"
        local rest="${BASH_REMATCH[3]}"
        local body=""
        local line
        local found_end=0
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$line" =~ ^[[:space:]]*${delim}[[:space:]]*\)?[[:space:]]*$ ]]; then
                found_end=1
                break
            fi
            if [[ -n "$body" ]]; then
                body+=$'\n'"$line"
            else
                body="$line"
            fi
        done <<< "$rest"
        if (( found_end )); then
            PCF_UNWRAPPED_MSG="$body"
            return 0
        fi
    fi

    # Any other command substitution in -m defers to PostToolUse
    # shellcheck disable=SC2016
    if [[ "$raw" == *'$('* || "$raw" == *'`'* ]]; then
        PCF_DEFER_TO_POST=1
    fi
}

# Parse a command segment (array of tokens) to see if it is a `git commit` invocation.
pcf_try_parse_segment() {
    local seg=("$@")
    local n=${#seg[@]}
    local idx=0
    local tok

    # Skip leading KEY=VALUE env assignments
    while (( idx < n )); do
        tok="${seg[idx]}"
        if [[ "$tok" =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]]; then
            (( idx++ ))
        else
            break
        fi
    done

    if (( idx >= n )) || [[ "${seg[idx]}" != "git" ]]; then
        return 1
    fi
    (( idx++ ))

    local target_dir=""
    local target_git_dir=""
    local target_work_tree=""
    local explicit_paths=()

    # Parse leading global git options before the subcommand
    while (( idx < n )); do
        tok="${seg[idx]}"
        case "$tok" in
            commit)
                break
                ;;
            -C)
                (( idx++ ))
                if (( idx >= n )); then
                    explicit_paths+=("")
                    break
                fi
                target_dir=$(pcf_join_dir "$target_dir" "${seg[idx]}")
                explicit_paths+=("$target_dir")
                (( idx++ ))
                ;;
            -C*)
                target_dir=$(pcf_join_dir "$target_dir" "${tok#-C}")
                explicit_paths+=("$target_dir")
                (( idx++ ))
                ;;
            --git-dir)
                (( idx++ ))
                if (( idx >= n )); then
                    explicit_paths+=("")
                    break
                fi
                target_git_dir=$(pcf_expand_tilde "${seg[idx]}")
                explicit_paths+=("$target_git_dir")
                (( idx++ ))
                ;;
            --git-dir=*)
                target_git_dir=$(pcf_expand_tilde "${tok#--git-dir=}")
                explicit_paths+=("$target_git_dir")
                (( idx++ ))
                ;;
            --work-tree)
                (( idx++ ))
                if (( idx >= n )); then
                    explicit_paths+=("")
                    break
                fi
                target_work_tree=$(pcf_expand_tilde "${seg[idx]}")
                target_dir="$target_work_tree"
                explicit_paths+=("$target_work_tree")
                (( idx++ ))
                ;;
            --work-tree=*)
                target_work_tree=$(pcf_expand_tilde "${tok#--work-tree=}")
                target_dir="$target_work_tree"
                explicit_paths+=("$target_work_tree")
                (( idx++ ))
                ;;
            -c)
                (( idx += 2 ))
                ;;
            -c*)
                (( idx++ ))
                ;;
            --no-pager|-p|--paginate|--bare|--no-replace-objects|--literal-pathspecs)
                (( idx++ ))
                ;;
            --*=*)
                (( idx++ ))
                ;;
            *)
                return 1
                ;;
        esac
    done

    if (( idx >= n )) || [[ "${seg[idx]}" != "commit" ]]; then
        return 1
    fi
    (( idx++ ))

    PCF_IS_GIT_COMMIT=1
    PCF_TARGET_DIR="$target_dir"
    PCF_TARGET_GIT_DIR="$target_git_dir"
    PCF_TARGET_WORK_TREE="$target_work_tree"
    if (( ${#explicit_paths[@]} > 0 )); then
        PCF_EXPLICIT_PATHS=("${explicit_paths[@]}")
    else
        PCF_EXPLICIT_PATHS=()
    fi

    # Parse git commit options
    while (( idx < n )); do
        tok="${seg[idx]}"
        case "$tok" in
            --)
                break
                ;;
            --amend)
                PCF_HAS_AMEND=1
                (( idx++ ))
                ;;
            --no-edit)
                PCF_HAS_NO_EDIT=1
                (( idx++ ))
                ;;
            --signoff)
                PCF_HAS_SIGNOFF_FLAG=1
                (( idx++ ))
                ;;
            -m|--message)
                PCF_HAS_MSG_FLAG=1
                (( idx++ ))
                if (( idx < n )); then
                    pcf_unwrap_message_arg "${seg[idx]}"
                    PCF_MSG_VALUES+=("$PCF_UNWRAPPED_MSG")
                    (( idx++ ))
                else
                    PCF_MSG_VALUES+=("")
                fi
                ;;
            --message=*)
                PCF_HAS_MSG_FLAG=1
                pcf_unwrap_message_arg "${tok#--message=}"
                PCF_MSG_VALUES+=("$PCF_UNWRAPPED_MSG")
                (( idx++ ))
                ;;
            -m*)
                PCF_HAS_MSG_FLAG=1
                pcf_unwrap_message_arg "${tok#-m}"
                PCF_MSG_VALUES+=("$PCF_UNWRAPPED_MSG")
                (( idx++ ))
                ;;
            -F|--file)
                PCF_HAS_FILE_FLAG=1
                (( idx += 2 ))
                ;;
            --file=*|-F*)
                PCF_HAS_FILE_FLAG=1
                (( idx++ ))
                ;;
            --trailer)
                (( idx++ ))
                if (( idx < n )); then
                    PCF_TRAILER_VALUES+=("${seg[idx]}")
                    (( idx++ ))
                fi
                ;;
            --trailer=*)
                PCF_TRAILER_VALUES+=("${tok#--trailer=}")
                (( idx++ ))
                ;;
            -c|-C|--reuse-message|--reedit-message|--fixup|--squash|--author|--date|--cleanup|-t|--template|--pathspec-from-file)
                (( idx += 2 ))
                ;;
            -[a-zA-Z]*)
                local flags="${tok#-}"
                if [[ "$flags" == *s* ]]; then
                    PCF_HAS_SIGNOFF_FLAG=1
                fi
                if [[ "$flags" == *F* ]]; then
                    PCF_HAS_FILE_FLAG=1
                    if [[ "$flags" == *F ]]; then
                        (( idx += 2 ))
                    else
                        (( idx++ ))
                    fi
                    continue
                fi
                if [[ "$flags" == *m* ]]; then
                    PCF_HAS_MSG_FLAG=1
                    if [[ "$flags" == *m ]]; then
                        (( idx++ ))
                        if (( idx < n )); then
                            pcf_unwrap_message_arg "${seg[idx]}"
                            PCF_MSG_VALUES+=("$PCF_UNWRAPPED_MSG")
                            (( idx++ ))
                        else
                            PCF_MSG_VALUES+=("")
                        fi
                    else
                        local after_m="${flags#*m}"
                        pcf_unwrap_message_arg "$after_m"
                        PCF_MSG_VALUES+=("$PCF_UNWRAPPED_MSG")
                        (( idx++ ))
                    fi
                    continue
                fi
                (( idx++ ))
                ;;
            *)
                (( idx++ ))
                ;;
        esac
    done

    return 0
}

pcf_parse_command() {
    local cmd="$1"
    pcf_reset_state
    if [[ -z "$cmd" ]]; then
        return 1
    fi

    pcf_tokenize_command "$cmd"

    local seg=()
    local tok
    for tok in "${PCF_TOKENS[@]}"; do
        if [[ "$tok" == $'\x1f' ]]; then
            if (( ${#seg[@]} > 0 )); then
                if pcf_try_parse_segment "${seg[@]}"; then
                    return 0
                fi
                seg=()
            fi
        else
            seg+=("$tok")
        fi
    done

    if (( ${#seg[@]} > 0 )); then
        if pcf_try_parse_segment "${seg[@]}"; then
            return 0
        fi
    fi

    return 1
}

pcf_validate_target_paths() {
    if (( ${#PCF_EXPLICIT_PATHS[@]} > 0 )); then
        local p
        for p in "${PCF_EXPLICIT_PATHS[@]}"; do
            if [[ -z "$p" || ! -e "$p" ]]; then
                echo "ERROR: Target repository path does not exist: ${p:-<empty>}" >&2
                exit 2
            fi
        done
    fi
}

pcf_run_git() {
    local git_args=()
    if [[ -n "$PCF_TARGET_DIR" ]]; then
        git_args+=(-C "$PCF_TARGET_DIR")
    fi
    if [[ -n "$PCF_TARGET_GIT_DIR" ]]; then
        git_args+=(--git-dir="$PCF_TARGET_GIT_DIR")
    fi
    if [[ -n "$PCF_TARGET_WORK_TREE" ]]; then
        git_args+=(--work-tree="$PCF_TARGET_WORK_TREE")
    fi
    git "${git_args[@]}" "$@"
}

pcf_load_repo_config() {
    pcf_validate_target_paths
    if ! pcf_run_git rev-parse --git-dir >/dev/null 2>&1; then
        return 1
    fi
    PCF_TRAILERS_REQUIRED=$(pcf_run_git config --get --type=bool claude-code.commit-trailers-required 2>/dev/null || echo "false")
    local pat
    pat=$(pcf_run_git config --get claude-code.commit-subject-pattern 2>/dev/null || true)
    if [[ -n "$pat" ]]; then
        PCF_SUBJECT_PATTERN="$pat"
    else
        PCF_SUBJECT_PATTERN="$PCF_DEFAULT_SUBJECT_PATTERN"
    fi
    return 0
}

pcf_first_nonempty_line() {
    local text="$1"
    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ -n "${line//[[:space:]]/}" ]]; then
            printf '%s' "$line"
            return 0
        fi
    done <<< "$text"
}

pcf_validate_commit_text() {
    local subject="$1"
    local full_text="$2"
    local has_signoff_flag="${3:-0}"
    local errors=""
    local anchored_pattern="$PCF_SUBJECT_PATTERN"
    if [[ "$anchored_pattern" != ^* ]]; then
        anchored_pattern="^${anchored_pattern}"
    fi

    if [[ -z "$subject" ]] || ! printf '%s\n' "$subject" | grep -qE "$anchored_pattern"; then
        errors="${errors}\n  - ${PCF_ERR_PREFIX}"
    fi

    if ! printf '%s\n' "$full_text" | grep -qE '(^|[[:space:]])Assisted-by:'; then
        errors="${errors}\n  - ${PCF_ERR_ASSISTED_BY}"
    fi

    if (( ! has_signoff_flag )) && ! printf '%s\n' "$full_text" | grep -qE '(^|[[:space:]])Signed-off-by:'; then
        errors="${errors}\n  - ${PCF_ERR_SIGNED_OFF_BY}"
    fi

    if [[ -n "$errors" ]]; then
        printf "BLOCK: commit message missing required elements:%b\n" "$errors" >&2
        return 2
    fi
    return 0
}
