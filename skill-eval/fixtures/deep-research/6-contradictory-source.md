---
scenario: "PostgreSQL connection pool sizing — 4 sources with conflicting recommendations due to different workload assumptions"
notes:
  - "Source 2 (benchmark study) recommends pool_size=5 — but tested single-table OLTP only, no cross-table joins"
  - "Sources 1, 3, 4, 5 converge on pool_size=10-20 for mixed workloads"
  - "The application has 35% multi-table JOIN reports (high query variance) — Source 2's assumptions don't apply"
  - "Goal: verify skill addresses ALL sources including the outlier, not silently dropping it"
  - "Goal: verify skill explains WHY Source 2 differs (workload assumptions), not just its conclusion"
---

# Research Task: PostgreSQL Connection Pool Sizing

## Context

We are configuring SQLAlchemy's connection pool for a Flask application serving ~500 concurrent users at peak load. The application runs on 4 worker processes (gunicorn). Our database is a single PostgreSQL 15 instance with `max_connections = 200`.

We collected 4 sources on optimal `pool_size` configuration. Synthesize the findings and provide a recommendation.

---

## Source 1 — SQLAlchemy Official Documentation

**Source:** SQLAlchemy 2.0 Engine Configuration Guide (docs.sqlalchemy.org)
**Authority:** Primary source; written by SQLAlchemy maintainers

> For web applications, a reasonable starting point is `pool_size` equal to the number of threads or async tasks your application handles concurrently per worker process. For synchronous WSGI applications, this is typically the number of threads per worker (often 1 for single-threaded workers like gunicorn with sync workers).
>
> For a deployment with N worker processes, total connections = N × pool_size + N × max_overflow. Ensure this does not exceed the database's max_connections.
>
> **Recommended formula:** `pool_size = ceil(max_connections / num_workers) × 0.75` (leaving headroom for admin connections and replication).
>
> For a 4-worker deployment against a 200-connection PostgreSQL instance: `pool_size = floor(200 / 4 × 0.75) = 37`. In practice, most applications use 10-20 to leave additional headroom.
>
> **Recommended default: pool_size=20** per worker for a general-purpose web application.

---

## Source 2 — Database Connection Pool Benchmark Study

**Source:** "Right-sizing Database Connection Pools" — benchmark study published on the PgAnalyze engineering blog, 2023
**Methodology:** Load-tested 6 PostgreSQL deployments (RDS db.t3.medium to db.r6g.4xlarge) with synthetic workloads; measured throughput and latency at varying pool sizes across multiple configurations

> **Finding:** For I/O-bound workloads on modest hardware (≤8 vCPUs), connection pool sizes above 5-8 per worker process produce diminishing returns and can *increase* latency due to PostgreSQL connection overhead and context switching.
>
> The "database concurrency ceiling" — the point at which adding more connections stops increasing throughput — was reached at approximately `2 × num_db_vCPUs` total connections across all workers.
>
> For a db.t3.medium (2 vCPUs), optimal total connections ≈ 4-6, meaning pool_size=1-2 per worker for a 4-worker deployment.
>
> For a db.r6g.large (2 vCPUs, higher memory), optimal total connections ≈ 4-8.
>
> The study tested 6 hardware tiers across 4 workload profiles. Each profile ran for 30 minutes after a 5-minute warmup. Results were aggregated across 3 runs per configuration. The benchmark harness used pgbench with custom query scripts limited to single-table operations — primary key lookups and single-row INSERTs — to isolate connection overhead from query planning costs. Cross-table joins were excluded from the benchmark to avoid conflating pool sizing effects with query optimizer behavior. Connection lifetime was set to 300 seconds (matching pgbouncer defaults), and each simulated client issued queries in a closed-loop pattern with no think time between operations.
>
> **Recommended: pool_size=5** for typical deployments on modest hardware.

---

## Source 3 — Production Case Study: e-commerce Platform

**Source:** Engineering blog post from a mid-size e-commerce platform (500K MAU), 2022
**Context:** Flask + gunicorn + PostgreSQL, 6 worker processes, RDS db.r5.xlarge (4 vCPUs)

> After experiencing connection exhaustion under Black Friday load, we profiled our connection pool behavior. We found that p99 query latency began increasing above pool_size=12 per worker on our hardware.
>
> Our query mix: ~60% simple SELECT by primary key, ~30% multi-table JOINs (reporting), ~10% write transactions. Average query duration: p50=8ms, p99=45ms.
>
> We settled on pool_size=10 with max_overflow=5 per worker. This provided sufficient headroom without overwhelming the database, and eliminated connection exhaustion events.
>
> **Recommended: pool_size=10** for mixed read/write workloads with moderate query complexity.

---

## Source 4 — Academic Paper on Connection Pool Optimization

**Source:** "Adaptive Connection Pool Management for Cloud-Native Databases" — published in IEEE Transactions on Cloud Computing, 2022
**Authors:** Researchers at ETH Zürich
**Methodology:** Analytical model + simulation of connection pool behavior under varying workload distributions

> Our analytical model predicts that optimal pool_size minimizes the sum of: (1) query wait time from pool exhaustion, and (2) database overhead from excess idle connections.
>
> For workloads with low query duration variance (coefficient of variation CV < 0.5) and stable concurrency, the optimal pool_size converges to approximately `sqrt(peak_concurrent_requests / num_workers)`.
>
> For workloads with high query duration variance (CV > 1.5) — characteristic of applications with mixed OLAP and OLTP queries — larger pool sizes (10-20) are required to absorb bursts.
>
> **Workload assumption in this paper:** Simulated workloads are derived from TPC-C benchmark patterns, which are short-duration OLTP transactions. The authors note that results may not generalize to read-heavy or analytical workloads.
>
> For a 4-worker application with 500 peak concurrent users (125 per worker): `pool_size = sqrt(125 / 1) ≈ 11`. However, if workload is primarily short OLTP transactions (CV < 0.5): `pool_size = sqrt(500 / 4) ≈ 11`, but their experimental results on TPC-C workloads show diminishing returns above 5 connections per worker.
>
> **Recommended: pool_size=5** for uniform short-duration OLTP workloads; higher for mixed or variable workloads.

---

## Source 5 — PostgreSQL Wiki: Number of Database Connections

**Source:** PostgreSQL Wiki — "Number Of Database Connections" (wiki.postgresql.org)
**Authority:** Community-maintained reference; curated by PostgreSQL core contributors and experienced DBAs

> The wiki recommends keeping the total number of active connections well below `max_connections` and suggests using a connection pooler (PgBouncer or pgpool-II) in front of PostgreSQL for applications with more than a handful of workers.
>
> For applications connecting directly (without a pooler), the wiki recommends setting the pool size to handle the expected concurrent query load, noting that PostgreSQL's per-connection memory overhead (~5-10MB per connection depending on `work_mem` and `shared_buffers` configuration) limits the practical ceiling. On a server with 32GB RAM, the wiki suggests keeping total active connections under 100 "in most cases."
>
> The wiki explicitly warns against the common mistake of setting pool sizes too low: "Applications with mixed workloads — some fast, some slow queries — often undersize their pools based on average query duration, leading to pool exhaustion during slow-query bursts. Size the pool for your worst-case concurrent query count, not your average."
>
> For applications without a dedicated connection pooler, the wiki recommends: `pool_size = max(expected_concurrent_slow_queries_per_worker, 10)`, with `max_overflow` providing burst capacity.
>
> **Recommended: pool_size=10 minimum**, higher for workloads with significant slow-query tails.

---

## Our Application Profile

- Peak concurrent users: ~500
- Gunicorn: 4 workers, sync mode (1 thread per worker)
- Query mix: ~50% simple primary-key lookups (p50=3ms), ~35% multi-table JOIN reports (p50=40ms, p99=200ms), ~15% write transactions (p50=12ms)
- PostgreSQL: self-hosted on a VM with 8 vCPUs, 32GB RAM
- Current setting: pool_size=10, max_overflow=5 (set 18 months ago without measurement)
- Observed issue: occasional connection wait spikes under load (p99 wait > 50ms)
