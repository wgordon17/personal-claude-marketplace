# Issue Types Catalog

Complete reference for all issue types detected by the file-audit system.

---

## Category: unused_code

Issues related to code that is never used or referenced.

### unreferenced_function

**Severity:** warning

**Detection:** LSP `findReferences` returns zero external references.

**Criteria:**
- Function is defined but never called from any other file
- Internal self-references (recursion) don't count as "used"
- Exported functions with no callers are flagged

**False positive indicators:**
- Function is listed in `__all__` (Python)
- Function name matches known callback patterns (`on_*`, `handle_*`)
- Function is decorated with framework decorators (`@app.route`, `@api_view`)
- Function is used via reflection/getattr

**Example:**
```json
{
  "type": "unused_code",
  "subtype": "unreferenced_function",
  "severity": "warning",
  "location": {"line": 45, "end_line": 67},
  "symbol": "legacy_authenticate",
  "description": "Function `legacy_authenticate` has zero references in the codebase",
  "evidence": "LSP findReferences returned empty result",
  "suggested_fix": "Remove function if no longer needed, or add @deprecated decorator if intentionally kept"
}
```

---

### unreferenced_variable

**Severity:** info

**Detection:** LSP `findReferences` returns only the definition, no reads.

**Criteria:**
- Variable is assigned but never read
- Module-level constants with no references
- Class attributes never accessed

**False positive indicators:**
- Variable is exported (public API)
- Variable name starts with `_` (intentionally unused)
- Variable is used in string interpolation or templates

**Example:**
```json
{
  "type": "unused_code",
  "subtype": "unreferenced_variable",
  "severity": "info",
  "location": {"line": 23},
  "symbol": "DEBUG_MODE",
  "description": "Variable `DEBUG_MODE` is assigned but never read",
  "evidence": "LSP findReferences returned only definition, no usages",
  "suggested_fix": "Remove if not needed, or document why it's kept"
}
```

---

### unreferenced_class

**Severity:** warning

**Detection:** LSP `findReferences` returns zero instantiations or subclasses.

**Criteria:**
- Class is defined but never instantiated
- No subclasses extend this class
- Class methods are never called statically

**Example:**
```json
{
  "type": "unused_code",
  "subtype": "unreferenced_class",
  "severity": "warning",
  "location": {"line": 100, "end_line": 150},
  "symbol": "LegacyUserModel",
  "description": "Class `LegacyUserModel` is never instantiated or extended",
  "suggested_fix": "Remove if migration is complete, or document if kept for compatibility"
}
```

---

### dead_import

**Severity:** info

**Detection:** Import statement exists but imported symbol is never used in file.

**Criteria:**
- Import appears at top of file
- Imported symbol not referenced anywhere in file body

**False positive indicators:**
- Import is for type hints only (`if TYPE_CHECKING`)
- Import has side effects (registering, patching)
- Re-export pattern (`from x import y; __all__ = ['y']`)

**Example:**
```json
{
  "type": "unused_code",
  "subtype": "dead_import",
  "severity": "info",
  "location": {"line": 5},
  "symbol": "unused_helper",
  "description": "Import `unused_helper` is not used in this file",
  "suggested_fix": "Remove unused import"
}
```

---

### unreachable_code

**Severity:** warning

**Detection:** Code appears after unconditional return, raise, break, or continue.

**Criteria:**
- Statements after `return` in a function
- Code after `raise` in a try block (not in finally)
- Dead branches after `if True:` or `if False:`

**Example:**
```json
{
  "type": "unused_code",
  "subtype": "unreachable_code",
  "severity": "warning",
  "location": {"line": 78, "end_line": 82},
  "description": "Code after return statement is unreachable",
  "suggested_fix": "Remove unreachable code or fix control flow"
}
```

---

## Category: incorrect_usage

Issues related to incorrect or suboptimal use of libraries and APIs.

### deprecated_api

**Severity:** warning

**Detection:** Context7 docs indicate the API is deprecated.

**Criteria:**
- Function/method is marked deprecated in library docs
- Documentation recommends alternative
- Version notes indicate removal timeline

**Example:**
```json
{
  "type": "incorrect_usage",
  "subtype": "deprecated_api",
  "severity": "warning",
  "location": {"line": 67},
  "description": "Using deprecated @validator decorator from Pydantic",
  "evidence": "Context7 docs: '@validator is deprecated since Pydantic v2, use @field_validator'",
  "suggested_fix": "Replace @validator with @field_validator and update signature",
  "auto_fixable": true,
  "fix_code": "@field_validator('password')\n@classmethod\ndef validate_password(cls, v: str) -> str:"
}
```

---

### wrong_signature

**Severity:** warning

**Detection:** Context7 docs show different expected parameters.

**Criteria:**
- Wrong number of arguments
- Wrong argument types
- Missing required parameters
- Deprecated parameter names

**Example:**
```json
{
  "type": "incorrect_usage",
  "subtype": "wrong_signature",
  "severity": "warning",
  "location": {"line": 34},
  "description": "bcrypt.hashpw() called with wrong argument order",
  "evidence": "Context7 docs: 'hashpw(password, salt)' but code has 'hashpw(salt, password)'",
  "suggested_fix": "Swap argument order: bcrypt.hashpw(password.encode(), salt)"
}
```

---

### missing_error_handling

**Severity:** warning

**Detection:** Context7 docs show function raises exceptions not caught.

**Criteria:**
- Function documented to raise specific exceptions
- No try/except around the call
- No upstream handler visible

**Example:**
```json
{
  "type": "incorrect_usage",
  "subtype": "missing_error_handling",
  "severity": "warning",
  "location": {"line": 89},
  "description": "requests.get() called without handling ConnectionError, Timeout",
  "evidence": "Context7 docs list Timeout, ConnectionError as common exceptions",
  "suggested_fix": "Wrap in try/except or add timeout parameter"
}
```

---

### type_mismatch

**Severity:** warning

**Detection:** LSP hover shows type incompatibility.

**Criteria:**
- Argument type doesn't match parameter type
- Return value used incorrectly
- Type assertion would fail at runtime

**Example:**
```json
{
  "type": "incorrect_usage",
  "subtype": "type_mismatch",
  "severity": "warning",
  "location": {"line": 56},
  "description": "Passing str to function expecting int",
  "evidence": "LSP hover: process_count(count: int) but called with string",
  "suggested_fix": "Convert to int: process_count(int(count_str))"
}
```

---

### security_issue

**Severity:** error

**Detection:** Known insecure patterns or Context7 security warnings.

**Criteria:**
- SQL string concatenation (injection risk)
- Insecure random for crypto (using `random` instead of `secrets`)
- Hardcoded credentials
- Disabled SSL verification
- Unsafe deserialization

**Example:**
```json
{
  "type": "incorrect_usage",
  "subtype": "security_issue",
  "severity": "error",
  "location": {"line": 123},
  "description": "SQL query uses string formatting instead of parameterized query",
  "evidence": "f\"SELECT * FROM users WHERE id = {user_id}\" is vulnerable to injection",
  "suggested_fix": "Use parameterized query: cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))"
}
```

---

## Category: duplication

Issues related to duplicated code patterns across files.

### exact_duplicate

**Severity:** warning

**Detection:** Content hash of normalized code matches another file exactly.

**Criteria:**
- After normalization (strip comments, normalize whitespace), code is identical
- Pattern is >5 lines or is a regex/constant

**Example:**
```json
{
  "type": "duplication",
  "subtype": "exact_duplicate",
  "severity": "warning",
  "location": {"line": 34, "end_line": 34},
  "description": "Email validation regex duplicated from src/utils/validators.py",
  "evidence": "Identical pattern: ^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$",
  "suggested_fix": "Import EMAIL_REGEX from src/utils/validators instead of duplicating"
}
```

---

### near_duplicate

**Severity:** info

**Detection:** Structural hash matches with >80% content similarity.

**Criteria:**
- AST structure is identical
- Variable names differ
- Minor implementation differences

**Example:**
```json
{
  "type": "duplication",
  "subtype": "near_duplicate",
  "severity": "info",
  "location": {"line": 50, "end_line": 65},
  "description": "Function structurally similar to validate_email in src/auth/validators.py",
  "evidence": "Same AST structure, 85% content similarity",
  "suggested_fix": "Consider extracting to shared utility with parameters for differences"
}
```

---

### pattern_duplicate

**Severity:** info

**Detection:** Same algorithm/pattern implemented in multiple places.

**Criteria:**
- Retry logic with backoff
- Rate limiting implementation
- Caching patterns
- Validation sequences

**Example:**
```json
{
  "type": "duplication",
  "subtype": "pattern_duplicate",
  "severity": "info",
  "location": {"line": 78, "end_line": 95},
  "description": "Retry-with-backoff pattern duplicated from src/api/client.py",
  "suggested_fix": "Use shared retry decorator from src/utils/retry.py"
}
```

---

## Category: documentation_drift

Issues where code behavior differs from documented behavior in project memory files.

### code_doc_mismatch

**Severity:** warning

**Detection:** Comparison of actual code behavior vs CONTEXT.md claims.

**Criteria:**
- CONTEXT.md describes one behavior, code implements different behavior
- Architecture document says one thing, implementation does another

**Example:**
```json
{
  "type": "documentation_drift",
  "subtype": "code_doc_mismatch",
  "severity": "warning",
  "location": {"line": 1, "end_line": 100},
  "description": "CONTEXT.md claims rate limiting on all endpoints, but this file has none",
  "code_behavior": "No rate limiting middleware or decorator detected",
  "documented_behavior": "All endpoints have rate limiting (hack/CONTEXT.md:45)",
  "suggested_fix": "Either add rate limiting or update CONTEXT.md to note exceptions"
}
```

---

### missing_feature

**Severity:** warning

**Detection:** Feature marked done in TODO.md but not implemented in code.

**Criteria:**
- TODO.md has `[x]` item claiming feature is complete
- Feature not found in expected location
- Tests for feature don't exist or are skipped

**Example:**
```json
{
  "type": "documentation_drift",
  "subtype": "missing_feature",
  "severity": "warning",
  "location": {"line": 1},
  "description": "TODO.md claims 'password reset flow complete' but no reset endpoint found",
  "documented_behavior": "Password reset flow (hack/TODO.md:23, marked complete)",
  "code_behavior": "No password reset endpoint in auth routes",
  "suggested_fix": "Either implement the feature or uncheck the TODO item"
}
```

---

### undocumented_feature

**Severity:** info

**Detection:** Significant functionality exists without documentation.

**Criteria:**
- Major feature (>100 lines) not mentioned in CONTEXT.md
- Public API not documented
- Significant behavior not noted anywhere

**Example:**
```json
{
  "type": "documentation_drift",
  "subtype": "undocumented_feature",
  "severity": "info",
  "location": {"line": 200, "end_line": 350},
  "symbol": "AdminDashboard",
  "description": "AdminDashboard class (150 lines) not documented in CONTEXT.md",
  "code_behavior": "Full admin dashboard implementation with user management",
  "suggested_fix": "Add section to CONTEXT.md describing admin dashboard architecture"
}
```

---

## Category: style

Style and quality issues (lower priority).

### naming_convention

**Severity:** info

**Detection:** Symbol name doesn't match language conventions.

**Criteria:**
- Python: snake_case for functions, PascalCase for classes
- JavaScript: camelCase for functions, PascalCase for classes
- Constants not in UPPER_SNAKE_CASE

---

### complexity

**Severity:** info

**Detection:** Function exceeds complexity thresholds.

**Criteria:**
- Cyclomatic complexity > 10
- Function > 50 lines
- Nesting depth > 4

---

### missing_docstring

**Severity:** info

**Detection:** Public API without documentation.

**Criteria:**
- Public function/class has no docstring
- Module has no module docstring
- Exported API has no documentation

---

### magic_number

**Severity:** info

**Detection:** Unexplained numeric/string literals in code.

**Criteria:**
- Number other than 0, 1, 2, 10, 100 used in comparison
- String literal that looks like configuration
- Repeated magic values

---

## Severity Reference

| Severity | Priority | When to Use |
|----------|----------|-------------|
| **error** | Critical | Security issues, broken code, data loss risk |
| **warning** | High | Deprecated APIs, unused code, duplicates, drift |
| **info** | Low | Style issues, minor improvements, suggestions |

---

## Auto-Fix Eligibility

Issues marked `auto_fixable: true` include a `fix_code` field with replacement code.

**Auto-fixable patterns:**
- Deprecated API replacements (when signature is compatible)
- Import cleanup (dead imports)
- Simple refactors (constant extraction)

**Never auto-fix:**
- Security issues (require human review)
- Complex refactors (duplicates spanning files)
- Behavior changes (documentation drift)
