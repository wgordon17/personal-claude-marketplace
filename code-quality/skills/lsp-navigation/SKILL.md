---
name: lsp-navigation
description: PROACTIVE skill - Use when navigating code, understanding symbol definitions, finding references, or exploring call hierarchies. Triggers include questions like "where is X defined", "what calls Y", "show usages of Z", or any code exploration task. Prefer LSP over grep for semantic navigation.
allowed-tools: [LSP, Read, Grep, Glob]
---

# lsp-navigation

This skill optimizes code navigation by using LSP (Language Server Protocol) for semantic understanding rather than text-based search.

## When to Use LSP — Checklist

Use LSP when you need to:
- [ ] Find where a symbol (function, class, variable) is **defined**
- [ ] Find all **references/usages** of a symbol
- [ ] Get **type information** or **documentation** for a symbol
- [ ] List all **symbols** in a file (functions, classes, etc.)
- [ ] Search for symbols by name **across the workspace**
- [ ] Find **implementations** of an interface or abstract method
- [ ] Understand **call hierarchy** (who calls what)

## Operations Reference

| Operation | Purpose | Example Use Case |
|-----------|---------|------------------|
| `goToDefinition` | Jump to where symbol is defined | "Where is `processRequest` defined?" |
| `findReferences` | All locations using this symbol | "What code calls `authenticate()`?" |
| `hover` | Get type info and docs | "What type does `user` have here?" |
| `documentSymbol` | List all symbols in file | "What functions are in this file?" |
| `workspaceSymbol` | Search symbols by name | "Find class named `Config`" |
| `goToImplementation` | Find interface implementations | "What implements `Repository`?" |
| `prepareCallHierarchy` | Get callable item at position | Prepare for call hierarchy queries |
| `incomingCalls` | What calls this function | "What code invokes `validate()`?" |
| `outgoingCalls` | What this function calls | "What does `main()` call?" |

## LSP vs Text Search Decision Tree

```
Need to find something in code?
├── Is it a SYMBOL (function, class, variable)?
│   ├── Need its definition? → LSP goToDefinition
│   ├── Need all usages? → LSP findReferences
│   ├── Need type/docs? → LSP hover
│   └── Need call flow? → LSP incomingCalls/outgoingCalls
├── Is it a TEXT PATTERN (string, comment, TODO)?
│   └── Use Grep
├── Is it a FILE PATTERN (*.ts, src/**/*.py)?
│   └── Use Glob
└── Unknown symbol name?
    └── LSP workspaceSymbol (fuzzy search)
```

## Key Differences: LSP vs Grep

| Aspect | LSP | Grep |
|--------|-----|------|
| Search type | Semantic (understands code) | Text pattern matching |
| Finds imports | Yes, follows module resolution | Only if text matches |
| Distinguishes definitions from usages | Yes | No |
| Type-aware | Yes | No |
| Works across languages | Per-language server | Yes |
| Requires language server | Yes | No |

**Rule of thumb:** If you're looking for a *symbol* (something with a definition), use LSP. If you're looking for *text* (a string, pattern, or comment), use Grep.

## Language-Specific Notes

### Python (pyright via uvx)

- Best for type-aware navigation
- Works with type hints, stubs, and PEP 484/526/604 annotations
- Recognizes decorators, comprehensions, class hierarchies
- Understands `__init__.py` module structure
- Handles relative and absolute imports

**Pyright-specific behaviors:**
- Reports diagnostics (type errors) in results
- Resolves `TYPE_CHECKING` imports
- Understands dataclasses, Pydantic models, etc.

### TypeScript/JavaScript (vtsls via npx)

- Excellent JSDoc support
- Works across `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`
- Understands module resolution (`tsconfig.json` paths)
- Handles type definitions (`.d.ts` files)

**vtsls-specific behaviors:**
- Resolves path aliases from tsconfig
- Understands barrel exports
- Works with monorepo structures

### Go (gopls)

- Standard Go tooling integration
- Excellent interface implementation detection
- Package-aware navigation
- Understands embedded types

**gopls-specific behaviors:**
- Finds interface implementations across packages
- Understands struct embedding
- Works with Go modules

### HTML/CSS (vscode-langservers via npx)

- Element and attribute completion
- CSS selector navigation
- Works with embedded styles

## Usage Examples

### Find where a class is defined

```
LSP(operation="goToDefinition", filePath="src/api/routes.py", line=15, character=23)
```

Position cursor on the class name (e.g., `UserController` at line 15, col 23).

### Find all callers of a function

```
LSP(operation="findReferences", filePath="src/utils/auth.ts", line=42, character=12)
```

Returns all locations where this function is called.

### List all functions in a module

```
LSP(operation="documentSymbol", filePath="src/services/user.go", line=1, character=1)
```

Returns structured list of all symbols (functions, types, constants).

### Search for a class across workspace

```
LSP(operation="workspaceSymbol", filePath="src/any_file.py", line=1, character=1)
```

Note: For `workspaceSymbol`, the file just needs to match the language. The search is global.

### Get type information

```
LSP(operation="hover", filePath="src/handler.ts", line=67, character=20)
```

Returns type signature, documentation, and JSDoc/docstring.

### Find interface implementations

```
LSP(operation="goToImplementation", filePath="pkg/repository/interface.go", line=12, character=10)
```

Finds all concrete implementations of an interface.

### Understand call hierarchy

**Step 1: Prepare the call hierarchy item:**
```
LSP(operation="prepareCallHierarchy", filePath="src/core.py", line=100, character=5)
```

**Step 2: Find incoming calls (who calls this):**
```
LSP(operation="incomingCalls", filePath="src/core.py", line=100, character=5)
```

**Step 3: Find outgoing calls (what this calls):**
```
LSP(operation="outgoingCalls", filePath="src/core.py", line=100, character=5)
```

## Parameter Reference

All LSP operations require:

| Parameter | Type | Description |
|-----------|------|-------------|
| `operation` | string | One of the operations listed above |
| `filePath` | string | Absolute or relative path to the file |
| `line` | integer | Line number (1-based, as shown in editors) |
| `character` | integer | Column position (1-based, as shown in editors) |

**Important notes:**
- Line and character are **1-based** (first line is 1, first column is 1)
- Position the cursor ON the symbol you're querying
- For multi-character symbols, any position within the symbol works

## Troubleshooting

### LSP Server Not Starting

1. **Check if file type has an LSP server:**
   - Python: `.py`, `.pyi`
   - TypeScript/JavaScript: `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`
   - Go: `.go`
   - HTML: `.html`, `.htm`
   - CSS: `.css`

2. **Verify prerequisites are installed:**
   ```bash
   uvx --version          # For Python
   npx --version          # For TypeScript/HTML
   gopls version          # For Go
   ```

3. **Check debug logs:**
   ```bash
   tail -f ~/.claude/debug/latest
   ```

### No Results Returned

- Ensure cursor position is ON a symbol
- Verify file path is correct (absolute paths recommended)
- Check that the file exists and is readable
- For external library definitions, the LSP may not index them

### Wrong Results

- Ensure line/character are 1-based (not 0-based)
- Verify you're on the correct symbol (not whitespace or operators)
- Try `hover` first to confirm LSP recognizes the symbol

### Cache Issues

**Clear pyright cache:**
```bash
uv cache clean pyright
```

**Clear npx cache:**
```bash
rm -rf ~/.npm/_npx
```

## Integration with uv-python

The pyright language server runs via `uvx`, using the same infrastructure as other Python tools:

- Uses `uvx --from pyright pyright-langserver --stdio`
- No global installation needed
- Cache: `~/.cache/uv/`
- Always gets latest version on first run
- Clear cache: `uv cache clean pyright`

## Available Language Servers

Current configuration (installed from `personal-claude-marketplace`):

| Plugin | Language | Command |
|--------|----------|---------|
| pyright-uvx | Python | `uvx --from pyright pyright-langserver --stdio` |
| vtsls-npx | TypeScript/JS | `npx -y @vtsls/language-server --stdio` |
| gopls-go | Go | `gopls serve` |
| vscode-html-css-npx | HTML/CSS | `npx -y vscode-langservers-extracted vscode-html-language-server --stdio` |

