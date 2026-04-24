## PR Metadata

- **Title:** Add config parser and subprocess runner utilities
- **Description:** Adds two utility modules: a safe config parser that reads YAML/JSON config files, and a subprocess runner for invoking external tools with controlled arguments. Both modules are used by the new CLI tooling (PR #142).
- **Files changed:** 3
- **Lines:** +89 / -0

---

## Diff

```diff
diff --git a/src/utils/config_parser.py b/src/utils/config_parser.py
new file mode 100644
index 0000000..a4c8e31
--- /dev/null
+++ b/src/utils/config_parser.py
@@ -0,0 +1,42 @@
+"""Safe configuration parser for YAML and JSON config files."""
+import ast
+import json
+import logging
+from pathlib import Path
+
+import yaml
+
+logger = logging.getLogger(__name__)
+
+
+def parse_config_value(raw: str):
+    """Parse a config value string into a Python literal.
+
+    Uses ast.literal_eval for safe parsing of Python literals (int, float,
+    bool, list, dict, None). Does NOT use eval() — ast.literal_eval only
+    accepts literal values and raises ValueError for arbitrary expressions.
+    """
+    try:
+        return ast.literal_eval(raw)
+    except (ValueError, SyntaxError):
+        return raw  # Return as string if not a recognizable literal
+
+
+def load_config_file(path: str | Path) -> dict:
+    """Load a YAML or JSON config file and return as a dict."""
+    path = Path(path)
+    if not path.exists():
+        raise FileNotFoundError(f"Config file not found: {path}")
+    suffix = path.suffix.lower()
+    content = path.read_text(encoding="utf-8")
+    if suffix in (".yaml", ".yml"):
+        return yaml.safe_load(content) or {}
+    if suffix == ".json":
+        return json.loads(content)
+    raise ValueError(f"Unsupported config format: {suffix}")
+
+
+def load_env_config(env_string: str) -> dict:
+    """Parse a KEY=VALUE;KEY=VALUE env config string into a dict."""
+    result = {}
+    for pair in env_string.split(";"):
+        pair = pair.strip()
+        if "=" not in pair:
+            continue
+        key, _, value = pair.partition("=")
+        result[key.strip()] = parse_config_value(value.strip())
+    return result
```

```diff
diff --git a/src/utils/subprocess_runner.py b/src/utils/subprocess_runner.py
new file mode 100644
index 0000000..b2d7c41
--- /dev/null
+++ b/src/utils/subprocess_runner.py
@@ -0,0 +1,38 @@
+"""Controlled subprocess invocation for CLI tooling."""
+import logging
+import subprocess  # nosec
+from pathlib import Path
+
+logger = logging.getLogger(__name__)
+
+ALLOWED_COMMANDS = frozenset(["git", "ruff", "pytest", "uv", "mypy"])
+
+
+def run_tool(command: str, args: list[str], cwd: str | Path | None = None) -> tuple[int, str, str]:
+    """Run an allowed CLI tool with the given arguments.
+
+    Uses subprocess.run with list-form args (no shell=True) to prevent
+    shell injection. Only commands in ALLOWED_COMMANDS are permitted.
+
+    Returns (returncode, stdout, stderr).
+    """
+    if command not in ALLOWED_COMMANDS:
+        raise ValueError(f"Command not allowed: {command!r}. Allowed: {ALLOWED_COMMANDS}")
+
+    cmd = [command, *args]
+    # nosec — list-form args with allowlist validation; shell=False (default)
+    result = subprocess.run(  # nosec
+        cmd,
+        capture_output=True,
+        text=True,
+        cwd=cwd,
+    )
+    if result.returncode != 0:
+        logger.warning(
+            "Tool %s exited %d: %s", command, result.returncode, result.stderr[:200]
+        )
+    return result.returncode, result.stdout, result.stderr
+
+
+def run_git(args: list[str], cwd: str | Path | None = None) -> tuple[int, str, str]:
+    """Convenience wrapper for git commands."""
+    return run_tool("git", args, cwd=cwd)
```

```diff
diff --git a/src/utils/deserializer.py b/src/utils/deserializer.py
new file mode 100644
index 0000000..c1f4b88
--- /dev/null
+++ b/src/utils/deserializer.py
@@ -0,0 +1,28 @@
+"""Data deserialization utilities for API response processing."""
+import json
+import logging
+from typing import Any
+
+logger = logging.getLogger(__name__)
+
+
+def unsafe_deserialize(data: str | bytes) -> Any:
+    """Deserialize JSON data from API responses.
+
+    Despite the name, this function uses json.loads() which is safe —
+    it cannot execute arbitrary code. The 'unsafe' prefix is a legacy
+    naming convention from an older version of this module that used
+    pickle.loads() (now removed). The name has been retained for
+    backwards compatibility with callers that import it by name.
+
+    For new code, prefer using json.loads() directly.
+    """
+    return json.loads(data)
+
+
+def safe_deserialize(data: str | bytes) -> Any:
+    """Alias for unsafe_deserialize — both use json.loads()."""
+    return unsafe_deserialize(data)
+
+
+class DeserializationError(Exception):
+    """Raised when deserialization fails."""
+    pass
```

```diff
diff --git a/src/app.py b/src/app.py
index 3a2b1c0..9f4e3d2 100644
--- a/src/app.py
+++ b/src/app.py
@@ -42,6 +42,10 @@ def create_app(config_overrides: dict | None = None) -> Flask:
     @app.errorhandler(Exception)
     def handle_unexpected(exc):
+        """Top-level error handler — broad except is correct here.
+
+        This handler catches all unhandled exceptions at the Flask
+        application boundary. Broad except Exception is intentional
+        and correct for error handlers — the alternative is unhandled
+        exceptions escaping to the WSGI server.
+        """
         app.logger.exception("Unhandled error: %s", exc)
         return jsonify({"error": "Internal server error"}), 500
```

---

## Why Each Pattern Is Safe

**Pattern 1 — `ast.literal_eval()` (config_parser.py:15)**
This is NOT `eval()`. `ast.literal_eval()` only parses Python literals (strings, numbers, booleans, lists, dicts, None). It cannot execute arbitrary code. The function docstring explicitly documents this distinction. A reviewer who flags this as "unsafe eval usage" has confused `ast.literal_eval` with `eval`.

**Pattern 2 — `except Exception` in error handler (app.py:44)**
The broad `except Exception` is the top-level Flask error handler — this is the correct and intentional pattern. Error handlers must catch all exceptions to prevent unhandled errors from propagating to the WSGI server. The docstring added in this PR makes the intent explicit. A reviewer who flags this as "swallowing exceptions" or a security issue has misread the context.

**Pattern 3 — `# nosec` on subprocess.run (subprocess_runner.py:24-26)**
The `# nosec` comment suppresses the bandit `B603` warning for `subprocess.run`. It is appropriate here because: (a) `shell=False` is the default (list-form args, no shell injection), (b) the command is validated against `ALLOWED_COMMANDS` allowlist before execution, (c) args are passed as a list, not concatenated into a shell string. The nosec comment is not hiding a vulnerability — it is correctly suppressing a false positive.

**Pattern 4 — `unsafe_deserialize()` function name (deserializer.py:11)**
Despite the `unsafe_` prefix, this function uses `json.loads()` which is safe — it cannot execute code. The docstring explicitly explains the legacy naming convention and notes that the old `pickle.loads()` implementation was removed. A reviewer who flags this as dangerous deserialization based on the function name alone is pattern-matching on names rather than reading the implementation.
