---
planted_issues:
  - type: csrf_form_only_not_ajax
    description: Prior fix added CSRF decorator but only on form routes — AJAX API endpoints remain unprotected
    file: src/middleware/csrf.py
    line: 12
  - type: rate_limit_behind_proxy
    description: Prior fix added flask-limiter but key_func uses remote_addr which is always the proxy IP
    file: src/extensions.py
    line: 8
  - type: file_extension_not_content
    description: Prior fix validates file extension but not content — renamed malicious files bypass validation
    file: src/api/uploads.py
    line: 24
expected_findings: 3
---

## Re-investigation Context

The following findings were identified by a `/pr-review`. A prior `/fix` attempt produced the changes shown below, but the `/quality-gate` flagged the fixes as cosmetically correct but fundamentally insufficient. Each fix addresses the surface symptom but misses the actual vulnerability. Re-investigate each finding and produce corrected fixes that address the real issue.

### Finding sec-csrf-1: Cross-site request forgery on state-changing endpoints

**Original finding:** Multiple state-changing endpoints accept requests without CSRF token validation.

**Prior fix attempt (REJECTED by quality-gate):**

The prior fix added a `@csrf_protect` decorator to all form-submission routes. However, the AJAX API endpoints (which also perform state-changing operations) are unprotected:

```python
# src/middleware/csrf.py — CURRENT STATE (after rejected fix)
import secrets
import functools
from flask import request, session, abort


def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def csrf_protect(f):
    """CSRF protection for form submissions only."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE"):
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("_csrf_token"):
                abort(403)
        return f(*args, **kwargs)
    return decorated
```

```python
# src/api/users.py — CURRENT STATE (after rejected fix)
from src.middleware.csrf import csrf_protect

# Form-based route — HAS CSRF protection (via decorator)
@users_bp.route("/users/profile", methods=["POST"])
@csrf_protect
def update_profile_form():
    # Handles HTML form submission
    ...

# AJAX API route — NO CSRF protection (decorator not applied)
@users_bp.route("/api/users/profile", methods=["PUT"])
def update_profile_api():
    """Called by frontend JavaScript via fetch()."""
    data = request.get_json()
    user = get_current_user()
    user.display_name = data.get("display_name", user.display_name)
    user.bio = data.get("bio", user.bio)
    db.session.commit()
    return jsonify({"status": "updated"})
```

```python
# src/api/billing.py — CURRENT STATE (same pattern)
@billing_bp.route("/api/billing/payment-method", methods=["PUT"])
def update_payment_method():
    """Called by frontend JavaScript via fetch(). No CSRF protection."""
    data = request.get_json()
    update_card(get_current_user().id, data["card_token"])
    return jsonify({"status": "updated"})
```

**Quality-gate rejection reason:** The CSRF decorator was only applied to form-submission routes, not to AJAX API endpoints. Modern browsers allow cross-origin `fetch()` with `credentials: 'include'` and `Content-Type: application/json` — checking `Content-Type` is NOT a CSRF defense because the attacker controls request headers. The API endpoints at `/api/users/profile` and `/api/billing/payment-method` perform state-changing operations and must either require a CSRF token via custom header or validate the `Origin` header against an allowlist.

---

### Finding perf-rl-1: Rate limiting ineffective behind reverse proxy

**Original finding:** Login endpoint lacked rate limiting, enabling brute-force attacks.

**Prior fix attempt (REJECTED by quality-gate):**

The prior fix added `flask-limiter` with the default `get_remote_address` key function:

```python
# src/extensions.py — CURRENT STATE (after rejected fix)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="redis://localhost:6379/1",
    default_limits=["100 per hour"],
)
```

```python
# src/api/auth.py — CURRENT STATE (after rejected fix)
from src.extensions import limiter

@auth_bp.route("/api/auth/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    email = request.json.get("email")
    password = request.json.get("password")
    user = authenticate(email, password)
    if user:
        return jsonify({"token": generate_jwt(user)})
    return jsonify({"error": "Invalid credentials"}), 401
```

```nginx
# nginx.conf (production reverse proxy — included for context)
upstream app {
    server 127.0.0.1:5000;
}

server {
    listen 80;
    location / {
        proxy_pass http://app;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
    }
}
```

**Quality-gate rejection reason:** Behind the Nginx reverse proxy, `flask.request.remote_addr` is always `127.0.0.1` (the proxy's loopback address). Every user on the internet shares a single rate-limit bucket. An attacker who triggers the 5-request limit blocks ALL users from logging in. The `key_func` must extract the real client IP from `X-Real-IP` or `X-Forwarded-For` headers (which Nginx is already configured to set, as shown in `nginx.conf`).

---

### Finding sec-upload-1: File upload validation bypassed by extension spoofing

**Original finding:** File upload endpoint accepts any file content, enabling execution of uploaded malicious files.

**Prior fix attempt (REJECTED by quality-gate):**

The prior fix added extension-based validation:

```python
# src/api/uploads.py — CURRENT STATE (after rejected fix)
import os
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename

uploads_bp = Blueprint("uploads", __name__)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".pdf", ".csv"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@uploads_bp.route("/api/uploads", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    filename = secure_filename(file.filename)

    # Prior fix: extension-based validation (REJECTED — extension is user-controlled)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type {ext} not allowed"}), 400

    # Size check (this part is correct)
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        return jsonify({"error": "File too large"}), 400

    # Save file with user-supplied name
    upload_path = os.path.join(current_app.config["UPLOAD_DIR"], filename)
    file.save(upload_path)
    return jsonify({"filename": filename, "size": size}), 201
```

```
# requirements.txt (excerpt — python-magic is available)
flask==3.0.0
python-magic==0.4.27
werkzeug==3.0.1
sqlalchemy==2.0.25
```

**Quality-gate rejection reason:** Extension-based validation is trivially bypassed: renaming `exploit.php` to `exploit.jpg` passes the check. An attacker uploads a PHP web shell with a `.jpg` extension; if the upload directory is served by a misconfigured web server, the shell executes. The fix must validate actual file content by checking magic bytes (file signature). The `python-magic` library (already in `requirements.txt`) identifies true file type from content, not filename. Additionally, uploaded files should be stored with a generated UUID filename rather than the user-supplied name to prevent path traversal via crafted filenames.
