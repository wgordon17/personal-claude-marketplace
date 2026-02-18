# External Tools Reference

Reference for detecting, running, and parsing external analysis tools. Used during
`/unfuck` Phase 0 (detection) and Phase 1 (discovery agents run tools).

## Language Detection

Detect project languages by checking for indicator files in the project root.
Multiple languages are common — detect ALL that apply.

| Indicator File(s) | Language/Ecosystem | Primary Tools |
|-------------------|--------------------|---------------|
| `package.json`, `tsconfig.json`, `*.ts`, `*.tsx`, `*.js`, `*.jsx` | JavaScript/TypeScript | Knip, jscpd, dependency-cruiser, Madge, ESLint |
| `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile` | Python | Vulture, deadcode, radon, Bandit, ruff |
| `go.mod` | Go | `go vet`, `staticcheck`, `deadcode` (golang.org/x/tools) |
| `Cargo.toml` | Rust | `cargo-udeps`, `cargo clippy` |
| `pom.xml`, `build.gradle`, `build.gradle.kts` | Java/Kotlin | SpotBugs, PMD, Checkstyle |
| `Gemfile`, `*.gemspec` | Ruby | Reek, RuboCop, Brakeman |
| `composer.json` | PHP | PHPStan, Psalm, PHP-CS-Fixer |
| `*.csproj`, `*.sln` | .NET/C# | dotnet-format, Roslyn analyzers |

**Detection command:**
```bash
# Run from project root
languages=()
[ -f package.json ] || [ -f tsconfig.json ] && languages+=(javascript)
[ -f pyproject.toml ] || [ -f setup.py ] || [ -f requirements.txt ] && languages+=(python)
[ -f go.mod ] && languages+=(go)
[ -f Cargo.toml ] && languages+=(rust)
[ -f pom.xml ] || [ -f build.gradle ] && languages+=(java)
[ -f Gemfile ] && languages+=(ruby)
[ -f composer.json ] && languages+=(php)
```

---

## Tool Matrix

### JavaScript/TypeScript Tools

#### Knip — Dead Code & Unused Dependencies
- **Detects:** Unused files, exports, dependencies, dev dependencies, unlisted binaries
- **Detection:** `npx knip --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `npx knip --reporter json 2>/dev/null`
- **JSON output shape:**
```json
{
  "files": ["src/unused-file.ts"],
  "issues": [
    {
      "type": "export",
      "filePath": "src/utils.ts",
      "symbol": "formatDate",
      "line": 42,
      "col": 1
    }
  ],
  "dependencies": ["lodash"],
  "devDependencies": ["@types/unused"]
}
```
- **Extraction:** Map each issue to a discovery finding. `type` -> subcategory, `filePath`+`line` -> location.
- **Fallback:** LSP findReferences on all exports (slower but comprehensive)

#### jscpd — Code Duplication
- **Detects:** Exact and near-exact code duplicates across files
- **Detection:** `npx jscpd --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `npx jscpd --reporters json --output {run_dir}/discovery/ --min-lines 5 --min-tokens 50 . 2>/dev/null`
- **JSON output shape ({run_dir}/discovery/jscpd-report.json):**
```json
{
  "duplicates": [
    {
      "format": "typescript",
      "lines": 15,
      "tokens": 120,
      "firstFile": {
        "name": "src/a.ts",
        "start": 10,
        "end": 25,
        "startLoc": {"line": 10, "column": 0}
      },
      "secondFile": {
        "name": "src/b.ts",
        "start": 30,
        "end": 45,
        "startLoc": {"line": 30, "column": 0}
      },
      "fragment": "const result = items.filter(...)..."
    }
  ],
  "statistics": {
    "total": {
      "lines": 5000,
      "sources": 50,
      "clones": 12,
      "duplicatedLines": 180
    }
  }
}
```
- **Extraction:** Each duplicate -> one finding with both file locations.
- **Fallback:** Agent reads files in batches, normalizes whitespace/variable names, compares structure.

#### dependency-cruiser — Architecture & Circular Dependencies
- **Detects:** Circular dependencies, layer violations, orphan modules
- **Detection:** `npx depcruise --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `npx depcruise --output-type json --config .dependency-cruiser.cjs src/ 2>/dev/null || npx depcruise --output-type json src/ 2>/dev/null`
- **JSON output shape:**
```json
{
  "modules": [
    {
      "source": "src/a.ts",
      "dependencies": [
        {"resolved": "src/b.ts", "circular": true}
      ]
    }
  ],
  "summary": {
    "violations": [
      {
        "type": "cycle",
        "from": "src/a.ts",
        "to": "src/b.ts",
        "cycle": ["src/a.ts", "src/b.ts", "src/a.ts"]
      }
    ]
  }
}
```
- **Extraction:** Each violation -> one finding. Cycles -> "circular-dependency" subcategory.
- **Fallback:** Agent traces imports manually using Grep + LSP.

#### Madge — Circular Dependencies (simpler alternative)
- **Detects:** Circular dependencies specifically
- **Detection:** `npx madge --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `npx madge --json --circular . 2>/dev/null`
- **JSON output:** Array of circular dependency chains: `[["a.ts", "b.ts", "a.ts"]]`
- **Fallback:** Same as dependency-cruiser fallback.

#### ESLint — Code Quality & Complexity
- **Detects:** Code quality issues, complexity, style violations
- **Detection:** `npx eslint --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `npx eslint --format json . 2>/dev/null` (respects project .eslintrc)
- **Note:** Only useful if the project already has ESLint configured. Don't run with default config.
- **Fallback:** Agent manual analysis.

---

### Python Tools

#### Vulture — Dead Code
- **Detects:** Unused functions, classes, variables, imports, unreachable code
- **Detection:** `uvx vulture --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `uvx vulture . --min-confidence 80 2>/dev/null`
- **Output format (stdout, not JSON):**
```
src/utils.py:42: unused function 'old_handler' (60% confidence)
src/models.py:15: unused import 'typing.Optional' (90% confidence)
```
- **Parsing:** Split each line on `:` to extract file, line, description. Parse confidence from parentheses.
- **Extraction:** Each line -> one finding. Filter by confidence >= 80.
- **Fallback:** Agent uses LSP findReferences.

#### deadcode — Dead Code (with auto-fix capability)
- **Detects:** Similar to Vulture but with more detection rules and auto-fix
- **Detection:** `uvx deadcode --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `uvx deadcode . 2>/dev/null` (report only, no auto-fix during discovery)
- **Output format:** Similar to Vulture (stdout)
- **Fallback:** Vulture or agent analysis.

#### radon — Cyclomatic Complexity
- **Detects:** Functions/methods with high cyclomatic complexity
- **Detection:** `uvx radon --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `uvx radon cc -j . -n C 2>/dev/null` (`-n C` = only report grade C or worse, `-j` = JSON)
- **JSON output shape:**
```json
{
  "src/handler.py": [
    {
      "type": "function",
      "name": "process_request",
      "lineno": 42,
      "endline": 120,
      "complexity": 15,
      "rank": "C",
      "col_offset": 0,
      "classname": null
    }
  ]
}
```
- **Extraction:** Each function with rank C or worse -> one complexity finding.
- **Fallback:** Agent counts branches manually (if/elif/else/for/while/try/except).

#### Bandit — Security
- **Detects:** Common security issues in Python code (OWASP-aligned)
- **Detection:** `uvx bandit --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `uvx bandit -r . -f json -ll 2>/dev/null` (`-ll` = medium severity and above)
- **JSON output shape:**
```json
{
  "results": [
    {
      "test_id": "B105",
      "test_name": "hardcoded_password_string",
      "filename": "src/config.py",
      "line_number": 15,
      "line_range": [15],
      "severity": "LOW",
      "confidence": "MEDIUM",
      "issue_text": "Possible hardcoded password: 'secret123'"
    }
  ]
}
```
- **Extraction:** Each result -> one security finding. Map Bandit severity to our severity scale.
- **Fallback:** Agent OWASP walkthrough.

#### ruff — Code Quality & Imports
- **Detects:** Lint issues, unused imports, code style violations
- **Detection:** `uvx ruff --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `uvx ruff check --output-format json . 2>/dev/null`
- **JSON output shape:**
```json
[
  {
    "code": "F401",
    "message": "'os' imported but unused",
    "filename": "src/utils.py",
    "location": {"row": 1, "column": 1},
    "fix": {"message": "Remove unused import", "edits": []}
  }
]
```
- **Extraction:** F401 (unused imports) -> dead-code findings. Other codes -> complexity findings.
- **Fallback:** Agent analysis.

---

### Language-Agnostic Tools

#### Semgrep — Security Patterns
- **Detects:** Security vulnerabilities, code patterns matching known bad practices
- **Detection:** `env -u HTTPS_PROXY -u HTTP_PROXY uvx semgrep --version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `env -u HTTPS_PROXY -u HTTP_PROXY uvx semgrep --config auto --json --quiet . 2>/dev/null`
- **Note:** Semgrep crashes if `HTTPS_PROXY` or `HTTP_PROXY` are set to empty strings (common in Claude Code environments). The `env -u` prefix unsets them for the subprocess.
- **JSON output shape:**
```json
{
  "results": [
    {
      "check_id": "python.lang.security.audit.exec-detected",
      "path": "src/handler.py",
      "start": {"line": 42, "col": 5},
      "end": {"line": 42, "col": 30},
      "extra": {
        "message": "Detected use of exec(). This is dangerous.",
        "severity": "WARNING",
        "metadata": {
          "category": "security",
          "subcategory": ["audit"]
        }
      }
    }
  ]
}
```
- **Auto-fix support:** `env -u HTTPS_PROXY -u HTTP_PROXY uvx semgrep --config auto --autofix` (used in Phase 3, not Phase 1)
- **Extraction:** Each result -> one security finding.
- **Fallback:** Agent OWASP walkthrough with pattern matching via Grep.

#### gitleaks — Secret Detection
- **Detects:** Hardcoded secrets, API keys, passwords, tokens in code and git history
- **Detection:** `go run github.com/gitleaks/gitleaks/v8@latest version 2>/dev/null && echo "available" || echo "unavailable"`
- **Run:** `go run github.com/gitleaks/gitleaks/v8@latest detect --report-format json --report-path {run_dir}/discovery/gitleaks.json --no-banner 2>/dev/null`
- **Note:** Uses `go run` to compile and run without permanent installation, like `uvx` for Python and `npx` for JS. Requires Go to be installed.
- **JSON output shape ({run_dir}/discovery/gitleaks.json):**
```json
[
  {
    "Description": "AWS Access Key",
    "File": "config/settings.py",
    "StartLine": 15,
    "EndLine": 15,
    "Secret": "AKIA...",
    "Match": "aws_access_key_id = 'AKIA...'",
    "RuleID": "aws-access-key-id"
  }
]
```
- **Extraction:** Each leak -> one CRITICAL security finding.
- **Fallback:** Agent regex scanning for common secret patterns:
  ```
  (?i)(api[_-]?key|secret|password|token|credential)\s*[:=]\s*['"][^'"]{8,}
  ```

#### jscpd — Cross-Language Duplication
- See JavaScript/TypeScript section. jscpd works on any language.

---

## Phase 0 Detection Script

The orchestrator runs this during Phase 0 to detect languages and available tools.
Results are written to `{run_dir}/available-tools.json`.

```bash
#!/bin/bash
# Tool detection script for /unfuck Phase 0
# Outputs JSON to {run_dir}/available-tools.json

PROJECT_ROOT="$(pwd)"
OUTPUT_FILE="{run_dir}/available-tools.json"

# Detect languages
languages="["
[ -f package.json ] || [ -f tsconfig.json ] && languages+="\"javascript\","
[ -f pyproject.toml ] || [ -f setup.py ] || [ -f requirements.txt ] && languages+="\"python\","
[ -f go.mod ] && languages+="\"go\","
[ -f Cargo.toml ] && languages+="\"rust\","
[ -f pom.xml ] || [ -f build.gradle ] && languages+="\"java\","
[ -f Gemfile ] && languages+="\"ruby\","
[ -f composer.json ] && languages+="\"php\","
languages="${languages%,}]"  # Remove trailing comma

# Detect tools
detect_tool() {
  local name="$1"
  local cmd="$2"
  if eval "$cmd" > /dev/null 2>&1; then
    local version
    version=$(eval "$cmd" 2>/dev/null | head -1)
    echo "\"$name\": {\"available\": true, \"version\": \"$version\"}"
  else
    echo "\"$name\": {\"available\": false, \"fallback\": \"agent-analysis\"}"
  fi
}

tools="{"
tools+=$(detect_tool "knip" "npx knip --version")","
tools+=$(detect_tool "jscpd" "npx jscpd --version")","
tools+=$(detect_tool "dependency-cruiser" "npx depcruise --version")","
tools+=$(detect_tool "madge" "npx madge --version")","
tools+=$(detect_tool "eslint" "npx eslint --version")","
tools+=$(detect_tool "vulture" "uvx vulture --version")","
tools+=$(detect_tool "deadcode" "uvx deadcode --version")","
tools+=$(detect_tool "radon" "uvx radon --version")","
tools+=$(detect_tool "bandit" "uvx bandit --version")","
tools+=$(detect_tool "ruff" "uvx ruff --version")","
tools+=$(detect_tool "semgrep" "env -u HTTPS_PROXY -u HTTP_PROXY uvx semgrep --version")","
tools+=$(detect_tool "gitleaks" "go run github.com/gitleaks/gitleaks/v8@latest version")
tools+="}"

# Detect test runner
test_runner="unknown"
[ -f Makefile ] && grep -q "^test:" Makefile && test_runner="make test"
[ -f pytest.ini ] || [ -f pyproject.toml ] && grep -q "pytest" pyproject.toml 2>/dev/null && test_runner="uv run pytest"
[ -f package.json ] && grep -q "\"test\"" package.json && test_runner="npm test"
[ -f go.mod ] && test_runner="go test ./..."
[ -f Cargo.toml ] && test_runner="cargo test"

# Detect formatter
formatter="unknown"
[ -f Makefile ] && grep -q "^format:" Makefile && formatter="make format"
[ -f pyproject.toml ] && grep -q "ruff" pyproject.toml 2>/dev/null && formatter="uvx ruff format . && uvx ruff check --fix ."
[ -f .prettierrc ] || [ -f .prettierrc.json ] && formatter="npx prettier --write ."

# Write output
cat > "$OUTPUT_FILE" << JSONEOF
{
  "project_root": "$PROJECT_ROOT",
  "languages": $languages,
  "tools": $tools,
  "test_runner": "$test_runner",
  "formatter": "$formatter"
}
JSONEOF

echo "Tool detection complete. Results written to $OUTPUT_FILE"
```

## Fallback Strategy Summary

| When tool is unavailable | Agent does instead |
|--------------------------|-------------------|
| Knip | LSP findReferences on all exports |
| jscpd | Structural comparison: normalize code, hash function bodies, compare |
| dependency-cruiser / Madge | Grep for import statements, build graph manually |
| Vulture / deadcode | LSP findReferences on all Python symbols |
| radon | Count branches manually (if/elif/else/for/while/try/except per function) |
| Semgrep | OWASP walkthrough with pattern matching via Grep |
| gitleaks | Regex scan for secret patterns |
| Bandit | Manual security pattern review |
| ruff | Agent import analysis |
| ESLint | Agent code quality analysis |

**Key principle:** External tools are accelerators, not requirements. Every analysis can be
performed by the agent alone — tools just make it faster and more reliable.
