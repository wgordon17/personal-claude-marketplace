---
planted_issues:
  - ssrf_root_cause: "SSRF in tasks/export.py — validation allows internal URLs"
---

## Bug Report: Export webhooks sometimes fail with connection refused

**Reporter:** Customer support
**Severity:** Medium

### Symptom
Some users report that task export webhook deliveries fail with "Connection refused." The webhook URLs look valid (https://). Failures seem to correlate with certain URL patterns.

### Example failing URLs
- https://10.0.1.50:8080/webhook
- https://172.16.0.1/api/notify
- https://192.168.1.100/hooks/tasks

### Relevant Source Files

#### src/tasks/export.py
{codebase:dirty-flask-app/src/tasks/export.py}

#### src/auth/permissions.py
{codebase:dirty-flask-app/src/auth/permissions.py}
