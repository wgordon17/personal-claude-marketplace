---
planted_issues:
  - type: research_gap
    file: src/middleware/rate_limiter.py
    line: 22
    resolution: sliding_window_strategy
expected_findings: 1
---

## CODE REVIEW Findings

The following finding was identified by a domain reviewer and verified.

### PERFORMANCE

**Finding pr-perf-1:** Rate limiter uses fixed-window algorithm causing burst allowance at window boundaries
**Location:** `src/middleware/rate_limiter.py:22`
**Evidence:** The current implementation uses a fixed-window counter that resets at interval boundaries. A client can send `max_requests` at the end of one window and another `max_requests` at the start of the next window, effectively doubling the allowed rate. The `throttle-guard` library (v2.4+) supports sliding-window via a `strategy` parameter, but the current code does not use it.
Recommended resolution: /deep-research targeting [does throttle-guard v2.4 support sliding-window rate limiting and what is the correct strategy parameter value]

```python
# src/middleware/rate_limiter.py
import time
from collections import defaultdict
from dataclasses import dataclass, field

from throttle_guard import RateLimiter, RateLimitConfig


@dataclass
class RequestCounter:
    count: int = 0
    window_start: float = 0.0


class AppRateLimiter:
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.counters: dict[str, RequestCounter] = defaultdict(RequestCounter)
        self.limiter = RateLimiter(
            config=RateLimitConfig(
                max_requests=max_requests,
                window_seconds=window_seconds,
            )
        )

    def is_allowed(self, client_id: str) -> bool:
        now = time.monotonic()
        counter = self.counters[client_id]

        if now - counter.window_start >= self.window_seconds:
            counter.count = 0
            counter.window_start = now

        counter.count += 1
        return counter.count <= self.max_requests

    def get_remaining(self, client_id: str) -> int:
        counter = self.counters[client_id]
        remaining = self.max_requests - counter.count
        return max(0, remaining)

    def reset(self, client_id: str) -> None:
        if client_id in self.counters:
            del self.counters[client_id]
```

---

### Simulated /deep-research Output

The following research was gathered via /deep-research investigating the throttle-guard library:

**Research Report: throttle-guard Sliding Window Support**

**Sources checked:**
- throttle-guard v2.4.0 changelog (PyPI): Confirmed `strategy` parameter added in v2.4.0
- throttle-guard source code (GitHub): `RateLimitConfig` accepts `strategy: str` parameter
- throttle-guard documentation: Three strategies supported: `"fixed"` (default), `"sliding"`, `"token_bucket"`

**Key findings:**
1. The `RateLimitConfig` class accepts a `strategy` parameter since v2.4.0
2. Setting `strategy="sliding"` enables sliding-window rate limiting
3. The sliding-window implementation uses a sub-second precision counter that tracks requests across window boundaries
4. No additional configuration is required beyond setting the strategy parameter
5. The `RateLimiter` class internally handles the sliding-window logic when `strategy="sliding"` is set
6. The custom `RequestCounter` and manual window tracking in the current code are redundant when using the library's built-in rate limiting

**Conclusion:** The fix should set `strategy="sliding"` in the `RateLimitConfig` and delegate rate-limit checking to `self.limiter` instead of using the manual counter logic.
