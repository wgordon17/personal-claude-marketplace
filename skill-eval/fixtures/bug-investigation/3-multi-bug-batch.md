---
planted_issues:
  - type: routing_typo
    file: src/api/routes.py
    line: 12
    description: "Route registered as /api/user instead of /api/users"
  - type: missing_smtp_config
    file: src/notifications/email.py
    line: 22
    description: "SMTP host/port read from env vars that are never set in deployment"
  - type: timezone_not_converted
    file: src/api/dashboard_stats.py
    line: 34
    description: "Timestamps stored as UTC but rendered without conversion to user's local timezone"
expected_findings: 3
---

## Bug Reports (Batch)

Three bugs reported simultaneously by the QA team after staging deployment:

---

### Bug 1: 404 on /api/users endpoint

**Reported by:** Frontend team

**Description:** All requests to `GET /api/users` return 404 Not Found. The endpoint worked in the previous release. Frontend console shows `GET /api/users 404` on the team management page.

**Codebase context:**

```python
# src/api/routes.py
from flask import Flask
from src.api.user_handlers import user_bp
from src.api.project_handlers import project_bp
from src.api.dashboard_stats import stats_bp


def register_routes(app: Flask):
    """Register all API blueprints."""
    app.register_blueprint(project_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(user_bp, url_prefix="/api/user")
```

```python
# src/api/user_handlers.py
from flask import Blueprint, jsonify, g
from src.db import get_connection

user_bp = Blueprint("users", __name__)


@user_bp.route("/users", methods=["GET"])
def list_users():
    """List all users visible to the current user."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, email, role FROM users ORDER BY username"
        )
        users = [
            {"id": r[0], "username": r[1], "email": r[2], "role": r[3]}
            for r in cursor.fetchall()
        ]
        return jsonify(users)
    finally:
        conn.close()


@user_bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, email, role FROM users WHERE id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return jsonify({"error": "User not found"}), 404
        return jsonify({"id": row[0], "username": row[1], "email": row[2], "role": row[3]})
    finally:
        conn.close()
```

---

### Bug 2: Email notifications not sending

**Reported by:** Customer success team

**Description:** Users are not receiving email notifications for new task assignments. No errors in application logs. The notification service appears to run but emails never arrive.

**Codebase context:**

```python
# src/notifications/email.py
import os
import smtplib
from email.mime.text import MIMEText
import logging

logger = logging.getLogger(__name__)


def send_notification_email(to_address: str, subject: str, body: str) -> bool:
    """Send an email notification. Returns True on success."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_host:
        logger.debug("SMTP not configured, skipping email")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_address

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except smtplib.SMTPException as exc:
        logger.error("Failed to send email to %s: %s", to_address, exc)
        return False
```

```python
# src/notifications/dispatcher.py
from src.notifications.email import send_notification_email
import logging

logger = logging.getLogger(__name__)


def notify_task_assigned(user_email: str, task_title: str, project_name: str):
    """Notify a user they've been assigned a task."""
    subject = f"New task assigned: {task_title}"
    body = (
        f"You have been assigned a new task in {project_name}.\n\n"
        f"Task: {task_title}\n\n"
        "Log in to view details."
    )
    result = send_notification_email(user_email, subject, body)
    if not result:
        logger.info("Email not sent for task '%s' to %s", task_title, user_email)
```

```yaml
# deploy/staging.env (environment variables set in deployment)
DATABASE_URL=postgresql://app:secret@db:5432/appdb
REDIS_URL=redis://redis:6379/0
SECRET_KEY=staging-secret-key-do-not-use-in-prod
# Note: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS are not defined
```

---

### Bug 3: Dashboard shows wrong timezone

**Reported by:** International users (support tickets #4901, #4903, #4907)

**Description:** Dashboard activity feed shows timestamps in UTC instead of the user's local timezone. Users in Tokyo see "Last updated: 2:00 AM" when the actual local time was 11:00 AM JST.

**Codebase context:**

```python
# src/api/dashboard_stats.py
from flask import Blueprint, jsonify, g
from src.db import get_connection

stats_bp = Blueprint("stats", __name__, url_prefix="/api/stats")


@stats_bp.route("/activity")
def get_activity_feed():
    """Get recent activity for the dashboard."""
    user_id = g.current_user["id"]
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT a.id, a.action, a.created_at, p.name "
            "FROM activity_log a "
            "JOIN projects p ON a.project_id = p.id "
            "WHERE a.user_id = %s "
            "ORDER BY a.created_at DESC LIMIT 20",
            (user_id,),
        )
        activities = []
        for row in cursor.fetchall():
            activities.append({
                "id": row[0],
                "action": row[1],
                "timestamp": row[2].isoformat(),
                "project": row[3],
            })
        return jsonify(activities)
    finally:
        conn.close()
```

```python
# src/models/user.py
from src.db import get_connection


def get_user_profile(user_id: int) -> dict | None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, email, timezone FROM users WHERE id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "email": row[2],
            "timezone": row[3],  # e.g., "Asia/Tokyo", "America/New_York"
        }
    finally:
        conn.close()
```
