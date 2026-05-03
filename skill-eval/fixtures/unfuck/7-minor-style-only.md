# Codebase: Task Notification Helpers

A small utility library for formatting and sending task-related notifications. 4 files, written by a single engineer, functional and correct.

---

## File: src/notifications/formatter.py

```python
import re
from datetime import datetime


def format_task_title(title):
    """
    This function takes a task title string and formats it for display.
    It removes leading and trailing whitespace from the title string.
    It also collapses any internal multiple spaces into a single space.
    Returns the formatted title string ready for display in the UI.
    """
    title = title.strip()
    title = re.sub(r' +', ' ', title)
    return title


def format_due_date(due_date, include_time=False):
    """
    Formats a datetime object into a human-readable string.
    When include_time is True, the output includes the hour and minute.
    When include_time is False, only the date portion is returned.
    The date format used is Month Day, Year (e.g. January 5, 2026).
    """
    if include_time:
        return due_date.strftime('%B %d, %Y at %I:%M %p')
    return due_date.strftime('%B %d, %Y')


def format_priority_label(priority):
    """Returns a display string for a task priority level."""
    labels = {
        'low': 'Low Priority',
        'medium': 'Medium Priority',
        'high': 'High Priority',
        'critical': 'CRITICAL',
    }
    return labels.get(priority, 'Unknown Priority')


def truncate_description(description, max_length=120):
    """Truncate a task description to max_length characters for preview."""
    if len(description) <= max_length:
        return description
    return description[:max_length].rstrip() + '...'
```

---

## File: src/notifications/sender.py

```python
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def send_email_notification(recipient, subject, body, smtp_host, smtp_port):
    """
    This function sends an email notification to the specified recipient.
    It constructs a MIME multipart message with both plain text and HTML parts.
    The function connects to the SMTP server and sends the message.
    Any exceptions during sending are logged and re-raised to the caller.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "notifications@taskapp.internal"
    msg["To"] = recipient

    text_part = MIMEText(body, "plain")
    html_part = MIMEText(f"<p>{body}</p>", "html")
    msg.attach(text_part)
    msg.attach(html_part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.sendmail("notifications@taskapp.internal", recipient, msg.as_string())
        logger.info("Notification sent to %s", recipient)
    except smtplib.SMTPException as exc:
        logger.error("Failed to send notification to %s: %s", recipient, exc)
        raise


def build_subject(task_title, event_type):
    """Build an email subject line for a task event."""
    prefixes = {
        "assigned": "Task assigned to you",
        "due_soon": "Task due soon",
        "overdue": "Task overdue",
        "completed": "Task completed",
    }
    prefix = prefixes.get(event_type, "Task update")
    return f"{prefix}: {task_title}"
```

---

## File: src/notifications/templates.py

```python
ASSIGNMENT_TEMPLATE = '''Hello,

You have been assigned the following task:

  Title: {title}
  Priority: {priority}
  Due date: {due_date}
  Description: {description}

Please log in to view the full task details.

Best regards,
The Task Management Team
'''

DUE_SOON_TEMPLATE = '''Hello,

This is a reminder that the following task is due soon:

  Title: {title}
  Due date: {due_date}
  Priority: {priority}

Please take action before the due date.

Best regards,
The Task Management Team
'''

OVERDUE_TEMPLATE = '''Hello,

The following task is now overdue:

  Title: {title}
  Due date: {due_date}
  Priority: {priority}

Please update the task status or contact your project manager.

Best regards,
The Task Management Team
'''


def render_template(template_name, **kwargs):
    '''Render a notification template with the given context variables.'''
    templates = {
        'assignment': ASSIGNMENT_TEMPLATE,
        'due_soon': DUE_SOON_TEMPLATE,
        'overdue': OVERDUE_TEMPLATE,
    }
    template = templates.get(template_name)
    if template is None:
        raise ValueError(f"Unknown template: {template_name}")
    return template.format(**kwargs)
```

---

## File: src/notifications/scheduler.py

```python
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def should_send_due_soon_notification(task, hours_before=24):
    """
    Determines whether a due-soon notification should be sent for a task.
    Returns True if the task is due within the specified number of hours
    and the task status is not 'done' or 'archived'.
    Returns False otherwise.
    """
    if task.status in ("done", "archived"):
        return False
    if task.due_date is None:
        return False
    now = datetime.utcnow()
    threshold = timedelta(hours=hours_before)
    time_until_due = task.due_date - now
    return timedelta(0) < time_until_due <= threshold


def should_send_overdue_notification(task):
    """Returns True if the task is past its due date and not yet complete."""
    if task.status in ("done", "archived"):
        return False
    if task.due_date is None:
        return False
    return datetime.utcnow() > task.due_date


def get_pending_notifications(tasks, hours_before=24):
    """
    Filter a list of tasks to those requiring notifications.
    Returns a dict with 'due_soon' and 'overdue' keys,
    each containing a list of task objects.
    """
    pending_notification_tasks = {
        "due_soon": [],
        "overdue": [],
    }
    for task in tasks:
        if should_send_overdue_notification(task):
            pending_notification_tasks["overdue"].append(task)
        elif should_send_due_soon_notification(task, hours_before):
            pending_notification_tasks["due_soon"].append(task)
    return pending_notification_tasks
```

---

## What Is NOT Present

- No security vulnerabilities (no raw SQL, no unsanitized user input, no hardcoded secrets)
- No dead code (all functions are used by callers in the application)
- No AI slop patterns (no unnecessary wrapper classes, no catch-rethrow, no process_data/handle_data duplication)
- No architectural concerns (the module structure is appropriate for its scope)
- No unused imports (all imports are used)
