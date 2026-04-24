# Implementation Plan: In-App Notification System

## Goal
Build a real-time in-app notification system for the task management platform. Users receive notifications for: task assignments, comment mentions, project membership changes, and deadline reminders. Notifications are delivered via WebSocket for active sessions and stored for offline users.

## Key Decisions
- Use Flask-SocketIO for WebSocket delivery
- Notifications stored in PostgreSQL (new `notifications` table)
- Redis pub/sub as the message broker between app workers and WebSocket server
- Deadline reminders triggered by Celery Beat scheduled task
- Notification preferences configurable per user per event type

## File Structure
```
src/
  notifications/
    models.py            # Notification SQLAlchemy model
    service.py           # Core notification creation and dispatch
    dispatcher.py        # WebSocket delivery via SocketIO
    preferences.py       # User notification preference management
    reminders.py         # Celery Beat task for deadline reminders
  api/
    notifications.py     # REST endpoints: list, mark-read, preferences
  config/
    notifications.yaml   # Notification type definitions and templates
tests/
  notifications/
    test_service.py
    test_dispatcher.py
    test_reminders.py
    test_preferences.py
```

## Tasks

### Task 1 — Notification Model and Migration
**Depends on:** None
**Parallelizable with:** Task 2
**Files:** `src/notifications/models.py`, `migrations/0022_add_notifications.py`

Create the `Notification` SQLAlchemy model: `id`, `user_id`, `type` (enum), `payload` (JSONB), `is_read`, `created_at`, `read_at`. Add index on `(user_id, is_read, created_at)` for efficient unread-first queries. Write and apply the Alembic migration.

**Verification:** `alembic upgrade head` completes; model imports cleanly.

---

### Task 2 — Notification Type Configuration
**Depends on:** None
**Parallelizable with:** Task 1
**Files:** `src/config/notifications.yaml`, `src/notifications/preferences.py`

Define notification type registry in `src/config/notifications.yaml`. Each type specifies: `id`, `display_name`, `default_enabled`, `template`. Types: `task_assigned`, `comment_mention`, `project_member_added`, `deadline_reminder`. Implement `get_user_preferences(user_id)` and `update_user_preferences(user_id, prefs)` in `preferences.py`, reading defaults from the YAML config.

**Note:** `preferences.py` reads notification type definitions from `src/config/notifications.yaml` at import time. Any later task that adds new notification types to the YAML must be careful not to introduce keys that `preferences.py` does not yet know how to handle.

**Verification:** Unit tests for `get_user_preferences` pass with mocked DB.

---

### Task 3 — Core Notification Service
**Depends on:** Tasks 1, 2
**Parallelizable with:** None
**Files:** `src/notifications/service.py`

Implement `create_notification(user_id, type, payload)` which: (1) checks user preferences to determine if the notification type is enabled for this user, (2) writes a `Notification` record to the database, (3) publishes a Redis pub/sub message to channel `notifications:{user_id}` for real-time delivery. Implement `list_notifications(user_id, unread_only, page, per_page)` and `mark_read(user_id, notification_ids)`.

**Verification:** Unit tests with mocked Redis and DB pass.

---

### Task 4 — WebSocket Dispatcher
**Depends on:** Task 3
**Parallelizable with:** Task 5
**Files:** `src/notifications/dispatcher.py`

Implement the WebSocket dispatcher. Subscribe to Redis channel `notifications:{user_id}` for each connected user. On message received, emit a `notification` SocketIO event to the user's room. Use `get_notification_template(type)` from `src/notifications/preferences.py` to format the notification payload for the client.

**Note:** `get_notification_template` is defined in `preferences.py` as a helper that looks up template strings from the YAML config. Task 4 calls this function but only lists Task 3 as a dependency. `get_notification_template` is implemented in Task 6 as part of the preferences API extension — Task 4 will fail at runtime if executed before Task 6 provides this function.

**Verification:** Manual test: trigger a notification while connected via WebSocket, confirm event received in browser console.

---

### Task 5 — REST Notification Endpoints
**Depends on:** Task 3
**Parallelizable with:** Task 4
**Files:** `src/api/notifications.py`

Implement REST endpoints: `GET /notifications/` (paginated list, supports `?unread_only=true`), `POST /notifications/mark-read` (accepts list of notification IDs), `GET /notifications/preferences`, `PUT /notifications/preferences`. Wire up to `src/api/app.py` blueprint registration.

**Verification:** `pytest tests/notifications/test_api.py -v` passes.

---

### Task 6 — Notification Preferences API Extension
**Depends on:** Task 2
**Parallelizable with:** Task 5
**Files:** `src/notifications/preferences.py`

Extend `preferences.py` with `get_notification_template(type)` — returns the display template string for a notification type from the YAML config. Add `list_notification_types()` for the preferences UI. These helpers are used by the dispatcher (Task 4) and the preferences endpoints (Task 5).

**Verification:** Unit tests for `get_notification_template` with all 4 notification types pass.

---

### Task 7 — Deadline Reminder Celery Task
**Depends on:** Task 3
**Parallelizable with:** Task 2
**Files:** `src/notifications/reminders.py`, `src/config/notifications.yaml`

Implement a Celery Beat periodic task `send_deadline_reminders()` that runs every hour. Queries tasks with `due_date` within the next 24 hours and `status != done`. For each, calls `create_notification(owner_id, "deadline_reminder", {...})`. Register the task in `celery_config.py` beat schedule.

**Note:** This task modifies `src/config/notifications.yaml` to add the `deadline_reminder` template entry. Task 2 also writes to `src/config/notifications.yaml` as part of the initial notification type registry setup. If Tasks 2 and 7 are executed concurrently (both are listed as parallelizable), they will produce conflicting writes to the same config file.

**Verification:** Manual test: set a task due date to tomorrow, run the beat task, confirm notification created.

---

### Task 8 — Integration Tests and End-to-End Verification
**Depends on:** Tasks 4, 5, 6, 7
**Parallelizable with:** None
**Files:** `tests/notifications/test_service.py`, `tests/notifications/test_dispatcher.py`, `tests/notifications/test_reminders.py`, `tests/notifications/test_preferences.py`

Write integration tests covering: full notification creation through WebSocket delivery (mocked SocketIO), preference-gated notification suppression, deadline reminder scheduling, and mark-read idempotency. Run full test suite with `pytest tests/notifications/ -v --tb=short`.

**Verification:** All tests pass; no regressions in existing test suite.
