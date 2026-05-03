## Git Diff — Task Export and Reporting Refactor

This diff refactors the task export system to add a reporting layer and a new "saved export" feature. The change touches 5 files.

---

```diff
diff --git a/src/tasks/export_helpers.py b/src/tasks/export_helpers.py
new file mode 100644
index 0000000..a3f7c12
--- /dev/null
+++ b/src/tasks/export_helpers.py
@@ -0,0 +1,42 @@
+"""Shared helpers for task export and reporting."""
+import html
+import re
+
+
+ALLOWED_SORT_FIELDS = {"title", "status", "priority", "created_at", "due_date"}
+
+
+def sanitize_export_field(value: str) -> str:
+    """Sanitize a field value for safe inclusion in HTML export output.
+
+    Escapes HTML entities to prevent XSS in rendered export pages.
+    """
+    if not isinstance(value, str):
+        value = str(value)
+    return html.escape(value)
+
+
+def validate_sort_field(field: str) -> str:
+    """Validate and return an allowed sort field name.
+
+    Raises ValueError for unknown fields to prevent SQL injection
+    via ORDER BY clause.
+    """
+    if field not in ALLOWED_SORT_FIELDS:
+        raise ValueError(f"Invalid sort field: {field!r}. Allowed: {ALLOWED_SORT_FIELDS}")
+    return field
+
+
+def build_export_filename(user_id: int, fmt: str) -> str:
+    """Build a safe export filename."""
+    fmt = re.sub(r"[^a-z]", "", fmt.lower())[:10]
+    return f"export_{user_id}.{fmt or 'json'}"
+
+
+def truncate_description(description: str, max_len: int = 200) -> str:
+    """Truncate long descriptions for export summary views."""
+    if not description:
+        return ""
+    description = description.strip()
+    if len(description) <= max_len:
+        return description
+    return description[:max_len] + "…"
```

---

```diff
diff --git a/src/tasks/export.py b/src/tasks/export.py
index 4a8c21d..d91f734 100644
--- a/src/tasks/export.py
+++ b/src/tasks/export.py
@@ -1,12 +1,14 @@
 import csv
 import io
 import json
 import requests
 from flask import Blueprint, request, jsonify, g
 from src.db import get_session
 from src.models.task import Task
 from src.auth.permissions import require_auth
+from src.tasks.export_helpers import sanitize_export_field, validate_sort_field, build_export_filename
+from sqlalchemy import text

 export_bp = Blueprint("export", __name__)

@@ -18,6 +20,31 @@ def validate_webhook_url(url):
     return True, None


+def render_html_export(tasks, title: str = "Task Export") -> str:
+    """Render tasks as an HTML report page.
+
+    Uses sanitize_export_field() on all user-derived fields before
+    embedding in HTML output.
+    """
+    rows = []
+    for task in tasks:
+        d = task.to_dict()
+        rows.append(
+            f"<tr>"
+            f"<td>{sanitize_export_field(d.get('title', ''))}</td>"
+            f"<td>{sanitize_export_field(d.get('status', ''))}</td>"
+            f"<td>{sanitize_export_field(d.get('priority', ''))}</td>"
+            f"<td>{sanitize_export_field(d.get('description', ''))}</td>"
+            f"</tr>"
+        )
+    title_safe = sanitize_export_field(title)
+    body = "\n".join(rows)
+    return f"<html><body><h1>{title_safe}</h1><table>{body}</table></body></html>"
+
+
+def get_tasks_sorted(db_session, user_id: int, sort_by: str = "created_at") -> list:
+    """Fetch tasks for a user with validated sort field."""
+    sort_field = validate_sort_field(sort_by)
+    query = text(f"SELECT * FROM tasks WHERE owner_id = :uid ORDER BY {sort_field} DESC")
+    rows = db_session.execute(query, {"uid": user_id}).fetchall()
+    return [dict(r._mapping) for r in rows]
+
+
 @export_bp.route("/export", methods=["POST"])
 @require_auth
 def export_tasks():
```

---

```diff
diff --git a/src/tasks/saved_exports.py b/src/tasks/saved_exports.py
new file mode 100644
index 0000000..b4e1f20
--- /dev/null
+++ b/src/tasks/saved_exports.py
@@ -0,0 +1,88 @@
+"""Saved export configurations — users can save named export filter presets."""
+from flask import Blueprint, request, jsonify, g
+from src.db import get_session
+from src.tasks.export_helpers import validate_sort_field, build_export_filename, sanitize_export_field
+from src.auth.permissions import require_auth
+
+saved_exports_bp = Blueprint("saved_exports", __name__)
+
+# In-memory store for saved export configs (keyed by user_id)
+_saved_configs: dict[int, list[dict]] = {}
+
+
+def save_export_config(user_id: int, name: str, filters: dict, sort_by: str) -> dict:
+    """Save a named export configuration for a user."""
+    sort_by = validate_sort_field(sort_by)
+    config = {"name": name, "filters": filters, "sort_by": sort_by}
+    _saved_configs.setdefault(user_id, []).append(config)
+    return config
+
+
+def list_saved_configs(user_id: int) -> list[dict]:
+    """Return all saved export configs for a user."""
+    return _saved_configs.get(user_id, [])
+
+
+@saved_exports_bp.route("/exports/saved", methods=["POST"])
+@require_auth
+def create_saved_export():
+    data = request.get_json() or {}
+    name = data.get("name", "Untitled")
+    filters = data.get("filters", {})
+    sort_by = data.get("sort_by", "created_at")
+    try:
+        config = save_export_config(g.current_user.id, name, filters, sort_by)
+    except ValueError as e:
+        return jsonify({"error": str(e)}), 422
+    return jsonify(config), 201
+
+
+def _render_export_table(config_name: str, task_rows: list[dict]) -> str:
+    """Render task rows as an HTML table for saved export results.
+
+    Sanitizes the config name for the page title. Task field values
+    are rendered from the database rows.
+    """
+    safe_title = sanitize_export_field(config_name)
+    rows_html = []
+    for t in task_rows:
+        title_val = sanitize_export_field(t.get("title", ""))
+        status_val = t.get("status", "")
+        desc_val = t.get("description", "")
+        rows_html.append(
+            f"<tr><td>{title_val}</td><td>{status_val}</td><td>{desc_val}</td></tr>"
+        )
+    return f"<html><body><h1>{safe_title}</h1><table>{''.join(rows_html)}</table></body></html>"
+
+
+@saved_exports_bp.route("/exports/saved/<int:config_idx>/run", methods=["POST"])
+@require_auth
+def run_saved_export(config_idx: int):
+    """Execute a saved export configuration and return results as HTML."""
+    configs = list_saved_configs(g.current_user.id)
+    if config_idx >= len(configs):
+        return jsonify({"error": "Config not found"}), 404
+
+    config = configs[config_idx]
+    db_session = get_session()
+
+    # Fetch tasks matching the saved filter config
+    status_filter = config["filters"].get("status")
+    sort_by = config["sort_by"]
+
+    from src.tasks.export_helpers import validate_sort_field
+    from sqlalchemy import text
+
+    sort_field = validate_sort_field(sort_by)
+
+    # Build the query — note: sort_field is validated by validate_sort_field above
+    if status_filter:
+        query = text(
+            f"SELECT * FROM tasks WHERE owner_id = :uid AND status = :status ORDER BY {sort_field} DESC"
+        )
+        rows = db_session.execute(query, {"uid": g.current_user.id, "status": status_filter}).fetchall()
+    else:
+        query = text(f"SELECT * FROM tasks WHERE owner_id = :uid ORDER BY {sort_field} DESC")
+        rows = db_session.execute(query, {"uid": g.current_user.id}).fetchall()
+
+    tasks_data = [dict(r._mapping) for r in rows]
+    html_out = _render_export_table(config.get("name", "Export"), tasks_data)
+    return html_out, 200, {"Content-Type": "text/html"}
```

---

```diff
diff --git a/src/tasks/search.py b/src/tasks/search.py
index 7c4a8f2..e3d2b91 100644
--- a/src/tasks/search.py
+++ b/src/tasks/search.py
@@ -44,6 +44,19 @@ def search_tasks():
     save_search_term(user.id, term, db_session)

     tasks = db_session.query(Task).filter(
         (Task.owner_id == user.id) | (Task.is_public == True),  # noqa: E712
         Task.title.ilike(f"%{term}%"),
     ).all()

     return jsonify({"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}), 200
+
+
+@search_bp.route("/search/export-results", methods=["POST"])
+@require_auth
+def export_search_results():
+    """Export the results of a search as an HTML page."""
+    data = request.get_json() or {}
+    term = data.get("q", "").strip()
+    if not term:
+        return jsonify({"error": "Search term required"}), 400
+
+    user = g.current_user
+    db_session = get_session()
+
+    # Save and replay the search (same path as search_tasks)
+    save_search_term(user.id, term, db_session)
+    tasks = search_tasks_by_saved_term(user.id, db_session)
+
+    # Render results as HTML — tasks are raw dicts from search_tasks_by_saved_term
+    rows_html = []
+    for t in tasks:
+        rows_html.append(
+            f"<tr><td>{t.get('title', '')}</td><td>{t.get('status', '')}</td></tr>"
+        )
+    return f"<html><body><table>{''.join(rows_html)}</table></body></html>", 200, {"Content-Type": "text/html"}
```

---

```diff
diff --git a/tests/tasks/test_export.py b/tests/tasks/test_export.py
index 0000000..c3d1e9f 100644
--- /dev/null
+++ b/tests/tasks/test_export.py
@@ -0,0 +1,47 @@
+import pytest
+from src.tasks.export_helpers import sanitize_export_field, validate_sort_field, build_export_filename
+
+
+class TestSanitizeExportField:
+    def test_escapes_html_entities(self):
+        assert sanitize_export_field("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
+
+    def test_passthrough_plain_text(self):
+        assert sanitize_export_field("Task title") == "Task title"
+
+    def test_converts_non_string(self):
+        assert sanitize_export_field(42) == "42"
+
+
+class TestValidateSortField:
+    def test_valid_field_returned(self):
+        assert validate_sort_field("title") == "title"
+        assert validate_sort_field("created_at") == "created_at"
+
+    def test_invalid_field_raises(self):
+        with pytest.raises(ValueError, match="Invalid sort field"):
+            validate_sort_field("malicious; DROP TABLE tasks --")
+
+    def test_all_allowed_fields_pass(self):
+        from src.tasks.export_helpers import ALLOWED_SORT_FIELDS
+        for field in ALLOWED_SORT_FIELDS:
+            assert validate_sort_field(field) == field
+
+
+class TestBuildExportFilename:
+    def test_builds_correct_filename(self):
+        assert build_export_filename(42, "json") == "export_42.json"
+
+    def test_strips_non_alpha(self):
+        assert build_export_filename(1, "csv; DROP") == "export_1.csv"
+
+    def test_truncates_long_format(self):
+        result = build_export_filename(1, "verylongformat")
+        assert len(result.split(".")[1]) <= 10
+
+
+class TestHTMLExport:
+    def test_html_export_escapes_fields(self, client, auth_headers, test_task):
+        response = client.post("/export", json={"format": "html"}, headers=auth_headers)
+        assert response.status_code == 200
+        assert "<script>" not in response.text
+
+    def test_saved_export_run_returns_html(self, client, auth_headers):
+        client.post("/exports/saved", json={"name": "My Export", "sort_by": "title"}, headers=auth_headers)
+        response = client.post("/exports/saved/0/run", headers=auth_headers)
+        assert response.status_code == 200
```
