"""Targeted additive schema ensure for NeoStaffing Shift Flow plans."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingShiftFlowPlan


def ensure_neostaffing_shift_flow_plan_table(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False
    with app.app_context():
        connection = db.session.connection()
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 7_483_327_341_913})
        StaffingShiftFlowPlan.__table__.create(bind=connection, checkfirst=True)
        db.session.commit()
    return True


def _is_postgresql(app):
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return uri.startswith(("postgresql:", "postgresql+", "postgres:", "postgres+"))
