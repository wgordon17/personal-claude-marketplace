#!/bin/bash
# Global Betterleaks v1/v2 secret scanner hook for Git 2.54+ (hook.betterleaks.*)
# Runs on `prepare-commit-msg` so `git commit -n` / `--no-verify` cannot bypass it.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${BETTERLEAKS_CONFIG_DIR:-$HOME/.config/betterleaks}"
ACTIVE_CONFIG="$CONFIG_DIR/betterleaks-unified.toml"
STAMP_FILE="$CONFIG_DIR/.compiled-for-version"
SYNC_SCRIPT="$CONFIG_DIR/sync_overlay.py"
if [ ! -f "$SYNC_SCRIPT" ]; then
    SYNC_SCRIPT="$SCRIPT_DIR/../scripts/sync-betterleaks-overlay.py"
fi

if ! command -v betterleaks >/dev/null 2>&1; then
    echo "betterleaks: binary not found in PATH; refusing commit" >&2
    exit 1
fi

CURRENT_VER="$(betterleaks --version 2>/dev/null || true)"
STAMP_VER=""
if [ -f "$STAMP_FILE" ]; then
    STAMP_VER="$(tr -d '\r\n' < "$STAMP_FILE")"
fi

# Auto-recompile config in <0.3s if betterleaks was upgraded (e.g. v1.9.0 -> v2.0.0 via brew)
if [ ! -f "$ACTIVE_CONFIG" ] || [ "$CURRENT_VER" != "$STAMP_VER" ]; then
    if command -v uv >/dev/null 2>&1 && [ -f "$SYNC_SCRIPT" ]; then
        uv run "$SYNC_SCRIPT" --offline >/dev/null 2>&1 || uv run "$SYNC_SCRIPT" >/dev/null
    fi
elif [ -n "$(find "$ACTIVE_CONFIG" -mtime +7 2>/dev/null)" ]; then
    # Non-blocking background refresh when overlay cache is >7 days old
    if command -v uv >/dev/null 2>&1 && [ -f "$SYNC_SCRIPT" ]; then
        (uv run "$SYNC_SCRIPT" >/dev/null 2>&1 &)
    fi
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

if [[ "$CURRENT_VER" =~ (^|[^0-9])2\.[0-9]+\.[0-9]+ ]]; then
    # Betterleaks v2.x CLI syntax
    exec betterleaks git --staged --redact --confidence medium --config "$ACTIVE_CONFIG" "$REPO_ROOT"
else
    # Betterleaks v1.x (v1.9.0) CLI syntax
    IGNORE_ARGS=()
    if [ -f "$REPO_ROOT/.betterleaksignore" ]; then
        IGNORE_ARGS=(--gitleaks-ignore-path "$REPO_ROOT/.betterleaksignore")
    elif [ -f "$REPO_ROOT/.gitleaksignore" ]; then
        IGNORE_ARGS=(--gitleaks-ignore-path "$REPO_ROOT/.gitleaksignore")
    fi
    exec betterleaks git --pre-commit --staged --redact --no-banner --confidence medium \
        --config "$ACTIVE_CONFIG" ${IGNORE_ARGS[@]+"${IGNORE_ARGS[@]}"} "$REPO_ROOT"
fi
