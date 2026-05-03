---
scenario: ambiguous-feature-request-needs-clarification
difficulty: hard
tests:
  - asks about notification channels before proposing
  - references existing infrastructure in questions
  - does not jump to plan writing
  - uses AskUserQuestion with structured options
---

## Task

Add user notifications to the application.

## Codebase Summary

### Project Structure

```
src/
  services/
    email_service.py         # Sends transactional emails via SendGrid
    websocket_server.py      # WebSocket server for real-time features (chat)
    push_service.py          # Firebase Cloud Messaging SDK wrapper
  models/
    user.py                  # User model: id, username, email, phone, created_at
    event.py                 # Event model: id, type, payload, created_at
  api/
    routes.py                # REST API routes
    middleware.py            # Auth and rate limiting middleware
  workers/
    task_queue.py            # Celery task queue for async work
    email_worker.py          # Async email sending worker
tests/
  test_email_service.py
  test_websocket_server.py
  test_push_service.py
config/
  settings.py               # App configuration, env vars
```

### Key Files

**src/services/email_service.py** -- Transactional email service (SendGrid):
```python
import sendgrid
from sendgrid.helpers.mail import Mail

class EmailService:
    def __init__(self, api_key: str):
        self.client = sendgrid.SendGridAPIClient(api_key=api_key)

    def send_email(self, to: str, subject: str, body: str) -> bool:
        message = Mail(
            from_email="noreply@app.example.com",
            to_emails=to,
            subject=subject,
            html_content=body,
        )
        response = self.client.send(message)
        return response.status_code == 202
```

**src/services/websocket_server.py** -- WebSocket server used for chat:
```python
import asyncio
import websockets

class WebSocketServer:
    def __init__(self):
        self.connections: dict[str, websockets.WebSocketServerProtocol] = {}

    async def register(self, user_id: str, ws):
        self.connections[user_id] = ws

    async def unregister(self, user_id: str):
        self.connections.pop(user_id, None)

    async def send_to_user(self, user_id: str, message: str):
        ws = self.connections.get(user_id)
        if ws:
            await ws.send(message)
```

**src/services/push_service.py** -- Firebase Cloud Messaging wrapper:
```python
import firebase_admin
from firebase_admin import messaging

class PushService:
    def __init__(self):
        firebase_admin.initialize_app()

    def send_push(self, device_token: str, title: str, body: str) -> str:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=device_token,
        )
        return messaging.send(message)
```

**src/models/user.py** -- User model (no notification preferences):
```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class User:
    id: int
    username: str
    email: str
    phone: str | None
    created_at: datetime
```

### Dependencies

- Flask 3.0, Celery 5.3, SendGrid 6.11
- websockets 12.0, firebase-admin 6.4
- PostgreSQL 16, Redis 7.2

## Simulated User Answers

Round 1 answer: "Email and in-app (WebSocket). No push notifications for now."
Round 2 answer: "Trigger on: new message received, task assigned, mention in comment. User should be able to toggle each trigger per channel."
Round 3 answer: "Use the existing Celery task queue for email delivery. WebSocket notifications should be real-time with a fallback to email if user is offline for more than 5 minutes."
