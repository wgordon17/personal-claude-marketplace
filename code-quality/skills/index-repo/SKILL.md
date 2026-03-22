---
name: index-repo
description: |
  Generate a PROJECT_INDEX.md for token-efficient codebase orientation. Use when starting work
  on an unfamiliar repo, when agents need project context without reading the full codebase,
  or when asked to "index the repo", "create a project index", "map the codebase".
allowed-tools: [Read, Glob, Grep, Write, Bash]
---

# Repository Indexing

Generate a compact PROJECT_INDEX.md (~3K tokens) that gives any agent full project orientation
without reading the entire codebase (~50-60K tokens).

## When to Use

- Starting work on an unfamiliar repository
- Agents need project context (unfuck, swarm, or any multi-agent workflow)
- User asks to "index", "map", or "summarize" the repo structure
- PROJECT_INDEX.md is missing or stale

## Index Creation Flow

### Phase 1: Analyze Repository Structure

Run 5 parallel Glob searches:

1. **Code:** `src/**/*.{ts,py,js,tsx,jsx,go,rs}`, `lib/**/*.{ts,py,js}`
2. **Documentation:** `docs/**/*.md`, `*.md` (root level)
3. **Configuration:** `*.toml`, `*.yaml`, `*.yml`, `*.json` (exclude lock files, node_modules)
4. **Tests:** `tests/**/*`, `**/*.test.*`, `**/*.spec.*`
5. **Scripts & Tools:** `scripts/**/*`, `bin/**/*`, `Makefile`

### Phase 2: Extract Metadata

For each file category, identify:
- Entry points (main.py, index.ts, cli.py, __main__.py)
- Key modules and their public API surface
- Dependencies (from pyproject.toml, package.json, go.mod, Cargo.toml)
- Build/test/lint commands (from Makefile, scripts, CI config)

### Phase 3: Generate PROJECT_INDEX.md

Determine output location: check for a project memory directory (`hack/`, `.local/`, `scratch/`,
`.dev/`) and write there. Fall back to project root only if none exists.

```markdown
# Project Index: {project_name}

Generated: {timestamp}

## Project Structure

{tree view of main directories, 2 levels deep}

## Entry Points

- CLI: {path} — {description}
- API: {path} — {description}
- Tests: {path} — {description}

## Core Modules

### {module_name}
- Path: {path}
- Purpose: {1-line description}
- Key exports: {list}

## Configuration

- {config_file}: {purpose}

## Build & Test

- Build: {command}
- Test: {command}
- Lint: {command}

## Key Dependencies

- {dependency}: {version} — {purpose}

## Documentation

- {doc_file}: {topic}
```

### Phase 4: Validation

- All entry points identified?
- Core modules documented?
- Index size < 5KB?
- Build/test commands accurate? (verify by reading Makefile/CI config)

## Modes

**Full index** (default): Analyze everything, write PROJECT_INDEX.md
**Update**: Re-read existing PROJECT_INDEX.md, update only changed sections
**Quick**: Skip tests and docs, index only code structure and entry points

## Output

Creates `PROJECT_INDEX.md` in the project memory directory (`hack/`, `.local/`, `scratch/`,
`.dev/`) or project root if none exists (~3K tokens, human-readable).
