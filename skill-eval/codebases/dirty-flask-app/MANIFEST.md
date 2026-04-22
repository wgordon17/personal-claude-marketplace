# Dirty Flask App — Planted Issue Manifest

> **WARNING:** This directory contains INTENTIONALLY VULNERABLE code for security
> review evaluation testing. Do NOT use these patterns in production code.

## Planted Issues

| # | File | Lines | Type | CWE | Severity | Subtlety | Skills | Distractor Note |
|---|------|-------|------|-----|----------|----------|--------|-----------------|
| 1 | src/auth/permissions.py | L22-35 | Security (TOCTOU) | CWE-367 | Critical | Adversarial | quality-gate, bug-investigation, fix, unfuck, speculative | Comment says "Atomic permission check"; `perform_privileged_action` re-queries user after the check |
| 2 | src/auth/login.py | L38-40 | Security (Session Fixation) | CWE-384 | High | Moderate | quality-gate, unfuck | Session is mutated in place after auth—no `session.clear()` or regeneration of session ID before writing user data |
| 3 | src/auth/tokens.py | L27-31 | Security (JWT Algorithm Confusion) | CWE-327 | Critical | Adversarial | quality-gate, unfuck, deep-research | Comment says "Enforce RS256 only" but `algorithms=` accepts HS256, RS256, and none |
| 4 | src/tasks/handlers.py | L26-30 | Performance (N+1 Query) | CWE-1073 | Medium | Moderate | quality-gate, fix, unfuck, swarm | `list_tasks()` fetches all tasks then accesses `task.comments` in a loop, triggering one SELECT per task via lazy load |
| 5 | src/tasks/export.py | L60-65 | Security (SSRF) | CWE-918 | Critical | Moderate | quality-gate, pr-review, bug-investigation, unfuck | `validate_webhook_url` only checks `startswith("https://")` — does not block 10.x, 172.16.x, 192.168.x, 127.0.0.1 |
| 6 | src/tasks/search.py | L36-43 | Security (Second-Order SQL Injection) | CWE-89 | Critical | Adversarial | quality-gate, pr-review, unfuck | `save_search_term` uses parameterized queries (safe); `search_tasks_by_saved_term` retrieves stored term and f-string interpolates it into raw SQL at L41 |
| 7 | src/middleware/rate_limit.py | L34-41 | Concurrency (Non-Atomic Counter) | CWE-362 | Medium | Adversarial | quality-gate, fix, unfuck, roadmap | Comment says "GIL protects this"; `get` then `set` on module-level dict is not atomic under threading or async; window reset at L37-38 is also a race |
| 8 | src/middleware/logging.py | L37-45 | Privacy (PII in Logs) | CWE-532 | High | Moderate | quality-gate, pr-review, fix, unfuck, roadmap | Comment says "DEBUG logging disabled in production"; code logs full request body (including passwords and tokens) whenever DEBUG level is enabled |
| 9 | src/utils/cache.py | L7-8 | Reliability (Unbounded Cache) | CWE-400 | Medium | Subtle | quality-gate, fix, unfuck | `_cache` dict has no TTL, no max size, no eviction policy; grows without bound under load; `cache_set` uses a lock but never evicts |
| 10 | tests/test_handlers.py | L74-80 | Testing (Meaningless Assertion) | CWE-617 | Low | Moderate | fix, unfuck | `test_create_task_response_shape` makes a real request then asserts `True` — always passes regardless of response content |
| 11 | tests/test_auth.py | — | Testing (Missing Edge Cases) | CWE-1091 | Low | Subtle | unfuck | Happy-path login/register/logout covered; missing: SQL injection in username, expired token acceptance, concurrent sessions, permission revocation mid-session |

## Clean Distractor Files

The following files contain **no planted vulnerabilities** and should NOT be flagged by a correct security review:

- `src/app.py` — Flask factory pattern, correct teardown, standard error handlers
- `src/db.py` — SQLAlchemy session management, standard init/teardown
- `src/models/user.py` — SQLAlchemy model with enum roles, no raw queries
- `src/models/task.py` — SQLAlchemy model with relationships, clean `to_dict`
- `src/models/comment.py` — SQLAlchemy model, uses parameterized filter in `visible_for_task`
- `src/utils/validators.py` — Input validation with regex, length checks, type checks; entirely clean
- `tests/conftest.py` — Correct test setup with rollbacks, no planted issues

## Issue Detail Notes

### Issue 1 — TOCTOU (permissions.py L22-35)
`check_permission()` reads `user.role` in one query (L25). `perform_privileged_action()` calls `check_permission()` then opens a new DB session and re-fetches the user (L43). Between these two DB round-trips, an admin could revoke the user's role. The misleading `# Atomic permission check` comment is placed directly above the non-atomic function signature.

### Issue 2 — Session Fixation (login.py L38-40)
After credential verification, the code writes to `session["user_id"]`, `session["role"]`, and `session["logged_in"]` without first calling `session.clear()` or equivalent. Flask's `session` object inherits the session ID from the pre-authentication request. An attacker who plants a session ID before login can adopt the authenticated session.

### Issue 3 — JWT Algorithm Confusion (tokens.py L27-31)
The comment at L24 says `# Enforce RS256 only — reject HS256/none to prevent algorithm confusion` but the actual `algorithms=` parameter at L30 accepts `["HS256", "RS256", "none"]`. Accepting `"none"` means a token with header `{"alg": "none"}` and no signature is accepted as valid. An attacker can forge arbitrary claims by signing with `alg=none`. The `create_token` function (L11-20) correctly uses HS256 only, making the discrepancy easy to miss. The misleading comment is the adversarial element — it claims the exact opposite of what the code does.

### Issue 4 — N+1 Query (handlers.py L26-30)
`list_tasks()` runs one query to fetch all tasks. The loop then accesses `task.comments` (L28-29) on each task object. Because `comments` is defined as `lazy="select"` on the Task model, SQLAlchemy issues one additional `SELECT` per task. For N tasks, this results in N+1 total database queries.

### Issue 5 — SSRF (export.py L60-65)
`validate_webhook_url()` checks only that the URL begins with `https://`. It does not resolve the hostname or block RFC-1918 addresses. An attacker can supply `https://127.0.0.1/...` or `https://192.168.1.1/...` to probe internal services. The `requests.get()` call at L65 executes unconditionally after the insufficient validation.

### Issue 6 — Second-Order SQL Injection (search.py L36-43)
`save_search_term()` (L15-25) safely parameterizes both the SELECT and INSERT using SQLAlchemy's `text()` with named bind parameters. `search_tasks_by_saved_term()` (L36-43) retrieves the stored term from the DB and then constructs the query via f-string at L41, interpolating the user-controlled value directly into raw SQL. The write path looks secure; only the read path is vulnerable.

### Issue 7 — Non-Atomic Counter Race (rate_limit.py L34-41)
The `# GIL protects this` comment is incorrect. The GIL prevents concurrent bytecode execution but does not make compound `get`/`set` operations atomic when context switches occur between them. Under load, two threads can both read `count = 0`, both write `count = 1`, and the effective increment is lost. The window reset at L37-38 has the same race.

### Issue 8 — PII in Logs (logging.py L37-45)
`_before_request()` logs the full raw request body at DEBUG level. Request bodies include plaintext passwords during `/auth/login` and Authorization headers during authenticated calls. The comment `# DEBUG logging disabled in production` is misleading — the code enables debug logging dynamically via `logger.isEnabledFor(logging.DEBUG)`, which can be enabled at runtime by log-level configuration even in production.

### Issue 9 — Unbounded Cache (cache.py L7-8)
`_cache` is a plain module-level dict. `cache_set()` acquires a lock and writes unconditionally. There is no TTL, no LRU eviction, no max-size limit, and no background cleanup. Under steady load the cache grows without bound, eventually exhausting process memory. The locking (L8) looks responsible and can mask the real problem.

### Issue 10 — Meaningless Assertion (test_handlers.py L74-80)
`test_create_task_response_shape` calls `client.post(...)` and then asserts `True`. The test always passes regardless of the actual HTTP status code or response body. It is disguised among correctly written tests in the same class.

### Issue 11 — Missing Edge Cases (test_auth.py)
`TestLogin` covers successful login, wrong password, unknown user, and missing fields. Missing coverage: (a) SQL/NoSQL injection payloads in username/password fields; (b) expired JWT tokens still being accepted; (c) two concurrent sessions for the same user; (d) user whose role is revoked mid-session still succeeding on subsequent requests.
