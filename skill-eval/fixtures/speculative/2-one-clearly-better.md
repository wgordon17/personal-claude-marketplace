---
scenario: one-approach-dominates-n-plus-one
difficulty: hard
notes:
  - "Approach A clearly dominates across all criteria"
  - "Approach B is correct but inferior — tests whether judge avoids false balance"
  - "Judge should NOT recommend hybrid when one approach is strictly better"
---

## Problem

Fix the N+1 query in the user list endpoint. The `/api/users` endpoint loads all users, then for each user makes a separate query to load their department and role. With 200 users, the endpoint makes 401 queries (1 + 200 departments + 200 roles) and takes 1.2 seconds. Target: under 100ms with 3 or fewer queries.

### Existing Codebase

```
src/
  api/
    users.py               # User listing endpoint (N+1 problem here)
  models/
    user.py                # User model with department_id and role_id FKs
    department.py          # Department model
    role.py                # Role model
  services/
    user_service.py        # get_all_users() called by endpoint
tests/
  test_users_api.py        # 6 existing tests, none test query count
```

### Current Problematic Code

```python
# src/services/user_service.py
def get_all_users() -> list[dict]:
    users = db.session.query(User).all()
    result = []
    for user in users:
        result.append({
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "department": user.department.name,  # N+1: lazy load
            "role": user.role.title,             # N+1: lazy load
        })
    return result
```

### Success Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Correctness | 0.5 | Eliminates N+1, returns same data, handles edge cases |
| Performance | 0.3 | Query count, response time, scalability with user count |
| Readability | 0.2 | Code clarity, maintainability, follows SQLAlchemy conventions |

---

## Competitor A: select_related / joinedload

### ImplementationResult

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "competitor-1",
  "status": "complete",
  "approach": "Used SQLAlchemy's joinedload() option to eager-load department and role relationships in a single query with JOINs. The fix is a one-line change to the query plus a relationship configuration update. Total change: 4 lines modified across 2 files.",
  "files_created": [
    "tests/test_query_count.py"
  ],
  "files_modified": [
    "src/services/user_service.py",
    "src/models/user.py"
  ],
  "test_results": {
    "tests_run": 10,
    "tests_passed": 10,
    "tests_failed": 0,
    "command": "uv run pytest tests/ -v"
  },
  "self_assessment": [
    {
      "criterion": "Correctness",
      "score": 9,
      "rationale": "Single query with JOINs loads all data. Returns identical JSON output. Handles users with no department (LEFT OUTER JOIN). Added test_query_count.py verifying exactly 1 query for 200 users."
    },
    {
      "criterion": "Performance",
      "score": 9,
      "rationale": "Reduces 401 queries to 1. Measured response time: 18ms for 200 users (down from 1.2s). JOIN is optimized by PostgreSQL query planner with existing FK indexes."
    },
    {
      "criterion": "Readability",
      "score": 9,
      "rationale": "Uses idiomatic SQLAlchemy pattern (joinedload). Any developer familiar with SQLAlchemy ORMs recognizes this immediately. The fix is 4 lines — minimal cognitive overhead."
    }
  ],
  "trade_offs": "JOIN produces wider rows (denormalized), which uses slightly more memory during result set transfer. For very large result sets (10k+ users) with many relationships, subqueryload() might be more memory-efficient. Not relevant at the current 200-user scale.",
  "failure_reason": null,
  "turn_count": 11
}
```

### Code Diff

```diff
diff --git a/src/models/user.py b/src/models/user.py
index 1a2b3c4..5d6e7f8 100644
--- a/src/models/user.py
+++ b/src/models/user.py
@@ -1,4 +1,5 @@
 from src.db import db
+from sqlalchemy.orm import relationship


 class User(db.Model):
@@ -8,3 +9,5 @@ class User(db.Model):
     email = db.Column(db.String(255), unique=True, nullable=False)
     department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
     role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))
+    department = relationship("Department", lazy="joined")
+    role = relationship("Role", lazy="joined")
diff --git a/src/services/user_service.py b/src/services/user_service.py
index 2b3c4d5..6e7f8a9 100644
--- a/src/services/user_service.py
+++ b/src/services/user_service.py
@@ -1,7 +1,10 @@
+from sqlalchemy.orm import joinedload
+
 from src.db import db
 from src.models.user import User


 def get_all_users() -> list[dict]:
-    users = db.session.query(User).all()
+    users = db.session.query(User).options(
+        joinedload(User.department), joinedload(User.role)
+    ).all()
     result = []
```

```diff
diff --git a/tests/test_query_count.py b/tests/test_query_count.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/tests/test_query_count.py
@@ -0,0 +1,18 @@
+from unittest.mock import patch
+
+from src.services.user_service import get_all_users
+
+
+def test_no_n_plus_one(db_session, query_counter):
+    """Verify get_all_users uses at most 1 query regardless of user count."""
+    # Seed 200 users with departments and roles
+    _seed_users(db_session, count=200)
+
+    with query_counter as counter:
+        result = get_all_users()
+
+    assert len(result) == 200
+    assert counter.count == 1, (
+        f"Expected 1 query, got {counter.count}. "
+        f"N+1 regression detected."
+    )
```

---

## Competitor B: Raw SQL with Manual Mapping

### ImplementationResult

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "competitor-2",
  "status": "complete",
  "approach": "Replaced the ORM query with a raw SQL query using db.session.execute(). The SQL performs explicit LEFT JOINs on departments and roles tables, selecting only needed columns. Manual mapping converts each row tuple to a dict. Bypasses ORM relationship loading entirely.",
  "files_created": [
    "tests/test_raw_query.py"
  ],
  "files_modified": [
    "src/services/user_service.py"
  ],
  "test_results": {
    "tests_run": 9,
    "tests_passed": 9,
    "tests_failed": 0,
    "command": "uv run pytest tests/ -v"
  },
  "self_assessment": [
    {
      "criterion": "Correctness",
      "score": 8,
      "rationale": "Single raw SQL query returns correct data. Handles NULL departments/roles via LEFT JOIN. However, column selection is hardcoded — adding a new field requires updating the SQL string and the tuple index mapping."
    },
    {
      "criterion": "Performance",
      "score": 8,
      "rationale": "Single query, slightly faster than ORM JOIN due to bypassing ORM hydration overhead. Measured 14ms for 200 users. The difference vs joinedload is marginal (4ms) and unlikely to matter in practice."
    },
    {
      "criterion": "Readability",
      "score": 4,
      "rationale": "Raw SQL string embedded in Python is harder to maintain than ORM patterns. Column-to-dict mapping uses tuple indices (row[0], row[1], ...) which break if column order changes. No IDE autocompletion or type checking on query results."
    }
  ],
  "trade_offs": "Abandons ORM abstraction — raw SQL is tightly coupled to the database schema. Column additions require updating the SQL, the index mapping, and the test assertions. No type safety. Not portable across databases (PostgreSQL-specific syntax in string formatting). Harder for team members unfamiliar with raw SQL to maintain.",
  "failure_reason": null,
  "turn_count": 16
}
```

### Code Diff

```diff
diff --git a/src/services/user_service.py b/src/services/user_service.py
index 2b3c4d5..9c8d7e6 100644
--- a/src/services/user_service.py
+++ b/src/services/user_service.py
@@ -1,14 +1,27 @@
+from sqlalchemy import text
+
 from src.db import db
-from src.models.user import User


 def get_all_users() -> list[dict]:
-    users = db.session.query(User).all()
-    result = []
-    for user in users:
-        result.append({
-            "id": user.id,
-            "name": user.name,
-            "email": user.email,
-            "department": user.department.name,
-            "role": user.role.title,
-        })
-    return result
+    sql = text("""
+        SELECT u.id, u.name, u.email, d.name, r.title
+        FROM users u
+        LEFT JOIN departments d ON u.department_id = d.id
+        LEFT JOIN roles r ON u.role_id = r.id
+        ORDER BY u.id
+    """)
+    rows = db.session.execute(sql).fetchall()
+    return [
+        {
+            "id": row[0],
+            "name": row[1],
+            "email": row[2],
+            "department": row[3],
+            "role": row[4],
+        }
+        for row in rows
+    ]
```
