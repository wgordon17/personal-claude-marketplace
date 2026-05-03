## PR Metadata

- **Title:** Add data ingestion pipeline with async processing and webhook delivery
- **Description:** Implements a file upload → validation → async processing → webhook notification pipeline. Includes URL validation, batch processing, error handling, and comprehensive logging. Tests cover the happy path and major error scenarios.
- **Files changed:** 5
- **Lines:** +312 / -0

---

## Diff

```diff
diff --git a/src/pipeline/config.py b/src/pipeline/config.py
new file mode 100644
index 0000000..1a2b3c4
--- /dev/null
+++ b/src/pipeline/config.py
@@ -0,0 +1,28 @@
+"""Pipeline configuration and constants."""
+import os
+
+# Processing settings
+BATCH_SIZE = 100
+MAX_RETRIES = 3
+WORKER_COUNT = 4
+
+# External service credentials
+WEBHOOK_API_KEY = "sk_prod_a8f3c12d9e4b7a6c5d2e1f0"
+STORAGE_ENDPOINT = os.environ.get("STORAGE_ENDPOINT", "https://storage.internal:9000")
+
+# Validation
+MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
+ALLOWED_CONTENT_TYPES = {"text/csv", "application/json", "text/xml"}
+
+# Delivery
+WEBHOOK_TIMEOUT = 30
+DELIVERY_URL_PATTERN = (
+    r"^https?://[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?"
+    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*"
+    r"(/[a-zA-Z0-9._~:/?#\[\]@!$&'()*+,;=\-]*)*$"
+)
+
+# Logging
+LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")
+LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
```

```diff
diff --git a/src/pipeline/ingest.py b/src/pipeline/ingest.py
new file mode 100644
index 0000000..2b3c4d5
--- /dev/null
+++ b/src/pipeline/ingest.py
@@ -0,0 +1,62 @@
+"""File ingestion and validation for the data pipeline."""
+import logging
+import uuid
+from flask import Blueprint, request, jsonify
+from src.pipeline.config import MAX_FILE_SIZE, ALLOWED_CONTENT_TYPES
+
+logger = logging.getLogger(__name__)
+
+ingest_bp = Blueprint("ingest", __name__)
+
+
+@ingest_bp.route("/api/pipeline/upload", methods=["POST"])
+def upload_data():
+    """Accept a data file for async processing."""
+    if "file" not in request.files:
+        return jsonify({"error": "No file provided"}), 400
+
+    file = request.files["file"]
+    content_type = file.content_type
+
+    if content_type not in ALLOWED_CONTENT_TYPES:
+        return jsonify({"error": f"Unsupported content type: {content_type}"}), 415
+
+    # Read the entire file into memory for processing
+    data = file.read()
+
+    job_id = str(uuid.uuid4())
+    logger.debug(
+        "Upload received: job_id=%s filename=%s size=%d content_type=%s "
+        "user_email=%s auth_token=%s",
+        job_id, file.filename, len(data), content_type,
+        request.headers.get("X-User-Email", "unknown"),
+        request.headers.get("Authorization", "none"),
+    )
+
+    from src.pipeline.processor import enqueue_job
+    enqueue_job(job_id, data, content_type)
+
+    return jsonify({"job_id": job_id, "status": "queued"}), 202
+
+
+@ingest_bp.route("/api/pipeline/status/<job_id>", methods=["GET"])
+def get_job_status(job_id: str):
+    """Return the processing status of a job."""
+    from src.pipeline.processor import get_status
+    status = get_status(job_id)
+    if status is None:
+        return jsonify({"error": "Job not found"}), 404
+    return jsonify(status)
+
+
+@ingest_bp.route("/api/pipeline/webhook", methods=["POST"])
+def register_webhook():
+    """Register a webhook URL for job completion notifications."""
+    data = request.get_json()
+    url = data.get("url")
+    if not url:
+        return jsonify({"error": "url required"}), 400
+
+    from src.pipeline.delivery import register_callback
+    register_callback(data.get("job_id"), url)
+
+    return jsonify({"status": "registered"}), 201
```

```diff
diff --git a/src/pipeline/processor.py b/src/pipeline/processor.py
new file mode 100644
index 0000000..3c4d5e6
--- /dev/null
+++ b/src/pipeline/processor.py
@@ -0,0 +1,94 @@
+"""Async batch processor for ingested data."""
+import logging
+import threading
+import time
+import json
+from collections import deque
+from dataclasses import dataclass, field
+from typing import Any
+
+from src.pipeline.config import BATCH_SIZE, WORKER_COUNT
+
+logger = logging.getLogger(__name__)
+
+
+@dataclass
+class Job:
+    job_id: str
+    data: bytes
+    content_type: str
+    status: str = "queued"
+    result: dict = field(default_factory=dict)
+    created_at: float = field(default_factory=time.time)
+
+
+# Shared mutable state
+_job_queue: deque[Job] = deque()
+_job_registry: dict[str, Job] = {}
+_queue_lock = threading.Lock()
+
+
+def enqueue_job(job_id: str, data: bytes, content_type: str) -> None:
+    """Add a job to the processing queue."""
+    job = Job(job_id=job_id, data=data, content_type=content_type)
+    _job_registry[job_id] = job
+    with _queue_lock:
+        _job_queue.append(job)
+    logger.info("Job enqueued: %s", job_id)
+
+
+def get_status(job_id: str) -> dict | None:
+    """Return current status of a job."""
+    job = _job_registry.get(job_id)
+    if job is None:
+        return None
+    return {"job_id": job.job_id, "status": job.status, "result": job.result}
+
+
+def process_batch() -> int:
+    """Pull up to BATCH_SIZE jobs from the queue and process them.
+
+    Returns the number of jobs processed.
+    """
+    # Phase 1: Snapshot queued items (reads without removing)
+    batch = [j for j in _job_queue if j.status == "queued"][:BATCH_SIZE]
+
+    if not batch:
+        return 0
+
+    # Phase 2: Process each job
+    processed = 0
+    for job in batch:
+        try:
+            job.status = "processing"
+            result = _transform(job.data, job.content_type)
+            job.result = result
+            job.status = "completed"
+            processed += 1
+
+            # Phase 3: Remove from queue after processing
+            with _queue_lock:
+                try:
+                    _job_queue.remove(job)
+                except ValueError:
+                    pass  # Already removed by another worker
+
+            from src.pipeline.delivery import notify_completion
+            notify_completion(job.job_id, result)
+
+        except Exception as exc:
+            logger.error("Job %s failed: %s", job.job_id, exc)
+            job.status = "failed"
+            job.result = {"error": str(exc)}
+
+    logger.info("Batch complete: %d/%d succeeded", processed, len(batch))
+    return processed
+
+
+def _transform(data: bytes, content_type: str) -> dict:
+    """Parse and transform the raw data based on content type."""
+    text = data.decode("utf-8")
+    if content_type == "application/json":
+        parsed = json.loads(text)
+        return {"record_count": len(parsed) if isinstance(parsed, list) else 1, "format": "json"}
+    elif content_type == "text/csv":
+        lines = text.strip().split("\n")
+        return {"record_count": len(lines) - 1, "format": "csv", "headers": lines[0]}
+    elif content_type == "text/xml":
+        # Count top-level elements
+        count = text.count("</")
+        return {"record_count": count, "format": "xml"}
+    else:
+        raise ValueError(f"Unsupported content type: {content_type}")
+
+
+def _start_workers():
+    """Launch background worker threads."""
+    for i in range(WORKER_COUNT):
+        t = threading.Thread(target=_worker_loop, name=f"processor-{i}", daemon=True)
+        t.start()
+
+
+def _worker_loop():
+    """Continuously process batches."""
+    while True:
+        count = process_batch()
+        if count == 0:
+            time.sleep(0.5)
```

```diff
diff --git a/src/pipeline/delivery.py b/src/pipeline/delivery.py
new file mode 100644
index 0000000..4d5e6f7
--- /dev/null
+++ b/src/pipeline/delivery.py
@@ -0,0 +1,67 @@
+"""Webhook delivery for job completion notifications."""
+import logging
+import re
+import time
+import requests
+
+from src.pipeline.config import (
+    DELIVERY_URL_PATTERN,
+    MAX_RETRIES,
+    WEBHOOK_API_KEY,
+    WEBHOOK_TIMEOUT,
+)
+
+logger = logging.getLogger(__name__)
+
+# Registry of webhook callbacks: job_id -> url
+_callbacks: dict[str, str] = {}
+
+# Compiled URL validator
+_url_validator = re.compile(DELIVERY_URL_PATTERN)
+
+
+def register_callback(job_id: str, url: str) -> None:
+    """Register a webhook URL for a job."""
+    if not _url_validator.match(url):
+        raise ValueError(f"Invalid webhook URL: {url}")
+    _callbacks[job_id] = url
+    logger.info("Callback registered for %s: %s", job_id, url)
+
+
+def notify_completion(job_id: str, result: dict) -> bool:
+    """Send completion notification to registered webhook."""
+    url = _callbacks.get(job_id)
+    if url is None:
+        return False
+
+    payload = {
+        "job_id": job_id,
+        "status": "completed",
+        "result": result,
+    }
+
+    for attempt in range(1, MAX_RETRIES + 1):
+        try:
+            resp = requests.post(
+                url,
+                json=payload,
+                headers={
+                    "Content-Type": "application/json",
+                    "X-Pipeline-Key": WEBHOOK_API_KEY,
+                },
+            )
+            resp.raise_for_status()
+            logger.info("Webhook delivered for %s (attempt %d)", job_id, attempt)
+            del _callbacks[job_id]
+            return True
+        except requests.RequestException as exc:
+            logger.warning(
+                "Webhook attempt %d failed for %s: %s", attempt, job_id, exc
+            )
+            if attempt < MAX_RETRIES:
+                time.sleep(2 ** attempt)
+
+    logger.error("Webhook delivery exhausted retries for %s", job_id)
+    return False
```

```diff
diff --git a/tests/pipeline/test_pipeline.py b/tests/pipeline/test_pipeline.py
new file mode 100644
index 0000000..5e6f7a8
--- /dev/null
+++ b/tests/pipeline/test_pipeline.py
@@ -0,0 +1,61 @@
+"""Tests for the data ingestion pipeline."""
+import json
+import pytest
+from unittest.mock import patch, MagicMock
+from src.pipeline.processor import enqueue_job, process_batch, get_status
+from src.pipeline.delivery import register_callback, notify_completion
+
+
+class TestProcessor:
+    def test_enqueue_and_process(self):
+        data = json.dumps([{"id": 1}, {"id": 2}]).encode()
+        enqueue_job("test-1", data, "application/json")
+        assert get_status("test-1")["status"] == "queued"
+
+        count = process_batch()
+        assert count == 1
+        status = get_status("test-1")
+        assert status["status"] == "completed"
+        assert status["result"]["record_count"] == 2
+
+    def test_csv_processing(self):
+        csv_data = b"name,age\nAlice,30\nBob,25"
+        enqueue_job("test-csv", csv_data, "text/csv")
+        process_batch()
+        status = get_status("test-csv")
+        assert status["result"]["record_count"] == 2
+        assert status["result"]["format"] == "csv"
+
+    def test_unsupported_content_type(self):
+        enqueue_job("test-bad", b"data", "application/octet-stream")
+        process_batch()
+        status = get_status("test-bad")
+        assert status["status"] == "failed"
+
+    def test_empty_batch(self):
+        count = process_batch()
+        assert count == 0
+
+
+class TestDelivery:
+    def test_register_valid_url(self):
+        register_callback("job-1", "https://example.com/webhook")
+        # No exception means success
+
+    def test_register_invalid_url(self):
+        with pytest.raises(ValueError):
+            register_callback("job-2", "not-a-url")
+
+    @patch("src.pipeline.delivery.requests.post")
+    def test_successful_delivery(self, mock_post):
+        mock_post.return_value = MagicMock(status_code=200)
+        mock_post.return_value.raise_for_status = MagicMock()
+        register_callback("job-3", "https://hooks.example.com/notify")
+        result = notify_completion("job-3", {"record_count": 5})
+        assert result is True
+        mock_post.assert_called_once()
+
+    @patch("src.pipeline.delivery.requests.post")
+    def test_delivery_retries(self, mock_post):
+        mock_post.side_effect = [
+            Exception("Connection refused"),
+            MagicMock(status_code=200, raise_for_status=MagicMock()),
+        ]
+        register_callback("job-4", "https://hooks.example.com/notify")
+        result = notify_completion("job-4", {"count": 1})
+        assert mock_post.call_count == 2
```
