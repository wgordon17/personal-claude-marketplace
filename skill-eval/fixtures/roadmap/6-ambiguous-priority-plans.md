# Plan Set: Three High-Priority Plans

All three plans are marked high priority by the product team. They require sequencing based on dependencies and risk.

---

## Plan A: UI Redesign — Task Dashboard

**Priority:** HIGH (product team designation)
**Estimated effort:** 8 days
**User impact:** High — affects all 4,200 daily active users
**Technical risk:** Low — frontend-only changes, no backend modifications

**Goal:** Redesign the task dashboard with improved filtering, drag-and-drop reordering, and a new kanban view. Uses the existing REST API with no changes to API contracts or data models.

**File Structure:**
```
frontend/
  src/
    pages/
      Dashboard/
        index.tsx          # Main dashboard page (replaces current)
        KanbanView.tsx     # New kanban board component
        ListView.tsx       # Improved list view
        FilterPanel.tsx    # Advanced filter sidebar
    components/
      TaskCard/
        index.tsx          # Redesigned task card
        DragHandle.tsx     # Drag-and-drop handle
    hooks/
      useTaskBoard.ts      # State management for board views
      useDragDrop.ts       # Drag-and-drop logic
tests/
  frontend/
    Dashboard.test.tsx
    KanbanView.test.tsx
```

**Key decisions:**
- React DnD for drag-and-drop (already in package.json)
- No API changes — reads from existing `/tasks/` and `/projects/` endpoints
- Feature flag `ENABLE_NEW_DASHBOARD` gates rollout
- Kanban view groups tasks by their project status field (e.g., "not_started", "in_progress", "completed", "cancelled") — the grouping logic maps fine-grained status values into these display categories

**Dependencies within plan:** All tasks parallelizable after initial component scaffold.

**Cross-plan dependencies:**
- The dashboard reads task data using existing session auth. No JWT or new auth token handling required for the frontend.

---

## Plan B: Authentication Migration — Session to JWT

**Priority:** HIGH (product team designation)
**Estimated effort:** 10 days
**User impact:** Medium — transparent to most users; affects API consumers and mobile clients
**Technical risk:** High — touches authentication path for all requests; regression = all users locked out

**Goal:** Migrate from Flask session-based authentication to JWT tokens. Existing session auth will be deprecated. All API endpoints will accept `Authorization: Bearer <token>` in addition to the current session cookie during a transition period.

**File Structure:**
```
src/
  auth/
    jwt_auth.py            # JWT issuance, validation, refresh
    session_auth.py        # Existing session auth (kept for transition)
    middleware.py          # Updated to accept both JWT and session
  api/
    auth.py                # Updated login/logout to issue JWTs
  models/
    token_blacklist.py     # JWT revocation list (Redis-backed)
migrations/
  0023_add_token_blacklist.py
tests/
  auth/
    test_jwt_auth.py
    test_middleware.py
    test_token_blacklist.py
```

**Key decisions:**
- RS256 algorithm; public/private key pair generated per environment
- 1-hour access token TTL; 30-day refresh token
- Dual-auth transition period: both session and JWT accepted for 60 days
- Token blacklist stored in Redis for fast revocation lookup

**Dependencies within plan:** `jwt_auth.py` must be implemented before `middleware.py`. Migration must run before token blacklist model is used.

**Cross-plan dependencies:**
- The next available migration slot is `0023`. Any pending schema migrations should land first to maintain sequential migration history — out-of-order migrations complicate rollback procedures.
- JWT auth is a backend change. The frontend can be updated to send the `Authorization` header independently of the dashboard redesign.

**Risk note:** A regression in this plan would prevent all users from authenticating. Requires full rollback plan, feature flags, and staged rollout (5% → 25% → 100% of users). This is the highest-risk plan in the set.

---

## Plan C: Database Schema Change — Task Status Categories

**Priority:** HIGH (product team designation)
**Estimated effort:** 3 days
**User impact:** None directly (data migration only; no UI change in this plan)
**Technical risk:** High — schema migration on the `tasks` table (4.2M rows); requires zero-downtime migration strategy

**Goal:** Add a `task_status_categories` table and a computed `status_category` column to the `tasks` table. Status categories (e.g., "not_started", "in_progress", "completed", "cancelled") group the existing fine-grained status values for analytics filtering and downstream features that need status grouping.

**File Structure:**
```
migrations/
  0022_add_task_status_categories.py   # New table + backfill + column add
src/
  models/
    task_status_category.py            # New ORM model
    task.py                            # Updated with status_category relationship
tests/
  migrations/
    test_0022_migration.py             # Verifies migration on test data
  models/
    test_task_status_category.py
```

**Key decisions:**
- Zero-downtime migration: add column as nullable → backfill in batches → add NOT NULL constraint → add index
- Backfill script processes 10,000 rows/batch with 50ms sleep between batches (avoids table lock)
- `status_category` is a foreign key into `task_status_categories`; backfill maps existing status values to categories

**Dependencies within plan:** Migration must run before model is used. Backfill must complete before NOT NULL constraint is added.

**Cross-plan dependencies:**
- This plan has no upstream dependencies and can begin immediately.
- Migration `0022` is the next available slot. Any plans requiring a later migration slot should coordinate accordingly.

---

## Priority Summary

| Plan | Team priority label | User impact | Technical risk | Estimated effort |
|------|---------------------|-------------|----------------|-----------------|
| A — UI Redesign | HIGH | High | Low | 8 days |
| B — Auth Migration | HIGH | Medium | High (lockout risk) | 10 days |
| C — DB Schema | HIGH | None (transparent) | High (migration) | 3 days |

All three plans carry the same "HIGH" label from the product team. Sequencing should account for risk, effort, and any data or schema requirements across plans.
