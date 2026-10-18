# pyright-uvx

Python language server (via uvx - always latest).

## Purpose

Provides Python Language Server Protocol (LSP) intelligence (`.py`, `.pyi`) for Claude Code using Pyright executed on-demand via `uvx`.

## Prerequisites & Command

- **Prerequisite:** [`uv`](https://docs.astral.sh/uv/) (`uvx` on `PATH`)
- **LSP Command (`.lsp.json`):** `uvx --from pyright pyright-langserver --stdio`

## Installation

```bash
claude plugin install pyright-uvx@personal-claude-marketplace
```

## Verification

```bash
uvx --from pyright pyright-langserver --version
```

