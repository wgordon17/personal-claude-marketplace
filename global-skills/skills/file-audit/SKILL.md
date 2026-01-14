---
name: file-audit
description: |
  Deep code quality audit system. Use when asked to:
  - "Audit the codebase"
  - "Find unused code"
  - "Check for duplicates"
  - "Validate library usage"
  - "Review the entire project"
  Analyzes files in parallel with LSP and Context7, detecting issues, duplicates, and documentation drift.
allowed-tools: [LSP, Read, Grep, Glob, Task, Write, Bash, mcp__plugin_context7_context7__resolve-library-id, mcp__plugin_context7_context7__query-docs]
---

# file-audit

Comprehensive, resumable code quality audit system that analyzes every non-gitignored file in a project to build a complete inventory of symbols, dependencies, issues, and duplicates.

## Quick Start

```
/file-audit                    # Analyze entire project
/file-audit src/               # Analyze specific directory
/file-audit --resume           # Resume interrupted audit
/file-audit --status           # Show progress
/file-audit path/to/file.py    # Analyze single file
```

---

## Architecture

```
ORCHESTRATOR (you)
├── Phase 1: Discovery
│   ├── git ls-files (find non-gitignored files)
│   ├── Read project memory (hack/CONTEXT.md, TODO.md, NOTES.md)
│   └── Initialize queue.json
│
├── Phase 2: Parallel Analysis
│   ├── Spawn 3-5 file analyzer agents IN PARALLEL
│   ├── Each analyzes one file independently
│   └── Collect results + extracted patterns
│
├── Phase 3: Post-Analysis
│   ├── Run duplicate detection across ALL patterns
│   ├── Cross-reference duplicates across files
│   └── Inject duplicate issues into file entries
│
└── Phase 4: Finalization
    ├── Assemble inventory.json
    ├── Generate TODOs
    └── Write summary
```

---

## Orchestrator Workflow

### Step 1: Initialization

```bash
# Check for existing queue
if hack/file-audit/queue.json exists:
    Resume from last batch position
else:
    # Discover files
    git ls-files --cached --others --exclude-standard

    # Read project memory
    Read hack/CONTEXT.md, hack/TODO.md, hack/NOTES.md (if exist)

    # Create queue
    Initialize hack/file-audit/queue.json with all files as "pending"
```

### Step 2: Parallel Processing Loop

```
REPEAT until queue empty:
    1. Get next batch of 3-5 "pending" files
    2. Mark batch as "in_progress", save queue
    3. Spawn 3-5 Task agents IN PARALLEL:
       Task(
         subagent_type="general-purpose",
         prompt=analyzer_prompt(file_path, project_memory)
       )
    4. Wait for all agents to complete
    5. Collect result JSONs + extracted patterns
    6. Mark batch as "completed", save queue
```

### Step 3: Post-Analysis Duplicate Detection

```
1. Collect ALL patterns from ALL file results
2. Build hash → pattern index
3. For each hash with multiple occurrences:
   - Create duplicate issue
   - Reference all locations
4. Inject duplicate issues into relevant file entries
```

### Step 4: Finalization

```
1. Assemble master inventory.json
2. Generate summary statistics:
   - Total files analyzed
   - Issues by category (unused_code, incorrect_usage, duplication, documentation_drift)
   - Issues by severity (error, warning, info)
3. Generate consolidated TODO list
4. Write hack/file-audit/inventory.json
```

---

## Single-File Analyzer Prompt Template

When spawning a file analyzer agent, use this prompt structure:

```markdown
You are a single-file code analyzer. Analyze the following file deeply and return structured JSON.

## File to Analyze
Path: {file_path}
Language: {language}

## Project Context
Dependencies: {project_dependencies}
Project Memory:
- CONTEXT.md: {context_md_summary}
- TODO.md: {todo_md_summary}
- NOTES.md: {notes_md_summary}

## Your Task

1. **Read the file** completely

2. **LSP Symbol Analysis**
   - Use `LSP(operation="documentSymbol", filePath="{file_path}", line=1, character=1)` to enumerate symbols
   - For each symbol:
     - `hover` for type info
     - `findReferences` to check usage (zero refs = potentially unused)
     - `outgoingCalls` to map dependencies

3. **Dependency Extraction**
   - Parse imports/requires
   - Categorize: project file | external library | stdlib

4. **Library Usage Validation** (for external libraries)
   - Use Context7 `resolve-library-id` then `query-docs`
   - Check for: deprecated APIs, wrong signatures, missing error handling

5. **Documentation Drift Check**
   - Compare code behavior vs CONTEXT.md claims
   - Flag mismatches

6. **Pattern Extraction**
   - Extract functions >5 lines, regexes, magic constants
   - Normalize (strip comments, whitespace)
   - Hash each pattern

7. **Return JSON** in this exact format:

```json
{
  "path": "{file_path}",
  "purpose": "One-sentence description of what this file does",
  "analyzed_at": "ISO timestamp",
  "symbols": [
    {
      "name": "function_name",
      "type": "function|class|variable",
      "signature": "type signature if available",
      "line": 15,
      "used_by": ["path:line", ...],
      "calls": ["path:function", ...]
    }
  ],
  "external_dependencies": [
    {
      "library": "library_name",
      "functions_used": ["func1", "func2"],
      "usage_assessment": "correct|deprecated_api|wrong_signature",
      "notes": "explanation if issue"
    }
  ],
  "issues": [
    {
      "type": "unused_code|incorrect_usage|documentation_drift",
      "subtype": "unreferenced_function|deprecated_api|code_doc_mismatch|...",
      "severity": "error|warning|info",
      "location": {"line": 45, "end_line": 67},
      "description": "Human-readable description",
      "evidence": "What evidence supports this (LSP output, Context7 docs, etc)",
      "suggested_fix": "How to fix this"
    }
  ],
  "patterns": [
    {
      "hash": "sha256_first_12_chars",
      "name": "descriptive_name",
      "type": "function|regex|constant",
      "location": {"line": 23, "end_line": 35},
      "normalized_content": "normalized code for comparison"
    }
  ]
}
```

IMPORTANT:
- Be thorough: this is a deep audit
- Evidence-based: every issue must have concrete evidence
- No false positives: only flag issues you're confident about
```

---

## Output Files

### hack/file-audit/queue.json

```json
{
  "status": "in_progress|completed",
  "total_files": 150,
  "completed": 45,
  "current_batch": ["src/api/routes.py", "src/auth/login.py"],
  "started_at": "2026-01-10T14:00:00Z",
  "files": [
    {"path": "src/auth/login.py", "status": "completed", "analyzed_at": "..."},
    {"path": "src/api/routes.py", "status": "in_progress"},
    {"path": "src/utils/helpers.py", "status": "pending"}
  ]
}
```

### hack/file-audit/inventory.json

```json
{
  "project": "project-name",
  "analyzed_at": "2026-01-10",
  "summary": {
    "total_files": 150,
    "total_symbols": 1234,
    "issues_by_severity": {"error": 5, "warning": 23, "info": 45},
    "issues_by_type": {
      "unused_code": 12,
      "incorrect_usage": 8,
      "duplication": 15,
      "documentation_drift": 3
    }
  },
  "files": [
    { "...file analysis results..." }
  ],
  "pattern_registry": {
    "abc123": {
      "name": "email_validation_regex",
      "occurrences": ["src/auth/login.py:23", "src/auth/register.py:34"]
    }
  },
  "todos": [
    {
      "file": "src/auth/login.py",
      "issue_type": "unused_code",
      "action": "Remove `legacy_auth` function",
      "priority": "medium"
    }
  ]
}
```

---

## Issue Types

### unused_code
- `unreferenced_function`: Function has zero references (LSP findReferences empty)
- `unreferenced_variable`: Variable assigned but never read
- `dead_import`: Import statement not used in file

### incorrect_usage
- `deprecated_api`: Using deprecated function/method (Context7 flagged)
- `wrong_signature`: Incorrect parameters passed (Context7 mismatch)
- `missing_error_handling`: Function raises but not caught

### duplication
- `exact_duplicate`: Identical code (content hash match)
- `near_duplicate`: Very similar code (structural hash + >80% similarity)

### documentation_drift
- `code_doc_mismatch`: Code behavior differs from CONTEXT.md claims
- `missing_feature`: Documented feature not implemented in code
- `undocumented_feature`: Significant code without documentation

---

## Severity Levels

| Severity | Criteria | Action |
|----------|----------|--------|
| **error** | Broken code, security vulnerability | Fix immediately |
| **warning** | Deprecated API, unused code, duplicates | Fix soon |
| **info** | Style issue, minor optimization | Nice to fix |

---

## Resume Logic

When `/file-audit --resume` is invoked:

1. Read `hack/file-audit/queue.json`
2. Find files with `status: "pending"` or `status: "in_progress"`
3. Reset any `in_progress` to `pending` (they were interrupted)
4. Continue from next pending batch
5. Collect new results + merge with existing partial inventory

---

## LSP Fallback

When LSP is unavailable for a file type:

1. Use regex-based symbol extraction (see lsp-navigation skill for patterns)
2. Use grep for reference finding (higher false positive rate)
3. Mark in output: `"analysis_method": "regex_fallback"`
4. Skip type information: `"type_info_available": false`

---

## Best Practices

1. **Run incrementally**: For large codebases, audit in directory chunks
2. **Check queue status**: Use `/file-audit --status` to monitor progress
3. **Review todos first**: Generated TODOs are prioritized by severity
4. **Trust but verify**: Issues are evidence-based but review before bulk fixes
5. **Update project memory**: If drift detected, decide whether to update code or docs

---

## Integration with Project Memory

The analyzer reads `hack/` files to:

1. **Understand intended behavior**: CONTEXT.md describes architecture
2. **Check for drift**: Compare code vs documented intent
3. **Identify stale docs**: Flag when code changed but docs didn't
4. **Understand priorities**: TODO.md shows active work

This enables detection of `documentation_drift` issues that pure code analysis would miss.
