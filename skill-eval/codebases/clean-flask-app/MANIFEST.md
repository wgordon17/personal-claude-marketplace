# Clean Flask App — Design Rationale

## Purpose
This codebase demonstrates genuine best practices for false-positive resistance testing.
When code review skills analyze this code, they should find NO significant issues.

## Design Decisions
| Pattern | File | Why It's Correct | Common False Positive |
|---------|------|------------------|-----------------------|
| os.environ["SECRET_KEY"] | src/app.py | Runtime env var read, fail-fast on missing | "Hardcoded secret key" |
| except Exception in error handler | src/app.py | Top-level Flask handler must catch all | "Broad exception" |
| ast.literal_eval() | src/middleware/auth.py | Safe for literal parsing, not eval() | "Use of eval" |
| threading.Lock in rate limiter | src/middleware/rate_limit.py | GIL doesn't protect compound read-modify-write | None (correct pattern) |
| Exception swallowing in notification | src/services/notification.py | Notification is best-effort, failure logged | "Swallowed exception" |
| algorithms=["RS256"] in jwt.decode | src/middleware/auth.py | Explicit algorithm prevents confusion attacks | None (correct pattern) |
| ipaddress.ip_address() SSRF check | src/utils/validation.py | Validates webhook URLs against internal ranges | None (correct pattern) |
| joinedload() for relationships | src/models/project.py | Prevents N+1 query pattern | None (correct pattern) |
| Session clear on login | src/api/auth.py | Prevents session fixation | None (correct pattern) |
| DEBUG: False default | src/app.py | Safe default, overridable via env | "Hardcoded debug flag" |
