"""Targeted production schema ensure for NeoSektor transition settings."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoSektorOperationalSetting


NEOSEKTOR_SHEETS_SCHEMA_LOCK_KEY = 7_483_327_341_904
NEOSEKTOR_SHEETS_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neosektor_sheets_compat_columns(app):
    """Ensure only additive NeoSektor transition columns exist."""
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
            connection.execute(
                text(
                    f"ALTER TABLE {NeoSektorOperationalSetting.__tablename__} "
                    "ADD COLUMN IF NOT EXISTS integration_mode VARCHAR(40) "
                    "NOT NULL DEFAULT 'google_primary'"
                )
            )
            connection.execute(
                text(
                    f"ALTER TABLE {NeoSektorOperationalSetting.__tablename__} "
                    "ADD COLUMN IF NOT EXISTS google_mirror_sync_needed BOOLEAN "
                    "NOT NULL DEFAULT FALSE"
                )
            )
            connection.execute(
                text(
                    f"ALTER TABLE {NeoSektorOperationalSetting.__tablename__} "
                    "ADD COLUMN IF NOT EXISTS google_mirror_last_error VARCHAR(255)"
                )
            )
            connection.execute(
                text(
                    f"ALTER TABLE {NeoSektorOperationalSetting.__tablename__} "
                    "ADD COLUMN IF NOT EXISTS google_mirror_failed_at_utc TIMESTAMP"
                )
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoSektor transition-column ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoSektor transition-column ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
