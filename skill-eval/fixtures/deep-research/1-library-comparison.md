---
# Fixture metadata (stripped by loader)
scenario: "Library comparison with clear winner given project constraints"
notes:
  - "Three libraries with distinct tradeoff profiles"
  - "Project constraints (FastAPI, Redis, per-user) clearly favor one option"
  - "Goal: verify skill makes a concrete recommendation, not 'it depends'"
  - "Simulated search results — no real URLs"
---

## Research Question

Which Python rate limiting library should we use for our API?

## Project Context

- **Framework:** FastAPI (async, ASGI)
- **Existing infrastructure:** Redis already deployed for session caching
- **Requirements:** Per-user rate limits (authenticated endpoints), 10,000 RPM capacity
- **Team:** 4 developers, moderate Python experience, no prior rate limiting implementation
- **Timeline:** Ship within 2 sprints

## Simulated Search Results

### Source 1: slowapi (PyPI, GitHub)

slowapi is a rate limiting library for Starlette/FastAPI, inspired by flask-limiter. It wraps the limits library.

- **GitHub stars:** 1,200
- **Last release:** 2025-08-14 (v0.1.10)
- **Dependencies:** limits, starlette
- **Storage backends:** In-memory, Redis, Memcached, MongoDB
- **Configuration:** Decorator-based (`@limiter.limit("5/minute")`)
- **Async support:** Full async support via ASGI middleware
- **Per-user support:** Yes, via key_func parameter (e.g., `get_remote_address` or custom `get_user_id`)
- **Documentation:** Moderate — README covers basics, limited advanced examples
- **Known issues:** GitHub issue #142: memory leak with in-memory backend under high concurrency (fixed in 0.1.9). Issue #156: Redis sentinel support incomplete.

### Source 2: fastapi-limiter (PyPI, GitHub)

A rate limiting library specifically built for FastAPI with Redis backend.

- **GitHub stars:** 450
- **Last release:** 2024-11-02 (v0.1.6)
- **Dependencies:** redis, aioredis
- **Storage backends:** Redis only
- **Configuration:** Dependency injection via FastAPI Depends
- **Async support:** Native async (aioredis)
- **Per-user support:** Yes, via identifier callback
- **Documentation:** Minimal — README with 2 examples
- **Known issues:** GitHub issue #78: no fallback if Redis is down (raises 500). Issue #91: no sliding window support. Last maintainer commit: 2024-09.

### Source 3: limits (PyPI, ReadTheDocs)

Low-level rate limiting library. Powers slowapi and flask-limiter. Not framework-specific.

- **GitHub stars:** 800
- **Last release:** 2025-12-01 (v3.14.1)
- **Dependencies:** None (storage backends optional)
- **Storage backends:** In-memory, Redis, Memcached, MongoDB, Etcd
- **Configuration:** Programmatic API — no decorators, manual integration
- **Async support:** Sync only — requires wrapping in run_in_executor for async frameworks
- **Per-user support:** Manual implementation via namespaced keys
- **Documentation:** Comprehensive — full ReadTheDocs site with API reference
- **Known issues:** GitHub issue #301: async wrapper adds 2-5ms overhead per check. Well-maintained, consistent releases.

### Source 4: Blog Post — "Rate Limiting FastAPI in Production" (dev.to, 2025-10)

Author compared slowapi and fastapi-limiter for a SaaS product with 50K daily active users. Key findings:
- slowapi handled their load without issues after switching to Redis backend
- fastapi-limiter's Redis-only design was simpler but the stale aioredis dependency concerned them
- They chose slowapi because it offered fallback to in-memory when Redis was temporarily unavailable
- Recommended against raw limits library for FastAPI due to boilerplate

### Source 5: Stack Overflow Thread — "FastAPI rate limiting best practices" (2025-09, 45 upvotes)

Top answer recommends slowapi for most FastAPI projects. Notes that fastapi-limiter hasn't been updated in over a year. One commenter suggests using raw limits for maximum control. Another warns about fastapi-limiter's aioredis dependency being deprecated in favor of redis-py async.

### Source 6: GitHub Discussion — slowapi maintainer roadmap (2025-11)

Maintainer confirms plans for v0.2.0 with sliding window support and improved Redis sentinel. Active contributor base. Monthly release cadence.

### Source 7: Benchmark Results — "Python Rate Limiter Benchmarks" (GitHub repo, 2025-08)

| Library | Requests/sec (Redis) | P99 Latency | Memory (100K keys) |
|---------|---------------------|-------------|---------------------|
| slowapi | 8,500 | 3.2ms | 45MB |
| fastapi-limiter | 9,100 | 2.8ms | 38MB |
| limits (raw) | 11,200 | 1.9ms | 32MB |

Note: fastapi-limiter benchmark used aioredis; slowapi used redis-py async. Raw limits used sync with executor wrapping.
