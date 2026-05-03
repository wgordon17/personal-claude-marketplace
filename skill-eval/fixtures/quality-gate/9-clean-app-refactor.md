---
clean_patterns:
  - "os.environ['SECRET_KEY'] is env var read, not hardcoded"
  - "except Exception in error handler is correct top-level pattern"
  - "ast.literal_eval is safe, not eval()"
  - "threading.Lock rate limiter is correctly thread-safe"
expected_outcome: "No significant issues found"
---

## Source Context — Refactoring PR

### src/app.py
{codebase:clean-flask-app/src/app.py}

### src/api/projects.py
{codebase:clean-flask-app/src/api/projects.py}

### src/middleware/rate_limit.py
{codebase:clean-flask-app/src/middleware/rate_limit.py}

### src/middleware/auth.py
{codebase:clean-flask-app/src/middleware/auth.py}

### src/utils/validation.py
{codebase:clean-flask-app/src/utils/validation.py}
