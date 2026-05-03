---
plans:
  - plan_a: "Fix auth issues (must be first)"
  - plan_b: "Add audit logging (shares auth files with Plan A)"
  - plan_c: "Refactor middleware (shares rate_limit.py with Plan B)"
---

## Roadmap: Three Implementation Plans

### Plan A: Fix Authentication Vulnerabilities
**Priority:** Critical
**Files:** src/auth/permissions.py, src/auth/tokens.py, src/auth/login.py
**Tasks:**
1. Fix TOCTOU race in permissions.py
2. Enforce RS256-only in tokens.py
3. Add session regeneration in login.py

### Plan B: Add Audit Logging
**Priority:** High
**Files:** src/auth/login.py, src/auth/permissions.py, src/tasks/handlers.py, src/middleware/logging.py
**Tasks:**
1. Create audit log table and model
2. Add logging to auth operations (login.py, permissions.py)
3. Add logging to task operations (handlers.py)
4. Refactor existing logging middleware (logging.py)

### Plan C: Middleware Refactoring
**Priority:** Medium
**Files:** src/middleware/rate_limit.py, src/middleware/logging.py, src/app.py
**Tasks:**
1. Fix non-atomic counter in rate_limit.py
2. Consolidate logging middleware
3. Update app.py middleware registration

### File Contention Matrix
| File | Plan A | Plan B | Plan C |
|------|--------|--------|--------|
| src/auth/permissions.py | ✓ | ✓ | |
| src/auth/login.py | ✓ | ✓ | |
| src/auth/tokens.py | ✓ | | |
| src/middleware/logging.py | | ✓ | ✓ |
| src/middleware/rate_limit.py | | | ✓ |
