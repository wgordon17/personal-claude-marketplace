---
scaffold:
  files:
    - requirements.txt
    - app/__init__.py
    - app/models.py
    - tests/conftest.py
  requirements:
    - flask
    - pytest
planted_issues:
  - Should choose PyJWT over manual JWT parsing
  - Should use decorator pattern for role checking
  - Should generate tokens with secrets.token_urlsafe() not custom hash
---

# Auth Plan: JWT Authentication with RBAC in Flask

## Goal
Add JWT-based authentication and role-based access control to the Flask app.
Users should be able to log in and receive a token. Protected endpoints should
reject unauthenticated requests and enforce role restrictions.

## Outcomes Required
- Login endpoint issues a signed JWT on valid credentials
- Token verification middleware rejects invalid or expired tokens
- Role checking enforces admin vs user access on protected routes
- Tokens are generated securely
- Tests cover the happy path and key failure cases

## Notes
The existing app uses Flask patterns with SQLAlchemy models. Follow project
conventions. The implementation details (library choice, abstraction structure,
decorator vs middleware approach) are up to you — focus on correctness and
security.
