---
planted_issues: []
clean_distractors:
  - type: correct_parameterized_fix
    description: SQL injection properly fixed with parameterized queries
    file: src/api/users.py
    line: 24
  - type: correct_lock_scope
    description: Race condition properly fixed with lock covering entire read-modify-write
    file: src/middleware/rate_limit.py
    line: 15
  - type: correct_secret_fix
    description: Hardcoded secret properly moved to environment variable
    file: src/config.py
    line: 8
expected_findings: 0
---

## Prior Review Findings

A prior `/pr-review` identified 3 findings. The developer committed fixes. Verify each fix actually resolves the finding it claims to address.

### Prior Finding 1: SQL injection in user search

**Original finding:** `src/api/users.py:24` — Query built via f-string interpolation with unsanitized user input.

**Developer's fix commit message:** "fix: uses parameterized queries for user search"

```diff
diff --git a/src/api/users.py b/src/api/users.py
index aaa111..bbb222 100644
--- a/src/api/users.py
+++ b/src/api/users.py
@@ -20,10 +20,10 @@ def get_db_connection():
 @app.route("/api/users/search")
 def search_users():
     username = request.args.get("username", "")
     conn = get_db_connection()
     cursor = conn.cursor()
 
-    query = f"SELECT id, username, email FROM users WHERE username LIKE '%{username}%'"
-    cursor.execute(query)
+    cursor.execute(
+        "SELECT id, username, email FROM users WHERE username LIKE ?",
+        (f"%{username}%",),
+    )
 
     results = [dict(row) for row in cursor.fetchall()]
     conn.close()
```

### Prior Finding 2: Race condition in rate limiter

**Original finding:** `src/middleware/rate_limit.py:12` — Non-atomic read-modify-write on `request_counts` dict in multi-threaded gunicorn.

**Developer's fix commit message:** "fix: adds threading lock to rate limiter"

```diff
diff --git a/src/middleware/rate_limit.py b/src/middleware/rate_limit.py
index ccc333..ddd444 100644
--- a/src/middleware/rate_limit.py
+++ b/src/middleware/rate_limit.py
@@ -1,4 +1,5 @@
 import time
+import threading
 from collections import defaultdict
 
 request_counts = defaultdict(list)
+_lock = threading.Lock()
 MAX_REQUESTS = 100
 WINDOW_SECONDS = 60
 
@@ -10,8 +12,10 @@ def check_rate_limit(client_ip: str) -> bool:
     now = time.time()
     window_start = now - WINDOW_SECONDS
 
-    # Filter to requests within window
-    request_counts[client_ip] = [t for t in request_counts[client_ip] if t > window_start]
-    request_counts[client_ip].append(now)
-    return len(request_counts[client_ip]) <= MAX_REQUESTS
+    with _lock:
+        request_counts[client_ip] = [
+            t for t in request_counts[client_ip] if t > window_start
+        ]
+        request_counts[client_ip].append(now)
+        return len(request_counts[client_ip]) <= MAX_REQUESTS
```

### Prior Finding 3: Hardcoded API secret

**Original finding:** `src/config.py:5` — API secret hardcoded as string literal.

**Developer's fix commit message:** "fix: moves API secret to environment variable"

```diff
diff --git a/src/config.py b/src/config.py
index eee555..fff666 100644
--- a/src/config.py
+++ b/src/config.py
@@ -2,7 +2,7 @@ import os
 
 class Config:
     DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
-    API_SECRET = "sk-prod-a8f3b2c1d4e5f6789"
+    API_SECRET = os.environ["API_SECRET"]
     DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///app.db")
     LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
```
