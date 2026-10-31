"""Database-backed abuse protection for authentication entry points."""

from datetime import datetime, timedelta
import hashlib
import hmac
import ipaddress
import logging
import math

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.auth_rate_limit_state import AuthRateLimitState


LOGIN_ACTION = "login"
PASSWORD_RESET_ACTION = "password_reset"
REGISTRATION_ACTION = "registration"
IP_SUBJECT = "ip"
IDENTIFIER_SUBJECT = "identifier"

logger = logging.getLogger(__name__)


def initialize_auth_rate_limit_storage(app):
    """Validate the configured shared abuse-control backend at app startup.

    The table itself is created by the existing schema sync/bootstrap workflow.
    Keeping this check connection-free lets configuration validation run before a
    production database is available while still requiring a shared backend.
    """
    if not app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
        return
    for setting, default in (
        ("AUTH_REGISTRATION_WINDOW_SECONDS", 3600),
        ("AUTH_REGISTRATION_IP_MAX_ATTEMPTS", 120),
        ("AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS", 5),
    ):
        if int(app.config.get(setting, default)) <= 0:
            raise RuntimeError(f"{setting} must be positive.")
    if app.config.get("TESTING"):
        return
    if app.config.get("AUTH_RATE_LIMIT_STORAGE") != "database":
        raise RuntimeError(
            "AUTH_RATE_LIMIT_STORAGE must be 'database' while authentication rate "
            "limiting is enabled."
        )


def client_ip_for_request(request):
    """Use forwarded client IP data only from an explicitly known proxy."""
    remote_ip = _normalized_ip(request.remote_addr) or "unknown"
    if not current_app.config.get("AUTH_TRUST_PROXY_HEADERS", False):
        return remote_ip
    if not _is_trusted_proxy(remote_ip):
        return remote_ip

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if not forwarded_for:
        return remote_ip
    forwarded_ip = _normalized_ip(forwarded_for.split(",", 1)[0])
    return forwarded_ip or remote_ip


def login_is_limited(client_ip, identifier):
    return _is_limited(LOGIN_ACTION, client_ip, identifier)


def record_login_failure(client_ip, identifier):
    _record_attempt(
        LOGIN_ACTION,
        client_ip,
        identifier,
        window_seconds=current_app.config["AUTH_LOGIN_WINDOW_SECONDS"],
        ip_max_attempts=current_app.config["AUTH_LOGIN_IP_MAX_FAILURES"],
        identifier_max_attempts=current_app.config[
            "AUTH_LOGIN_IDENTIFIER_MAX_FAILURES"
        ],
        base_cooldown_seconds=current_app.config[
            "AUTH_LOGIN_BASE_COOLDOWN_SECONDS"
        ],
        max_cooldown_seconds=current_app.config["AUTH_LOGIN_MAX_COOLDOWN_SECONDS"],
    )


def clear_login_failures(client_ip, identifier):
    _clear_states(LOGIN_ACTION, client_ip, identifier)


def clear_login_identifier_failures(*identifiers):
    """Clear only supplied LOGIN identifiers in the caller's transaction.

    Recovery must not release IP protection or other authentication actions.
    """
    if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
        return
    normalized = {_normalize_identifier(value) for value in identifiers}
    for identifier in sorted(normalized - {""}):
        state = _get_state(LOGIN_ACTION, IDENTIFIER_SUBJECT, identifier, lock=True)
        if state is not None:
            db.session.delete(state)


def password_reset_is_limited(client_ip, identifier):
    return _is_limited(PASSWORD_RESET_ACTION, client_ip, identifier)


def record_password_reset_request(client_ip, identifier):
    _record_attempt(
        PASSWORD_RESET_ACTION,
        client_ip,
        identifier,
        window_seconds=current_app.config["AUTH_PASSWORD_RESET_WINDOW_SECONDS"],
        ip_max_attempts=current_app.config[
            "AUTH_PASSWORD_RESET_IP_MAX_ATTEMPTS"
        ],
        identifier_max_attempts=current_app.config[
            "AUTH_PASSWORD_RESET_IDENTIFIER_MAX_ATTEMPTS"
        ],
        base_cooldown_seconds=current_app.config[
            "AUTH_PASSWORD_RESET_BASE_COOLDOWN_SECONDS"
        ],
        max_cooldown_seconds=current_app.config[
            "AUTH_PASSWORD_RESET_MAX_COOLDOWN_SECONDS"
        ],
    )


def reserve_registration_attempt(client_ip, identifier):
    """Atomically admit an attempt, returning seconds to retry (zero if allowed).

    Commit the short reservation transaction BEFORE hashing, provisioning or
    email delivery. A failed registration still consumes its reservation.
    All callers lock IP then identifier; uniqueness/savepoints also serialize
    first use across PostgreSQL workers. This is a fixed window: rejected
    requests neither extend the cooldown nor spend the shared IP allowance
    when only the identifier is blocked.
    """
    if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
        return 0

    now = datetime.utcnow()
    window = timedelta(seconds=current_app.config["AUTH_REGISTRATION_WINDOW_SECONDS"])
    subjects = [(IP_SUBJECT, _normalized_ip(client_ip) or "unknown",
                 current_app.config["AUTH_REGISTRATION_IP_MAX_ATTEMPTS"])]
    # Match registration's strip/lower email identity contract exactly. The
    # existing login/reset casefold policy remains unchanged.
    identifier = str(identifier or "").strip().lower()
    if identifier:
        subjects.append((IDENTIFIER_SUBJECT, identifier,
                         current_app.config["AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS"]))

    states = []
    for subject_type, subject_value, maximum in subjects:
        state = _get_state(REGISTRATION_ACTION, subject_type, subject_value,
                           lock=True, refresh=True)
        if state is None:
            state = _create_state(REGISTRATION_ACTION, subject_type, subject_value, now)
        if now - state.window_started_at >= window:
            state.window_started_at = now
            state.attempt_count = 0
            state.blocked_until = None
        if state.attempt_count >= maximum:
            state.blocked_until = state.window_started_at + window
            retry_after = max(1, math.ceil((state.blocked_until - now).total_seconds()))
            db.session.commit()
            return retry_after
        states.append((state, maximum))

    for state, maximum in states:
        state.attempt_count += 1
        if state.attempt_count >= maximum:
            state.blocked_until = state.window_started_at + window
    db.session.commit()
    return 0


def _is_limited(action, client_ip, identifier):
    if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
        return False

    now = datetime.utcnow()
    limited_subject_types = []
    for subject_type, subject_value in _subjects(client_ip, identifier):
        state = _get_state(action, subject_type, subject_value, lock=False)
        if state and state.blocked_until and state.blocked_until > now:
            limited_subject_types.append(subject_type)

    if limited_subject_types:
        logger.warning(
            "Authentication rate limit applied: action=%s subjects=%s",
            action,
            ",".join(limited_subject_types),
        )
        return True
    return False


def _record_attempt(
    action,
    client_ip,
    identifier,
    *,
    window_seconds,
    ip_max_attempts,
    identifier_max_attempts,
    base_cooldown_seconds,
    max_cooldown_seconds,
):
    if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
        return

    now = datetime.utcnow()
    for subject_type, subject_value in _subjects(client_ip, identifier):
        max_attempts = (
            ip_max_attempts if subject_type == IP_SUBJECT else identifier_max_attempts
        )
        state = _get_state(action, subject_type, subject_value, lock=True)
        if state is None:
            state = _create_state(action, subject_type, subject_value, now)

        if now - state.window_started_at >= timedelta(seconds=window_seconds):
            state.window_started_at = now
            state.attempt_count = 0
            state.blocked_until = None

        state.attempt_count += 1
        if state.attempt_count >= max_attempts:
            overflow = state.attempt_count - max_attempts
            cooldown = min(
                base_cooldown_seconds * (2**overflow),
                max_cooldown_seconds,
            )
            state.blocked_until = now + timedelta(seconds=cooldown)


def _clear_states(action, client_ip, identifier):
    if not current_app.config.get("AUTH_RATE_LIMIT_ENABLED", True):
        return

    for subject_type, subject_value in _subjects(client_ip, identifier):
        state = _get_state(action, subject_type, subject_value, lock=True)
        if state is not None:
            db.session.delete(state)


def _subjects(client_ip, identifier):
    subjects = [(IP_SUBJECT, _normalized_ip(client_ip) or "unknown")]
    normalized_identifier = _normalize_identifier(identifier)
    if normalized_identifier:
        subjects.append((IDENTIFIER_SUBJECT, normalized_identifier))
    return subjects


def _get_state(action, subject_type, subject_value, *, lock, refresh=False):
    query = AuthRateLimitState.query.filter_by(
        action=action,
        subject_type=subject_type,
        subject_digest=_subject_digest(action, subject_type, subject_value),
    )
    if lock:
        query = query.with_for_update()
    if refresh:
        query = query.populate_existing()
    return query.first()


def _create_state(action, subject_type, subject_value, now):
    state = AuthRateLimitState(
        action=action,
        subject_type=subject_type,
        subject_digest=_subject_digest(action, subject_type, subject_value),
        window_started_at=now,
    )
    try:
        with db.session.begin_nested():
            db.session.add(state)
            db.session.flush()
        return state
    except IntegrityError:
        return _get_state(action, subject_type, subject_value, lock=True)


def _subject_digest(action, subject_type, subject_value):
    message = f"{action}:{subject_type}:{subject_value}".encode("utf-8")
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _normalize_identifier(value):
    return str(value or "").strip().casefold()


def _normalized_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def _is_trusted_proxy(remote_ip):
    configured_proxies = current_app.config.get("AUTH_TRUSTED_PROXY_IPS", ())
    if isinstance(configured_proxies, str):
        configured_proxies = configured_proxies.split(",")

    try:
        parsed_remote_ip = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False

    for configured_proxy in configured_proxies:
        configured_proxy = str(configured_proxy).strip()
        if not configured_proxy:
            continue
        try:
            if parsed_remote_ip in ipaddress.ip_network(configured_proxy, strict=False):
                return True
        except ValueError:
            continue
    return False
