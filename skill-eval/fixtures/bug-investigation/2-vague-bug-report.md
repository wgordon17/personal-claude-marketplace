---
planted_issues:
  - type: n_plus_1_query
    file: src/api/dashboard.py
    line: 31
    description: "Loop issues individual SELECT for each project's task count (red herring)"
  - type: unbuffered_file_read
    file: src/reports/export.py
    line: 14
    description: "Reads entire CSV into memory without streaming (red herring)"
  - type: synchronous_http_call
    file: src/api/dashboard.py
    line: 45
    description: "Blocking HTTP call to weather API in request path (actual cause)"
expected_findings: 3
primary_cause: synchronous_http_call
---

## Bug Report

**Title:** The app is slow sometimes

**Reported by:** End user (support ticket #4821)

**Reproduction Steps:** None provided.

**User's description:** "The dashboard page takes forever to load. Sometimes it's fine, sometimes I'm waiting 10+ seconds. Started happening about two weeks ago."

**Environment:** Production (v3.1.0), Python 3.12 / Flask 3.0, PostgreSQL 15, deployed on AWS ECS

---

## Codebase Context

### `src/api/dashboard.py`

```python
# src/api/dashboard.py
import requests
from flask import Blueprint, jsonify, g
from src.db import get_connection

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")

WEATHER_API_URL = "https://api.weatherservice.example.com/v1/current"
WEATHER_API_KEY = "wk_prod_a8f3e2d1c9b7"


@dashboard_bp.route("/")
def get_dashboard():
    user_id = g.current_user["id"]
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Get user's projects
        cursor.execute(
            "SELECT id, name, created_at FROM projects WHERE owner_id = %s",
            (user_id,),
        )
        projects = cursor.fetchall()

        # Get task counts per project
        enriched = []
        for project in projects:
            cursor.execute(
                "SELECT COUNT(*) FROM tasks WHERE project_id = %s",
                (project[0],),
            )
            task_count = cursor.fetchone()[0]
            enriched.append({
                "id": project[0],
                "name": project[1],
                "created_at": str(project[2]),
                "task_count": task_count,
            })

        # Fetch weather widget data for the dashboard
        weather = _fetch_weather(g.current_user.get("city", "New York"))

        return jsonify({
            "projects": enriched,
            "weather": weather,
        })
    finally:
        conn.close()


def _fetch_weather(city: str) -> dict:
    """Fetch current weather for the dashboard widget."""
    try:
        resp = requests.get(
            WEATHER_API_URL,
            params={"city": city, "key": WEATHER_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"temp": data["temp"], "condition": data["condition"]}
    except requests.RequestException:
        return {"temp": "N/A", "condition": "unavailable"}
```

### `src/reports/export.py`

```python
# src/reports/export.py
import csv
import io
from flask import Blueprint, send_file, g
from src.db import get_connection

export_bp = Blueprint("export", __name__, url_prefix="/api/export")


@export_bp.route("/tasks")
def export_tasks_csv():
    """Export all tasks for the current user as CSV."""
    user_id = g.current_user["id"]
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT t.id, t.title, t.status, p.name "
            "FROM tasks t JOIN projects p ON t.project_id = p.id "
            "WHERE p.owner_id = %s",
            (user_id,),
        )
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Task ID", "Title", "Status", "Project"])
        for row in rows:
            writer.writerow(row)

        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="tasks.csv",
        )
    finally:
        conn.close()
```

### `src/middleware/timing.py`

```python
# src/middleware/timing.py
import time
import logging
from flask import g, request

logger = logging.getLogger(__name__)


def register_timing_middleware(app):
    @app.before_request
    def start_timer():
        g.start_time = time.monotonic()

    @app.after_request
    def log_request_time(response):
        if hasattr(g, "start_time"):
            elapsed = time.monotonic() - g.start_time
            if elapsed > 2.0:
                logger.warning(
                    "Slow request: %s %s took %.2fs",
                    request.method,
                    request.path,
                    elapsed,
                )
        return response
```
