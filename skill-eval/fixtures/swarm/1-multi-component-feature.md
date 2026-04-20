---
scenario: multi-component-feature-oauth2
difficulty: hard
tests:
  - decomposition into 5+ components
  - parallel pipeline identification (Google/GitHub providers, web/mobile UI)
  - early serial phase for database migration
  - security design review triggered for auth-related work
---

## Task

Add OAuth2 login with Google and GitHub providers to the application. Users should be able to sign in with either provider from both the web and mobile apps. Existing username/password authentication must continue to work alongside OAuth2.

## Codebase Summary

### Project Structure

```
src/
  auth/
    middleware.py          # JWT validation middleware, checks Authorization header
    password_handler.py    # bcrypt-based password login (existing)
    session.py             # Session token creation and validation
  models/
    user.py                # User model: id, username, email, password_hash, created_at
    session.py             # Session model: id, user_id, token, expires_at
  api/
    routes.py              # REST API routes, imports auth middleware
  db/
    migrations/
      001_initial.sql      # Creates users and sessions tables
    connection.py          # Database connection pool (PostgreSQL)
web-app/
  src/
    components/
      LoginForm.tsx        # Username/password login form
      Header.tsx           # Navigation header with login/logout buttons
    hooks/
      useAuth.ts           # Auth state management hook
    api/
      auth.ts              # Auth API client (login, logout, refresh)
mobile-app/
  src/
    screens/
      LoginScreen.tsx      # Mobile login screen
      HomeScreen.tsx       # Mobile home screen with user info
    services/
      AuthService.ts       # Mobile auth service
    navigation/
      AuthNavigator.tsx    # Auth flow navigation stack
```

### Key Files

**src/auth/middleware.py** — JWT validation middleware applied to all protected routes:
```python
from functools import wraps
from flask import request, g
from src.auth.session import validate_token

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return {"error": "No token provided"}, 401
        user = validate_token(token)
        if not user:
            return {"error": "Invalid token"}, 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated
```

**src/models/user.py** — Current user model (no OAuth fields):
```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class User:
    id: int
    username: str
    email: str
    password_hash: str
    created_at: datetime
```

**src/db/migrations/001_initial.sql** — Existing schema:
```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    token VARCHAR(512) NOT NULL,
    expires_at TIMESTAMP NOT NULL
);
```

### Dependencies

- Flask 3.0, SQLAlchemy 2.0, PyJWT 2.8, bcrypt 4.1
- React 18, TypeScript 5.3, React Native 0.73
- PostgreSQL 16

### Constraints

- The existing password login flow must not break.
- Both OAuth providers must use the Authorization Code flow (not Implicit).
- OAuth tokens must be stored server-side, never exposed to the frontend.
- The user model needs to support accounts linked to multiple auth providers.
- Database migration must be backward-compatible (no dropping columns).
