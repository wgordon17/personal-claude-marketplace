## CODE REVIEW Findings

The following 6 findings were identified by domain reviewers and verified. All findings are valid.

---

### Finding 1 — Severity: Medium
**Category:** Error Handling
**Title:** `create_project` endpoint does not return a consistent error envelope on validation failure
**Affected files:** `src/api/projects.py:62-78`

**Evidence:** When `validate_project_name()` returns `False`, the endpoint returns `{"error": "Name must be 1-100 characters"}` as a plain string. All other endpoints in the codebase wrap errors in `{"error": {"code": "VALIDATION_ERROR", "message": "..."}}`. This inconsistency breaks the frontend error parser at `ui/src/api/errors.ts:18`.

```python
# src/api/projects.py:70-73
if not validate_project_name(name):
    return jsonify({"error": "Name must be 1-100 characters"}), 422
    # Should be: {"error": {"code": "VALIDATION_ERROR", "message": "..."}}
```

**Fix:** Update the `jsonify` call to use the structured error envelope: `jsonify({"error": {"code": "VALIDATION_ERROR", "message": "Name must be 1-100 characters"}})`. Apply the same pattern to the description check at L73. Two-line change in `projects.py`.

---

### Finding 2 — Severity: High
**Category:** Security
**Title:** `export_report()` passes raw user-supplied `format` parameter to file path construction without validation
**Affected files:** `src/api/reports.py:94-108`

**Evidence:** The `export_report` endpoint reads `format` from the JSON body and passes it directly to `_build_export_path(user_id, format)` in `src/utils/file_utils.py`. `_build_export_path` calls `sanitize_filename(format)` — but `sanitize_filename` in `src/utils/file_utils.py` only strips spaces and special characters; it does not validate that the result is a known allowed extension. An attacker can submit `format=../../../../etc/cron.d/backdoor` and, after stripping special characters, produce a path like `exports/42/etccrondbacked` that writes to an unexpected location.

```python
# src/api/reports.py:94-108
@reports_bp.route("/reports/export", methods=["POST"])
@require_auth
def export_report():
    data = request.get_json() or {}
    export_format = data.get("format", "json")   # user-controlled
    filters = {k: v for k, v in data.items() if k in {"status", "priority"}}

    report = generate_task_report(g.current_user.id, filters, get_session())
    path = _build_export_path(g.current_user.id, export_format)  # no allowlist check
    _write_report(report, path, export_format)
    return send_file(path), 200
```

```python
# src/utils/file_utils.py:22-28  (shared utility — also used by Finding 5's call site)
def sanitize_filename(name: str) -> str:
    # Strips non-alphanumeric characters but does NOT validate against an allowlist
    return re.sub(r"[^a-zA-Z0-9_\-]", "", name)
```

**Fix:** Add an allowlist check in `export_report()` before calling `_build_export_path`: validate `export_format` against `ALLOWED_FORMATS = {"json", "csv", "xlsx"}` and return 422 if it is not in the set. This check belongs at the API boundary, not inside `sanitize_filename` (which is a general-purpose utility). One `if export_format not in ALLOWED_FORMATS` guard at `reports.py:97`.

---

### Finding 3 — Severity: Low
**Category:** Code Quality
**Title:** `list_projects` endpoint has a dead `include_archived` query parameter that is parsed but never used
**Affected files:** `src/api/projects.py:26-38`

**Evidence:** `request.args.get("include_archived", False, type=bool)` is parsed into a local variable but never passed to `list_projects_for_user()`. The query builder in `project.py` has no `include_archived` parameter. Dead parameter handling misleads API consumers and clutters request parsing.

```python
# src/api/projects.py:29-33
def list_projects():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))
    include_archived = request.args.get("include_archived", False, type=bool)  # unused
    with get_db() as db:
        projects = list_projects_for_user(db, g.user_id, page=page, per_page=per_page)
```

**Fix:** Remove the `include_archived` line. If the parameter is intended for a future feature, it should be tracked in the backlog rather than silently parsed and ignored.

---

### Finding 4 — Severity: Medium
**Category:** Testing
**Title:** `test_create_project` does not assert the response body — only the status code
**Affected files:** `tests/api/test_projects.py:44-57`

**Evidence:** The test calls `client.post("/projects/", json={...})` and asserts `assert response.status_code == 201` but does not assert `response.json["id"]` or `response.json["name"]`. A regression that returns `201` with an empty body would not be caught.

```python
# tests/api/test_projects.py:44-57
def test_create_project(client, auth_headers):
    response = client.post(
        "/projects/",
        json={"name": "Test Project", "description": "A test"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    # Missing: assert "id" in response.json
    # Missing: assert response.json["name"] == "Test Project"
```

**Fix:** Add assertions for `response.json["id"]` (must be a positive integer) and `response.json["name"] == "Test Project"` after the status code check. Two lines.

---

### Finding 5 — Severity: High
**Category:** Security
**Title:** `download_attachment()` passes user-supplied `filename` parameter to path construction without allowlist validation
**Affected files:** `src/api/attachments.py:55-71`

**Evidence:** The `download_attachment` endpoint reads `filename` from `request.args` and passes it directly to `_build_attachment_path(ticket_id, filename)`. `_build_attachment_path` calls `sanitize_filename(filename)` — the same shared utility used by `export_report` (Finding 2). `sanitize_filename` strips special characters but does not validate that the result is a known file that actually exists in the ticket's attachment directory. An attacker can probe for arbitrary filenames under the `attachments/` tree.

```python
# src/api/attachments.py:55-71
@attachments_bp.route("/tickets/<int:ticket_id>/attachments/download", methods=["GET"])
@require_auth
def download_attachment(ticket_id: int):
    filename = request.args.get("filename", "")   # user-controlled
    if not filename:
        return jsonify({"error": "filename required"}), 400

    path = _build_attachment_path(ticket_id, filename)  # sanitize_filename called inside
    if not path.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(path), 200
```

**Root cause (shared with Finding 2):** Both Finding 2 and Finding 5 exploit the same gap: `sanitize_filename` in `src/utils/file_utils.py` is used as if it were sufficient for path safety, but it only sanitizes character content — it does not enforce an allowlist of valid values. Each call site must add its own allowlist or existence check at the API boundary.

**Fix:** In `download_attachment()`, after sanitizing, verify the resulting path is within the expected ticket attachment directory: `if not path.resolve().is_relative_to(ATTACHMENTS_ROOT / str(ticket_id)): return 403`. Additionally validate `filename` against the set of filenames actually stored for the ticket (query the `attachments` table). These checks belong at `attachments.py:61-65`, not in the shared utility.

---

### Finding 6 — Severity: Low
**Category:** Documentation
**Title:** `ProjectMembership` model docstring describes the old many-to-many join table schema; column names are stale
**Affected files:** `src/models/project.py:8-15`

**Evidence:** The `ProjectMembership` class docstring says `Columns: project_id, member_id, joined_at` but the actual columns are `project_id, user_id, created_at` (renamed in migration 0014). The docstring has not been updated since the migration.

```python
# src/models/project.py:8-15
class ProjectMembership:
    """Join table for project members.
    Columns: project_id, member_id, joined_at   <-- stale, should be user_id, created_at
    """
    project_id: int
    user_id: int      # was: member_id
    created_at: date  # was: joined_at
```

**Fix:** Update the docstring to reflect the current column names: `Columns: project_id, user_id, created_at`.
