## Plan A: Email Notification System

**Goal:** Add email notifications for task assignments and deadline reminders.

**Architecture Summary:** Adds an email service that sends templated notifications via SendGrid API. Templates stored in `src/notifications/templates/`. Notification triggers added to task assignment and deadline check workflows.

**Files:**
| File | Purpose |
|------|---------|
| `src/notifications/email_service.py` | SendGrid API integration |
| `src/notifications/templates/assignment.html` | Task assignment email template |
| `src/notifications/templates/reminder.html` | Deadline reminder template |
| `src/tasks/assignment.py` | Task assignment workflow (modified) |
| `src/tasks/deadline_checker.py` | Deadline check cron job (modified) |
| `tests/test_email_service.py` | Email service unit tests |

**Tasks:**
1. Create `email_service.py` with SendGrid integration (Dependencies: none)
2. Create HTML email templates (Dependencies: none)
3. Add notification trigger to `assignment.py` (Dependencies: Task 1)
4. Add notification trigger to `deadline_checker.py` (Dependencies: Task 1)
5. Write unit tests with mocked SendGrid client (Dependencies: Task 1, 3, 4)

---

## Plan B: Export to CSV Feature

**Goal:** Allow users to export their task lists as CSV files.

**Architecture Summary:** Adds CSV export endpoints to the task API. Uses Python's built-in `csv` module. Exports include task title, status, assignee, due date, and custom fields. Streaming response for large datasets.

**Files:**
| File | Purpose |
|------|---------|
| `src/tasks/export.py` | CSV generation and streaming |
| `src/api/task_routes.py` | New GET /tasks/export endpoint (modified) |
| `tests/test_export.py` | Export endpoint tests |

**Tasks:**
1. Create `export.py` with CSV generation logic (Dependencies: none)
2. Add `/tasks/export` endpoint to `task_routes.py` (Dependencies: Task 1)
3. Write endpoint tests with fixture data (Dependencies: Task 1, 2)

---

## Plan C: Audit Log System

**Goal:** Record all user actions (create, update, delete) for compliance reporting.

**Architecture Summary:** Adds middleware that captures request metadata and writes audit entries to a dedicated `audit_log` table. Includes an admin endpoint to query audit records. Uses the `MessageBroker` service for async log writes to avoid blocking request handlers.

**Files:**
| File | Purpose |
|------|---------|
| `src/audit/middleware.py` | Request capture middleware |
| `src/audit/models.py` | AuditEntry SQLAlchemy model |
| `src/audit/writer.py` | Async audit log writer via MessageBroker |
| `src/api/admin_routes.py` | Admin audit query endpoint (modified) |
| `db/migrations/0045_audit_log.py` | Audit log table migration |
| `tests/test_audit.py` | Audit system tests |

**Tasks:**
1. Create `AuditEntry` model and migration (Dependencies: none)
2. Create audit middleware for request capture (Dependencies: Task 1)
3. Create async writer using `MessageBroker` service (Dependencies: Task 1)
4. Add admin query endpoint (Dependencies: Task 1, 2)
5. Write tests (Dependencies: Task 1, 2, 3, 4)

---

## Project Context

This is a Python/Flask web application. Plans A, B, and C touch completely different directories (`src/notifications/`, `src/tasks/export.py`, `src/audit/`). There is zero file overlap between the three plans.

**Note on Plan C:** The `MessageBroker` service referenced in Plan C Task 3 does not currently exist in the codebase. Plan C assumes it will be available but no plan creates it. The roadmap should identify this phantom dependency but should NOT use it to create artificial dependencies between the three plans — they remain independently executable. The missing `MessageBroker` is a Plan C internal feasibility issue, not a cross-plan dependency.
