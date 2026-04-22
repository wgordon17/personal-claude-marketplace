---
planted_issues:
  - toctou_race: "TOCTOU in permissions.py — separate check and action queries"
  - jwt_confusion: "Algorithm confusion in tokens.py — accepts none/HS256"
  - non_atomic_counter: "Non-atomic counter race in rate_limit.py"
clean_distractors:
  - "validators.py input validation is correct — should NOT be flagged"
---

## Source Context

### src/auth/permissions.py
{codebase:dirty-flask-app/src/auth/permissions.py}

### src/auth/tokens.py
{codebase:dirty-flask-app/src/auth/tokens.py}

### src/middleware/rate_limit.py
{codebase:dirty-flask-app/src/middleware/rate_limit.py}

### src/utils/validators.py (clean — distractor)
{codebase:dirty-flask-app/src/utils/validators.py}
