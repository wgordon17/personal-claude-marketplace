---
# Fixture metadata (stripped by loader)
planted_issues:
  - missing_file_structure: "Task 2 references src/services/notification_service.py but it is not in the File Structure section"
  - missing_test_command: "Task 4 has no test command specified"
  - circular_dependency: "Task 3 depends on Task 5 and Task 5 depends on Task 3"
  - unresolved_assumption: "[ASSUMPTION: scope] is never resolved in the plan"
---

# Plan: Add Notification System

**Branch:** feat/notifications
**Status:** In Progress
**Created:** 2026-04-15

## Objective

Add real-time notification support for user mentions, task assignments, and deadline reminders.

[ASSUMPTION: scope] Whether to include email notifications or only in-app.

## File Structure

```
src/
  models/
    notification.py         # Notification ORM model
  api/
    notifications.py        # REST endpoints for notification CRUD
  workers/
    deadline_checker.py     # Background job for deadline reminders
tests/
  test_notifications_api.py # API endpoint tests
  test_deadline_checker.py  # Worker tests
```

## Tasks

### Task 1: Create Notification Model
- **Files:** `src/models/notification.py`
- **Description:** Define SQLAlchemy model with fields: id, user_id, type (mention/assignment/deadline), message, read_at, created_at. Add foreign key to users table. Create Alembic migration.
- **Test command:** `uv run pytest tests/test_notifications_api.py -k test_model`
- **Dependencies:** None

### Task 2: Build Notification Service Layer
- **Files:** `src/services/notification_service.py`
- **Description:** Create service class with methods: create_notification(), mark_as_read(), get_unread_count(), get_notifications_paginated(). Include batch mark-all-read support.
- **Test command:** `uv run pytest tests/test_notification_service.py`
- **Dependencies:** Task 1

### Task 3: REST API Endpoints
- **Files:** `src/api/notifications.py`
- **Description:** Implement GET /notifications (paginated), PATCH /notifications/:id/read, POST /notifications/read-all. Add rate limiting. Wire up WebSocket push from Task 5.
- **Test command:** `uv run pytest tests/test_notifications_api.py`
- **Dependencies:** Task 2, Task 5

### Task 4: Mention Detection Parser
- **Files:** `src/parsers/mention_parser.py`
- **Description:** Parse @username mentions from markdown text. Handle edge cases: escaped mentions, mentions in code blocks, non-existent users. Create notifications for valid mentions.
- **Dependencies:** Task 2

### Task 5: WebSocket Push Channel
- **Files:** `src/ws/notification_channel.py`
- **Description:** Set up Flask-SocketIO channel for real-time notification delivery. Authenticate via session token. Broadcast to specific user rooms. Integrate with REST endpoints from Task 3.
- **Test command:** `uv run pytest tests/test_notification_ws.py`
- **Dependencies:** Task 3

### Task 6: Deadline Reminder Worker
- **Files:** `src/workers/deadline_checker.py`
- **Description:** Background job (APScheduler) that checks for tasks due within 24 hours and creates reminder notifications. Deduplicate reminders to avoid spamming.
- **Test command:** `uv run pytest tests/test_deadline_checker.py`
- **Dependencies:** Task 1
