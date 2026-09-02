"""Bounded per-worker protection for live-state endpoints."""

from collections import OrderedDict
from threading import Lock
from time import monotonic

from flask import current_app, jsonify, request
from flask_login import current_user

from app.services.live_screen_registry import live_screen_for_refresh_request


_MAX_KEYS = 4096
_last_requests = OrderedDict()
_lock = Lock()


def enforce_live_refresh_request_cadence():
    route_values = dict(request.view_args or {})
    route_values.update(request.args)
    screen = live_screen_for_refresh_request(request.endpoint, route_values)
    if screen is None or request.method not in {"GET", "HEAD"}:
        return None
    if current_app.testing and not current_app.config.get("TEST_LIVE_REFRESH_RATE_LIMIT_ENABLED"):
        return None

    try:
        configured_minimum = float(
            current_app.config.get("LIVE_REFRESH_SERVER_MIN_INTERVAL_SECONDS", 5)
        )
    except (TypeError, ValueError):
        configured_minimum = 5.0
    minimum = max(5.0, configured_minimum)
    identity = getattr(current_user, "id", None) or request.remote_addr or "anonymous"
    key = (str(identity), screen.screen_key)
    now = monotonic()
    with _lock:
        previous = _last_requests.get(key)
        if previous is not None and now - previous < minimum:
            retry_after = max(1, int(minimum - (now - previous) + 0.999))
            response = jsonify({
                "ok": False,
                "error": "Live refresh is cooling down.",
                "retry_after_seconds": retry_after,
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            response.headers["Cache-Control"] = "no-store"
            return response
        _last_requests[key] = now
        _last_requests.move_to_end(key)
        while len(_last_requests) > _MAX_KEYS:
            _last_requests.popitem(last=False)
    return None


def reset_live_refresh_guard_for_testing():
    with _lock:
        _last_requests.clear()
