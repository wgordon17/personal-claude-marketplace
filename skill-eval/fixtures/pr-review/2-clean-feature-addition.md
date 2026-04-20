---
planted_issues: []
expected_findings: 0
---

diff --git a/src/cache/ttl_cache.py b/src/cache/ttl_cache.py
new file mode 100644
index 0000000..d4c8a19
--- /dev/null
+++ b/src/cache/ttl_cache.py
@@ -0,0 +1,68 @@
+from __future__ import annotations
+
+import threading
+import time
+from typing import Any
+
+
+class TTLCache:
+    """Thread-safe in-memory cache with per-key TTL and write-through invalidation."""
+
+    def __init__(self, default_ttl: float = 300.0, max_size: int = 1024) -> None:
+        self._store: dict[str, tuple[Any, float]] = {}
+        self._lock = threading.Lock()
+        self._default_ttl = default_ttl
+        self._max_size = max_size
+
+    def get(self, key: str) -> Any | None:
+        """Return cached value if present and not expired, else None."""
+        with self._lock:
+            entry = self._store.get(key)
+            if entry is None:
+                return None
+            value, expires_at = entry
+            if time.monotonic() > expires_at:
+                del self._store[key]
+                return None
+            return value
+
+    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
+        """Store a value with an optional per-key TTL override."""
+        effective_ttl = ttl if ttl is not None else self._default_ttl
+        expires_at = time.monotonic() + effective_ttl
+        with self._lock:
+            if len(self._store) >= self._max_size and key not in self._store:
+                self._evict_expired()
+                if len(self._store) >= self._max_size:
+                    oldest_key = min(self._store, key=lambda k: self._store[k][1])
+                    del self._store[oldest_key]
+            self._store[key] = (value, expires_at)
+
+    def invalidate(self, key: str) -> bool:
+        """Remove a key from the cache. Returns True if key existed."""
+        with self._lock:
+            return self._store.pop(key, None) is not None
+
+    def invalidate_prefix(self, prefix: str) -> int:
+        """Remove all keys starting with prefix. Returns count of removed keys."""
+        with self._lock:
+            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
+            for k in keys_to_remove:
+                del self._store[k]
+            return len(keys_to_remove)
+
+    def clear(self) -> None:
+        """Remove all entries."""
+        with self._lock:
+            self._store.clear()
+
+    @property
+    def size(self) -> int:
+        """Return the number of entries (including expired but not yet evicted)."""
+        with self._lock:
+            return len(self._store)
+
+    def _evict_expired(self) -> int:
+        """Remove expired entries. Must be called with lock held. Returns count removed."""
+        now = time.monotonic()
+        expired = [k for k, (_, exp) in self._store.items() if now > exp]
+        for k in expired:
+            del self._store[k]
+        return len(expired)
+
diff --git a/src/services/product_service.py b/src/services/product_service.py
index 8a1b3c2..e5f9d71 100644
--- a/src/services/product_service.py
+++ b/src/services/product_service.py
@@ -1,6 +1,7 @@
 from sqlalchemy.orm import Session
 
+from src.cache.ttl_cache import TTLCache
 from src.models.product import Product
 from src.schemas.product import ProductCreate, ProductUpdate
 
@@ -8,14 +9,22 @@ from src.schemas.product import ProductCreate, ProductUpdate
 class ProductService:
     """Service layer for product CRUD operations."""
 
-    def __init__(self, db: Session) -> None:
+    def __init__(self, db: Session, cache: TTLCache | None = None) -> None:
         self._db = db
+        self._cache = cache or TTLCache(default_ttl=120.0)
 
     def get_by_id(self, product_id: int) -> Product | None:
+        cache_key = f"product:{product_id}"
+        cached = self._cache.get(cache_key)
+        if cached is not None:
+            return cached
         product = self._db.query(Product).filter(Product.id == product_id).first()
+        if product is not None:
+            self._cache.set(cache_key, product)
         return product
 
     def create(self, payload: ProductCreate) -> Product:
+        self._cache.invalidate_prefix("product:")
         product = Product(**payload.model_dump())
         self._db.add(product)
         self._db.commit()
@@ -23,6 +32,7 @@ class ProductService:
         return product
 
     def update(self, product_id: int, payload: ProductUpdate) -> Product | None:
+        self._cache.invalidate(f"product:{product_id}")
         product = self.get_by_id(product_id)
         if product is None:
             return None
@@ -34,6 +44,7 @@ class ProductService:
         return product
 
     def delete(self, product_id: int) -> bool:
+        self._cache.invalidate(f"product:{product_id}")
         product = self._db.query(Product).filter(Product.id == product_id).first()
         if product is None:
             return False
diff --git a/tests/test_ttl_cache.py b/tests/test_ttl_cache.py
new file mode 100644
index 0000000..c7d3a82
--- /dev/null
+++ b/tests/test_ttl_cache.py
@@ -0,0 +1,72 @@
+import time
+from unittest.mock import patch
+
+import pytest
+
+from src.cache.ttl_cache import TTLCache
+
+
+class TestTTLCache:
+    def test_get_miss_returns_none(self):
+        cache = TTLCache()
+        assert cache.get("nonexistent") is None
+
+    def test_set_and_get_hit(self):
+        cache = TTLCache()
+        cache.set("key1", {"name": "Widget"})
+        assert cache.get("key1") == {"name": "Widget"}
+
+    def test_expiry_returns_none(self):
+        cache = TTLCache(default_ttl=0.01)
+        cache.set("key1", "value1")
+        time.sleep(0.02)
+        assert cache.get("key1") is None
+
+    def test_custom_ttl_override(self):
+        cache = TTLCache(default_ttl=300.0)
+        cache.set("short", "val", ttl=0.01)
+        time.sleep(0.02)
+        assert cache.get("short") is None
+
+    def test_invalidate_existing_key(self):
+        cache = TTLCache()
+        cache.set("key1", "value1")
+        assert cache.invalidate("key1") is True
+        assert cache.get("key1") is None
+
+    def test_invalidate_missing_key(self):
+        cache = TTLCache()
+        assert cache.invalidate("missing") is False
+
+    def test_invalidate_prefix(self):
+        cache = TTLCache()
+        cache.set("product:1", "a")
+        cache.set("product:2", "b")
+        cache.set("user:1", "c")
+        removed = cache.invalidate_prefix("product:")
+        assert removed == 2
+        assert cache.get("product:1") is None
+        assert cache.get("user:1") == "c"
+
+    def test_clear(self):
+        cache = TTLCache()
+        cache.set("a", 1)
+        cache.set("b", 2)
+        cache.clear()
+        assert cache.size == 0
+
+    def test_max_size_evicts_oldest(self):
+        cache = TTLCache(max_size=2)
+        cache.set("a", 1)
+        cache.set("b", 2)
+        cache.set("c", 3)
+        assert cache.size == 2
+        assert cache.get("a") is None
+        assert cache.get("c") == 3
+
+    def test_thread_safety(self):
+        import threading
+
+        cache = TTLCache()
+        errors = []
+
+        def writer(prefix: str):
+            try:
+                for i in range(100):
+                    cache.set(f"{prefix}:{i}", i)
+                    cache.get(f"{prefix}:{i}")
+            except Exception as exc:
+                errors.append(exc)
+
+        threads = [threading.Thread(target=writer, args=(f"t{n}",)) for n in range(4)]
+        for t in threads:
+            t.start()
+        for t in threads:
+            t.join()
+        assert errors == []
+
