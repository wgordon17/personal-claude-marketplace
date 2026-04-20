---
planted_issues:
  - type: missing_validation_helper
    file: src/utils.py
    line: 0
    order: 1
  - type: missing_validation_call
    file: src/handlers.py
    line: 28
    order: 2
    depends_on: utils.py
  - type: missing_tests
    file: tests/test_utils.py
    line: 0
    order: 3
    depends_on: utils.py
expected_findings: 3
dependency_chain: [utils.py, handlers.py, test_utils.py]
---

## CODE REVIEW Findings

The following 3 findings were identified by domain reviewers and verified. They form a dependency chain: the validation helper must exist before it can be called, and tests must target the helper.

### CORRECTNESS

**Finding pr-corr-1:** Request handlers accept malformed payloads without validation
**Location:** `src/handlers.py:28`
**Evidence:** The `create_project` and `update_project` handlers extract fields from request JSON without validating required fields, types, or constraints. The validation helper referenced in the architecture design (`validate_project_payload` from `src/utils.py`) does not exist yet.

```python
# src/handlers.py
from flask import Blueprint, request, jsonify
from db import get_db_connection
from auth import require_auth

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/api/projects", methods=["GET"])
@require_auth
def list_projects():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, status FROM projects")
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)


@projects_bp.route("/api/projects", methods=["POST"])
@require_auth
def create_project():
    data = request.get_json()
    name = data.get("name")
    description = data.get("description", "")
    status = data.get("status", "active")
    owner_id = data.get("owner_id")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects (name, description, status, owner_id) VALUES (?, ?, ?, ?)",
        (name, description, status, owner_id),
    )
    conn.commit()
    project_id = cursor.lastrowid
    conn.close()
    return jsonify({"id": project_id, "name": name, "status": status}), 201


@projects_bp.route("/api/projects/<int:project_id>", methods=["PUT"])
@require_auth
def update_project(project_id):
    data = request.get_json()
    name = data.get("name")
    description = data.get("description")
    status = data.get("status")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET name = ?, description = ?, status = ? WHERE id = ?",
        (name, description, status, project_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"id": project_id, "name": name, "status": status})
```

---

### ARCHITECTURE

**Finding pr-arch-1:** Missing shared validation utility referenced by handlers
**Location:** `src/utils.py`
**Evidence:** The architecture design specifies a `validate_project_payload` function in `src/utils.py` that handlers should call before database operations. This function does not exist. It should validate: (1) `name` is a non-empty string of max 200 chars, (2) `status` is one of the allowed values, (3) `owner_id` is a positive integer when provided.

```python
# src/utils.py (current contents)
import re
from typing import Any


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def truncate(text: str, max_length: int = 100) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


ALLOWED_STATUSES = frozenset({"active", "archived", "draft", "completed"})
```

---

### TESTING GAPS

**Finding pr-test-1:** No tests for validation utility
**Location:** `tests/test_utils.py`
**Evidence:** The `src/utils.py` module has no corresponding test file. The existing utility functions (`slugify`, `truncate`) and the new `validate_project_payload` function (once added) need tests covering edge cases and error paths.

```python
# tests/test_utils.py does not exist

# tests/conftest.py (for reference)
import pytest
from app import create_app


@pytest.fixture
def app():
    app = create_app(testing=True)
    yield app


@pytest.fixture
def client(app):
    return app.test_client()
```
