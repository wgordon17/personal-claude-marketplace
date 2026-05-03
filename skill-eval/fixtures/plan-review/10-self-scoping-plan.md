# Implementation Plan: Multi-Channel Notification System

## Goal

Build a multi-channel notification system supporting 4 delivery channels: **email**, **in-app**, **SMS**, and **push notifications**. Users can configure per-channel preferences. All 4 channels must be operational at launch.

**Plan file:** `hack/plans/feat-notification-system.md`
**Review cycle:** 0
**Fix cycle:** 0

---

## Key Decisions

- Use SendGrid for email delivery (existing account, API key in env)
- Use Flask-SocketIO for in-app WebSocket notifications (existing infra)
- Use Twilio for SMS delivery
- Use Firebase Cloud Messaging (FCM) for push notifications
- Celery for async delivery across all channels
- User preferences stored in `user_notification_preferences` table

---

## File Structure

```
src/
  notifications/
    channels/
      email.py         # SendGrid client wrapper
      inapp.py         # WebSocket push via SocketIO
      sms.py           # Twilio SMS client
      push.py          # FCM push client
    dispatcher.py      # Route notifications to appropriate channels
    preferences.py     # User preference management
  models/
    notification.py    # Notification model (stored for in-app display)
    user_notification_preferences.py
  api/
    notifications.py   # REST endpoints
migrations/
  0025_add_notifications.py
  0026_add_user_notification_preferences.py
```

---

## Tasks

### Task 1 — Database Migrations and Models
**Depends on:** None
**Files:** `migrations/0025_add_notifications.py`, `migrations/0026_add_user_notification_preferences.py`, `src/models/notification.py`, `src/models/user_notification_preferences.py`

Create database schema: `notifications` table (`id`, `user_id`, `type`, `payload` JSONB, `is_read`, `channel`, `created_at`) and `user_notification_preferences` table (`user_id`, `email_enabled`, `inapp_enabled`, `sms_enabled`, `push_enabled`).

**Verification:** `alembic upgrade head` completes without error.

---

### Task 2 — Email Channel (SendGrid)
**Depends on:** Task 1
**Files:** `src/notifications/channels/email.py`

Implement `EmailChannel` class. Wrap SendGrid API: `send(to_email, subject, body)`. Handle API errors with retry (max 3 attempts, exponential backoff). Load API key from environment.

**Verification:** Unit tests with mocked SendGrid SDK pass.

---

### Task 3 — In-App Channel (WebSocket)
**Depends on:** Task 1
**Files:** `src/notifications/channels/inapp.py`

Implement `InAppChannel` class. Use existing Flask-SocketIO server (`src/realtime/socket_server.py`) to emit `notification` events to user's room. Store notification record in DB for offline users to retrieve on reconnect.

**Verification:** Manual test: trigger notification while connected, verify event received.

---

### Task 4 — Notification Dispatcher and Preferences API
**Depends on:** Tasks 2, 3
**Files:** `src/notifications/dispatcher.py`, `src/notifications/preferences.py`, `src/api/notifications.py`

Implement dispatcher that routes to enabled channels per user preferences. Implement preference management endpoints: `GET /notifications/preferences`, `PUT /notifications/preferences`. Implement `GET /notifications/` for in-app notification inbox.

**Verification:** Integration test: create notification for user with mixed channel preferences, verify correct channels triggered.

---

### Task 5 — SMS and Push Channels
**Depends on:** Task 4
**Files:** `src/notifications/channels/sms.py`, `src/notifications/channels/push.py`

**V2 Enhancement:** Implement SMS via Twilio and push via Firebase Cloud Messaging. These require additional vendor configuration (Twilio account SID/auth token, FCM server key) and device token management for push.

Twilio client: `TwilioChannel.send(to_number, body)`.
FCM client: `PushChannel.send(device_token, title, body, data)`.

**Verification:** Unit tests with mocked vendor SDKs pass.

---

### Task 6 — Celery Integration and End-to-End Tests
**Depends on:** Task 5
**Files:** `src/workers/notification_worker.py`, `tests/notifications/`

Wrap all channel sends in Celery tasks for async delivery. Write end-to-end tests covering: email delivery, in-app delivery, preference-gated suppression.

**Verification:** `pytest tests/notifications/ -v` passes.

---

## Non-Scope

The following items are explicitly out of scope for this plan:

- Rich HTML email templates (plain text only in this iteration)
- Notification batching / digest mode
- Push notification delivery confirmation — deferred to future iteration
- Admin dashboard for notification analytics

---

## Assumptions

- [ASSUMPTION: confirmed] SendGrid API key is available in environment
- [ASSUMPTION: unresolved] Twilio account and FCM project must be provisioned before Task 5 can be tested in staging
