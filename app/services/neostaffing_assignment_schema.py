"""Explicit, transactional upgrade for multi-sort assignments.

No denormalized sort ID: ancestry checks use the live hierarchy, including
reparent operations. Checks are scoped to the affected employee; only explicit
hierarchy edits validate the assignment set.
"""
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import StaffingShiftFlowPlan, StaffingWorkAssignment


# A recursive projection also supports Work Areas attached directly to an
# Operation. Cycles are already rejected by Org Chart validation.
ANCESTRY = """WITH RECURSIVE ancestry(area_id, id, parent_id, unit_type) AS (
 SELECT id, id, parent_id, unit_type FROM staffing_units
 UNION SELECT a.area_id, u.id, u.parent_id, u.unit_type
 FROM ancestry a JOIN staffing_units u ON u.id=a.parent_id
)"""
INVALID = ANCESTRY + """
 SELECT 1 FROM staffing_work_assignments w JOIN staffing_people p ON p.id=w.person_id
 LEFT JOIN ancestry a ON a.area_id=w.work_area_unit_id AND a.unit_type='sort'
 WHERE w.active GROUP BY w.person_id, p.classification
 HAVING (COUNT(*) > 1 AND p.classification NOT IN
 ('full_time_combo','domiciled_full_time_combo','non_domiciled_full_time_combo'))
 OR COUNT(*) <> COUNT(DISTINCT a.id)
"""
HOME = """SELECT w.work_area_unit_id FROM staffing_work_assignments w
 JOIN staffing_units area ON area.id=w.work_area_unit_id AND area.unit_type='work_area'
 JOIN staffing_units d ON d.id=area.parent_id AND d.unit_type='department' AND lower(trim(d.name))='shift'
 JOIN staffing_units o ON o.id=d.parent_id AND o.unit_type='operation' AND lower(trim(o.name))='ramp'
 JOIN staffing_units s ON s.id=o.parent_id AND s.unit_type='sort' AND lower(trim(s.name))='night'
 WHERE w.active AND w.person_id=staffing_shift_flow_plans.staffing_person_id"""


def _rebuild_sqlite(model):
    """Copy the exact old columns/IDs; no assignment is discarded."""
    table = model.__table__
    name = table.name
    legacy = name + "_assignment_upgrade"
    connection = db.session.connection()
    if inspect(connection).has_table(legacy):
        raise RuntimeError(f"Unexpected unfinished migration table: {legacy}")
    connection.execute(text("PRAGMA legacy_alter_table=ON"))
    connection.execute(text(f"ALTER TABLE {name} RENAME TO {legacy}"))
    from app.services.schema_sync import _drop_sqlite_indexes_for_table
    _drop_sqlite_indexes_for_table(legacy)
    table.create(connection)
    columns = ", ".join(column.name for column in table.columns)
    connection.execute(text(f"INSERT INTO {name} ({columns}) SELECT {columns} FROM {legacy}"))
    connection.execute(text(f"DROP TABLE {legacy}"))
    connection.execute(text("PRAGMA legacy_alter_table=OFF"))


def sync_staffing_assignment_schema():
    connection = db.session.connection()
    inspector = inspect(connection)
    if not inspector.has_table("staffing_work_assignments"):
        return
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(text("SELECT pg_advisory_xact_lock(61090142)"))
        connection.execute(text("ALTER TABLE staffing_work_assignments DROP CONSTRAINT IF EXISTS uq_staffing_work_assignments_person"))
        connection.execute(text("ALTER TABLE staffing_shift_flow_plans ALTER COLUMN final_door_work_area_id DROP NOT NULL"))
    else:
        if any(item["column_names"] == ["person_id"] for item in inspector.get_unique_constraints("staffing_work_assignments")):
            _rebuild_sqlite(StaffingWorkAssignment)
        if any(col["name"] == "final_door_work_area_id" and not col["nullable"]
               for col in inspector.get_columns("staffing_shift_flow_plans")):
            _rebuild_sqlite(StaffingShiftFlowPlan)
    if connection.execute(text(INVALID)).first():
        raise RuntimeError("Existing Staffing assignments violate one active assignment per Sort/non-FT person. No records were discarded.")
    # Home wins over the old independent Sort Start. Preserve valid optional
    # values; remove dormant plans only for people who no longer belong to Shift.
    connection.execute(text(f"DELETE FROM staffing_shift_flow_plans WHERE NOT EXISTS ({HOME})"))
    connection.execute(text(f"UPDATE staffing_shift_flow_plans SET sort_start_work_area_id=({HOME}) WHERE sort_start_work_area_id <> ({HOME})"))
    connection.execute(text("""UPDATE staffing_shift_flow_plans SET ballmat_transition=NULL
        WHERE ballmat_transition IS NOT NULL AND NOT EXISTS (SELECT 1 FROM staffing_units
        WHERE id=sort_start_work_area_id AND lower(name) LIKE '%ballmat%')"""))
    for field in ("setup_work_area_id", "final_door_work_area_id"):
        connection.execute(text(f"""UPDATE staffing_shift_flow_plans SET {field}=NULL
            WHERE {field} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM staffing_units area
            JOIN staffing_units home ON home.id=sort_start_work_area_id
            WHERE area.id={field} AND area.active AND area.parent_id=home.parent_id)"""))
    install_assignment_guards(connection)


def install_assignment_guards(connection):
    scoped = INVALID.replace("WHERE w.active", "WHERE w.active AND w.person_id=checked_person")
    if connection.dialect.name == "postgresql":
        connection.execute(text(f"""CREATE OR REPLACE FUNCTION staffing_assignment_validate() RETURNS trigger AS $$
        DECLARE checked_person integer;
        BEGIN
          IF TG_TABLE_NAME='staffing_people' THEN checked_person := NEW.id;
          ELSE checked_person := NEW.person_id; END IF;
          PERFORM id FROM staffing_people WHERE id=checked_person FOR UPDATE;
          IF EXISTS ({scoped}) THEN
            RAISE EXCEPTION 'Only FT Combo may have multiple Work Assignments, one per Sort.' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END $$ LANGUAGE plpgsql"""))
        for table, action in (("staffing_work_assignments", "INSERT OR UPDATE"),
                              ("staffing_people", "UPDATE OF classification")):
            name = f"{table}_validate"
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
            connection.execute(text(f"CREATE TRIGGER {name} AFTER {action} ON {table} FOR EACH ROW EXECUTE FUNCTION staffing_assignment_validate()"))
        connection.execute(text(f"""CREATE OR REPLACE FUNCTION staffing_topology_validate() RETURNS trigger AS $$
        BEGIN
          PERFORM id FROM staffing_people WHERE id IN (SELECT person_id FROM staffing_work_assignments WHERE active) ORDER BY id FOR UPDATE;
          IF EXISTS ({INVALID}) THEN RAISE EXCEPTION 'Reparent would duplicate a Sort assignment.' USING ERRCODE='23514'; END IF;
          RETURN NULL;
        END $$ LANGUAGE plpgsql"""))
        connection.execute(text("DROP TRIGGER IF EXISTS staffing_topology_validate ON staffing_units"))
        connection.execute(text("CREATE TRIGGER staffing_topology_validate AFTER UPDATE OF parent_id ON staffing_units FOR EACH STATEMENT EXECUTE FUNCTION staffing_topology_validate()"))
        connection.execute(text(f"""CREATE OR REPLACE FUNCTION staffing_flow_home_validate() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM staffing_shift_flow_plans
             WHERE staffing_person_id=NEW.staffing_person_id
             AND (NOT EXISTS ({HOME}) OR sort_start_work_area_id <> ({HOME}))) THEN
            RAISE EXCEPTION 'Shift Sort Start must equal canonical Night Home.' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END $$ LANGUAGE plpgsql"""))
        connection.execute(text("DROP TRIGGER IF EXISTS staffing_flow_home_validate ON staffing_shift_flow_plans"))
        connection.execute(text("""CREATE CONSTRAINT TRIGGER staffing_flow_home_validate
        AFTER INSERT OR UPDATE ON staffing_shift_flow_plans DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION staffing_flow_home_validate()"""))
        connection.execute(text(f"""CREATE OR REPLACE FUNCTION staffing_flow_home_sync() RETURNS trigger AS $$
        DECLARE affected integer;
        BEGIN
          IF TG_OP='DELETE' THEN affected:=OLD.person_id; ELSE affected:=NEW.person_id; END IF;
          DELETE FROM staffing_shift_flow_plans WHERE staffing_person_id=affected AND NOT EXISTS ({HOME});
          UPDATE staffing_shift_flow_plans SET sort_start_work_area_id=({HOME}), updated_at=clock_timestamp()
          WHERE staffing_person_id=affected AND sort_start_work_area_id <> ({HOME});
          RETURN NULL;
        END $$ LANGUAGE plpgsql"""))
        connection.execute(text("DROP TRIGGER IF EXISTS staffing_flow_home_sync ON staffing_work_assignments"))
        # ORM lifecycle work runs during flush. The database backstop runs at
        # commit, after those changes, so it does not delete/update the same
        # plan halfway through SQLAlchemy's unit of work.
        connection.execute(text("CREATE CONSTRAINT TRIGGER staffing_flow_home_sync AFTER INSERT OR UPDATE OR DELETE ON staffing_work_assignments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION staffing_flow_home_sync()"))
    else:
        for table in ("staffing_work_assignments", "staffing_people", "staffing_units"):
            for action in ("INSERT", "UPDATE"):
                name = f"{table}_validate_{action.lower()}"
                query = INVALID if table == "staffing_units" else scoped.replace("checked_person", "NEW.id" if table == "staffing_people" else "NEW.person_id")
                connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
                connection.execute(text(f"""CREATE TRIGGER {name} AFTER {action} ON {table}
                BEGIN SELECT CASE WHEN EXISTS ({query}) THEN
                RAISE(ABORT, 'Only FT Combo may have multiple Work Assignments, one per Sort.') END; END"""))
