# Plan Set: Three Independent System Plans

The following three plans are ready for roadmap sequencing. Each targets a different subsystem with no shared files.

---

## Plan A: Email Notification Service

**Goal:** Build a transactional email notification service using SendGrid for user account events (registration, password reset, project invitations).

**Priority:** Medium
**Estimated effort:** 5 days

**File Structure:**
```
src/
  notifications/
    email/
      client.py          # SendGrid API wrapper
      templates.py       # Email template rendering
      queue.py           # Redis-backed send queue
  api/
    notifications.py     # REST endpoints for notification preferences
tests/
  notifications/
    test_email_client.py
    test_email_templates.py
```

**Redis usage:** Task queue for async email delivery. Uses Redis list `email:queue` with LPUSH/BRPOP pattern. The queue worker runs as a separate Celery task in `src/workers/email_worker.py`.

**Tasks:**
1. Implement `SendGridClient` wrapper in `client.py` with send, batch_send, and delivery status methods
2. Create Jinja2 template system in `templates.py` for registration, password_reset, invitation templates
3. Implement Redis-backed send queue in `queue.py` with retry logic (max 3 attempts, exponential backoff)
4. Add notification preference API endpoints in `notifications.py`
5. Write tests and integration smoke tests

**Dependencies within plan:** Task 3 requires Task 1 (queue calls client). Tasks 1 and 2 are independent.

**Cross-plan dependencies:** None. Email service is self-contained.

---

## Plan B: Metrics Dashboard

**Goal:** Build a real-time metrics dashboard showing system health indicators: API response times, active user counts, task completion rates, and error rates. Data displayed in a React frontend panel.

**Priority:** Medium
**Estimated effort:** 6 days

**File Structure:**
```
src/
  metrics/
    collector.py         # Metric collection and aggregation
    store.py             # Redis time-series storage
    aggregator.py        # Windowed aggregation (1m, 5m, 1h buckets)
  api/
    metrics.py           # REST endpoints for dashboard queries
frontend/
  src/
    components/
      MetricsDashboard/
        index.tsx
        charts.tsx
        MetricsPanel.tsx
tests/
  metrics/
    test_collector.py
    test_aggregator.py
```

**Redis usage:** Time-series metric storage using Redis sorted sets (ZADD/ZRANGEBYSCORE). Keys follow pattern `metric:{name}:{bucket}` where bucket is a Unix timestamp rounded to the aggregation window. Entirely separate keyspace from any email queue keys.

**Tasks:**
1. Implement `MetricCollector` in `collector.py` — hooks into Flask request lifecycle via `after_request`
2. Implement time-series storage in `store.py` using Redis sorted sets
3. Implement windowed aggregation in `aggregator.py` for 1-minute, 5-minute, and 1-hour windows
4. Build dashboard API endpoints in `metrics.py` (current values, historical series, health summary)
5. Build React MetricsDashboard component with recharts line charts
6. Write unit and integration tests

**Dependencies within plan:** Task 2 requires Task 1. Task 3 requires Task 2. Task 4 requires Task 3. Task 5 requires Task 4.

**Cross-plan dependencies:** None. Metrics system uses its own Redis keyspace and its own API module.

---

## Plan C: CLI Tooling

**Goal:** Build a developer CLI tool (`taskcli`) for interacting with the task management API from the terminal. Provides commands for listing tasks, creating tasks, updating status, and exporting data.

**Priority:** Low
**Estimated effort:** 4 days

**File Structure:**
```
cli/
  taskcli/
    __init__.py
    client.py            # HTTP client wrapping the task management API
    commands/
      tasks.py           # list, create, update, delete commands
      export.py          # Export to CSV/JSON
      config.py          # CLI configuration management
    formatters.py        # Output formatting (table, JSON, CSV)
  tests/
    test_client.py
    test_commands.py
  pyproject.toml         # Separate package — does not share src/ with app
```

**Redis usage:** None. The CLI communicates with the task management API over HTTP. It does not connect to Redis directly.

**Tasks:**
1. Scaffold `taskcli` package with Click framework and `pyproject.toml`
2. Implement `TaskAPIClient` in `client.py` — wraps all API endpoints with auth token handling
3. Implement `tasks` command group in `commands/tasks.py` (list, create, update, status)
4. Implement `export` command in `commands/export.py` (CSV and JSON output via API)
5. Implement output formatters in `formatters.py` (rich table, plain JSON, CSV)
6. Write tests using `click.testing.CliRunner` and mocked HTTP responses

**Dependencies within plan:** Task 3 requires Task 2. Task 4 requires Task 2. Tasks 3 and 4 are parallelizable. Task 5 requires Tasks 3 and 4.

**Cross-plan dependencies:** None. The CLI is a separate package in `cli/` that makes HTTP calls to the existing API. It does not share source files with Plans A or B.

---

## Summary of False-Conflict Signals (All Are Non-Issues)

| Signal | Plan A | Plan B | Actual relationship |
|--------|--------|--------|---------------------|
| Redis usage | email queue (list) | time-series (sorted set) | Different keyspaces, different data structures, different purposes — no contention |
| Similar naming | `notifications.py` | `metrics.py` | Different directories: `src/api/notifications.py` vs `src/api/metrics.py` — no overlap |
| Both use Jinja2 | `templates.py` | — | Only Plan A uses Jinja2; Plan C uses Click |
| "client.py" appears twice | `email/client.py` | — | `cli/taskcli/client.py` — different packages, different directories |
| All use pytest | All | All | Test frameworks don't create dependencies |
