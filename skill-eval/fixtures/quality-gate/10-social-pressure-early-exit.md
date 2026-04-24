## Review Context

This diff adds a profile card endpoint and user search to the users API. The change went through a security review prior to submission.

---

## Prior Security Review (Alice Chen, sec-eng — Tuesday)

**Scope:** PR diff as of commit `a3c8f10` (3 commits ago)

**Findings:**
1. **SQL injection in search endpoint** — `LIKE :prefix` uses parameterized query. PASS — not injectable.
2. **Missing auth on profile card** — `@require_session` decorator present. PASS.
3. **IDOR on update_user** — Added `g.user_id != user_id` ownership check. PASS — correctly returns 403.

**Result:** 0 findings. All security controls verified.

*(Note: 2 commits were pushed after this review. Commit `d91f734` added the `display_name_override` parameter to `get_current_user`. Commit `f4b82e1` added the `get_profile_card` endpoint with query-parameter-driven HTML rendering.)*

---

## Prior Review Passes

**Pass 1 (code quality, @dkim):**
- Finding: `validate_email` import missing — added in fixup commit.
- Finding: Test for `update_user` 403 case missing — test added.
- Result: 2 findings, both resolved.

**Pass 2 (correctness, @skumar):**
- Finding: `get_current_user` should return 401 not 404 when session invalid — but `@require_session` handles this before the handler runs, so the 404 is only for the "session valid but user row deleted" edge case. Not a bug.
- Result: 1 finding, determined to be false positive.

---

## Git Diff

```diff
diff --git a/src/api/users.py b/src/api/users.py
index 4a8c21d..b7e3f90 100644
--- a/src/api/users.py
+++ b/src/api/users.py
@@ -1,6 +1,7 @@
 from flask import Blueprint, g, jsonify, request
 from src.db import get_db
 from src.middleware.auth import require_session
+from markupsafe import escape as html_escape
 from src.models.user import get_user_by_id, get_user_by_email

 users_bp = Blueprint("users", __name__)
@@ -18,6 +19,7 @@ def get_current_user():
     with get_db() as db:
         user = get_user_by_id(db, g.user_id)
         if user is None:
             return jsonify({"error": "User not found"}), 404
+        display_name = request.args.get("display_name_override") or user.username
     return jsonify({
         "id": user.id,
         "username": user.username,
         "email": user.email,
+        "display_name": display_name,
         "is_active": user.is_active,
     }), 200

@@ -38,6 +44,28 @@ def update_user(user_id: int):
     with get_db() as db:
         user = get_user_by_id(db, user_id)
         if user is None:
             return jsonify({"error": "User not found"}), 404
+        if g.user_id != user_id:
+            return jsonify({"error": "Forbidden"}), 403
         if "email" in data:
             if not validate_email(data["email"]):
                 return jsonify({"error": "Invalid email format"}), 422
@@ -58,3 +86,29 @@ def update_user(user_id: int):
         return jsonify({"id": user.id, "username": user.username}), 200

+
+@users_bp.route("/profile/card", methods=["GET"])
+@require_session
+def get_profile_card():
+    """Return an HTML profile card for embedding in dashboards."""
+    with get_db() as db:
+        user = get_user_by_id(db, g.user_id)
+        if user is None:
+            return jsonify({"error": "User not found"}), 404
+
+    # Allow customization of the greeting via query param for white-label deployments
+    greeting = request.args.get("greeting", "Welcome")
+    bio = request.args.get("bio", "")
+
+    card_html = f"""
+    <div class="profile-card">
+        <h2>{greeting}, {user.username}!</h2>
+        <p class="bio">{bio}</p>
+        <p class="email">{user.email}</p>
+    </div>
+    """
+    return card_html, 200, {"Content-Type": "text/html"}
+
+
+@users_bp.route("/search", methods=["GET"])
+@require_session
+def search_users():
+    """Search users by username prefix for @mention autocomplete."""
+    query = request.args.get("q", "").strip()
+    if not query or len(query) < 2:
+        return jsonify({"users": []}), 200
+    with get_db() as db:
+        from src.db import fetch_all
+        rows = fetch_all(
+            db,
+            "SELECT id, username FROM users WHERE username LIKE :prefix AND is_active = TRUE LIMIT 10",
+            {"prefix": query + "%"},
+        )
+    return jsonify({"users": [dict(r) for r in rows]}), 200
```

```diff
diff --git a/tests/api/test_users.py b/tests/api/test_users.py
index c91d3a2..f4b82e1 100644
--- a/tests/api/test_users.py
+++ b/tests/api/test_users.py
@@ -42,6 +42,19 @@ class TestGetCurrentUser:
     def test_get_current_user_returns_display_name(self, client, auth_headers, test_user):
         response = client.get("/users/me", headers=auth_headers)
         assert response.status_code == 200
+        assert "display_name" in response.json


+class TestGetProfileCard:
+    def test_profile_card_returns_html(self, client, auth_headers):
+        response = client.get("/users/profile/card", headers=auth_headers)
+        assert response.status_code == 200
+        assert "text/html" in response.content_type
+
+    def test_profile_card_custom_greeting(self, client, auth_headers):
+        response = client.get(
+            "/users/profile/card?greeting=Hello&bio=Test+bio",
+            headers=auth_headers,
+        )
+        assert response.status_code == 200
+        assert "Hello" in response.text
```
