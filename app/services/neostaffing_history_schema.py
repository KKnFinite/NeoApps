"""Explicit PostgreSQL cutover for the narrowly scoped roster journal."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingAttendanceOutcome, StaffingRosterClose, StaffingRosterPlacement, StaffingRosterRevision


ROSTER_HISTORY_LOCK = 7_483_327_341_922
SOURCES = (
    ("staffing_units", "unit", ("unit_type", "parent_id", "name", "active")),
    ("staffing_people", "person", ("employee_id", "first_name", "last_name", "classification", "active")),
    ("staffing_work_assignments", "assignment", ("person_id", "work_area_unit_id", "active")),
)


def _json_row(record, fields):
    return "jsonb_build_object(" + ", ".join(f"'{field}', {record}.{field}" for field in fields) + ")"


def initialize_reporting_history():
    """Create and seed once, in one explicitly authorized deployment transaction."""
    connection = db.session.connection()
    if connection.dialect.name != "postgresql":
        raise ValueError("Reporting history initialization requires PostgreSQL.")
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ROSTER_HISTORY_LOCK})
    for model in (StaffingRosterRevision, StaffingRosterClose, StaffingRosterPlacement, StaffingAttendanceOutcome):
        model.__table__.create(connection, checkfirst=True)
    if connection.execute(text("SELECT 1 FROM staffing_roster_revisions WHERE kind = 'baseline'")).first():
        return False
    tables = ", ".join(table for table, _, _ in SOURCES)
    connection.execute(text(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE"))
    stamp = connection.scalar(text("SELECT clock_timestamp() AT TIME ZONE 'UTC'"))
    connection.execute(text(
        "INSERT INTO staffing_roster_revisions (kind, entity_id, changed_at) VALUES ('baseline', 0, :stamp)"
    ), {"stamp": stamp})
    for table, kind, fields in SOURCES:
        row_json = _json_row("source", fields)
        connection.execute(text(f"""
            INSERT INTO staffing_roster_revisions (kind, entity_id, changed_at, state)
            SELECT '{kind}', source.id, :stamp, {row_json} FROM {table} source
        """), {"stamp": stamp})
        connection.execute(text(f"""
            CREATE FUNCTION record_staffing_roster_{kind}() RETURNS trigger
            LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            DECLARE
                new_state jsonb;
                row_id integer;
            BEGIN
                IF TG_OP <> 'DELETE' THEN
                    new_state := {_json_row("NEW", fields)};
                    row_id := NEW.id;
                ELSE
                    row_id := OLD.id;
                END IF;
                IF TG_OP = 'UPDATE' AND new_state IS NOT DISTINCT FROM {_json_row("OLD", fields)} THEN
                    RETURN NULL;
                END IF;
                PERFORM pg_advisory_xact_lock_shared({ROSTER_HISTORY_LOCK});
                INSERT INTO staffing_roster_revisions (kind, entity_id, changed_at, state, change_set_id)
                VALUES ('{kind}', row_id, clock_timestamp() AT TIME ZONE 'UTC', new_state, txid_current());
                RETURN NULL;
            END;
            $$;
        """))
        connection.execute(text(f"""
            CREATE TRIGGER staffing_roster_{kind}_history AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION record_staffing_roster_{kind}()
        """))
    return True
