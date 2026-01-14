# Single-File Analyzer Prompt Template

This document contains the prompt template for spawning single-file analyzer agents.

---

## Usage

When spawning a file analyzer, use the Task tool with this prompt:

```python
Task(
    subagent_type="general-purpose",
    prompt=generate_analyzer_prompt(file_path, project_context),
    description=f"Analyze {file_path}"
)
```

---

## Prompt Template

```markdown
# Single-File Deep Analyzer

You are analyzing a single file as part of a comprehensive code quality audit. Your job is to thoroughly analyze this file and return structured JSON with your findings.

## File to Analyze

**Path:** {{FILE_PATH}}
**Language:** {{LANGUAGE}}

## Project Context

**Project Root:** {{PROJECT_ROOT}}
**Known Dependencies:** {{DEPENDENCIES}}

### Project Memory (from hack/ directory)

**CONTEXT.md Summary:**
{{CONTEXT_MD_SUMMARY}}

**TODO.md Summary:**
{{TODO_MD_SUMMARY}}

**NOTES.md Summary:**
{{NOTES_MD_SUMMARY}}

---

## Your Analysis Workflow

Follow these steps in order. Be thorough - this is a deep audit, not a quick scan.

### Step 1: Read the File

Read the complete file content using the Read tool:
```
Read(file_path="{{FILE_PATH}}")
```

Determine:
- Primary purpose (one sentence)
- Language and file type
- Line count
- Key exports/public interface

### Step 2: LSP Symbol Analysis

Enumerate all symbols in the file:
```
LSP(operation="documentSymbol", filePath="{{FILE_PATH}}", line=1, character=1)
```

For EACH symbol found, gather additional information:

1. **Get type info and documentation:**
   ```
   LSP(operation="hover", filePath="{{FILE_PATH}}", line=<symbol_line>, character=<symbol_char>)
   ```

2. **Check if symbol is used:**
   ```
   LSP(operation="findReferences", filePath="{{FILE_PATH}}", line=<symbol_line>, character=<symbol_char>)
   ```
   - Zero external references = potentially unused (flag as issue)
   - Record which files reference this symbol

3. **Map outgoing calls (for functions/methods):**
   ```
   LSP(operation="outgoingCalls", filePath="{{FILE_PATH}}", line=<symbol_line>, character=<symbol_char>)
   ```

If LSP is unavailable or errors, fall back to regex-based extraction and note `"analysis_method": "regex_fallback"` in output.

### Step 3: Dependency Extraction

Parse import/require statements and categorize each:

| Category | Detection | Examples |
|----------|-----------|----------|
| **Project file** | Relative import, path within project | `from .utils import`, `import ../helpers` |
| **External library** | Third-party package | `import fastapi`, `require('lodash')` |
| **Standard library** | Built-in module | `import os`, `require('fs')` |

### Step 4: Library Usage Validation (External Libraries Only)

For each external library used:

1. **Resolve library ID:**
   ```
   mcp__plugin_context7_context7__resolve-library-id(
     libraryName="<library_name>",
     query="<function_name> usage"
   )
   ```

2. **Query documentation:**
   ```
   mcp__plugin_context7_context7__query-docs(
     libraryId="<resolved_id>",
     query="is <function_name> deprecated, correct usage, common mistakes"
   )
   ```

3. **Compare actual usage vs documentation:**
   - Correct signature?
   - Deprecated API?
   - Missing error handling?
   - Security concerns?

If Context7 is unavailable, note `"context7_validated": false` in the dependency entry.

### Step 5: Documentation Drift Check

Compare the file's actual behavior against project memory:

1. **Check CONTEXT.md claims:**
   - Does this file implement what CONTEXT.md says it should?
   - Are there architectural claims that don't match reality?

2. **Check TODO.md completions:**
   - Are features marked `[x]` actually implemented here?
   - Are there features claimed complete that are missing?

3. **Check for undocumented functionality:**
   - Does this file contain significant features not mentioned in docs?
   - Are there major functions without any documentation reference?

Flag any mismatches as `documentation_drift` issues.

### Step 6: Pattern Extraction

Extract significant patterns for duplicate detection:

**Pattern types to extract:**
- Functions with body >5 lines containing control flow (if/for/while/try)
- All regex patterns (re.compile, /pattern/, regex literals)
- Magic constants (config strings, non-trivial numbers)
- Validation logic (input checking, sanitization)
- Algorithm implementations (retry, rate limiting, caching)

**For each pattern:**

1. **Identify the pattern**
2. **Extract the code**
3. **Normalize:**
   - Strip comments and docstrings
   - Normalize whitespace to single spaces
   - Replace variable names with placeholders (_arg1, _var1)
4. **Generate hash:** First 12 characters of SHA-256 of normalized content
5. **Give it a descriptive name**

### Step 7: Issue Synthesis

Compile all findings into issues:

| Source | Issue Type |
|--------|------------|
| LSP findReferences = 0 | unused_code.unreferenced_function |
| Context7 deprecation warning | incorrect_usage.deprecated_api |
| Context7 signature mismatch | incorrect_usage.wrong_signature |
| Code vs CONTEXT.md mismatch | documentation_drift.code_doc_mismatch |
| TODO marked done but missing | documentation_drift.missing_feature |

For each issue, provide:
- Concrete evidence (what specifically was found)
- Exact location (line numbers)
- Suggested fix (actionable recommendation)

### Step 8: Return JSON

Return a single JSON object with this exact structure:

```json
{
  "path": "{{FILE_PATH}}",
  "purpose": "One-sentence description of what this file does",
  "analyzed_at": "2026-01-10T14:30:00Z",
  "line_count": 245,
  "analysis_method": "lsp",

  "symbols": [
    {
      "name": "authenticate_user",
      "type": "function",
      "signature": "(username: str, password: str) -> Optional[User]",
      "docstring": "Authenticates a user and returns User object or None",
      "line": 15,
      "end_line": 45,
      "visibility": "public",
      "used_by": ["src/api/routes.py:45", "src/api/routes.py:78"],
      "calls": ["src/db/users.py:get_user_by_username", "bcrypt:verify"],
      "reference_count": 2
    }
  ],

  "external_dependencies": [
    {
      "library": "bcrypt",
      "version_constraint": ">=4.0.0",
      "functions_used": ["hashpw", "checkpw", "gensalt"],
      "usage_assessment": "correct",
      "notes": null,
      "context7_validated": true,
      "context7_library_id": "/pyca/bcrypt"
    }
  ],

  "issues": [
    {
      "id": "issue-001",
      "type": "unused_code",
      "subtype": "unreferenced_function",
      "severity": "warning",
      "location": {"line": 120, "end_line": 145},
      "symbol": "legacy_authenticate",
      "description": "Function `legacy_authenticate` has zero references in the codebase",
      "evidence": "LSP findReferences returned empty result",
      "suggested_fix": "Remove function if no longer needed, or add @deprecated decorator if intentionally kept",
      "auto_fixable": false
    }
  ],

  "patterns": [
    {
      "hash": "abc123def456",
      "name": "email_validation_regex",
      "type": "regex",
      "location": {"line": 23, "end_line": 23},
      "normalized_content": "^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$"
    },
    {
      "hash": "ghi789jkl012",
      "name": "password_strength_check",
      "type": "function",
      "location": {"line": 50, "end_line": 62},
      "normalized_content": "if len(_arg1) < 8: return False; if not re.search('[A-Z]', _arg1): return False; ..."
    }
  ]
}
```

---

## Important Guidelines

### Be Thorough
This is a deep audit. Take time to:
- Check every symbol
- Validate every external library call
- Compare against project documentation
- Extract all significant patterns

### Evidence-Based
Every issue must have concrete evidence:
- "LSP findReferences returned empty"
- "Context7 docs state: '@validator is deprecated'"
- "CONTEXT.md claims rate limiting but none found"

Never flag issues based on suspicion alone.

### No False Positives
Only flag issues you're confident about. Consider:
- Framework decorators that register functions (@app.route)
- Reflection/dynamic usage patterns
- Intentional dead code (commented explanations)
- Test fixtures and mocks

### Actionable Suggestions
Every issue should have a clear suggested fix:
- Remove X
- Replace X with Y
- Add error handling for Z
- Update CONTEXT.md to reflect actual behavior

### Handle Errors Gracefully
If LSP fails:
- Note `"analysis_method": "regex_fallback"`
- Continue with regex-based extraction
- Note reduced confidence in output

If Context7 fails:
- Note `"context7_validated": false"`
- Skip library validation for that dependency
- Continue with other analysis
```

---

## Variable Substitution

When generating the prompt, substitute these variables:

| Variable | Source |
|----------|--------|
| `{{FILE_PATH}}` | Absolute or relative path to file |
| `{{LANGUAGE}}` | Detected from extension (.py, .ts, .go, etc.) |
| `{{PROJECT_ROOT}}` | Git root or specified project directory |
| `{{DEPENDENCIES}}` | From package.json, pyproject.toml, go.mod |
| `{{CONTEXT_MD_SUMMARY}}` | First 500 chars or key points from hack/CONTEXT.md |
| `{{TODO_MD_SUMMARY}}` | Active items from hack/TODO.md |
| `{{NOTES_MD_SUMMARY}}` | Key gotchas from hack/NOTES.md |

---

## Language Detection

| Extension | Language |
|-----------|----------|
| `.py`, `.pyi` | python |
| `.ts`, `.tsx` | typescript |
| `.js`, `.jsx`, `.mjs` | javascript |
| `.go` | go |
| `.rs` | rust |
| `.java` | java |
| `.rb` | ruby |
| `.php` | php |
| `.swift` | swift |
| `.kt` | kotlin |
| `.c`, `.h` | c |
| `.cpp`, `.hpp`, `.cc` | cpp |
| `.cs` | csharp |

---

## Example Invocation

```python
# In orchestrator
file_path = "src/auth/login.py"
language = "python"
project_memory = read_project_memory()

prompt = ANALYZER_TEMPLATE.format(
    FILE_PATH=file_path,
    LANGUAGE=language,
    PROJECT_ROOT="/path/to/project",
    DEPENDENCIES="fastapi, sqlalchemy, bcrypt, pydantic",
    CONTEXT_MD_SUMMARY=project_memory.get("context", "No CONTEXT.md found"),
    TODO_MD_SUMMARY=project_memory.get("todo", "No TODO.md found"),
    NOTES_MD_SUMMARY=project_memory.get("notes", "No NOTES.md found")
)

result = Task(
    subagent_type="general-purpose",
    prompt=prompt,
    description=f"Analyze {file_path}"
)
```
