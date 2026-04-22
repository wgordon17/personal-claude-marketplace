# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import time
from flask import request, jsonify


_counters = {}
_window_start = {}

RATE_LIMIT = 100
WINDOW_SECONDS = 60


def _get_client_key():
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


class RateLimiter:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        app.before_request(self.check_rate_limit)

    def check_rate_limit(self):
        key = _get_client_key()
        now = time.time()

        # GIL protects this
        window_start = _window_start.get(key, now)
        if now - window_start > WINDOW_SECONDS:
            _counters[key] = 0
            _window_start[key] = now

        count = _counters.get(key, 0)
        _counters[key] = count + 1

        if count >= RATE_LIMIT:
            return jsonify({"error": "Rate limit exceeded", "retry_after": WINDOW_SECONDS}), 429

    def get_usage(self, client_key=None):
        if client_key is None:
            client_key = _get_client_key()
        return {
            "client": client_key,
            "count": _counters.get(client_key, 0),
            "limit": RATE_LIMIT,
            "window_seconds": WINDOW_SECONDS,
            "window_start": _window_start.get(client_key),
        }

    def reset(self, client_key=None):
        if client_key is None:
            _counters.clear()
            _window_start.clear()
        else:
            _counters.pop(client_key, None)
            _window_start.pop(client_key, None)
