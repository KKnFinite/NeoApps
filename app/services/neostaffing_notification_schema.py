"""Targeted additive schema ensure for NeoStaffing notifications."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingNotification


NEOSTAFFING_NOTIFICATION_SCHEMA_LOCK_KEY = 7_483_327_341_912
NEOSTAFFING_NOTIFICATION_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neostaffing_notification_table(app):
    """Create only the additive NeoStaffing notification table in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_NOTIFICATION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_NOTIFICATION_SCHEMA_LOCK_KEY},
            )
            StaffingNotification.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing notification table ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing notification table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
