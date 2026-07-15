"""Shared CSRF protection for browser forms and same-origin API mutations."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from flask import current_app, jsonify, render_template, request, session
from markupsafe import Markup, escape


CSRF_FORM_FIELD = "csrf_token"
CSRF_SESSION_NONCE_KEY = "_csrf_session_nonce"
CSRF_HEADER_NAMES = ("X-CSRF-Token", "X-CSRFToken")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
FORM_OPEN_TAG_PATTERN = re.compile(
    r"(<form\b(?=[^>]*\bmethod\s*=\s*['\"]?(?:post|put|patch|delete)\b)[^>]*>)",
    re.IGNORECASE,
)


def csrf_protection_enabled():
    """Return whether CSRF checks are active for the current application."""
    if not current_app.config.get("CSRF_ENABLED", True):
        return False
    return not current_app.config.get("TESTING") or current_app.config.get(
        "CSRF_PROTECT_TESTING", False
    )


def csrf_token():
    """Return a short-lived token bound to the current Flask session."""
    if not csrf_protection_enabled():
        return ""

    nonce = _session_nonce()
    issued_at = str(int(time.time()))
    payload = f"{issued_at}.{nonce}"
    signature = _signature(payload)
    return f"{payload}.{signature}"


def csrf_field():
    """Render a hidden token field for templates that need one explicitly."""
    token = escape(csrf_token())
    return Markup(f'<input type="hidden" name="{CSRF_FORM_FIELD}" value="{token}">')


def clear_csrf_session_state():
    """Discard the token nonce when authentication state is rotated or cleared."""
    session.pop(CSRF_SESSION_NONCE_KEY, None)


def validate_csrf_request():
    """Validate the submitted form or fetch token for an unsafe request."""
    if request.method not in UNSAFE_METHODS or not csrf_protection_enabled():
        return True

    token = _request_token()
    if not token:
        return False

    try:
        issued_at, nonce, signature = token.rsplit(".", 2)
        issued_at_value = int(issued_at)
    except (AttributeError, TypeError, ValueError):
        return False

    payload = f"{issued_at}.{nonce}"
    if not hmac.compare_digest(signature, _signature(payload)):
        return False

    session_nonce = session.get(CSRF_SESSION_NONCE_KEY)
    if not isinstance(session_nonce, str) or not hmac.compare_digest(nonce, session_nonce):
        return False

    now = int(time.time())
    ttl = int(current_app.config["CSRF_TOKEN_TTL_SECONDS"])
    return issued_at_value <= now and now - issued_at_value <= ttl


def csrf_failure_response():
    """Return a safe response for browser and fetch CSRF validation failures."""
    if _is_api_request():
        return jsonify({"error": "CSRF validation failed. Refresh and try again."}), 400
    return render_template("csrf_error.html"), 400


def inject_csrf_tokens(response):
    """Add a token field to every rendered unsafe HTML form in one place."""
    if not csrf_protection_enabled() or response.mimetype != "text/html":
        return response

    body = response.get_data(as_text=True)
    if "<form" not in body.lower():
        return response

    field = str(csrf_field())
    updated_body = FORM_OPEN_TAG_PATTERN.sub(
        lambda match: f"{match.group(1)}{field}",
        body,
    )
    if updated_body != body:
        response.set_data(updated_body)
    return response


def _session_nonce():
    nonce = session.get(CSRF_SESSION_NONCE_KEY)
    if not isinstance(nonce, str) or not nonce:
        nonce = secrets.token_urlsafe(32)
        session[CSRF_SESSION_NONCE_KEY] = nonce
    return nonce


def _signature(payload):
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(
        secret,
        f"neoapps-csrf:{payload}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _request_token():
    for header_name in CSRF_HEADER_NAMES:
        value = request.headers.get(header_name)
        if value:
            return value
    return request.form.get(CSRF_FORM_FIELD, "")


def _is_api_request():
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
