---
planted_issues:
  - second_order_injection: "Second-order SQL injection in search.py — user input stored safely but later interpolated via f-string into a raw SQL query"
  - ssrf: "SSRF with fake validation in export.py — URL validation only checks startswith('https://') without blocking internal/private IP ranges"
  - pii_in_logs: "PII logging in logging.py — request bodies containing passwords and tokens logged at DEBUG level"
clean_distractors:
  - "cache.py unbounded cache is a performance issue, not security"
---

## Source Context

### src/tasks/search.py
{codebase:dirty-flask-app/src/tasks/search.py}

### src/tasks/export.py
{codebase:dirty-flask-app/src/tasks/export.py}

### src/middleware/logging.py
{codebase:dirty-flask-app/src/middleware/logging.py}

### src/utils/cache.py
{codebase:dirty-flask-app/src/utils/cache.py}
