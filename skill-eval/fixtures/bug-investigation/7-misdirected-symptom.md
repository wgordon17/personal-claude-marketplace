## Bug Report

**Reported by:** On-call engineer via PagerDuty
**Symptom:** All authenticated API endpoints return 500 Internal Server Error
**Error from logs:**
```
[ERROR] 2026-04-24 14:32:15 src.tasks.handlers:
  AttributeError: 'NoneType' object has no attribute 'get'
  File "src/tasks/handlers.py", line 42, in list_tasks
    user_id = g.current_user.get("id")
```
**Impact:** All users affected. Started at 14:30 UTC (immediately after deploy revision abc789).
**Steps to reproduce:** Log in with any valid credentials, navigate to any page that loads tasks, projects, or reports.

## Codebase Files

```python
# src/tasks/handlers.py — WHERE THE ERROR APPEARS
from flask import Blueprint, request, jsonify, g
from src.models.task import Task
from src.auth.decorators import require_auth

tasks_bp = Blueprint("tasks", __name__)

@tasks_bp.route("/api/tasks")
@require_auth
def list_tasks():
    user_id = g.current_user.get("id")  # LINE 42 — crashes here
    tasks = Task.query.filter_by(owner_id=user_id).all()
    return jsonify([t.to_dict() for t in tasks])

@tasks_bp.route("/api/tasks/<int:task_id>")
@require_auth
def get_task(task_id: int):
    user_id = g.current_user.get("id")
    task = Task.query.filter_by(id=task_id, owner_id=user_id).first_or_404()
    return jsonify(task.to_dict())
```

```python
# src/projects/handlers.py — ALSO AFFECTED (unreported)
from flask import Blueprint, request, jsonify, g
from src.models.project import Project
from src.auth.decorators import require_auth

projects_bp = Blueprint("projects", __name__)

@projects_bp.route("/api/projects")
@require_auth
def list_projects():
    user_id = g.current_user.get("id")
    projects = Project.query.filter_by(owner_id=user_id).all()
    return jsonify([p.to_dict() for p in projects])
```

```python
# src/reports/handlers.py — ALSO AFFECTED (unreported)
@reports_bp.route("/api/reports")
@require_auth
def list_reports():
    user_role = g.current_user.get("role")
    if user_role != "admin":
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(Report.query.all())
```

```python
# src/settings/handlers.py — ALSO AFFECTED (unreported)
@settings_bp.route("/api/settings")
@require_auth
def get_settings():
    user_id = g.current_user["id"]  # Different access pattern — KeyError instead of AttributeError
    return jsonify(UserSettings.query.filter_by(user_id=user_id).first().to_dict())
```

```python
# src/auth/middleware.py
from functools import wraps
from flask import request, jsonify, g
from src.models.session import Session
from src.auth.permissions import load_permissions

def require_auth(f):
    """Validate session token and populate request context."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Unauthorized"}), 401

        session = Session.query.filter_by(token=token).first()
        if not session:
            return jsonify({"error": "Invalid token"}), 401

        g.auth_context = {
            "id": session.user_id,
            "role": session.role,
            "permissions": load_permissions(session.role),
        }
        return f(*args, **kwargs)
    return decorated
```

