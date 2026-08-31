from sqlalchemy import inspect, text

from app.extensions import db
from app.models import (
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroCalloutAssignment,
    NeoSubZeroUccAssignment,
    NeoSubZeroUccTruckAssignment,
    NeoSubZeroPretreatState,
    NeoSubZeroSprayRecord,
    NeoSubZeroSetting,
    StaffingPersonQualification,
)

LOCK_KEY = 7_483_327_341_930
DEPARTURE_EVENT_OPTIONAL_COLUMNS = {
    "reason_for_application": "VARCHAR(120)",
}
NEOSUBZERO_TABLES = (
    NeoSubZeroPretreatState.__table__,
    NeoSubZeroDepartureDeiceEvent.__table__,
    NeoSubZeroSetting.__table__,
    StaffingPersonQualification.__table__,
    NeoSubZeroCalloutAssignment.__table__,
    NeoSubZeroUccAssignment.__table__,
    NeoSubZeroUccTruckAssignment.__table__,
    NeoSubZeroSprayRecord.__table__,
)


def _missing_tables(connection):
    inspector = inspect(connection)
    return tuple(table for table in NEOSUBZERO_TABLES if not inspector.has_table(table.name))


def _missing_departure_event_columns(connection):
    inspector = inspect(connection)
    table_name = NeoSubZeroDepartureDeiceEvent.__tablename__
    if not inspector.has_table(table_name):
        return ()
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    return tuple(
        (column_name, column_type)
        for column_name, column_type in DEPARTURE_EVENT_OPTIONAL_COLUMNS.items()
        if column_name not in existing
    )


def ensure_neosubzero_pretreat_table(app):
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    if app.config.get("TESTING") or not uri.startswith(("postgresql:", "postgresql+", "postgres:", "postgres+")):
        return False
    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                if (
                    not _missing_tables(read_connection)
                    and not _missing_departure_event_columns(read_connection)
                ):
                    return True
            connection = db.session.connection()
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
            missing = _missing_tables(connection)
            if missing:
                for table in missing:
                    table.create(bind=connection, checkfirst=False)
            missing_columns = _missing_departure_event_columns(connection)
            for column_name, column_type in missing_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE {NeoSubZeroDepartureDeiceEvent.__tablename__} "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
            if missing or missing_columns:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception:
            db.session.rollback()
            raise
    return True
