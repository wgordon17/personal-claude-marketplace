---
planted_issues:
  - file_contention: "Multiple components modify auth/ files simultaneously"
  - cross_cutting: "Audit logging touches all handler files"
---

## Feature Request: Add audit logging to all auth operations

Add comprehensive audit logging for every authentication and authorization event. Log: user_id, action, timestamp, IP address, success/failure, and affected resource.

### Scope
- Login attempts (src/auth/login.py)
- Permission checks (src/auth/permissions.py)
- Token generation/validation (src/auth/tokens.py)
- Task operations requiring auth (src/tasks/handlers.py)
- Export operations with auth (src/tasks/export.py)

### Relevant Source Files

#### src/auth/login.py
{codebase:dirty-flask-app/src/auth/login.py}

#### src/auth/permissions.py
{codebase:dirty-flask-app/src/auth/permissions.py}

#### src/auth/tokens.py
{codebase:dirty-flask-app/src/auth/tokens.py}

#### src/tasks/handlers.py
{codebase:dirty-flask-app/src/tasks/handlers.py}

#### src/tasks/export.py
{codebase:dirty-flask-app/src/tasks/export.py}
