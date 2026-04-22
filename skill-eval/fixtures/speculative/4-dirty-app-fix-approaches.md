---
approaches:
  - select_for_update: "Approach A — database-level locking"
  - single_query: "Approach B — merge check+action into one query"
user_criteria:
  correctness: 0.5
  performance_impact: 0.3
  code_complexity: 0.2
---

## Competing Approaches: Fix TOCTOU Race in Permission Check

### Problem
{codebase:dirty-flask-app/src/auth/permissions.py}

### Approach A: SELECT FOR UPDATE Locking
- Add `SELECT ... FOR UPDATE` to lock the user row during permission check
- Wrap check_permission() + perform_privileged_action() in a single transaction
- **Correctness:** Eliminates race completely — row-level lock prevents concurrent modification
- **Performance:** Adds lock contention under high concurrency; potential for deadlocks
- **Complexity:** Requires transaction management changes across callers

### Approach B: Single Combined Query
- Merge the permission check and action into a single SQL statement using a subquery
- `UPDATE tasks SET ... WHERE user_id IN (SELECT id FROM users WHERE role = 'editor')`
- **Correctness:** Eliminates race — atomic at the database level
- **Performance:** Single round-trip; no lock contention
- **Complexity:** More complex SQL; harder to add conditional logic later

### Evaluation Criteria (user-defined)
| Criterion | Weight |
|-----------|--------|
| Correctness | 0.5 |
| Performance impact | 0.3 |
| Code complexity | 0.2 |
