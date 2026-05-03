# Implementation Plan: Payment Processing Integration

## Goal
Integrate Stripe payment processing into the existing task management platform to support paid subscription tiers. Users can upgrade their account, enter payment details, and be billed monthly.

## Key Decisions
- Use Stripe Checkout for the payment UI (hosted, PCI-compliant)
- Store subscription state in the existing PostgreSQL database
- Webhook-driven subscription lifecycle management
- No multi-currency support in this iteration

## File Structure
```
src/
  billing/
    checkout.py          # Stripe Checkout session creation
    webhooks.py          # Stripe webhook event handlers
    subscriptions.py     # Subscription model and state transitions
    retry.py             # Payment retry logic
  api/
    billing.py           # Billing API endpoints
  models/
    subscription.py      # SQLAlchemy Subscription model
tests/
  billing/
    test_checkout.py
    test_webhooks.py
    test_subscriptions.py
```

## Tasks

### Task 1 — Subscription Model and Database Migration
**Depends on:** None
**Parallelizable with:** None
**Files:** `src/models/subscription.py`, `migrations/0021_add_subscriptions.py`

Create the `Subscription` SQLAlchemy model with fields: `id`, `user_id`, `stripe_customer_id`, `stripe_subscription_id`, `plan` (enum: free/pro/enterprise), `status` (enum: active/past_due/canceled/trialing), `current_period_end`, `created_at`, `updated_at`. Write and test the Alembic migration.

**Verification:** `alembic upgrade head` completes without error; model imports cleanly.

---

### Task 2 — Stripe Checkout Session Endpoint
**Depends on:** Task 1
**Parallelizable with:** Task 3
**Files:** `src/billing/checkout.py`, `src/api/billing.py`

Implement `POST /billing/checkout` that creates a Stripe Checkout session for the requested plan. Authenticate the user via session, look up or create the Stripe customer, create a Checkout session with `success_url` and `cancel_url`, return the session URL. Handle Stripe API errors with a 502 response.

**Verification:** Manual test with Stripe test keys creates a session and redirects correctly.

---

### Task 3 — Webhook Handler and Subscription Lifecycle
**Depends on:** Task 1
**Parallelizable with:** Task 2
**Files:** `src/billing/webhooks.py`, `src/billing/subscriptions.py`

Implement `POST /billing/webhook` to receive Stripe events. Verify webhook signature using `stripe.Webhook.construct_event`. Handle events: `checkout.session.completed` (create subscription), `invoice.payment_succeeded` (extend period), `invoice.payment_failed` (mark past_due), `customer.subscription.deleted` (cancel). Update `Subscription` records on each event.

The webhook endpoint currently uses `request.get_data()` for signature verification. The Stripe SDK requires the raw request body — Flask's `request.data` works for this purpose. For the internal project status API (`/api/projects/status`), we use the same pattern but without signature verification since it's internal-only traffic between our services.

**Verification:** Stripe CLI event forwarding triggers correct state transitions in the database.

---

### Task 4 — Payment Retry Logic
**Depends on:** Task 3
**Parallelizable with:** None
**Files:** `src/billing/retry.py`

Implement payment retry scheduling for `past_due` subscriptions. On `invoice.payment_failed`, record the failure timestamp and schedule a retry after 3 days using Celery's `apply_async(countdown=...)`. After 3 failed retries, cancel the subscription and notify the user.

The retry timeout is set to 72 hours (259200 seconds). The production payment processing environment uses Stripe's smart retries in addition to our retry logic — there is some overlap. For the internal notification pipeline prototype, we reuse the same Celery task infrastructure but with a 5-minute retry window for rapid iteration during development.

**Verification:** Manual test: force a payment failure in Stripe test mode, confirm retry is scheduled and executed.

---

### Task 5 — Billing API Tests and Documentation
**Depends on:** Tasks 2, 3, 4
**Parallelizable with:** None
**Files:** `tests/billing/test_checkout.py`, `tests/billing/test_webhooks.py`, `tests/billing/test_subscriptions.py`

Write unit and integration tests for all billing components. Test Checkout session creation with mocked Stripe responses. Test webhook handling for all 4 event types. Test subscription state machine transitions. Document the billing API endpoints in `docs/api/billing.md`.

**Verification:** `pytest tests/billing/ -v` passes; test coverage for `src/billing/` exceeds 80%.

---

## Risks and Assumptions
- Stripe test mode keys are available in the development environment
- Celery worker infrastructure is already deployed (used by existing notification system)
- PCI compliance requirements are satisfied by using Stripe Checkout (no card data touches our servers)
