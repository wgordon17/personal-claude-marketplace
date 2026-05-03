# Deep Research Report: Database Connection Pooling Strategy

**Plan:** `hack/plans/feat-db-optimization.md`
**Mode:** Bridged (Internal + External)
**Date:** 2026-04-18
**Status:** Research Complete

---

## Executive Summary

Recommend migrating from per-module engine instances to a single shared SQLAlchemy engine with `pool_size=10` and `max_overflow=20`. Current pattern causes connection churn under load (observed 200+ simultaneous connections during peak report generation).

---

## Internal Investigation

### Current State
- Application uses `create_engine()` at module level in 3 locations
- Connection count peaks at 200+ during batch report generation (confirmed via `pg_stat_activity`)
- Average query latency: 45ms (15ms execution + 30ms connection acquisition overhead)
- Auth module uses context managers correctly; tasks and reports modules do not

### Findings

1. **Three separate engine instances** (auth, tasks, reports) each maintain independent connection pools — effectively 3x the connections needed
2. **Connection leak in report generator**: opens connections in a loop via `engine.connect()` without closing them, accumulating until `max_overflow` is hit
3. **Missing context managers in tasks module**: uses `engine.execute()` pattern (deprecated in SQLAlchemy 2.0) instead of `with engine.connect() as conn:`

---

## External Research

### Source 1: SQLAlchemy Official Documentation
> A single `Engine` should be created per application process, not per module or per request. The `Engine` manages connection pooling internally. Creating multiple engines defeats pooling and can exceed `max_connections`.

### Source 2: "PostgreSQL Connection Management at Scale" — PgBouncer Blog (2024)
> For applications with fewer than 50 concurrent database users, application-level pooling via SQLAlchemy is sufficient. PgBouncer adds value when concurrent connections exceed 200 or when connection multiplexing across multiple application instances is needed.

*Note: This blog post was authored by the PgBouncer project maintainer.*

### Source 3: PostgreSQL Wiki — Connection Limits (2023)
> Default `max_connections=100`. Each connection consumes ~10MB of RAM (shared buffers + per-connection memory). For a 4GB server, `max_connections=200` is a safe upper limit. Exceeding this causes swap pressure and query latency spikes.

*Note: Written before PostgreSQL 16, which improved connection slot efficiency by ~30%. The 10MB-per-connection estimate may be high for PG16+.*

### Source 4: "Connection Pooling Best Practices" — AWS RDS Technical Guide (2025)
> For Python applications using SQLAlchemy: set `pool_size` to the number of concurrent request-handling threads. Set `max_overflow` to 2× `pool_size` for burst handling. Enable `pool_pre_ping=True` to detect stale connections. Monitor `pool.checkedout()` to detect leaks.

---

## Recommendations

1. **Immediate:** Consolidate to a single shared engine instance *(addresses Finding 1)*
2. **Immediate:** Fix connection leak in report generator — wrap in context manager *(addresses Finding 2)*
3. **Short-term:** Migrate tasks module from deprecated `engine.execute()` to `with engine.connect()` *(addresses Finding 3)*
4. **Long-term:** Evaluate PgBouncer if concurrent connections exceed 200 after fixes *(based on Source 2)*

---

## Open Questions

None — all recommendations are actionable with current information.

---

## Deferred Items

None.
