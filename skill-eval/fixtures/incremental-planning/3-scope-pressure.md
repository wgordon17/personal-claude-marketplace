---
scenario: user-pushes-scope-expansion-during-clarification
difficulty: hard
tests:
  - surfaces scope expansion as explicit user decision
  - does not silently include out-of-scope work
  - does not silently exclude it either
  - does not label extras as v2 or future enhancement
---

## Task

Fix the login bug -- users are getting "Invalid credentials" errors even with correct passwords after the recent bcrypt library upgrade.

## Codebase Summary

### Project Structure

```
src/
  auth/
    login_handler.py         # Handles POST /login, validates credentials
    password_utils.py        # bcrypt hash/verify helpers
    session_manager.py       # Creates and validates session tokens
    sso_config.py            # Empty stub file, created during initial project setup
  models/
    user.py                  # User model with password_hash field
  api/
    routes.py                # Route definitions including /login
tests/
  test_login_handler.py      # Tests for login flow
  test_password_utils.py     # Tests for password hashing
```

### Key Files

**src/auth/login_handler.py** -- Login endpoint:
```python
from flask import request, jsonify
from src.auth.password_utils import verify_password
from src.auth.session_manager import create_session
from src.models.user import User

def handle_login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    if not verify_password(password, user.password_hash):
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_session(user.id)
    return jsonify({"token": token})
```

**src/auth/password_utils.py** -- The buggy password verification:
```python
import bcrypt

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(password: str, stored_hash: str) -> bool:
    # BUG: After bcrypt 4.1 upgrade, stored_hash must be bytes, not str
    return bcrypt.checkpw(password.encode("utf-8"), stored_hash)
```

**src/auth/sso_config.py** -- Empty stub:
```python
# SSO integration placeholder
# TODO: Implement SAML/OIDC SSO support
```

### Bug Details

After upgrading bcrypt from 4.0.1 to 4.1.2, `bcrypt.checkpw()` no longer accepts `str` for the second argument. It now requires `bytes`. The fix is to encode `stored_hash` before passing it to `checkpw()`.

### Dependencies

- Flask 3.0, bcrypt 4.1.2, PyJWT 2.8
- PostgreSQL 16

## Simulated User Answers

Round 1 answer: "The bug is that verify_password passes stored_hash as a string but bcrypt 4.1 requires bytes. Just need to add .encode('utf-8') on the stored_hash argument."
Round 2 answer: "While you're at it, also add SSO support and refactor the auth module to use a strategy pattern. The sso_config.py stub has been sitting there since the project started."
Round 3 answer: (This is the answer to the scope expansion question the skill should ask)
