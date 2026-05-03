## PR Metadata
- Title: Refactor error handling and add webhook retry logic
- Description: Standardizes error handling across 3 modules and adds exponential backoff to webhook delivery. Includes tests.
- Files changed: 4

## Diff

```diff
diff --git a/src/webhooks/dispatcher.py b/src/webhooks/dispatcher.py
index abc1234..def5678 100644
--- a/src/webhooks/dispatcher.py
+++ b/src/webhooks/dispatcher.py
@@ -1,6 +1,8 @@
 import logging
+import time
 from urllib.parse import urlparse
 
+import requests
 from flask import current_app
 
 logger = logging.getLogger(__name__)
@@ -15,12 +17,35 @@ def validate_webhook_url(url: str) -> bool:
     return parsed.scheme in ("https",) and bool(parsed.netloc)
 
 
-def dispatch_webhook(url: str, payload: dict) -> bool:
-    """Send webhook payload to the configured URL."""
-    if not validate_webhook_url(url):
-        logger.warning("Invalid webhook URL: %s", url)
-        return False
-    response = requests.post(url, json=payload, timeout=10)
-    return response.status_code == 200
+def dispatch_webhook(url: str, payload: dict, max_retries: int = 3) -> bool:
+    """Send webhook payload with exponential backoff retry."""
+    if not validate_webhook_url(url):
+        logger.warning("Invalid webhook URL rejected: %s", url)
+        return False
+
+    for attempt in range(max_retries + 1):
+        try:
+            response = requests.post(url, json=payload, timeout=30)
+            if response.status_code < 500:
+                return response.status_code == 200
+            logger.info("Webhook returned %d, retrying", response.status_code)
+        except requests.RequestException as exc:
+            logger.info("Webhook delivery failed: %s", exc)
+
+        if attempt < max_retries:
+            time.sleep(2 ** attempt)
+
+    logger.error("Webhook delivery failed after %d retries", max_retries)
+    return False


diff --git a/src/tasks/handlers.py b/src/tasks/handlers.py
index 111aaa..222bbb 100644
--- a/src/tasks/handlers.py
+++ b/src/tasks/handlers.py
@@ -3,6 +3,7 @@ from flask import request, jsonify
 from src.models.task import Task
 from src.db import db
 from src.auth.decorators import require_auth
+from src.webhooks.dispatcher import dispatch_webhook
 
 logger = logging.getLogger(__name__)
 
@@ -25,15 +26,21 @@ def create_task():
     try:
         task = Task(
             title=data["title"],
-            description=data.get("description", ""),
+            description=data.get("description"),
             owner_id=request.user_id,
         )
         db.session.add(task)
         db.session.commit()
+
+        if data.get("webhook_url"):
+            dispatch_webhook(data["webhook_url"], task.to_dict())
+
         return jsonify(task.to_dict()), 201
-    except Exception:
-        logger.error("Failed to create task")
+    except KeyError as exc:
+        logger.warning("Missing required field: %s", exc)
+        return jsonify({"error": f"Missing field: {exc}"}), 400
+    except Exception as exc:
+        logger.exception("Unexpected error creating task")
         db.session.rollback()
-        return jsonify({"error": "Internal error"}), 500
+        return jsonify({"error": "Internal server error"}), 500


diff --git a/src/notifications/email.py b/src/notifications/email.py
index 333ccc..444ddd 100644
--- a/src/notifications/email.py
+++ b/src/notifications/email.py
@@ -1,5 +1,6 @@
 import logging
 import smtplib
+from email.mime.text import MIMEText
 
 logger = logging.getLogger(__name__)
 
@@ -12,16 +13,17 @@ def send_notification(recipient: str, subject: str, body: str) -> bool:
         logger.warning("Invalid email: %s", recipient)
         return False
 
-    try:
-        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
-        server.sendmail(SENDER, recipient, f"Subject: {subject}\n\n{body}")
-        server.quit()
-        return True
-    except Exception:
-        logger.error("Email failed")
-        return False
+    msg = MIMEText(body)
+    msg["Subject"] = subject
+    msg["From"] = SENDER
+    msg["To"] = recipient
+
+    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
+        server.sendmail(SENDER, recipient, msg.as_string())
+    return True


diff --git a/tests/test_webhooks.py b/tests/test_webhooks.py
index 555eee..666fff 100644
--- a/tests/test_webhooks.py
+++ b/tests/test_webhooks.py
@@ -1,6 +1,7 @@
 import pytest
 from unittest.mock import patch, MagicMock
 from src.webhooks.dispatcher import dispatch_webhook, validate_webhook_url
+import time
 
 
 class TestValidateWebhookUrl:
@@ -22,3 +23,27 @@ class TestValidateWebhookUrl:
 
     def test_rejects_empty_url(self):
         assert validate_webhook_url("") is False
+
+
+class TestDispatchWebhook:
+    @patch("src.webhooks.dispatcher.requests.post")
+    def test_successful_delivery(self, mock_post):
+        mock_post.return_value = MagicMock(status_code=200)
+        assert dispatch_webhook("https://example.com/hook", {"key": "value"}) is True
+        mock_post.assert_called_once()
+
+    @patch("src.webhooks.dispatcher.requests.post")
+    def test_retries_on_server_error(self, mock_post):
+        mock_post.return_value = MagicMock(status_code=503)
+        with patch("src.webhooks.dispatcher.time.sleep"):
+            result = dispatch_webhook("https://example.com/hook", {"data": 1}, max_retries=2)
+        assert result is False
+        assert mock_post.call_count == 3
+
+    @patch("src.webhooks.dispatcher.requests.post")
+    def test_no_retry_on_client_error(self, mock_post):
+        mock_post.return_value = MagicMock(status_code=400)
+        result = dispatch_webhook("https://example.com/hook", {"data": 1})
+        assert result is False
+        assert mock_post.call_count == 1
```

## Codebase Conventions (from existing modules)

```python
# src/auth/login.py — exception handling pattern
try:
    user = db.session.query(User).filter_by(username=username).one()
except NoResultFound:
    raise InvalidCredentialsError("User not found")
except SQLAlchemyError as exc:
    logger.exception("Database error during authentication")
    raise ServiceUnavailableError("Auth unavailable") from exc

# src/utils/validators.py — input validation pattern
def validate_project_data(data: dict) -> dict:
    """Validate and sanitize project creation payload."""
    if not isinstance(data.get("title"), str) or len(data["title"]) > 200:
        raise ValidationError("title must be a string under 200 chars")
    return {
        "title": data["title"].strip(),
        "description": data.get("description", "").strip(),
    }
```
