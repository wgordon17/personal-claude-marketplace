---
planted_issues: []
difficulty: hard
type: negative
notes: "Well-structured plan with no real issues. Tests false-positive resistance."
---

# Plan: Add Webhook Support for Event Notifications

**Branch:** feat/webhooks
**Status:** Draft
**Created:** 2026-04-12
**Goal:** Allow users to register webhook URLs that receive HTTP POST callbacks when key events occur (project created, task completed, member invited).

**Cynefin Domain:** Complicated

**Iterations:**
- review-cycle: 0
- fix-cycle: 0

## File Structure

```
src/
  webhooks/
    models.py               # WebhookSubscription ORM model
    dispatcher.py           # Async event dispatch to registered URLs
    payload_builder.py      # Construct typed JSON payloads per event
    signature.py            # HMAC-SHA256 request signing
  api/
    webhook_endpoints.py    # CRUD REST endpoints for webhook management
  workers/
    webhook_delivery.py     # Background retry worker for failed deliveries
tests/
  test_webhook_models.py
  test_dispatcher.py
  test_payload_builder.py
  test_signature.py
  test_webhook_endpoints.py
  test_webhook_delivery.py
```

## Key Decisions

- Use HMAC-SHA256 signing so consumers can verify payload authenticity. Each subscription gets a unique signing secret generated at registration time.
- Async dispatch via background worker (Celery task) to avoid blocking the request that triggers the event.
- Exponential backoff retry (3 attempts: 1min, 5min, 30min) for failed deliveries. After 3 failures, mark subscription as unhealthy and stop retrying until the user re-enables it.
- Payload format follows CloudEvents v1.0 spec for interoperability.

## Tasks

### Task 1: Webhook Subscription Model
- **Files:** `src/webhooks/models.py`
- **Description:** Define SQLAlchemy model WebhookSubscription with fields: id (UUID), user_id (FK), target_url (validated URL), event_types (JSON array of subscribed event names), signing_secret (generated, never exposed after creation), is_active (bool), failure_count (int), created_at, updated_at. Add unique constraint on (user_id, target_url). Create Alembic migration.
- **Test command:** `uv run pytest tests/test_webhook_models.py`
- **Dependencies:** None

### Task 2: Payload Builder
- **Files:** `src/webhooks/payload_builder.py`
- **Description:** Build typed JSON payloads for each event type: project.created, task.completed, member.invited. Each payload includes: event type, timestamp, actor (user who triggered), resource data (project/task/member details). Conform to CloudEvents v1.0 envelope (specversion, type, source, id, time, data). Validate payload against a JSON schema before dispatch.
- **Test command:** `uv run pytest tests/test_payload_builder.py`
- **Dependencies:** None

### Task 3: HMAC Signature Module
- **Files:** `src/webhooks/signature.py`
- **Description:** Implement sign_payload(secret, body_bytes) returning hex-encoded HMAC-SHA256 digest. Implement verify_signature(secret, body_bytes, provided_signature) with constant-time comparison via hmac.compare_digest(). Include a generate_secret() helper that produces a 32-byte URL-safe random string.
- **Test command:** `uv run pytest tests/test_signature.py`
- **Dependencies:** None

### Task 4: Webhook REST API
- **Files:** `src/api/webhook_endpoints.py`
- **Description:** CRUD endpoints: POST /webhooks (register, returns subscription with signing_secret shown once), GET /webhooks (list user subscriptions, secrets redacted), DELETE /webhooks/:id (soft-delete, sets is_active=false), PATCH /webhooks/:id/reactivate (reset failure_count, set is_active=true). Input validation: target_url must be HTTPS, event_types must be from allowed enum. Rate limit: 10 subscriptions per user.
- **Test command:** `uv run pytest tests/test_webhook_endpoints.py`
- **Dependencies:** Task 1, Task 3

### Task 5: Event Dispatcher and Delivery Worker
- **Files:** `src/webhooks/dispatcher.py`, `src/workers/webhook_delivery.py`
- **Description:** Dispatcher: on event trigger, query active subscriptions matching the event type, enqueue a Celery task per subscription. Delivery worker: POST the signed payload to target_url with X-Webhook-Signature header. On HTTP 2xx, log success. On failure (non-2xx or timeout after 10s), increment failure_count. After 3 consecutive failures, set is_active=false. Use exponential backoff between retries (1min, 5min, 30min).
- **Test command:** `uv run pytest tests/test_dispatcher.py tests/test_webhook_delivery.py`
- **Dependencies:** Task 1, Task 2, Task 3
