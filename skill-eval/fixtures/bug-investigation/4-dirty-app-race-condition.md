---
planted_issues:
  - toctou_root_cause: "TOCTOU race in auth/permissions.py — separate check and action queries"
red_herrings:
  - "Non-atomic rate limiter is a different issue, not the cause of permission flaps"
---

## Bug Report: Users intermittently lose permissions

**Reporter:** DevOps team
**Severity:** High
**Environment:** Production, gunicorn with 4 workers

### Symptom
Users with "editor" role intermittently get 403 Forbidden when creating tasks. The issue is sporadic — the same user can retry and succeed. More frequent during peak hours.

### Logs
Permission check passes for user_id=42 at 14:23:01.003, but action fails at 14:23:01.015 with "insufficient permissions". No role changes in the user_permissions table between these timestamps.

### Relevant Source Files

#### src/auth/permissions.py
{codebase:dirty-flask-app/src/auth/permissions.py}

#### src/middleware/rate_limit.py (also showing intermittent issues)
{codebase:dirty-flask-app/src/middleware/rate_limit.py}

#### src/auth/login.py
{codebase:dirty-flask-app/src/auth/login.py}
