import threading
import time
from collections import deque
from functools import wraps

from flask import jsonify, request


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque] = {}
        # Lock is required: GIL does not protect compound read-modify-write operations
        # (check length, remove stale, append) which must be atomic
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = deque()

            bucket = self._buckets[key]

            # Evict timestamps outside the sliding window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                return False

            bucket.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


_default_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60.0)
_auth_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)


def rate_limit(limiter: SlidingWindowRateLimiter = None):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            lim = limiter or _default_limiter
            key = request.remote_addr or "unknown"
            if not lim.is_allowed(key):
                return jsonify({"error": "Rate limit exceeded"}), 429
            return f(*args, **kwargs)

        return decorated

    return decorator


def auth_rate_limit(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.remote_addr or "unknown"
        if not _auth_limiter.is_allowed(key):
            return jsonify({"error": "Too many login attempts"}), 429
        return f(*args, **kwargs)

    return decorated
