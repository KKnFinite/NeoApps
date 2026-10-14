"""One flight identity per canonical sort, across arrival and departure."""
import re
from sqlalchemy import select, text
from app.extensions import db
from app.models import SortDateMission, SortDateOperation


def flight_identity(value):
    value = re.sub(r"\s+", "", str(value or "").upper())
    match = re.fullmatch(r"(?:UPS|5X)?([0-9]+)", value)
    return "UPS" + str(int(match[1])) if match else value


def reject_duplicate_flight(operation, mission):
    # Lock the parent BEFORE checking either direction. Hold through commit.
    with db.session.no_autoflush:
        db.session.execute(select(SortDateOperation.id).where(
            SortDateOperation.id == operation.id).with_for_update()).scalar_one()
        rows = db.session.execute(select(SortDateMission.id, SortDateMission.flight_number)
            .where(SortDateMission.sort_date_operation_id == operation.id)).all()
        if any(mid != mission.id and flight_identity(number) == flight_identity(mission.flight_number)
               for mid, number in rows):
            raise ValueError("A mission with this flight number already exists in this operation.")


def sync_mission_identity_schema(connection):
    """Add a transaction-level invariant without rewriting historical collisions.

    Only INSERT or identity changes are checked. Other historical record updates
    remain possible. Parent UPDATE serializes even repeatable-read writers (the
    loser must retry/reject rather than validate from an obsolete snapshot).
    """
    if connection.dialect.name != "postgresql":
        return
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text(r"""
      CREATE OR REPLACE FUNCTION neo_mission_flight_key(input text)
      RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
        SELECT CASE WHEN v ~ '^(UPS|5X)?[0-9]+$'
          THEN 'UPS' || COALESCE(NULLIF(ltrim(regexp_replace(v, '^(UPS|5X)', ''), '0'), ''), '0')
          ELSE v END FROM (SELECT regexp_replace(upper(input), '\s+', '', 'g') v) q
      $$
    """))
    connection.execute(text("""
      CREATE OR REPLACE FUNCTION neo_mission_identity_validate()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'UPDATE' THEN
          IF NEW.sort_date_operation_id = OLD.sort_date_operation_id
             AND NEW.flight_number = OLD.flight_number THEN RETURN NEW; END IF;
        END IF;
        UPDATE sort_date_operations SET id = id WHERE id = NEW.sort_date_operation_id;
        IF EXISTS (SELECT 1 FROM sort_date_missions m
          WHERE m.sort_date_operation_id = NEW.sort_date_operation_id
            AND m.id IS DISTINCT FROM NEW.id
            AND neo_mission_flight_key(m.flight_number) = neo_mission_flight_key(NEW.flight_number)) THEN
          RAISE EXCEPTION 'A mission with this flight number already exists in this operation.'
            USING ERRCODE = '23505', CONSTRAINT = 'neo_mission_identity';
        END IF;
        RETURN NEW;
      END $$
    """))
    connection.execute(text("DROP TRIGGER IF EXISTS neo_mission_identity ON sort_date_missions"))
    connection.execute(text("""CREATE TRIGGER neo_mission_identity
      BEFORE INSERT OR UPDATE OF flight_number, sort_date_operation_id ON sort_date_missions
      FOR EACH ROW EXECUTE FUNCTION neo_mission_identity_validate()"""))
