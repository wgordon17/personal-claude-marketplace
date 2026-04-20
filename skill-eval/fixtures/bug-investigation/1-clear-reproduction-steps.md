---
planted_issues:
  - type: sql_injection_via_string_interpolation
    file: src/auth/password_check.py
    line: 18
    description: "Password comparison uses string interpolation in SQL query instead of parameterized query"
expected_findings: 1
---

## Bug Report

**Title:** Login fails for users with special characters in password

**Reported by:** QA team

**Reproduction Steps:**
1. Create a user account with password `test'OR'1'='1`
2. Attempt to log in with the correct credentials
3. Login fails with HTTP 500 Internal Server Error
4. Users with simple alphanumeric passwords can log in without issue

**Expected behavior:** User should be able to log in regardless of special characters in password.

**Actual behavior:** Server returns 500 error. Application logs show `psycopg2.errors.SyntaxError: unterminated quoted string`.

**Environment:** Production (v2.4.1), PostgreSQL 15, Python 3.12 / Flask 3.0

---

## Codebase Context

### `src/auth/handlers.py`

```python
# src/auth/handlers.py
from flask import Blueprint, request, jsonify
from src.auth.password_check import verify_password
from src.auth.session import create_session_token

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = data.get("username", "")
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    user = verify_password(username, password)
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_session_token(user["id"])
    return jsonify({"token": token, "user_id": user["id"]}), 200
```

### `src/auth/password_check.py`

```python
# src/auth/password_check.py
import bcrypt
from src.db import get_connection


def verify_password(username: str, password: str) -> dict | None:
    """Verify user credentials and return user dict or None."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Look up the user by username
        query = (
            "SELECT id, username, password_hash FROM users "
            "WHERE username = '" + username + "'"
        )
        cursor.execute(query)
        row = cursor.fetchone()

        if row is None:
            return None

        user_id, uname, stored_hash = row
        if bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            return {"id": user_id, "username": uname}
        return None
    finally:
        conn.close()
```

### `src/auth/session.py`

```python
# src/auth/session.py
import secrets
from src.db import get_connection


def create_session_token(user_id: int) -> str:
    """Create a new session token for the given user."""
    token = secrets.token_urlsafe(32)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (user_id, token) VALUES (%s, %s)",
            (user_id, token),
        )
        conn.commit()
    finally:
        conn.close()
    return token
```
