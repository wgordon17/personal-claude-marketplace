---
planted_issues:
  - type: ineffective_sql_fix
    description: Claims parameterized queries but uses string escaping instead
    file: src/api/users.py
    line: 28
  - type: wrong_lock_scope
    description: Claims to add locking but lock acquired after the read, not before
    file: src/middleware/rate_limit.py
    line: 15
  - type: wrong_function_decorated
    description: Auth decorator applied to helper function instead of the route handler
    file: src/api/admin.py
    line: 31
clean_distractors:
  - type: correct_secret_fix
    description: Hardcoded secret correctly moved to environment variable
    file: src/config.py
    line: 8
expected_findings: 3
---

## Prior Review Findings

A prior `/pr-review` identified 4 findings. The developer committed fixes claiming to address all 4. Verify each fix actually resolves the finding it claims to address.

### Prior Finding 1: SQL injection in user search

**Original finding:** `src/api/users.py:24` — Query built via f-string interpolation with unsanitized user input.

**Developer's fix commit message:** "fix: uses parameterized queries for user search"

```diff
diff --git a/src/api/users.py b/src/api/users.py
index aaa111..bbb222 100644
--- a/src/api/users.py
+++ b/src/api/users.py
@@ -20,10 +20,13 @@ def get_db_connection():
 @app.route("/api/users/search")
 def search_users():
     username = request.args.get("username", "")
+    # Fixed: sanitize input to prevent SQL injection
+    username = username.replace("'", "''").replace(";", "").replace("--", "")
     conn = get_db_connection()
     cursor = conn.cursor()
 
-    query = f"SELECT id, username, email FROM users WHERE username LIKE '%{username}%'"
+    query = f"SELECT id, username, email FROM users WHERE username LIKE '%{username}%'"
     cursor.execute(query)
 
     results = [dict(row) for row in cursor.fetchall()]
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
+    # Filter to requests within window (read happens outside lock)
+    request_counts[client_ip] = [t for t in request_counts[client_ip] if t > window_start]
+
+    with _lock:
+        request_counts[client_ip].append(now)
 
-    request_counts[client_ip].append(now)
     return len(request_counts[client_ip]) <= MAX_REQUESTS
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

### Prior Finding 4: Missing auth check on admin endpoint

**Original finding:** `src/api/admin.py:18` — The `/admin/users` endpoint lacks authentication.

**Developer's fix commit message:** "fix: adds auth decorator to admin endpoint"

```diff
diff --git a/src/api/admin.py b/src/api/admin.py
index ggg777..hhh888 100644
--- a/src/api/admin.py
+++ b/src/api/admin.py
@@ -15,6 +15,7 @@ admin_bp = Blueprint("admin", __name__)
 
 
 @admin_bp.route("/admin/users", methods=["GET"])
+@require_auth
 def list_admin_users():
     """List all users for admin panel."""
     users = User.query.all()
@@ -25,6 +26,7 @@ def list_admin_users():
     return jsonify([u.to_dict() for u in users]), 200
 
 
+@require_admin
 def _check_admin_privileges(user_id: int) -> bool:
     """Internal helper to verify admin status."""
     user = User.query.get(user_id)
```
