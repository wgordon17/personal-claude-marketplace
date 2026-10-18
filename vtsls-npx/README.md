# vtsls-npx

TypeScript/JavaScript language server (via npx - always latest).

## Purpose

Provides TypeScript and JavaScript Language Server Protocol (LSP) intelligence (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`) for Claude Code using `@vtsls/language-server` executed on-demand via `npx`.

## Prerequisites & Command

- **Prerequisite:** [Node.js / npm](https://nodejs.org/) (`npx` on `PATH`)
- **LSP Command (`.lsp.json`):** `npx -y @vtsls/language-server --stdio`

## Installation

```bash
claude plugin install vtsls-npx@personal-claude-marketplace
```

## Verification

```bash
npx -y @vtsls/language-server --version
```

