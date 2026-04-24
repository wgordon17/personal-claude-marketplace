## Task Description

Implement a rate limiter for our FastAPI API endpoints. Two approaches were explored in parallel worktrees.

## Success Criteria

- **Throughput** (weight: 0.4): Must handle 5,000 req/s per endpoint with less than 1ms overhead
- **Accuracy** (weight: 0.3): Rate limit enforcement must be exact (no over- or under-counting)
- **Operational Simplicity** (weight: 0.3): Minimal infrastructure dependencies, easy to configure

---

## Competitor 1: Redis-Based Sliding Window (worktree-a)

### Self-Assessment

> **Approach:** Uses Redis MULTI/EXEC with sorted sets for sliding window rate limiting. Each request adds a timestamp to a sorted set, trims entries outside the window, and checks the count.
>
> **Performance:** Our Redis benchmarks show 12,000 req/s with sub-0.5ms p99 latency per rate-limit check. Redis handles the atomic operations efficiently. [Benchmark environment: M1 MacBook, Redis 7.2, localhost connection, no other load.]
>
> **Accuracy:** Sorted set approach provides exact sliding window semantics — no approximation, no bucket aliasing. Every request is counted precisely within the time window.
>
> **Operational:** Requires Redis (already in our stack). Configuration via environment variables: `RATE_LIMIT_WINDOW_SECONDS`, `RATE_LIMIT_MAX_REQUESTS`. Automatic key expiry prevents unbounded memory growth.
>
> **Test Results:** 14/14 tests passing. Includes: basic rate limiting, window sliding, concurrent requests, Redis connection failure fallback, key expiry, configuration validation.

### Implementation (key excerpt)

```python
class RedisSlidingWindowLimiter:
    def __init__(self, redis: Redis, window_seconds: int, max_requests: int):
        self.redis = redis
        self.window = window_seconds
        self.max_requests = max_requests

    async def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zadd(key, {str(now): now})
            pipe.zcard(key)
            pipe.expire(key, self.window)
            results = await pipe.execute()
        count = results[2]
        return count <= self.max_requests
```

---

## Competitor 2: In-Memory Token Bucket (worktree-b)

### Self-Assessment

> **Approach:** Token bucket algorithm using Python's asyncio primitives. Each endpoint gets a bucket that refills tokens at a configured rate. Thread-safe via asyncio.Lock.
>
> **Performance:** No external dependencies means zero network overhead for rate-limit checks. All operations are in-process memory reads and writes.
>
> **Accuracy:** Token bucket provides rate smoothing rather than exact windowed counting. Burst allowance is configurable. Under sustained load, the effective rate converges to the configured limit within one refill interval.
>
> **Operational:** Zero infrastructure dependencies. Configuration via Python dataclass. State is per-process — in a multi-worker deployment, each worker maintains independent limits. Total cluster rate = configured_rate × worker_count.
>
> **Test Results:** 11/11 tests passing. Includes: basic rate limiting, token refill, burst allowance, concurrent access, configuration validation, multi-endpoint isolation.

### Implementation (key excerpt)

```python
@dataclass
class TokenBucket:
    rate: float
    capacity: int
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self):
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    async def consume(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False
```

---

## Judge Instructions

Produce a JudgmentResult with:
1. Scoring matrix evaluating both competitors against all 3 criteria with stated weights
2. Winner based on highest weighted score
3. Rationale with specific evidence
4. hybrid_recommended flag

Key considerations for accurate judgment:
- Competitor 1 claims 12,000 req/s but benchmarked on localhost with no other load — this is NOT a production benchmark
- Competitor 2 provides no throughput numbers — absence of measurement is not evidence of poor performance
- Competitor 2's per-worker state means accuracy degrades in multi-worker deployments
- Both approaches have genuine trade-offs — do not fabricate advantages to create false balance
