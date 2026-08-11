"""Targeted production schema ensure for per-user MotherBrain alert state."""

from sqlalchemy import text

from app.extensions import db
from app.models import MotherBrainAlertUserState


MOTHERBRAIN_ALERT_USER_STATE_SCHEMA_LOCK_KEY = 7_483_327_341_903
MOTHERBRAIN_ALERT_USER_STATE_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_motherbrain_alert_user_state_table(app):
    """Ensure only the PostgreSQL-backed alert user-state table exists."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{MOTHERBRAIN_ALERT_USER_STATE_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": MOTHERBRAIN_ALERT_USER_STATE_SCHEMA_LOCK_KEY},
            )
            MotherBrainAlertUserState.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "MotherBrain alert user-state table ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("MotherBrain alert user-state table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
