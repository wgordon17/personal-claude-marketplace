---
scenario: security-sensitive-password-reset
difficulty: hard
tests:
  - triggers security design review phase
  - identifies token expiry as security consideration
  - identifies rate limiting requirement
  - identifies secure token generation
  - includes security reviewer in composition
---

## Task

Add a password reset flow with email verification. Users who forget their password should be able to request a reset link via email, click the link to verify their identity, and set a new password. The flow must be secure against common attack vectors.

## Codebase Summary

### Project Structure

```
src/
  auth/
    password_handler.py    # bcrypt password hashing and verification
    session.py             # Session token creation (uses uuid4)
    middleware.py           # JWT validation on protected routes
  models/
    user.py                # User model: id, username, email, password_hash
  services/
    email_service.py       # Sends transactional emails via SMTP
  api/
    auth_routes.py         # POST /login, POST /register endpoints
    user_routes.py         # GET /me, PUT /me/profile endpoints
  db/
    connection.py          # PostgreSQL connection pool
    migrations/
      001_users.sql        # Users table
      002_sessions.sql     # Sessions table
  templates/
    emails/
      welcome.html         # Welcome email template (existing)
tests/
  test_auth.py             # Tests for login and register
  test_email.py            # Tests for email sending (mocked SMTP)
```

### Key Files

**src/auth/password_handler.py** — Current password logic (no reset support):
```python
import bcrypt

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())
```

**src/services/email_service.py** — Existing email service (sends but does not template URLs):
```python
import smtplib
from email.mime.text import MIMEText
from src.config import SMTP_HOST, SMTP_PORT, FROM_ADDR

def send_email(to: str, subject: str, body_html: str) -> bool:
    msg = MIMEText(body_html, "html")
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = to
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.send_message(msg)
        return True
    except Exception:
        return False
```

**src/auth/session.py** — Session tokens use uuid4 (not cryptographically random):
```python
import uuid
from datetime import datetime, timedelta

def create_session_token(user_id: int) -> dict:
    return {
        "token": str(uuid.uuid4()),
        "user_id": user_id,
        "expires_at": datetime.utcnow() + timedelta(hours=1),
    }
```

### What Does NOT Exist

- No password reset table or model
- No rate limiting on any endpoint
- No security middleware (CSRF, brute-force protection)
- No token expiry enforcement beyond session tokens
- No email verification flow
- No cryptographic random token generation (only uuid4)

### Dependencies

- Python 3.13, Flask 3.0, bcrypt 4.1, PyJWT 2.8
- PostgreSQL 16
- No rate limiting library installed
- No CSRF protection library installed

### Constraints

- Reset tokens must expire within a configurable time window.
- Each reset token must be single-use (invalidated after password change).
- The reset endpoint must not reveal whether an email exists in the system.
- Reset requests must be rate-limited to prevent abuse.
- Reset tokens must be generated using cryptographically secure randomness.
- The email must contain a URL with the token, not the token in plain text.
