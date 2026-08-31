from sqlalchemy import inspect, text

from app.extensions import db
from app.models import (
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroCalloutAssignment,
    NeoSubZeroUccAssignment,
    NeoSubZeroPretreatState,
    NeoSubZeroSetting,
    StaffingPersonQualification,
)

LOCK_KEY = 7_483_327_341_930
NEOSUBZERO_TABLES = (
    NeoSubZeroPretreatState.__table__,
    NeoSubZeroDepartureDeiceEvent.__table__,
    NeoSubZeroSetting.__table__,
    StaffingPersonQualification.__table__,
    NeoSubZeroCalloutAssignment.__table__,
    NeoSubZeroUccAssignment.__table__,
)


def _missing_tables(connection):
    inspector = inspect(connection)
    return tuple(table for table in NEOSUBZERO_TABLES if not inspector.has_table(table.name))


def ensure_neosubzero_pretreat_table(app):
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    if app.config.get("TESTING") or not uri.startswith(("postgresql:", "postgresql+", "postgres:", "postgres+")):
        return False
    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                if not _missing_tables(read_connection):
                    return True
            connection = db.session.connection()
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
            missing = _missing_tables(connection)
            if missing:
                for table in missing:
                    table.create(bind=connection, checkfirst=False)
                db.session.commit()
            else:
                db.session.rollback()
        except Exception:
            db.session.rollback()
            raise
    return True
