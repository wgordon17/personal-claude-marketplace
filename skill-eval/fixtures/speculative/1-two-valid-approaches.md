---
scenario: two-valid-approaches-api-caching
difficulty: hard
notes:
  - "Both approaches are genuinely viable with real trade-offs"
  - "Neither approach dominates — the judge must apply weighted scoring faithfully"
  - "Tests whether the judge uses the stated criteria weights, not personal preference"
---

## Problem

Implement caching for the API layer of a Flask-based REST service. The service handles ~500 requests/second with a PostgreSQL backend. Response times for the `/api/products` endpoint average 200ms due to complex joins. The goal is to reduce p95 latency to under 50ms for cached responses.

### Existing Codebase

```
src/
  api/
    products.py            # Product listing endpoint with filtering
    categories.py          # Category tree endpoint
  models/
    product.py             # SQLAlchemy Product model with 6 relationships
  services/
    product_service.py     # Business logic layer, called by API handlers
  config.py                # App configuration (DATABASE_URL, etc.)
tests/
  test_products_api.py     # 12 existing endpoint tests
  test_product_service.py  # 8 service layer tests
```

### Success Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Performance | 0.4 | Cache hit latency, throughput improvement, memory efficiency |
| Simplicity | 0.3 | Lines of code added, conceptual overhead, ease of understanding |
| Maintainability | 0.3 | Ease of cache invalidation, debugging, monitoring, deployment |

---

## Competitor A: Redis Distributed Cache

### ImplementationResult

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "competitor-1",
  "status": "complete",
  "approach": "Redis-based distributed caching using redis-py with a custom cache decorator. Cache keys are derived from endpoint path + query parameters with SHA-256 hashing. TTL-based expiration with configurable per-endpoint TTLs. Includes cache warming on startup for top-100 product queries. Uses Redis Cluster for horizontal scaling.",
  "files_created": [
    "src/cache/redis_client.py",
    "src/cache/decorators.py",
    "src/cache/warming.py",
    "src/cache/invalidation.py",
    "src/cache/monitoring.py",
    "tests/test_cache_redis.py",
    "tests/test_cache_warming.py"
  ],
  "files_modified": [
    "src/api/products.py",
    "src/api/categories.py",
    "src/config.py",
    "requirements.txt"
  ],
  "test_results": {
    "tests_run": 34,
    "tests_passed": 34,
    "tests_failed": 0,
    "command": "uv run pytest tests/ -v"
  },
  "self_assessment": [
    {
      "criterion": "Performance",
      "score": 9,
      "rationale": "Redis achieves sub-1ms cache hit latency. Cache warming ensures top queries are hot on deploy. Cluster mode supports horizontal scaling beyond single-node memory limits. Benchmarked at 12,000 cached req/s on a single connection pool."
    },
    {
      "criterion": "Simplicity",
      "score": 5,
      "rationale": "Adds 7 new files and requires Redis infrastructure (server, connection pool, cluster config). The decorator pattern is clean but the warming and invalidation modules add conceptual overhead. Developers must understand Redis data structures and TTL semantics."
    },
    {
      "criterion": "Maintainability",
      "score": 7,
      "rationale": "Cache invalidation uses event-based invalidation tied to SQLAlchemy after_commit signals. Monitoring module exposes hit/miss rates via Prometheus metrics. Debugging requires Redis CLI knowledge. Deployment requires provisioning a Redis instance."
    }
  ],
  "trade_offs": "Adds infrastructure dependency (Redis server). Connection pool management adds failure modes (Redis down = degraded service, not outage, due to fallback-to-DB pattern). Cache warming startup adds 3-5 seconds to cold start. 7 new files increase codebase surface area.",
  "failure_reason": null,
  "turn_count": 28
}
```

### Code Diff (selected)

```diff
diff --git a/src/cache/decorators.py b/src/cache/decorators.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/cache/decorators.py
@@ -0,0 +1,42 @@
+import functools
+import hashlib
+import json
+
+from flask import request
+
+from src.cache.redis_client import get_redis
+
+
+def cached(ttl_seconds: int = 300, prefix: str = "api"):
+    def decorator(fn):
+        @functools.wraps(fn)
+        def wrapper(*args, **kwargs):
+            cache_key = _build_key(prefix, request.path, request.args)
+            client = get_redis()
+            try:
+                hit = client.get(cache_key)
+                if hit is not None:
+                    return json.loads(hit), 200
+            except Exception:
+                pass  # Redis down — fall through to DB
+
+            result = fn(*args, **kwargs)
+            try:
+                client.setex(cache_key, ttl_seconds, json.dumps(result))
+            except Exception:
+                pass  # Redis down — skip caching
+            return result
+        return wrapper
+    return decorator
+
+
+def _build_key(prefix: str, path: str, args: dict) -> str:
+    raw = f"{prefix}:{path}:{json.dumps(dict(sorted(args.items())), sort_keys=True)}"
+    return f"cache:{hashlib.sha256(raw.encode()).hexdigest()}"
+
+
+def invalidate_prefix(prefix: str):
+    client = get_redis()
+    for key in client.scan_iter(f"cache:{prefix}:*"):
+        client.delete(key)
```

```diff
diff --git a/src/api/products.py b/src/api/products.py
index 3a1b2c3..4d5e6f7 100644
--- a/src/api/products.py
+++ b/src/api/products.py
@@ -1,5 +1,6 @@
 from flask import Blueprint, jsonify, request
 from src.services.product_service import ProductService
+from src.cache.decorators import cached

 products_bp = Blueprint("products", __name__, url_prefix="/api/products")

@@ -7,6 +8,7 @@ products_bp = Blueprint("products", __name__, url_prefix="/api/products")

 @products_bp.route("/", methods=["GET"])
+@cached(ttl_seconds=120, prefix="products")
 def list_products():
     filters = {
         "category": request.args.get("category"),
```

---

## Competitor B: In-Memory LRU Cache

### ImplementationResult

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "competitor-2",
  "status": "complete",
  "approach": "In-memory LRU cache using functools.lru_cache with a thin wrapper for TTL support. Cache is per-process (each gunicorn worker has its own cache). Invalidation uses a version counter pattern — incrementing the counter effectively invalidates all entries without clearing memory immediately. No external dependencies required.",
  "files_created": [
    "src/cache/memory_cache.py",
    "tests/test_cache_memory.py"
  ],
  "files_modified": [
    "src/api/products.py",
    "src/api/categories.py"
  ],
  "test_results": {
    "tests_run": 26,
    "tests_passed": 26,
    "tests_failed": 0,
    "command": "uv run pytest tests/ -v"
  },
  "self_assessment": [
    {
      "criterion": "Performance",
      "score": 7,
      "rationale": "In-memory lookups are fast (sub-microsecond) but cache is per-worker, so with 4 gunicorn workers, memory usage is 4x and cold-start requires each worker to populate independently. No shared cache means cache hit rate is lower during initial ramp-up. maxsize=1024 entries per endpoint caps memory but limits effectiveness for long-tail queries."
    },
    {
      "criterion": "Simplicity",
      "score": 9,
      "rationale": "Adds 1 new file (120 lines) plus 2 lines per cached endpoint. No external dependencies. Uses Python stdlib (functools.lru_cache). Any Python developer understands the approach immediately. Total diff is under 160 lines."
    },
    {
      "criterion": "Maintainability",
      "score": 6,
      "rationale": "Version counter invalidation is simple but coarse — invalidates everything, not specific entries. No built-in monitoring (hit/miss rates require manual instrumentation). Debugging requires understanding per-worker isolation. Cannot share cache state across workers or deployments."
    }
  ],
  "trade_offs": "Cache is not shared across workers or instances — each worker maintains its own LRU cache, multiplying memory usage by worker count. Invalidation is all-or-nothing (version counter). No built-in observability. Cache is lost on worker restart. Not suitable if the service scales to multiple hosts.",
  "failure_reason": null,
  "turn_count": 14
}
```

### Code Diff (selected)

```diff
diff --git a/src/cache/memory_cache.py b/src/cache/memory_cache.py
new file mode 100644
index 0000000..b2c3d4e
--- /dev/null
+++ b/src/cache/memory_cache.py
@@ -0,0 +1,48 @@
+import functools
+import time
+from threading import Lock
+
+_version_lock = Lock()
+_versions: dict[str, int] = {}
+
+
+def get_version(prefix: str) -> int:
+    return _versions.get(prefix, 0)
+
+
+def bump_version(prefix: str) -> None:
+    with _version_lock:
+        _versions[prefix] = _versions.get(prefix, 0) + 1
+
+
+def ttl_lru_cache(maxsize: int = 1024, ttl_seconds: int = 300, prefix: str = "default"):
+    def decorator(fn):
+        _cache: dict[str, tuple[float, int, object]] = {}
+        _lock = Lock()
+
+        @functools.wraps(fn)
+        def wrapper(*args, **kwargs):
+            key = str((args, tuple(sorted(kwargs.items()))))
+            now = time.monotonic()
+            current_ver = get_version(prefix)
+
+            with _lock:
+                if key in _cache:
+                    ts, ver, val = _cache[key]
+                    if now - ts < ttl_seconds and ver == current_ver:
+                        return val
+
+            result = fn(*args, **kwargs)
+
+            with _lock:
+                if len(_cache) >= maxsize:
+                    oldest_key = min(_cache, key=lambda k: _cache[k][0])
+                    del _cache[oldest_key]
+                _cache[key] = (now, current_ver, result)
+
+            return result
+        return wrapper
+    return decorator
```

```diff
diff --git a/src/api/products.py b/src/api/products.py
index 3a1b2c3..5e6f7a8 100644
--- a/src/api/products.py
+++ b/src/api/products.py
@@ -1,5 +1,6 @@
 from flask import Blueprint, jsonify, request
 from src.services.product_service import ProductService
+from src.cache.memory_cache import ttl_lru_cache

 products_bp = Blueprint("products", __name__, url_prefix="/api/products")

@@ -7,6 +8,7 @@ products_bp = Blueprint("products", __name__, url_prefix="/api/products")

 @products_bp.route("/", methods=["GET"])
+@ttl_lru_cache(maxsize=1024, ttl_seconds=120, prefix="products")
 def list_products():
     filters = {
         "category": request.args.get("category"),
```
