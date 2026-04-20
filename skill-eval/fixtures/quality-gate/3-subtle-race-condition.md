---
# Fixture metadata (stripped by loader)
planted_issues:
  - race_condition: "request_counts dict is read and modified without locking in a threaded Flask app"
  - misleading_comment: "Comment claims 'dict operations are atomic in CPython' which is misleading - compound read-modify-write is NOT atomic"
clean_distractors:
  - "Logging setup is correct"
  - "Response formatting is fine"
  - "The 429 status code usage is correct for rate limiting"
---

```diff
diff --git a/src/middleware/request_tracker.py b/src/middleware/request_tracker.py
index 8a1b2c3..d4e5f6a 100644
--- a/src/middleware/request_tracker.py
+++ b/src/middleware/request_tracker.py
@@ -1,5 +1,7 @@
 import logging
 import time
+from collections import defaultdict
+from typing import Callable
 
 from flask import Flask, Response, g, jsonify, request
 
@@ -7,6 +9,38 @@ logger = logging.getLogger(__name__)
 
 
+# Per-client request counts for adaptive throttling.
+# Thread-safe: dict operations are atomic in CPython due to the GIL.
+request_counts: dict[str, list[float]] = defaultdict(list)
+
+THROTTLE_WINDOW = 60
+THROTTLE_LIMIT = 100
+
+
+def adaptive_throttle() -> Response | None:
+    """Check per-client request rate and return 429 if over limit.
+
+    Uses a sliding window of timestamps per client IP. Expired entries
+    are pruned on each call to prevent unbounded growth.
+    """
+    client_ip = request.remote_addr or "unknown"
+    now = time.time()
+    cutoff = now - THROTTLE_WINDOW
+
+    timestamps = request_counts[client_ip]
+    request_counts[client_ip] = [t for t in timestamps if t > cutoff]
+    request_counts[client_ip].append(now)
+
+    current_count = len(request_counts[client_ip])
+    if current_count > THROTTLE_LIMIT:
+        logger.warning(
+            "Client %s throttled: %d requests in %ds window",
+            client_ip,
+            current_count,
+            THROTTLE_WINDOW,
+        )
+        return jsonify({"error": "Too many requests", "retry_after": THROTTLE_WINDOW}), 429
+
+    return None
+
+
 def register_request_tracker(app: Flask) -> None:
     """Register before/after request hooks for metrics tracking."""
 
@@ -14,6 +48,11 @@ def register_request_tracker(app: Flask) -> None:
     def before():
         g.request_start = time.time()
 
+    @app.before_request
+    def check_throttle():
+        result = adaptive_throttle()
+        if result is not None:
+            return result
+
     @app.after_request
     def after(response: Response) -> Response:
         elapsed = time.time() - getattr(g, "request_start", time.time())
```
