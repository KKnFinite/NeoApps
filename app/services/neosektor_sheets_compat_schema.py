"""Targeted production schema ensure for NeoSektor Sheets read throttling."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoSektorOperationalSetting


NEOSEKTOR_SHEETS_SCHEMA_LOCK_KEY = 7_483_327_341_904
NEOSEKTOR_SHEETS_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neosektor_sheets_compat_columns(app):
    """Ensure only the additive Google-read throttle column exists."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSEKTOR_SHEETS_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSEKTOR_SHEETS_SCHEMA_LOCK_KEY},
            )
            connection.execute(
                text(
                    f"ALTER TABLE {NeoSektorOperationalSetting.__tablename__} "
                    "ADD COLUMN IF NOT EXISTS last_google_read_at_utc TIMESTAMP"
                )
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoSektor Sheets throttle-column ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoSektor Sheets throttle-column ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
