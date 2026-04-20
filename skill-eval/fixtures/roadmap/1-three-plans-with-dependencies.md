---
# Fixture metadata (stripped by loader)
scenario: "Three plans with A->B->C chain dependency"
expected_phases: 3
expected_ordering: "A in Phase 1, B in Phase 2, C in Phase 3"
---

### Plan A: Add User Model

**Goal:** Define the core User ORM model and database migration for the application.
**Cynefin Domain:** Clear
**Architecture Summary:** Adds a new SQLAlchemy model with migration, no external dependencies.

## File Structure

```
src/
  models/
    user.py              # User ORM model (id, username, email, password_hash, role, created_at)
  migrations/
    versions/
      001_add_user.py    # Alembic migration for users table
tests/
  test_user_model.py     # Unit tests for User model
```

## Task 1: Define User Model

**Files:** `src/models/user.py`, `src/models/__init__.py`

Create SQLAlchemy model with fields: id (UUID primary key), username (unique, indexed), email (unique), password_hash, role (enum: user/admin), created_at, updated_at. Export from models package.

## Task 2: Create Database Migration

**Files:** `src/migrations/versions/001_add_user.py`

Generate Alembic migration for the users table. Include indexes on username and email columns.

## Task 3: Add User Model Tests

**Files:** `tests/test_user_model.py`

Test model creation, unique constraint validation, role enum values, and timestamp auto-population.

---

### Plan B: Add Auth Middleware

**Goal:** Implement JWT authentication middleware that validates tokens and loads the current user.
**Cynefin Domain:** Complicated
**Architecture Summary:** Middleware layer that imports the User model from Plan A to look up authenticated users by ID from the JWT payload.

## File Structure

```
src/
  middleware/
    auth.py              # JWT authentication middleware
  utils/
    token.py             # JWT encode/decode helpers
tests/
  test_auth_middleware.py # Middleware tests with mocked User lookups
```

## Task 1: Implement Token Utilities

**Files:** `src/utils/token.py`

Create encode_token(user_id, role) and decode_token(token) functions using PyJWT. Support expiration and role claims.

## Task 2: Build Auth Middleware

**Files:** `src/middleware/auth.py`

Create a Flask before_request handler that extracts the Bearer token, decodes it, and loads the User from the database using `src/models/user.py::User.query.get(user_id)`. Attach `g.current_user` for downstream handlers. Return 401 on invalid/expired tokens.

## Task 3: Add Auth Tests

**Files:** `tests/test_auth_middleware.py`

Test valid token flow, expired token rejection, missing token handling, and role extraction. Mock User.query.get to avoid database dependency in tests.

---

### Plan C: Add Admin Panel

**Goal:** Build an admin dashboard that uses auth middleware to enforce admin-only access.
**Cynefin Domain:** Complicated
**Architecture Summary:** Admin routes protected by the auth middleware from Plan B. Reads `g.current_user.role` set by the middleware to enforce admin-only access. Queries the User model from Plan A to list and manage users.

## File Structure

```
src/
  admin/
    routes.py            # Admin panel Flask blueprint
    templates/
      admin_dashboard.html  # Dashboard template
      user_list.html        # User management template
tests/
  test_admin_routes.py   # Admin route tests
```

## Task 1: Create Admin Blueprint

**Files:** `src/admin/routes.py`

Define Flask blueprint with routes: GET /admin/dashboard, GET /admin/users, POST /admin/users/:id/role. Import `g.current_user` from auth middleware context. Check `g.current_user.role == 'admin'` on every route. Query `User.query.all()` for user listing.

## Task 2: Build Admin Templates

**Files:** `src/admin/templates/admin_dashboard.html`, `src/admin/templates/user_list.html`

Create Jinja2 templates for the dashboard overview and user management pages.

## Task 3: Add Admin Route Tests

**Files:** `tests/test_admin_routes.py`

Test admin-only access enforcement (non-admin gets 403), user listing, and role update. Mock both the auth middleware and User model.
