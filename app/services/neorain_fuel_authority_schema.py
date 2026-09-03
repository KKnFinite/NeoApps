"""Narrow production compatibility ensure for Rain fuel review audit rows."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoRainFuelReviewAcknowledgement, NeoRainGoogleFuelValue


_LOCK_KEY = 7_483_327_341_934


def ensure_neorain_fuel_authority_schema(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
            NeoRainFuelReviewAcknowledgement.__table__.create(bind=connection, checkfirst=True)
            NeoRainGoogleFuelValue.__table__.create(bind=connection, checkfirst=True)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    return True


def _is_postgresql(app):
    return str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower().startswith(("postgresql:", "postgresql+", "postgres:", "postgres+"))
