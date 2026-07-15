"""Session-bound authorization state for sensitive account flows."""

import time
from datetime import datetime, timedelta, timezone


AUTH_SESSION_VERSION_SESSION_KEY = "auth_session_version"
FORCED_PASSWORD_CHANGE_SESSION_KEY = "forced_password_change_user_id"
FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY = (
    "forced_password_change_authenticated_at"
)
FORCED_PASSWORD_CHANGE_SESSION_TTL_SECONDS = 15 * 60


def user_session_version(user):
    """Return a stable, backwards-compatible authentication session version."""
    return int(getattr(user, "auth_session_version", 1) or 1)


def rotate_user_session_version(user):
    """Invalidate sessions issued before a successful password change."""
    user.auth_session_version = user_session_version(user) + 1
    return user.auth_session_version


def temporary_password_expiration(hours, *, now=None):
    """Return a UTC expiration timestamp for an administrator-issued password."""
    return _as_utc(now or datetime.now(timezone.utc)) + timedelta(hours=int(hours))


def temporary_password_is_expired(user, *, now=None):
    """Whether a current emergency password has passed its UTC expiration."""
    if not user or not getattr(user, "password_reset_required", False):
        return False

    expires_at = getattr(user, "temporary_password_expires_at", None)
    if expires_at is None:
        return False

    return _as_utc(expires_at) <= _as_utc(now or datetime.now(timezone.utc))


def session_version_matches_user(session_data, user):
    """Whether this browser session was issued for the user's current version."""
    return session_data.get(AUTH_SESSION_VERSION_SESSION_KEY) == user_session_version(user)


def establish_authenticated_session(session_data, user, *, forced_password_change=False):
    """Record a fresh login and, when needed, its short-lived forced-change grant."""
    session_data[AUTH_SESSION_VERSION_SESSION_KEY] = user_session_version(user)
    clear_forced_password_change_session(session_data)
    if forced_password_change:
        session_data[FORCED_PASSWORD_CHANGE_SESSION_KEY] = user.id
        session_data[FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY] = int(time.time())


def forced_password_change_session_is_fresh(session_data, user, *, now=None):
    """Only a recent login with the required password may skip current-password entry."""
    if not session_version_matches_user(session_data, user):
        return False
    if session_data.get(FORCED_PASSWORD_CHANGE_SESSION_KEY) != user.id:
        return False

    authenticated_at = session_data.get(
        FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY
    )
    try:
        age = (time.time() if now is None else now) - int(authenticated_at)
    except (TypeError, ValueError):
        return False
    return 0 <= age <= FORCED_PASSWORD_CHANGE_SESSION_TTL_SECONDS


def clear_forced_password_change_session(session_data):
    """Discard the one-time forced-password-change grant."""
    session_data.pop(FORCED_PASSWORD_CHANGE_SESSION_KEY, None)
    session_data.pop(FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY, None)


def clear_authenticated_session_security_state(session_data):
    """Discard all session data used to authenticate or bypass a password prompt."""
    session_data.pop(AUTH_SESSION_VERSION_SESSION_KEY, None)
    clear_forced_password_change_session(session_data)


def _as_utc(value):
    """Normalize legacy naive database timestamps as UTC for safe comparison."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
