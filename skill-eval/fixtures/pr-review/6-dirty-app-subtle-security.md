---
planted_issues:
  - ssrf: "SSRF with fake URL validation in export.py"
  - second_order_injection: "Second-order SQL injection in search.py"
  - pii_in_logs: "PII logging at DEBUG level in logging.py"
clean_distractors:
  - "Task model structure is standard and correct"
---

## PR: Add task export and search features

**Description:** Adds webhook-based task export and full-text search to the task management API. Export supports JSON and CSV formats with delivery to user-configured webhook URLs. Search saves recent terms for replay. Logging updated to capture request details for observability.

### src/tasks/export.py
{codebase:dirty-flask-app/src/tasks/export.py}

### src/tasks/search.py
{codebase:dirty-flask-app/src/tasks/search.py}

### src/middleware/logging.py
{codebase:dirty-flask-app/src/middleware/logging.py}

### src/models/task.py
{codebase:dirty-flask-app/src/models/task.py}
