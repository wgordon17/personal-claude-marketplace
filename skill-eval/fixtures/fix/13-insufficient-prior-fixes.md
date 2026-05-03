---
planted_issues:
  - type: string_escaping_not_parameterized
    description: Prior fix used string escaping instead of parameterized queries
    file: src/api/search.py
    line: 18
  - type: partial_lock_scope
    description: Prior fix added lock but only around the write, not the read-modify-write
    file: src/middleware/throttle.py
    line: 22
  - type: weak_hash_still_used
    description: Prior fix changed from MD5 to SHA1 — still cryptographically weak for tokens
    file: src/auth/session.py
    line: 15
expected_findings: 3
---

## Re-investigation Context

The following findings were identified by a `/pr-review`. A prior `/fix` attempt produced the changes shown below, but the `/quality-gate` rejected the fixes as insufficient. Re-investigate each finding and produce corrected fixes that actually resolve the root cause.

### Finding pr-sec-1: SQL injection in search endpoint

**Original finding:** `src/api/search.py:18` — User input interpolated directly into SQL via f-string.

**Prior fix attempt (REJECTED by quality-gate):**

The prior fix added input escaping instead of parameterized queries:

```python
# src/api/search.py — CURRENT STATE (after rejected fix)
import sqlite3
from flask import Blueprint, request, jsonify

search_bp = Blueprint("search", __name__)

DATABASE = "app.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


@search_bp.route("/api/search")
def search_items():
    query_term = request.args.get("q", "")
    # Prior fix: sanitize input (REJECTED — does not prevent all injection vectors)
    query_term = query_term.replace("'", "''").replace(";", "").replace("--", "")
    conn = get_db()
    cursor = conn.cursor()
    sql = f"SELECT id, name, description FROM items WHERE name LIKE '%{query_term}%'"
    cursor.execute(sql)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)
```

**Quality-gate rejection reason:** String escaping is a denylist approach — it misses encoding-based bypasses (e.g., Unicode normalization, URL encoding). The fix must use parameterized queries.

---

### Finding pr-conc-1: Race condition in request throttle

**Original finding:** `src/middleware/throttle.py:22` — Non-atomic read-modify-write on shared counter dict.

**Prior fix attempt (REJECTED by quality-gate):**

The prior fix added a lock but with incorrect scope — the read still happens outside the lock:

```python
# src/middleware/throttle.py — CURRENT STATE (after rejected fix)
import time
import threading
from collections import defaultdict

_counts = defaultdict(list)
_lock = threading.Lock()
MAX_REQUESTS = 50
WINDOW = 60


def is_throttled(client_ip: str) -> bool:
    """Check if client has exceeded rate limit."""
    now = time.time()
    cutoff = now - WINDOW

    # Read and filter — happens OUTSIDE lock (still racy)
    _counts[client_ip] = [t for t in _counts[client_ip] if t > cutoff]
    current_count = len(_counts[client_ip])

    # Prior fix: lock only the append (REJECTED — read-filter-append must be atomic)
    with _lock:
        _counts[client_ip].append(now)

    return current_count >= MAX_REQUESTS
```

**Quality-gate rejection reason:** The lock only covers the append, but the read-filter-count sequence above it is still unprotected. A concurrent request can read stale counts. The entire read-modify-write must be inside the lock.

---

### Finding pr-sec-2: Weak session token generation

**Original finding:** `src/auth/session.py:15` — Session tokens generated with MD5, which is not collision-resistant.

**Prior fix attempt (REJECTED by quality-gate):**

The prior fix upgraded from MD5 to SHA1, which is also cryptographically deprecated:

```python
# src/auth/session.py — CURRENT STATE (after rejected fix)
import hashlib
import os
import time


def generate_session_token(user_id: int) -> str:
    """Generate a cryptographic session token."""
    raw = f"{user_id}:{time.time()}:{os.environ.get('APP_SECRET', 'dev-fallback')}"
    # Prior fix: upgraded from MD5 to SHA1 (REJECTED — SHA1 is also deprecated)
    return hashlib.sha1(raw.encode()).hexdigest()


def create_session(user_id: int, role: str) -> dict:
    """Create a new authenticated session."""
    token = generate_session_token(user_id)
    return {
        "user_id": user_id,
        "role": role,
        "token": token,
        "created_at": time.time(),
    }
```

**Quality-gate rejection reason:** SHA1 is deprecated for cryptographic use (NIST SP 800-131A). Session tokens should use `secrets.token_urlsafe()` or `os.urandom()` — they don't need to be derived from user data at all.
