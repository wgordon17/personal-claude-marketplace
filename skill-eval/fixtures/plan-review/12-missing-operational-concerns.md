# Implementation Plan: Webhook Notification System

**Goal:** Send webhook notifications when tasks are created, updated, or deleted. Support user-configured webhook URLs with JSON payloads.

**Workflow:** incremental

---

## File Structure

| File | Purpose |
|------|---------|
| `src/webhooks/models.py` | WebhookConfig SQLAlchemy model |
| `src/webhooks/dispatcher.py` | HTTP dispatch to configured URLs |
| `src/webhooks/serializers.py` | Task-to-JSON payload serialization |
| `src/api/webhook_routes.py` | CRUD endpoints for webhook configuration |
| `src/tasks/signals.py` | Post-save/delete signals triggering dispatch |
| `tests/test_webhooks.py` | Unit tests |
| `db/migrations/0044_webhooks.py` | Add webhook_configs table |

---

## Task 1: Webhook Config Model and Migration
**Dependencies:** None
**Files:** `src/webhooks/models.py`, `db/migrations/0044_webhooks.py`

Create `WebhookConfig` model with fields: `id`, `user_id` (FK to users), `url` (string, max 2048), `events` (JSON list of event type strings like `["task.created", "task.updated"]`), `created_at`, `is_active` (boolean, default True).

Add database migration to create `webhook_configs` table.

---

## Task 2: Webhook Configuration CRUD API
**Dependencies:** Task 1
**Files:** `src/api/webhook_routes.py`

Implement REST endpoints:
- `POST /webhooks` — create a new webhook configuration (validate URL format, validate event types against allowed list)
- `GET /webhooks` — list current user's webhook configs
- `PUT /webhooks/:id` — update URL or events (ownership check)
- `DELETE /webhooks/:id` — deactivate webhook config (ownership check)

---

## Task 3: Payload Serializer
**Dependencies:** None
**Files:** `src/webhooks/serializers.py`

Convert Task model instances to JSON webhook payloads. Include: `task_id`, `title`, `status`, `assignee_id`, `event_type`, `timestamp`. Serialize datetime fields as ISO 8601.

---

## Task 4: Webhook Dispatcher
**Dependencies:** Task 3
**Files:** `src/webhooks/dispatcher.py`

Send HTTP POST requests to configured webhook URLs with the serialized payload. Set `Content-Type: application/json`. Include a `X-Webhook-Signature` header with HMAC-SHA256 signature for payload verification.

```python
def dispatch_webhook(config: WebhookConfig, payload: dict) -> bool:
    """Send webhook payload to the configured URL."""
    response = requests.post(
        config.url,
        json=payload,
        headers={"X-Webhook-Signature": sign_payload(payload, config.secret)},
    )
    return response.status_code == 200
```

---

## Task 5: Signal Integration
**Dependencies:** Task 1, Task 4
**Files:** `src/tasks/signals.py`

Wire SQLAlchemy `after_insert`, `after_update`, and `after_delete` events on the Task model. For each event, query matching `WebhookConfig` records and call the dispatcher.

```python
@event.listens_for(Task, "after_insert")
def task_created(mapper, connection, target):
    configs = WebhookConfig.query.filter(
        WebhookConfig.user_id == target.owner_id,
        WebhookConfig.events.contains(["task.created"]),
        WebhookConfig.is_active == True,
    ).all()
    for config in configs:
        dispatch_webhook(config, serialize_task_event(target, "task.created"))
```

---

## Task 6: Tests
**Dependencies:** Task 1, 2, 3, 4, 5
**Files:** `tests/test_webhooks.py`

Unit tests covering: model creation, API CRUD operations, payload serialization (all fields present, correct types), dispatcher (mock HTTP calls), signal integration (events fire correctly).

---

## Non-scope

- Webhook delivery dashboard / UI
- Webhook event replay
- Multi-tenant webhook isolation (single-tenant application)
