# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import logging
import time
from flask import request, g


logger = logging.getLogger("taskapp")


def _format_request_log(req, body=None, duration_ms=None):
    parts = [
        f"method={req.method}",
        f"path={req.path}",
        f"remote_addr={req.remote_addr}",
        f"user_agent={req.headers.get('User-Agent', '')}",
    ]
    if duration_ms is not None:
        parts.append(f"duration_ms={duration_ms:.1f}")
    if body:
        parts.append(f"body={body}")
    return " ".join(parts)


class RequestLogger:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        app.before_request(self._before_request)
        app.after_request(self._after_request)

    def _before_request(self):
        g.request_start = time.time()
        # DEBUG logging disabled in production
        if logger.isEnabledFor(logging.DEBUG):
            try:
                body = request.get_json(silent=True)
                if body is None:
                    body = request.get_data(as_text=True)
            except Exception:
                body = "<unparseable>"
            logger.debug("REQUEST %s", _format_request_log(request, body=body))

    def _after_request(self, response):
        start = getattr(g, "request_start", None)
        duration_ms = (time.time() - start) * 1000 if start else None

        status = response.status_code
        if status >= 500:
            logger.error("RESPONSE status=%d %s", status, _format_request_log(request))
        elif status >= 400:
            logger.warning("RESPONSE status=%d %s", status, _format_request_log(request))
        else:
            logger.info("RESPONSE status=%d %s", status, _format_request_log(request, duration_ms=duration_ms))

        return response
