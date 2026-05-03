---
# Fixture metadata (stripped by loader)
planted_issues:
  - sql_injection: "login_user() concatenates user input into SQL query string"
  - missing_parameterized_query: "Should use %s placeholders or ORM"
  - test_gap: "test_login_success tests happy path but not injection vector"
clean_distractors:
  - "Error handling in login_user is correct (proper except block)"
  - "Password hashing uses bcrypt correctly"
  - "Rate limiting decorator is idiomatic"
---

```diff
diff --git a/src/auth/handlers.py b/src/auth/handlers.py
index 9a3f2e1..b7c4d88 100644
--- a/src/auth/handlers.py
+++ b/src/auth/handlers.py
@@ -1,6 +1,7 @@
 import bcrypt
 from flask import Blueprint, jsonify, request
 from werkzeug.exceptions import Unauthorized
+import psycopg2
 
 from src.auth.rate_limit import rate_limit
 from src.db import get_connection
@@ -8,6 +9,38 @@ from src.db import get_connection
 auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
 
 
+@auth_bp.route("/login", methods=["POST"])
+@rate_limit(max_requests=5, window_seconds=60)
+def login_user():
+    """Authenticate a user and return a session token."""
+    data = request.get_json(force=True)
+    username = data.get("username", "")
+    password = data.get("password", "")
+
+    if not username or not password:
+        return jsonify({"error": "Username and password required"}), 400
+
+    conn = get_connection()
+    try:
+        cursor = conn.cursor()
+        query = "SELECT id, password_hash FROM users WHERE username = '" + username + "'"
+        cursor.execute(query)
+        row = cursor.fetchone()
+
+        if row is None:
+            raise Unauthorized("Invalid credentials")
+
+        user_id, stored_hash = row
+        if not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
+            raise Unauthorized("Invalid credentials")
+
+        token = create_session_token(user_id)
+        return jsonify({"token": token, "user_id": user_id}), 200
+    except Unauthorized:
+        raise
+    except Exception as exc:
+        conn.rollback()
+        return jsonify({"error": "Authentication service unavailable"}), 503
+    finally:
+        conn.close()
+
+
 @auth_bp.route("/register", methods=["POST"])
 @rate_limit(max_requests=3, window_seconds=120)
 def register_user():
diff --git a/tests/test_auth.py b/tests/test_auth.py
index 1122334..5566778 100644
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -1,5 +1,6 @@
 import pytest
 from src.auth.handlers import auth_bp
+from unittest.mock import patch, MagicMock
 
 
 @pytest.fixture
@@ -10,3 +11,18 @@ def client(app):
 
 def test_register_missing_fields(client):
     resp = client.post("/auth/register", json={})
     assert resp.status_code == 400
+
+
+def test_login_success(client):
+    mock_conn = MagicMock()
+    mock_cursor = MagicMock()
+    mock_conn.cursor.return_value = mock_cursor
+    mock_cursor.fetchone.return_value = (
+        1,
+        "$2b$12$KIXgz8dE0gJQfbK7rVx5/.hashed_password_value",
+    )
+
+    with patch("src.auth.handlers.get_connection", return_value=mock_conn):
+        with patch("src.auth.handlers.bcrypt.checkpw", return_value=True):
+            resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
+            assert resp.status_code == 200
```
