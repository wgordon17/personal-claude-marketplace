# rust-analyzer-rustup

Rust language server (requires rust-analyzer via rustup).

## Purpose

Provides Rust Language Server Protocol (LSP) intelligence (`.rs`) for Claude Code with Cargo build scripts and procedural macro support enabled.

## Prerequisites & Command

- **Prerequisite:** [Rust / rustup](https://rustup.rs/) with `rust-analyzer` installed (`rustup component add rust-analyzer`)
- **LSP Command (`.lsp.json`):** `rust-analyzer` (stdio transport)

## Installation

```bash
claude plugin install rust-analyzer-rustup@personal-claude-marketplace
```

## Verification

```bash
rust-analyzer --version
```

