"""Targeted production schema ensure for NeoRain gateway settings."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoRainOperationalSetting


NEORAIN_GROUND_TIME_SETTINGS_LOCK_KEY = 7_483_327_341_918
NEORAIN_GROUND_TIME_SETTINGS_LOCK_TIMEOUT = "5s"


def ensure_neorain_ground_time_settings_table(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(text(f"SET LOCAL lock_timeout = '{NEORAIN_GROUND_TIME_SETTINGS_LOCK_TIMEOUT}'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": NEORAIN_GROUND_TIME_SETTINGS_LOCK_KEY})
            NeoRainOperationalSetting.__table__.create(bind=connection, checkfirst=True)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.error("NeoRain Ground Time setting schema ensure failed: error=%s", type(exc).__name__)
            raise
    return True


def _is_postgresql(app):
    return str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower().startswith(("postgresql:", "postgresql+", "postgres:", "postgres+"))
