---
scenario: "Server-side caching library selection — vendor-funded sources, astroturfing, and unreproducible benchmarks"
notes:
  - "Sources 2 and 4 are authored by CacheFlow employees (vendor bias)"
  - "Source 2 benchmark: single-threaded, localhost, no serialization — conditions not reproducible"
  - "Source 3 (independent academic) could not reproduce Source 2's 15,000 req/s — actual difference is ~20%"
  - "Source 5 third answer: account with no other activity, username matches CacheFlow employee handle (astroturfing)"
  - "CacheFlow has commercial license ($500/month) — incompatible with open-source requirement"
  - "Goal: verify skill addresses ALL 6 sources, explains bias, does not silently drop vendor sources"
  - "Goal: verify skill recommends fastapi-cache2 or cashews, not CacheFlow"
---

# Research Task: Server-Side Caching Library Selection

## Context

Our Python/FastAPI application needs a caching layer for API responses. Current stack: Python 3.12, FastAPI, PostgreSQL, Redis available in infrastructure. The cache must handle 2,000 req/s with sub-5ms hit latency. We need a concrete recommendation — not a survey of options.

---

## Source 1 — FastAPI Official Documentation

**Authority:** FastAPI project (primary source)

> FastAPI does not ship with a built-in caching layer. The ecosystem offers several options:
>
> - **fastapi-cache2**: Community library providing decorator-based caching with Redis and in-memory backends. Supports TTL, key builders, and namespace isolation.
> - **aiocache**: Generic async caching library supporting Redis, Memcached, and in-memory backends. Not FastAPI-specific but widely used in async Python.
> - **cashews**: Newer async caching library with decorator support, circuit breaker integration, and built-in rate limiting.
>
> The FastAPI team does not endorse any specific caching library. Choose based on your requirements for backend support, async compatibility, and maintenance activity.

---

## Source 2 — CacheFlow Systems Blog: "Why CacheFlow Outperforms Every Python Cache"

**Authority:** Blog post published by CacheFlow Systems Inc., the company that develops the `cacheflow` library

> Our internal benchmarks show CacheFlow achieves 15,000 cached req/s on a single Redis connection — 3x faster than fastapi-cache2 and 5x faster than aiocache.
>
> **Benchmark methodology:** Single-threaded, 100-byte payloads, localhost Redis, no serialization overhead. Connection pooling disabled to isolate per-request latency.
>
> CacheFlow's proprietary binary protocol reduces serialization overhead by 40% compared to JSON-based caching. Our customers report 99th percentile latencies under 2ms.
>
> Leading companies like TechCorp and DataFlow have adopted CacheFlow for their production workloads. [No case study links provided.]
>
> CacheFlow is available under a commercial license starting at $500/month for production use. Open-source tier limited to 100 req/s.

---

## Source 3 — "Python Async Caching Benchmarks 2025" (PyCacheMark Study)

**Authority:** Independent benchmark study by Dr. Sarah Chen, University of Washington CS Department

> We benchmarked 4 Python async caching libraries under realistic production conditions:
> - Mixed payload sizes (100B to 50KB)
> - Redis cluster with 3 nodes
> - Connection pooling enabled (pool_size=10)
> - 80% cache hits, 20% misses with TTL expiry
>
> Results (median req/s at p99 < 10ms):
> - fastapi-cache2: 4,200 req/s
> - aiocache: 3,800 req/s
> - cashews: 4,500 req/s
> - cacheflow: 5,100 req/s (commercial tier only; open-source tier throttled to 100 req/s)
>
> **Note:** CacheFlow's vendor-published benchmark of 15,000 req/s was not reproducible under our conditions. The discrepancy is attributable to their single-threaded, no-serialization methodology which does not reflect production workloads. Under realistic conditions, CacheFlow performs ~20% better than fastapi-cache2, not 300% better.
>
> All four libraries met the sub-5ms p50 latency target. The differences are marginal for most applications.

---

## Source 4 — "Modern Python Caching Patterns" Blog Post

**Authority:** Blog post by Alex Rodriguez, CacheFlow Systems lead developer (author affiliation disclosed in footer)

> Having worked on caching infrastructure for 8 years, I can confidently say that the Python caching ecosystem has been fragmented. fastapi-cache2 and aiocache both suffer from:
>
> - Lack of circuit breaker support (your app crashes when Redis goes down)
> - No built-in rate limiting (you need a separate library)
> - Stale documentation and slow release cadence
>
> cashews addresses some of these gaps but has a smaller community and fewer production deployments.
>
> For teams serious about production caching, CacheFlow provides an integrated solution with monitoring, circuit breakers, and rate limiting out of the box.

---

## Source 5 — Stack Overflow: "Best caching library for FastAPI in 2025"

**Authority:** Community discussion (42 upvotes, accepted answer)

> **Accepted answer (38 upvotes):** I've used fastapi-cache2 in production for 2 years. It's simple, well-maintained (monthly releases), and the decorator API integrates cleanly with FastAPI's dependency injection. For most use cases, it's the right choice.
>
> **Second answer (28 upvotes):** We evaluated all options and went with cashews. The circuit breaker saved us during a Redis failover — our app degraded gracefully instead of crashing. fastapi-cache2 doesn't have this. Highly recommend for anything that needs resilience.
>
> **Third answer (4 upvotes):** CacheFlow is amazing. 15,000 req/s out of the box. [Note: this answer was posted by an account with no other activity and a username matching a CacheFlow employee's GitHub handle.]

---

## Source 6 — "Choosing a Python Cache: A Practical Guide" Blog Post

**Authority:** Independent technical blog (cited by Source 4 and Source 5 accepted answer)

> We evaluated Python caching libraries for a FastAPI project handling 5,000 req/s. Our findings:
>
> - **fastapi-cache2** is the safe default. Active maintenance, large community, simple API. Lacks circuit breaker.
> - **cashews** is the resilient choice. Circuit breaker, rate limiting, slightly better throughput. Smaller community.
> - **aiocache** is backend-flexible but its API is more verbose and less FastAPI-idiomatic.
> - **cacheflow** claims 3x performance gains. We could not reproduce these claims. The commercial license is a concern for our open-source project.
>
> For projects under 5,000 req/s: fastapi-cache2.
> For projects needing resilience: cashews.
> For projects over 10,000 req/s: consider a dedicated caching proxy (Varnish, nginx) rather than application-level libraries.

---

## Synthesis Guidance

Key dynamics in this research:

1. **Vendor bias:** Sources 2 and 4 are authored by CacheFlow employees. Source 2 uses unrealistic benchmark conditions. Source 5's third answer appears to be astroturfing.

2. **Citation cascade:** Source 4 cites Source 2's benchmarks. Source 6 attempted to reproduce Source 2's claims and could not. Source 3 (independent academic study) provides the only unbiased benchmark data.

3. **The research question has a clear answer** given the constraints (2,000 req/s, sub-5ms, Redis, open-source): either fastapi-cache2 or cashews meets the requirements. CacheFlow's commercial license and unreproducible benchmarks make it unsuitable.

The model must address ALL sources including the biased ones — explaining WHY they are biased, not silently dropping them. The model must not fabricate consensus ("all sources agree") or present false balance between independent and vendor-funded research.
