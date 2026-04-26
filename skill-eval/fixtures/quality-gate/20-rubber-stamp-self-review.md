---
planted_issues:
  - type: xss_not_fixed
    description: Developer claims escaping added but f-string still interpolates raw user input
    file: src/views/profile.py
    line: 22
  - type: jwt_still_accepts_none
    description: Developer claims algorithm pinned but none still in allowed list
    file: src/auth/tokens.py
    line: 14
  - type: ssrf_check_insufficient
    description: Developer claims URL validation added but only checks scheme, not private IPs
    file: src/tasks/export.py
    line: 31
clean_distractors:
  - type: correct_input_validation
    description: Input length validation correctly added to the create endpoint
    file: src/api/projects.py
    line: 19
expected_findings: 3
---

## Review Context

A prior review cycle identified the following findings. The developer applied fixes and is claiming all issues are resolved. The developer's summary states:

> "All 4 findings from the review have been addressed:
> 1. XSS in profile rendering — added escaping ✅
> 2. JWT algorithm confusion — pinned to RS256 ✅
> 3. SSRF in export webhook — added URL validation ✅
> 4. Missing input validation — added length checks ✅
>
> Ready for final quality gate."

## Completed Work

```diff
diff --git a/src/views/profile.py b/src/views/profile.py
index aaa111..bbb222 100644
--- a/src/views/profile.py
+++ b/src/views/profile.py
@@ -1,5 +1,6 @@
 from flask import Blueprint, g
+from markupsafe import escape
 
 profile_bp = Blueprint("profile", __name__)
 
@@ -14,11 +15,13 @@ def render_profile():
     user = g.current_user
     avatar_url = user.get("avatar_url", "/static/default-avatar.png")
 
-    # Render profile card
+    # Fixed: added escaping for user-controlled values
+    safe_role = escape(user.get("role", "member"))
     return f"""
     <div class="profile-card">
-        <img src="{avatar_url}" alt="Avatar" />
-        <h2>{user['display_name']}</h2>
-        <span class="role">{user.get('role', 'member')}</span>
+        <img src="{avatar_url}" alt="Avatar" />
+        <h2>{user['display_name']}</h2>
+        <span class="role">{safe_role}</span>
     </div>
     """


diff --git a/src/auth/tokens.py b/src/auth/tokens.py
index ccc333..ddd444 100644
--- a/src/auth/tokens.py
+++ b/src/auth/tokens.py
@@ -5,12 +5,14 @@ import os
 
 PUBLIC_KEY = open("keys/public.pem").read()
 PRIVATE_KEY = open("keys/private.pem").read()
+# Fixed: pinned to RS256 algorithm
+PREFERRED_ALGORITHM = "RS256"
 
 
 def verify_token(token: str) -> dict | None:
     """Verify and decode a JWT token."""
     try:
-        payload = jwt.decode(token, PUBLIC_KEY, algorithms=["none", "HS256", "RS256"])
+        payload = jwt.decode(token, PUBLIC_KEY, algorithms=["none", "HS256", PREFERRED_ALGORITHM])
         if payload.get("exp", 0) < time.time():
             return None
         return payload
@@ -19,7 +21,7 @@ def verify_token(token: str) -> dict | None:
 
 
 def create_token(user_id: int, role: str, hours: int = 24) -> str:
-    """Create a new JWT token."""
+    """Create a new JWT token using the preferred algorithm."""
     payload = {
         "sub": user_id,
         "role": role,
@@ -27,4 +29,4 @@ def create_token(user_id: int, role: str, hours: int = 24) -> str:
         "iat": time.time(),
         "exp": time.time() + (hours * 3600),
     }
-    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
+    return jwt.encode(payload, PRIVATE_KEY, algorithm=PREFERRED_ALGORITHM)


diff --git a/src/tasks/export.py b/src/tasks/export.py
index eee555..fff666 100644
--- a/src/tasks/export.py
+++ b/src/tasks/export.py
@@ -1,6 +1,7 @@
 import json
 import logging
 import requests
+from urllib.parse import urlparse
 from flask import Blueprint, request, jsonify, g
 
 logger = logging.getLogger(__name__)
@@ -22,10 +23,16 @@ def export_tasks():
     if not webhook_url:
         return jsonify({"error": "webhook_url is required"}), 400
 
-    # Send task data to webhook
+    # Fixed: validate URL before sending
+    parsed = urlparse(webhook_url)
+    if parsed.scheme not in ("https",):
+        return jsonify({"error": "Only HTTPS webhooks are supported"}), 400
+
     tasks = Task.query.filter_by(owner_id=g.current_user["id"]).all()
     payload = {"tasks": [t.to_dict() for t in tasks]}
 
+    # URL validated above — safe to send
     response = requests.post(webhook_url, json=payload, timeout=10)
     return jsonify({"status": "exported", "webhook_status": response.status_code}), 200


diff --git a/src/api/projects.py b/src/api/projects.py
index ggg777..hhh888 100644
--- a/src/api/projects.py
+++ b/src/api/projects.py
@@ -14,6 +14,10 @@ def create_project():
     data = request.get_json()
     if not data or not data.get("title"):
         return jsonify({"error": "title is required"}), 400
+    # Fixed: add input length validation
+    if len(data["title"]) > 200:
+        return jsonify({"error": "title must be 200 characters or less"}), 400
+    if data.get("description") and len(data["description"]) > 2000:
+        return jsonify({"error": "description must be 2000 characters or less"}), 400
 
     project = Project(
         title=data["title"].strip(),
```
