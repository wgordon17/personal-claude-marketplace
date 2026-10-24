# vscode-html-css-npx

HTML/CSS language servers (via npx - always latest).

## Purpose

Provides HTML and CSS/SCSS/LESS Language Server Protocol (LSP) intelligence (`.html`, `.htm`, `.css`, `.scss`, `.less`) for Claude Code using `vscode-langservers-extracted` executed on-demand via `npx`.

## Prerequisites & Command

- **Prerequisite:** [Node.js / npm](https://nodejs.org/) (`npx` on `PATH`)
- **LSP Commands (`.lsp.json`):**
  - HTML: `npx -y --package=vscode-langservers-extracted vscode-html-language-server --stdio`
  - CSS: `npx -y --package=vscode-langservers-extracted vscode-css-language-server --stdio`

## Installation

```bash
claude plugin install vscode-html-css-npx@personal-claude-marketplace
```

## Verification

```bash
npx -y --package=vscode-langservers-extracted vscode-html-language-server --version
```

