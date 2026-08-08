"""Targeted production schema ensure for Google MotherBrain poll coordination."""

from sqlalchemy import text

from app.extensions import db
from app.models import MotherBrainGoogleLivePollState


# A stable transaction-scoped lock serializes this one additive DDL operation
# across concurrent Render workers without coordinating unrelated schema work.
GOOGLE_LIVE_POLL_SCHEMA_LOCK_KEY = 7_483_327_341_901
GOOGLE_LIVE_POLL_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_google_motherbrain_live_poll_state_table(app):
    """Ensure only the PostgreSQL-backed poll-state table exists at startup."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{GOOGLE_LIVE_POLL_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": GOOGLE_LIVE_POLL_SCHEMA_LOCK_KEY},
            )
            MotherBrainGoogleLivePollState.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "Google live-poll state table ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("Google live-poll state table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
