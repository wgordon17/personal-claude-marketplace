---
# Fixture metadata (stripped by loader)
planted_issues: []
notes:
  - "This is a NEGATIVE test case - no bugs, no vulnerabilities"
  - "The refactoring is correct: extracts shared parsing logic, updates callers, adds test"
  - "Goal: verify the skill does NOT fabricate findings on clean code"
---

```diff
diff --git a/src/ingestion/csv_loader.py b/src/ingestion/csv_loader.py
index 3a1b2c3..4d5e6f7 100644
--- a/src/ingestion/csv_loader.py
+++ b/src/ingestion/csv_loader.py
@@ -1,8 +1,7 @@
 import csv
 from pathlib import Path
 
-from src.ingestion.validators import validate_row
-from src.ingestion.normalizers import normalize_phone, normalize_date
+from src.ingestion.parsing import parse_and_validate_row
 
 
 def load_csv(filepath: str, schema: dict) -> list[dict]:
@@ -18,14 +17,7 @@ def load_csv(filepath: str, schema: dict) -> list[dict]:
         reader = csv.DictReader(fh)
         for line_num, raw_row in enumerate(reader, start=2):
             try:
-                row = {}
-                for field, spec in schema.items():
-                    value = raw_row.get(field, "").strip()
-                    if spec.get("type") == "phone":
-                        value = normalize_phone(value)
-                    elif spec.get("type") == "date":
-                        value = normalize_date(value)
-                    row[field] = validate_row(field, value, spec)
+                row = parse_and_validate_row(raw_row, schema)
                 results.append(row)
             except ValueError as exc:
                 errors.append({"line": line_num, "error": str(exc)})
diff --git a/src/ingestion/json_loader.py b/src/ingestion/json_loader.py
index 7a8b9c0..1d2e3f4 100644
--- a/src/ingestion/json_loader.py
+++ b/src/ingestion/json_loader.py
@@ -1,7 +1,6 @@
 import json
 
-from src.ingestion.validators import validate_row
-from src.ingestion.normalizers import normalize_phone, normalize_date
+from src.ingestion.parsing import parse_and_validate_row
 
 
 def load_json(filepath: str, schema: dict) -> list[dict]:
@@ -14,14 +13,7 @@ def load_json(filepath: str, schema: dict) -> list[dict]:
         records = data if isinstance(data, list) else [data]
         for idx, raw_row in enumerate(records):
             try:
-                row = {}
-                for field, spec in schema.items():
-                    value = str(raw_row.get(field, "")).strip()
-                    if spec.get("type") == "phone":
-                        value = normalize_phone(value)
-                    elif spec.get("type") == "date":
-                        value = normalize_date(value)
-                    row[field] = validate_row(field, value, spec)
+                row = parse_and_validate_row(raw_row, schema)
                 results.append(row)
             except ValueError as exc:
                 errors.append({"index": idx, "error": str(exc)})
diff --git a/src/ingestion/parsing.py b/src/ingestion/parsing.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/ingestion/parsing.py
@@ -0,0 +1,26 @@
+"""Shared row parsing logic extracted from csv_loader and json_loader."""
+
+from src.ingestion.normalizers import normalize_date, normalize_phone
+from src.ingestion.validators import validate_row
+
+
+def parse_and_validate_row(raw_row: dict, schema: dict) -> dict:
+    """Parse, normalize, and validate a single data row against a schema.
+
+    Args:
+        raw_row: Raw field-value mapping from the input source.
+        schema: Field specifications with type and validation rules.
+
+    Returns:
+        Validated and normalized row dict.
+
+    Raises:
+        ValueError: If any field fails validation.
+    """
+    row = {}
+    for field, spec in schema.items():
+        value = str(raw_row.get(field, "")).strip()
+        if spec.get("type") == "phone":
+            value = normalize_phone(value)
+        elif spec.get("type") == "date":
+            value = normalize_date(value)
+        row[field] = validate_row(field, value, spec)
+    return row
diff --git a/tests/test_parsing.py b/tests/test_parsing.py
new file mode 100644
index 0000000..b2c3d4e
--- /dev/null
+++ b/tests/test_parsing.py
@@ -0,0 +1,28 @@
+import pytest
+
+from src.ingestion.parsing import parse_and_validate_row
+
+
+def test_parse_valid_row():
+    schema = {
+        "name": {"type": "string", "required": True},
+        "phone": {"type": "phone", "required": False},
+    }
+    raw = {"name": "  Alice  ", "phone": "555-1234"}
+    result = parse_and_validate_row(raw, schema)
+    assert result["name"] == "Alice"
+    assert result["phone"] == "5551234"
+
+
+def test_parse_missing_optional_field():
+    schema = {
+        "name": {"type": "string", "required": True},
+        "phone": {"type": "phone", "required": False},
+    }
+    raw = {"name": "Bob"}
+    result = parse_and_validate_row(raw, schema)
+    assert result["name"] == "Bob"
+
+
+def test_parse_invalid_required_field():
+    schema = {"name": {"type": "string", "required": True}}
+    with pytest.raises(ValueError, match="name"):
+        parse_and_validate_row({}, schema)
```
