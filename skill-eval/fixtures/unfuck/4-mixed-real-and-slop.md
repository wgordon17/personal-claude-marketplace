---
planted_issues:
  - dead_import: "re is imported but never used"
  - catch_rethrow: "except Exception as e: raise e in fetch_user adds no context"
real_patterns_not_issues:
  - "get_active_users and get_inactive_users look similar but have different WHERE clauses and post-processing logic"
  - "ConnectionPool wrapper adds real value: max_connections, timeout, context manager protocol"
---

```python
import re
import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class ConnectionPool:
    """SQLite connection pool with bounded concurrency and timeout."""

    def __init__(self, db_path: str, max_connections: int = 5, timeout: float = 10.0):
        self._db_path = db_path
        self._max_connections = max_connections
        self._timeout = timeout
        self._active: list[sqlite3.Connection] = []

    @contextmanager
    def acquire(self):
        """Acquire a connection from the pool, blocking if at capacity."""
        if len(self._active) >= self._max_connections:
            raise RuntimeError(
                f"Connection pool exhausted (max={self._max_connections})"
            )
        conn = sqlite3.connect(self._db_path, timeout=self._timeout)
        self._active.append(conn)
        try:
            yield conn
        finally:
            conn.close()
            self._active.remove(conn)

    def active_count(self) -> int:
        """Return the number of currently active connections."""
        return len(self._active)


def fetch_user(pool: ConnectionPool, user_id: int) -> dict | None:
    """Fetch a single user by ID from the database."""
    with pool.acquire() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id, name, email, active FROM users WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
        except Exception as e:
            raise e
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "email": row[2], "active": bool(row[3])}


def get_active_users(pool: ConnectionPool) -> list[dict]:
    """Retrieve all active users, sorted by name."""
    with pool.acquire() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, email FROM users WHERE active = 1 ORDER BY name"
        )
        return [
            {"id": row[0], "name": row[1], "email": row[2]}
            for row in cursor.fetchall()
        ]


def get_inactive_users(pool: ConnectionPool, days_since: int = 90) -> list[dict]:
    """Retrieve inactive users who have been inactive for at least days_since days.

    Includes last_active timestamp for admin triage.
    """
    with pool.acquire() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, email, last_active FROM users "
            "WHERE active = 0 AND julianday('now') - julianday(last_active) >= ? "
            "ORDER BY last_active ASC",
            (days_since,),
        )
        return [
            {"id": row[0], "name": row[1], "email": row[2], "last_active": row[3]}
            for row in cursor.fetchall()
        ]
```
