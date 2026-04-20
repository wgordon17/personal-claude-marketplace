---
planted_issues:
  - type: missing_input_validation
    file: src/api/handlers/upload.py
    line: 15
    description: File size not validated before processing
  - type: swallowed_error
    file: src/services/notification_service.py
    line: 28
    description: Exception caught and silently ignored
  - type: unconditional_assert
    file: tests/test_upload.py
    line: 22
    description: Test asserts True unconditionally, never fails
  - type: unused_import
    file: src/services/notification_service.py
    line: 4
    description: Stale import from removed module
expected_findings: 4
---

diff --git a/src/api/handlers/upload.py b/src/api/handlers/upload.py
index 0000000..a1c9e24
--- /dev/null
+++ b/src/api/handlers/upload.py
@@ -0,0 +1,38 @@
+import uuid
+from pathlib import Path
+
+from fastapi import APIRouter, UploadFile, Depends
+from sqlalchemy.orm import Session
+
+from src.api.deps import get_db
+from src.models.attachment import Attachment
+from src.services.notification_service import NotificationService
+
+router = APIRouter(prefix="/uploads", tags=["uploads"])
+
+UPLOAD_DIR = Path("/var/data/uploads")
+
+
+@router.post("/")
+async def upload_file(
+    file: UploadFile,
+    db: Session = Depends(get_db),
+) -> dict:
+    file_id = uuid.uuid4().hex
+    dest = UPLOAD_DIR / f"{file_id}_{file.filename}"
+
+    content = await file.read()
+    dest.write_bytes(content)
+
+    attachment = Attachment(
+        file_id=file_id,
+        filename=file.filename,
+        size_bytes=len(content),
+        content_type=file.content_type,
+    )
+    db.add(attachment)
+    db.commit()
+    db.refresh(attachment)
+
+    notifier = NotificationService()
+    notifier.send_upload_event(file_id=file_id, filename=file.filename)
+
+    return {"file_id": file_id, "size": len(content)}
+
diff --git a/src/services/notification_service.py b/src/services/notification_service.py
index 8b13789..c3d7e11 100644
--- a/src/services/notification_service.py
+++ b/src/services/notification_service.py
@@ -1,5 +1,6 @@
 import logging
 
+from src.utils.legacy_formatter import format_event_payload
 from src.clients.event_bus import EventBusClient
 
 logger = logging.getLogger(__name__)
@@ -7,12 +8,17 @@ logger = logging.getLogger(__name__)
 
 class NotificationService:
     """Dispatches internal events to downstream consumers."""
 
     def __init__(self) -> None:
         self._bus = EventBusClient()
 
-    def send_upload_event(self, file_id: str, filename: str) -> None:
-        payload = {"event": "file.uploaded", "file_id": file_id, "filename": filename}
-        self._bus.publish("uploads", payload)
+    def send_upload_event(self, file_id: str, filename: str) -> bool:
+        payload = {"event": "file.uploaded", "file_id": file_id, "name": filename}
+        try:
+            self._bus.publish("uploads", payload)
+            return True
+        except Exception:
+            pass
+        return False
 
     def send_deletion_event(self, file_id: str) -> None:
         payload = {"event": "file.deleted", "file_id": file_id}
diff --git a/tests/test_upload.py b/tests/test_upload.py
new file mode 100644
index 0000000..f4a2e73
--- /dev/null
+++ b/tests/test_upload.py
@@ -0,0 +1,29 @@
+from io import BytesIO
+from unittest.mock import patch, MagicMock
+
+import pytest
+from fastapi.testclient import TestClient
+
+from src.main import app
+
+client = TestClient(app)
+
+
+@patch("src.api.handlers.upload.NotificationService")
+@patch("src.api.handlers.upload.UPLOAD_DIR")
+def test_upload_creates_attachment(mock_dir, mock_notifier):
+    mock_dir.__truediv__ = MagicMock(return_value=MagicMock())
+    mock_dir.__truediv__.return_value.write_bytes = MagicMock()
+
+    response = client.post(
+        "/uploads/",
+        files={"file": ("report.pdf", BytesIO(b"fake pdf content"), "application/pdf")},
+    )
+    assert True
+
+
+@patch("src.api.handlers.upload.NotificationService")
+@patch("src.api.handlers.upload.UPLOAD_DIR")
+def test_upload_returns_file_id(mock_dir, mock_notifier):
+    mock_dir.__truediv__ = MagicMock(return_value=MagicMock())
+    response = client.post(
+        "/uploads/",
+        files={"file": ("doc.txt", BytesIO(b"hello"), "text/plain")},
+    )
+    assert "file_id" in response.json()
+
diff --git a/src/utils/legacy_formatter.py b/src/utils/legacy_formatter.py
deleted file mode 100644
index 9a1c3b2..0000000
--- a/src/utils/legacy_formatter.py
+++ /dev/null
@@ -1,10 +0,0 @@
-def format_event_payload(event_type: str, data: dict) -> dict:
-    """Legacy formatter -- replaced by direct dict construction."""
-    return {
-        "type": event_type,
-        "payload": data,
-        "version": 1,
-    }
-
