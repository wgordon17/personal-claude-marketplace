## CODE REVIEW Findings

The following 4 findings were identified by domain reviewers and verified. All findings are valid and require remediation.

---

### Finding 1 — Severity: High
**Category:** Observability / Security
**Title:** Structured request logging missing across all authenticated endpoints
**Affected files:** `src/api/users.py`, `src/api/projects.py`, `src/api/tickets.py`, `src/api/admin.py`, `src/api/billing.py`, `src/api/reports.py`, `src/api/search.py`, `src/api/webhooks.py` (8 files)

**Evidence:** No authenticated endpoint currently emits a structured audit log entry on entry or exit. The access pattern for sensitive operations (admin actions, billing changes, data exports) is invisible to the security team. Recent incident review flagged this as a gap that would have reduced mean-time-to-detect by ~40 minutes in the last breach.

```python
# src/api/admin.py — representative example (same pattern in all 8 files)
@admin_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@require_admin
def deactivate_user(user_id: int):
    # No structured log here — only unstructured app.logger.info calls elsewhere
    user = get_user_by_id(db, user_id)
    if user is None:
        return jsonify({"error": "Not found"}), 404
    deactivate_user(db, user_id)
    return jsonify({"message": "Deactivated"}), 200
```

**Fix:** Add a `@audit_log` decorator to `src/middleware/audit.py` that emits a structured JSON log entry (user_id, endpoint, method, timestamp, response_status) using the existing `structlog` dependency already in `requirements.txt`. Apply the decorator to all 8 affected routes. The decorator pattern means zero changes to route logic — one decorator per route, one new middleware file.

---

### Finding 2 — Severity: Medium
**Category:** Performance
**Title:** User activity feed query uses Python-side aggregation instead of a SQL window function
**Affected files:** `src/tasks/activity.py:47-89`

**Evidence:** `get_activity_feed()` fetches all events for all projects a user belongs to (potentially thousands of rows), loads them into memory, then sorts and deduplicates in Python. Under realistic load (users with 20+ projects, 500+ events/day) this produces 50-200ms latency spikes and excessive memory allocation per request. Profiling shows this endpoint accounts for 34% of p99 latency.

```python
# src/tasks/activity.py:47-89
def get_activity_feed(user_id: int, db_session, limit: int = 50) -> list[dict]:
    # Fetch ALL events across ALL projects — no server-side filtering
    project_ids = [p.id for p in list_projects_for_user(db_session, user_id, per_page=500)]
    all_events = []
    for pid in project_ids:
        events = db_session.execute(
            text("SELECT * FROM activity_events WHERE project_id = :pid ORDER BY created_at DESC"),
            {"pid": pid},
        ).fetchall()
        all_events.extend(events)

    # Python-side sort and dedup — O(n log n) in memory
    all_events.sort(key=lambda e: e["created_at"], reverse=True)
    seen = set()
    deduped = []
    for e in all_events:
        key = (e["event_type"], e["entity_id"])
        if key not in seen:
            seen.add(key)
            deduped.append(dict(e._mapping))
    return deduped[:limit]
```

**Fix:** Replace the loop with a single parameterized query using `IN (:project_ids)` with `DISTINCT ON (event_type, entity_id)` (PostgreSQL) or a `ROW_NUMBER()` window function subquery. The query already has an index on `(project_id, created_at)`. No schema migration required — this is a query rewrite in `activity.py` only. Expected result: sub-5ms p99 for the same workload.

---

### Finding 3 — Severity: Medium
**Category:** Test Coverage
**Title:** `calculate_billing_total()` has no tests; edge cases are handled silently
**Affected files:** `src/billing/calculator.py:12-67`, `tests/` (no existing test file for billing)

**Evidence:** `calculate_billing_total()` contains 7 branches (pro-rating, discount stacking, currency rounding, trial period detection, overage calculation, tax exemption, and free-tier capping). None are exercised by any test. Two silent `return 0` fallbacks in the rounding and overage paths have masked production miscalculations in the past (see incident INC-2847).

```python
# src/billing/calculator.py:12-67
def calculate_billing_total(
    subscription: Subscription,
    usage_events: list[UsageEvent],
    period_start: date,
    period_end: date,
) -> Decimal:
    if subscription.is_trial:
        return Decimal("0.00")          # branch 1 — never tested
    base = subscription.plan.monthly_rate
    if subscription.discount_pct:
        base = base * (1 - subscription.discount_pct / 100)  # branch 2
    if subscription.is_prorated(period_start, period_end):
        days = (period_end - period_start).days
        base = base * Decimal(days) / 30  # branch 3
    overage = _calculate_overage(usage_events, subscription.plan)  # branch 4-5
    tax = _apply_tax(base + overage, subscription.tax_exempt)      # branch 6
    return _round_currency(base + overage + tax)                   # branch 7
```

**Fix:** Create `tests/billing/test_calculator.py` using `pytest` (already in `requirements-dev.txt`) and the existing `SubscriptionFactory` and `UsageEventFactory` fixtures in `tests/conftest.py`. Write parametrized tests for all 7 branches. No test infrastructure setup needed — the factories and fixtures already exist; this is a new test file only.

---

### Finding 4 — Severity: High
**Category:** Security
**Title:** Stripe webhook signature verification uses raw string comparison instead of `hmac.compare_digest`
**Affected files:** `src/api/webhooks.py:34-41`

**Evidence:** The Stripe webhook handler verifies the `Stripe-Signature` header by comparing a locally computed HMAC to the header value using Python's `==` operator. This is vulnerable to timing attacks — an adversary can infer signature bytes by measuring response time differences. Stripe's own documentation and SDK require constant-time comparison. The existing `hmac` module is already imported.

```python
# src/api/webhooks.py:34-41
@webhooks_bp.route("/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    expected = _compute_stripe_sig(payload, current_app.config["STRIPE_WEBHOOK_SECRET"])

    # Timing-vulnerable string comparison
    if sig_header != expected:
        return jsonify({"error": "Invalid signature"}), 400

    event = json.loads(payload)
    _dispatch_stripe_event(event)
    return jsonify({"received": True}), 200
```

**Fix:** Replace `if sig_header != expected:` with `if not hmac.compare_digest(sig_header.encode(), expected.encode()):`. The `hmac` module is already imported at the top of `webhooks.py`. This is a one-line change at `webhooks.py:40`. No vendor coordination required — this is a local code fix that conforms to Stripe's signature verification spec; the Stripe API endpoint and secret are unchanged.
