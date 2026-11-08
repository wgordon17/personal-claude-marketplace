#!/usr/bin/env bash
# shellcheck disable=SC2034
#
# Shared helper for project-level commit format enforcement.
# Sourced by both:
#   - dev-guard/hooks/validate-commit-message.sh (PostToolUse)
#   - dev-guard/hooks/enforce-project-commit-format.sh (PreToolUse)
#
# The parser is a static best-effort reader of `git commit` command strings. It
# understands env assignments, wrapper commands, path-qualified git, pipelines,
# subshells, heredoc bodies, `cd`, and git's global options. It does not evaluate
# shell. `bash -c`, `eval`, and git aliases are invisible to it; the PostToolUse
# hook re-validates HEAD for any command that mentions `git ... commit`, which
# covers `bash -c` and `eval` but not aliases.

PCF_DEFAULT_SUBJECT_PATTERN='^([A-Z][A-Z0-9]+-[0-9]+|NO-ISSUE): '

PCF_ERR_PREFIX="Missing Jira key or NO-ISSUE prefix (e.g., PROJ-123: or NO-ISSUE:)"
PCF_ERR_ASSISTED_BY="Missing Assisted-by trailer (e.g., Assisted-by: Claude <noreply@anthropic.com>)"
PCF_ERR_SIGNED_OFF_BY="Missing Signed-off-by trailer (use -s flag: git commit -s -m ...)"

# Per-commit state, cleared before each commit is parsed.
pcf_reset_commit_state() {
    PCF_TARGET_DIR=""
    PCF_TARGET_GIT_DIR=""
    PCF_TARGET_WORK_TREE=""
    PCF_TARGET_UNRESOLVED=0
    PCF_EXPLICIT_PATHS=()
    PCF_HAS_SIGNOFF_FLAG=0
    PCF_HAS_MSG_FLAG=0
    PCF_DEFER_TO_POST=0
    PCF_MSG_VALUES=()
    PCF_TRAILER_VALUES=()
    PCF_TRAILERS_REQUIRED="false"
    PCF_SUBJECT_PATTERN="$PCF_DEFAULT_SUBJECT_PATTERN"
}

# Whole-command state plus per-commit state.
pcf_reset_state() {
    PCF_CD_DIR=""
    PCF_CD_UNRESOLVED=0
    PCF_SAW_ANSI_C=0
    PCF_SEG_POS=0
    pcf_reset_commit_state
}
pcf_reset_state

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

# True when a path token depends on shell expansion ($VAR, $(...), backticks) and so
# cannot be resolved statically.
pcf_is_dynamic() {
    # shellcheck disable=SC2016
    [[ "$1" == *'$'* || "$1" == *'`'* ]]
}

# Tokenize a shell command string into tokens, emitting "\x1f" tokens at
# unquoted command separators (; & && | || ( ) and newline), stripping unquoted
# # comments through end-of-line, and skipping heredoc bodies.
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
    local hd_delims=() hd_strip=()
    local j c q delim strip hi rest line consumed cmpline

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

        # ANSI-C quoting ($'...') is not decoded here; remember it so commits defer.
        if [[ "$ch" == '$' && "$next_ch" == "'" ]]; then
            PCF_SAW_ANSI_C=1
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

        # Here-string (<<<): an ordinary redirection, not a heredoc.
        if [[ "$ch" == "<" && "$next_ch" == "<" && "${cmd:i+2:1}" == "<" ]]; then
            cur+="<<<"
            has_cur=1
            (( i += 3 ))
            continue
        fi

        # Heredoc operator (<<WORD, <<-WORD, <<'WORD'): note the delimiter so the
        # body, which starts after the next newline, is not tokenized as shell.
        if [[ "$ch" == "<" && "$next_ch" == "<" && "${cmd:i+2:1}" != "<" ]]; then
            j=$(( i + 2 ))
            strip=0
            if [[ "${cmd:j:1}" == "-" ]]; then
                strip=1
                (( j++ ))
            fi
            while [[ "${cmd:j:1}" == " " || "${cmd:j:1}" == $'\t' ]]; do
                (( j++ ))
            done
            delim=""
            q=""
            while (( j < len )); do
                c="${cmd:j:1}"
                if [[ -n "$q" ]]; then
                    if [[ "$c" == "$q" ]]; then
                        q=""
                    else
                        delim+="$c"
                    fi
                elif [[ "$c" == "'" || "$c" == '"' ]]; then
                    q="$c"
                else
                    case "$c" in
                        " " | $'\t' | $'\n' | ";" | "&" | "|" | "(" | ")" | "<" | ">") break ;;
                        *) delim+="$c" ;;
                    esac
                fi
                (( j++ ))
            done
            if [[ -n "$delim" ]]; then
                hd_delims+=("$delim")
                hd_strip+=("$strip")
            fi
            i=$j
            continue
        fi

        # Unquoted comment: '#' at start of token skips until newline
        if [[ "$ch" == "#" && $has_cur -eq 0 ]]; then
            while (( i < len )) && [[ "${cmd:i:1}" != $'\n' ]]; do
                (( i++ ))
            done
            continue
        fi

        # Command separators: ; & && | || ( ) and newline
        if [[ "$ch" == ";" || "$ch" == $'\n' || "$ch" == "&" || "$ch" == "|" \
              || "$ch" == "(" || "$ch" == ")" ]]; then
            if (( has_cur )); then
                PCF_TOKENS+=("$cur")
                cur=""
                has_cur=0
            fi
            PCF_TOKENS+=($'\x1f')
            if [[ ( "$ch" == "&" || "$ch" == "|" ) && "$next_ch" == "$ch" ]]; then
                (( i += 2 ))
            else
                (( i++ ))
            fi
            # Skip the body of any heredocs opened on the line that just ended.
            if [[ "$ch" == $'\n' ]] && (( ${#hd_delims[@]} > 0 )); then
                for hi in "${!hd_delims[@]}"; do
                    rest="${cmd:i}"
                    consumed=0
                    while IFS= read -r line || [[ -n "$line" ]]; do
                        consumed=$(( consumed + ${#line} + 1 ))
                        cmpline="$line"
                        if [[ "${hd_strip[hi]}" == "1" ]]; then
                            cmpline="${line#"${line%%[!$'\t']*}"}"
                        fi
                        if [[ "$cmpline" == "${hd_delims[hi]}" ]]; then
                            break
                        fi
                    done <<< "$rest"
                    (( i += consumed ))
                done
                if (( i > len )); then
                    i=$len
                fi
                hd_delims=()
                hd_strip=()
            fi
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

# Record one -m / --message value.
pcf_add_message() {
    PCF_HAS_MSG_FLAG=1
    pcf_unwrap_message_arg "$1"
    PCF_MSG_VALUES+=("$PCF_UNWRAPPED_MSG")
}

# Record one --trailer value. git accepts `Key=value` as well as `Key: value`.
pcf_add_trailer() {
    local t="$1"
    if [[ "$t" =~ ^([A-Za-z][A-Za-z0-9-]*)=(.*)$ ]]; then
        t="${BASH_REMATCH[1]}: ${BASH_REMATCH[2]}"
    fi
    PCF_TRAILER_VALUES+=("$t")
}

# Apply one of git's global path options (-C, --git-dir, --work-tree) to the
# current commit's target. Values that depend on shell expansion mark the target
# unresolved. An empty value is recorded as an explicit path so
# pcf_check_target_paths fails closed on it; relative values are checked against
# the directory git will actually be in (the -C / cd target).
pcf_apply_global_path() {
    local opt="$1" raw="$2" val
    if pcf_is_dynamic "$raw"; then
        PCF_TARGET_UNRESOLVED=1
        return 0
    fi
    case "$opt" in
        -C)
            PCF_TARGET_DIR=$(pcf_join_dir "$PCF_TARGET_DIR" "$raw")
            PCF_EXPLICIT_PATHS+=("$PCF_TARGET_DIR")
            ;;
        --git-dir)
            val=$(pcf_expand_tilde "$raw")
            PCF_TARGET_GIT_DIR="$val"
            PCF_EXPLICIT_PATHS+=("$(pcf_join_dir "$PCF_TARGET_DIR" "$val")")
            ;;
        --work-tree)
            val=$(pcf_expand_tilde "$raw")
            PCF_TARGET_WORK_TREE="$val"
            if [[ "$val" == /* ]]; then
                PCF_TARGET_DIR="$val"
            fi
            PCF_EXPLICIT_PATHS+=("$(pcf_join_dir "$PCF_TARGET_DIR" "$val")")
            ;;
    esac
}

# Parse a command segment (array of tokens) to see if it is a `git commit` invocation.
# On success the PCF_* state describes the commit; on failure the per-commit state
# is stale and callers must treat the parse as "not a commit".
pcf_try_parse_segment() {
    local seg=("$@")
    local n=${#seg[@]}
    local idx=0
    local tok

    PCF_TARGET_DIR="$PCF_CD_DIR"
    PCF_TARGET_GIT_DIR=""
    PCF_TARGET_WORK_TREE=""
    PCF_TARGET_UNRESOLVED="$PCF_CD_UNRESOLVED"
    PCF_EXPLICIT_PATHS=()

    # Skip env assignments, wrapper commands, and shell keywords before the command word
    while (( idx < n )); do
        tok="${seg[idx]}"
        case "$tok" in
            GIT_DIR=*) pcf_apply_global_path --git-dir "${tok#GIT_DIR=}" ;;
            GIT_WORK_TREE=*) pcf_apply_global_path --work-tree "${tok#GIT_WORK_TREE=}" ;;
            [A-Za-z_]*=*) ;;
            command|exec|time|nohup|env|'!'|'{'|if|then|do|else|elif|while|until|-*) ;;
            *) break ;;
        esac
        (( idx++ ))
    done

    if (( idx >= n )) || [[ "${seg[idx]}" != "git" && "${seg[idx]}" != */git ]]; then
        return 1
    fi
    (( idx++ ))

    # Parse leading global git options before the subcommand
    while (( idx < n )); do
        tok="${seg[idx]}"
        case "$tok" in
            commit)
                break
                ;;
            -C|--git-dir|--work-tree)
                pcf_apply_global_path "$tok" "${seg[idx+1]:-}"
                (( idx += 2 ))
                ;;
            -C?*)
                pcf_apply_global_path -C "${tok#-C}"
                (( idx++ ))
                ;;
            --git-dir=*|--work-tree=*)
                pcf_apply_global_path "${tok%%=*}" "${tok#*=}"
                (( idx++ ))
                ;;
            -c|--namespace|--super-prefix|--config-env|--attr-source)
                (( idx += 2 ))
                ;;
            -c?*|-p|-P|--paginate|--no-pager|--bare|--no-replace-objects|--literal-pathspecs \
                |--exec-path \
                |--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|--no-optional-locks \
                |--no-advice|--no-lazy-fetch|--*=*)
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

    # ANSI-C quoted words ($'...') are not decoded, so the message cannot be read.
    if (( PCF_SAW_ANSI_C )); then
        PCF_DEFER_TO_POST=1
    fi

    # Parse git commit options. Message-bearing options that cannot be read
    # statically (-F, -C/-c, --fixup, ...) defer to the PostToolUse check.
    local flags ch rest j
    while (( idx < n )); do
        tok="${seg[idx]}"
        (( idx++ ))
        case "$tok" in
            --)
                break
                ;;
            --signoff)
                PCF_HAS_SIGNOFF_FLAG=1
                ;;
            --message)
                pcf_add_message "${seg[idx]:-}"
                (( idx++ ))
                ;;
            --message=*)
                pcf_add_message "${tok#--message=}"
                ;;
            --trailer)
                pcf_add_trailer "${seg[idx]:-}"
                (( idx++ ))
                ;;
            --trailer=*)
                pcf_add_trailer "${tok#--trailer=}"
                ;;
            --file|--reuse-message|--reedit-message|--fixup|--squash|--template|--pathspec-from-file)
                PCF_DEFER_TO_POST=1
                (( idx++ ))
                ;;
            --file=*|--reuse-message=*|--reedit-message=*|--fixup=*|--squash=*|--template=*|--pathspec-from-file=*)
                PCF_DEFER_TO_POST=1
                ;;
            --author|--date|--cleanup)
                (( idx++ ))
                ;;
            --*)
                ;;
            -?*)
                # Short-flag cluster (e.g. -asm "msg", -am"msg"): scan left to right
                # and stop at the first flag that takes a value; the rest of the
                # token (or the next token) is that value, never more flags.
                flags="${tok#-}"
                for (( j = 0; j < ${#flags}; j++ )); do
                    ch="${flags:j:1}"
                    rest="${flags:j+1}"
                    case "$ch" in
                        s)
                            PCF_HAS_SIGNOFF_FLAG=1
                            ;;
                        m|F|C|c|t)
                            if [[ -z "$rest" ]]; then
                                rest="${seg[idx]:-}"
                                (( idx++ ))
                            fi
                            if [[ "$ch" == "m" ]]; then
                                pcf_add_message "$rest"
                            else
                                PCF_DEFER_TO_POST=1
                            fi
                            break
                            ;;
                        u|S)
                            break
                            ;;
                    esac
                done
                ;;
        esac
    done

    return 0
}

# Find the next `git commit` invocation after the current scan position. Returns 0 and
# sets the per-commit PCF_* state, or returns 1 when no further commit remains.
pcf_parse_next() {
    local seg=()
    local tok
    local n=${#PCF_TOKENS[@]}
    pcf_reset_commit_state

    while (( PCF_SEG_POS < n )); do
        tok="${PCF_TOKENS[PCF_SEG_POS]}"
        (( PCF_SEG_POS++ ))
        if [[ "$tok" != $'\x1f' ]]; then
            seg+=("$tok")
            continue
        fi
        if (( ${#seg[@]} > 0 )); then
            if pcf_try_parse_segment "${seg[@]}"; then
                return 0
            fi
            if [[ "${seg[0]}" == "cd" && -n "${seg[1]:-}" && "${seg[1]}" != -* ]]; then
                if pcf_is_dynamic "${seg[1]}"; then
                    PCF_CD_UNRESOLVED=1
                else
                    PCF_CD_DIR=$(pcf_join_dir "$PCF_CD_DIR" "${seg[1]}")
                    # An absolute cd makes the working directory known again.
                    if [[ "$(pcf_expand_tilde "${seg[1]}")" == /* ]]; then
                        PCF_CD_UNRESOLVED=0
                    fi
                fi
            fi
            seg=()
        fi
    done
    return 1
}

# Tokenize *cmd* and parse its first `git commit`. Returns 0 and sets the PCF_* state, or
# returns 1 with the state reset when the command contains no `git commit`.
# Further commits in the same command are available through pcf_parse_next.
pcf_parse_command() {
    local cmd="$1"
    pcf_reset_state
    if [[ -z "$cmd" ]]; then
        return 1
    fi

    pcf_tokenize_command "$cmd"
    PCF_TOKENS+=($'\x1f')

    if pcf_parse_next; then
        return 0
    fi
    pcf_reset_state
    return 1
}

# Fail closed (return 2) when an explicit -C / --git-dir / --work-tree path is
# missing, so a typo cannot silently disable enforcement.
pcf_check_target_paths() {
    local p
    if (( ${#PCF_EXPLICIT_PATHS[@]} > 0 )); then
        for p in "${PCF_EXPLICIT_PATHS[@]}"; do
            if [[ -z "$p" || ! -e "$p" ]]; then
                printf 'ERROR: Target repository path does not exist: %s\n' "${p:-<empty>}" >&2
                return 2
            fi
        done
    fi
    return 0
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

# Load the target repository's commit-format config into PCF_TRAILERS_REQUIRED
# and PCF_SUBJECT_PATTERN. Returns 0 on success, 1 when the target is not a git
# repository, and 2 when an explicit target path does not exist (fail closed).
# A target that depends on shell expansion cannot be resolved here, so it falls back to
# the current directory's repository.
pcf_load_repo_config() {
    if (( PCF_TARGET_UNRESOLVED )); then
        PCF_TARGET_DIR=""
        PCF_TARGET_GIT_DIR=""
        PCF_TARGET_WORK_TREE=""
        PCF_EXPLICIT_PATHS=()
    fi
    pcf_check_target_paths || return 2
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

# Validate a commit's subject and trailers. Trailers must start a line; the same
# words inside the subject or prose do not count.
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

    if ! printf '%s\n' "$full_text" | grep -qE '^[[:space:]]*Assisted-by:'; then
        errors="${errors}\n  - ${PCF_ERR_ASSISTED_BY}"
    fi

    if (( ! has_signoff_flag )) && ! printf '%s\n' "$full_text" | grep -qE '^[[:space:]]*Signed-off-by:'; then
        errors="${errors}\n  - ${PCF_ERR_SIGNED_OFF_BY}"
    fi

    if [[ -n "$errors" ]]; then
        printf "BLOCK: commit message missing required elements:%b\n" "$errors" >&2
        return 2
    fi
    return 0
}
