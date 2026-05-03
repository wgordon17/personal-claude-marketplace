# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import threading
import functools


_cache = {}
_lock = threading.Lock()


def cache_get(key):
    return _cache.get(key)


def cache_set(key, value):
    with _lock:
        _cache[key] = value


def cache_delete(key):
    with _lock:
        _cache.pop(key, None)


def cache_clear():
    with _lock:
        _cache.clear()


def cache_size():
    return len(_cache)


def cached(key_fn=None):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if key_fn is not None:
                cache_key = key_fn(*args, **kwargs)
            else:
                cache_key = f"{fn.__module__}.{fn.__qualname__}:{args}:{kwargs}"

            cached_value = cache_get(cache_key)
            if cached_value is not None:
                return cached_value

            result = fn(*args, **kwargs)
            cache_set(cache_key, result)
            return result
        return wrapper
    return decorator


def get_task_cached(task_id, fetch_fn):
    key = f"task:{task_id}"
    result = cache_get(key)
    if result is not None:
        return result
    result = fetch_fn(task_id)
    if result is not None:
        cache_set(key, result)
    return result


def invalidate_task(task_id):
    cache_delete(f"task:{task_id}")


def get_user_cached(user_id, fetch_fn):
    key = f"user:{user_id}"
    result = cache_get(key)
    if result is not None:
        return result
    result = fetch_fn(user_id)
    if result is not None:
        cache_set(key, result)
    return result


def invalidate_user(user_id):
    cache_delete(f"user:{user_id}")
