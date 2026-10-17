# gopls-go

Go language server (requires gopls on PATH).

## Purpose

Provides Go Language Server Protocol (LSP) intelligence (`.go`) for Claude Code using `gopls` with `GOTELEMETRY=off`.

## Prerequisites & Command

- **Prerequisite:** [Go](https://go.dev/) and `gopls` (`go install golang.org/x/tools/gopls@latest`) on `PATH`
- **LSP Command (`.lsp.json`):** `gopls` (stdio transport)

## Installation

```bash
claude plugin install gopls-go@personal-claude-marketplace
```

## Verification

```bash
gopls version
```

