"""Targeted production schema ensure for NeoRain Delay Info."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoRainDelayInfo


NEORAIN_DELAY_INFO_SCHEMA_LOCK_KEY = 7_483_327_341_921
NEORAIN_DELAY_INFO_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neorain_delay_info_table(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(text(f"SET LOCAL lock_timeout = '{NEORAIN_DELAY_INFO_SCHEMA_LOCK_TIMEOUT}'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": NEORAIN_DELAY_INFO_SCHEMA_LOCK_KEY})
            NeoRainDelayInfo.__table__.create(bind=connection, checkfirst=True)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    return True


def _is_postgresql(app):
    return str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower().startswith(("postgresql:", "postgresql+", "postgres:", "postgres+"))
