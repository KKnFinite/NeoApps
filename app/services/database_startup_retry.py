"""Bounded retries for transient database failures during application startup."""

import time

from sqlalchemy.exc import OperationalError

from app.extensions import db


_NON_TRANSIENT_ERROR_MARKERS = (
    "already exists",
    "duplicate column",
    "duplicate key",
    "invalid sql",
    "permission denied",
    "insufficient privilege",
    "syntax error",
    "undefined column",
    "does not exist",
)

_TRANSIENT_CONNECTION_MARKERS = (
    "connection refused",
    "connection reset",
    "connection temporarily unavailable",
    "connection timed out",
    "connection was closed",
    "could not connect to server",
    "database system is starting up",
    "server closed the connection unexpectedly",
    "ssl connection has been closed unexpectedly",
    "terminating connection",
    "timeout expired",
)


class StartupDatabaseConnectionError(RuntimeError):
    """Raised after all bounded startup database connection retries fail."""


def run_startup_database_action(app, action, *, action_name):
    """Run a startup database action with bounded retries for connection loss only."""
    attempts = _configured_attempts(app)
    delay_seconds = _configured_delay(app)
    max_delay_seconds = max(
        delay_seconds,
        _configured_float(app, "DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS", 8.0),
    )

    for attempt in range(1, attempts + 1):
        try:
            return action()
        except OperationalError as error:
            if not _is_transient_connection_error(error):
                raise

            _reset_database_connections()
            if attempt >= attempts:
                app.logger.warning(
                    "Database startup connection retries exhausted: action=%s attempts=%s",
                    action_name,
                    attempts,
                )
                raise StartupDatabaseConnectionError(
                    "NeoApps startup database initialization failed after "
                    f"{attempts} attempts because the database connection remained "
                    "unavailable."
                ) from error

            app.logger.warning(
                "Retrying transient database startup failure: action=%s attempt=%s/%s "
                "delay_seconds=%.2f",
                action_name,
                attempt,
                attempts,
                delay_seconds,
            )
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, max_delay_seconds)


def _configured_attempts(app):
    key = (
        "DATABASE_STARTUP_RETRY_ATTEMPTS_TESTING"
        if app.config.get("TESTING")
        else "DATABASE_STARTUP_RETRY_ATTEMPTS"
    )
    try:
        return max(1, int(app.config.get(key, 1)))
    except (TypeError, ValueError):
        return 1


def _configured_delay(app):
    return max(
        0.0,
        _configured_float(app, "DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS", 1.0),
    )


def _configured_float(app, key, default):
    try:
        return float(app.config.get(key, default))
    except (TypeError, ValueError):
        return default


def _is_transient_connection_error(error):
    message = str(getattr(error, "orig", error) or "").casefold()
    if any(marker in message for marker in _NON_TRANSIENT_ERROR_MARKERS):
        return False
    return bool(getattr(error, "connection_invalidated", False)) or any(
        marker in message for marker in _TRANSIENT_CONNECTION_MARKERS
    )


def _reset_database_connections():
    """Drop transaction and pooled state before retrying a broken connection."""
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.session.remove()
    except Exception:
        pass
    db.engine.dispose()
