## Review Findings

The following findings were identified during a pr-review of recent changes to the Flask application. Address each finding with a specific fix.

### Finding 1: Inconsistent Error Logging Across Modules
**Category:** Code Quality
**Severity:** Medium
**Files:** src/auth/login.py:45, src/tasks/handlers.py:78, src/users/profile.py:112, src/billing/invoices.py:34, src/reports/generator.py:91, src/webhooks/dispatcher.py:56
**Description:** Six modules use different error logging patterns. Some use `logger.error(str(e))`, others use `logger.exception(e)`, and two use `print(f"Error: {e}")`. This makes log aggregation unreliable — structured logging requires consistent format across all modules.
**Recommended fix:** Standardize all error logging to use `logger.exception()` with consistent message format. Consider creating a `logError()` helper to enforce consistency.

### Finding 2: Missing Input Validation on Webhook URL
**Category:** Security
**Severity:** High
**Files:** src/webhooks/dispatcher.py:23
**Description:** The webhook dispatcher accepts user-provided URLs without validating the scheme or blocking private IP ranges. This enables SSRF attacks against internal services.
**Code:**
```python
def dispatch_webhook(url: str, payload: dict) -> bool:
    """Send webhook payload to the configured URL."""
    response = requests.post(url, json=payload, timeout=10)
    return response.status_code == 200
```

### Finding 3: Hardcoded Retry Count in Background Tasks
**Category:** Correctness
**Severity:** Medium
**Files:** src/tasks/handlers.py:15, src/billing/invoices.py:22, src/reports/generator.py:8, src/webhooks/dispatcher.py:41
**Description:** Four modules hardcode `max_retries=3` in their task decorators. This should be configurable per-environment (development uses 1 retry, production uses 5).
**Recommended fix:** Extract retry configuration into an environment variable and create a `configureRetries()` decorator factory in a shared utils module.

### Finding 4: Missing Type Hints on Public Functions
**Category:** Code Quality
**Severity:** Low
**Files:** src/webhooks/dispatcher.py:23, src/webhooks/dispatcher.py:45, src/webhooks/dispatcher.py:67
**Description:** Three public functions in the webhook dispatcher module lack return type annotations. Per ADR-052, all public functions should use type hints. Add `-> None` or appropriate return types.

## Codebase Context

The project uses the following conventions (observable in existing modules):

```python
# From src/auth/login.py — error handling pattern
try:
    user = authenticate(username, password)
except InvalidCredentialsError:
    logger.warning("Failed login attempt for user=%s", username)
    raise
except DatabaseConnectionError as exc:
    logger.exception("Database unavailable during login")
    raise ServiceUnavailableError("Authentication service unavailable") from exc

# From src/utils/validation.py — naming convention
def validate_email_format(email: str) -> bool:
    ...

def sanitize_user_input(raw_input: str) -> str:
    ...

def parse_webhook_url(url: str) -> ParseResult:
    ...
```

**Note:** The reviewer's recommended fixes in Findings 1 and 3 use camelCase naming (`logError()`, `configureRetries()`). The codebase consistently uses snake_case. The reviewer also references "ADR-052" in Finding 4 — no such ADR exists in this project.
