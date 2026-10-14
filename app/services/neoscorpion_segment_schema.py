"""One-time legacy identity conversion, preserving work/tank/event primary keys."""
from sqlalchemy import inspect, text


def migrate_physical_segment_identity(connection):
    inspector = inspect(connection)
    table = "neoscorpion_fuel_work_states"
    tables = set(inspector.get_table_names())
    if table not in tables or "neoscorpion_fuel_assignments" not in tables:
        return False
    from app.models import NeoScorpionFuelWorkState
    for index in NeoScorpionFuelWorkState.__table__.indexes:
        index.create(bind=connection,checkfirst=True)
    legacy = any(item.get("name") == "uq_neoscorpion_fuel_work_state_assignment_tail"
                 for item in inspector.get_unique_constraints(table))
    if not legacy:
        _ensure_postgres_segment_guards(connection)
        return False  # Never infer a current visit again after the conversion.
    if connection.dialect.name == "postgresql":
        # One-time identity/constraint conversion must see a stable legacy
        # container, including events inserted by draining older workers.
        connection.execute(text("LOCK TABLE neoscorpion_fuel_assignments, neoscorpion_fuel_work_states, neoscorpion_fueling_events, neoscorpion_fuel_tank_states IN ACCESS EXCLUSIVE MODE"))
        inspector = inspect(connection)
        if not any(item.get("name") == "uq_neoscorpion_fuel_work_state_assignment_tail"
                   for item in inspector.get_unique_constraints(table)):
            _ensure_postgres_segment_guards(connection)
            return False  # A competing bootstrap already completed conversion.
    # The former uniqueness invariant proves a single matching legacy container.
    # This is an identity link only; no ON provenance or transfer is fabricated.
    connection.execute(text("""
        UPDATE neoscorpion_fuel_assignments SET active_work_state_id = (
            SELECT work.id FROM neoscorpion_fuel_work_states work
            JOIN sort_date_missions mission ON mission.id = neoscorpion_fuel_assignments.sort_date_mission_id
            WHERE work.fuel_assignment_id = neoscorpion_fuel_assignments.id
              AND work.tail_number = mission.assigned_tail_number
        ) WHERE active_work_state_id IS NULL
    """))
    connection.execute(text("""
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY fuel_assignment_id ORDER BY id) AS visit
            FROM neoscorpion_fuel_work_states
        ) UPDATE neoscorpion_fuel_work_states SET visit_number = (
            SELECT visit FROM numbered WHERE numbered.id = neoscorpion_fuel_work_states.id
        ) WHERE visit_number IS NULL
    """))
    if connection.dialect.name == "postgresql":
        connection.execute(text("ALTER TABLE neoscorpion_fuel_work_states DROP CONSTRAINT uq_neoscorpion_fuel_work_state_assignment_tail"))
        connection.execute(text("ALTER TABLE neoscorpion_fuel_work_states ADD CONSTRAINT uq_scorpion_work_assignment_visit UNIQUE (fuel_assignment_id, visit_number)"))
    elif connection.dialect.name == "sqlite":
        for index in inspector.get_indexes(table):
            if not index["name"].startswith("sqlite_autoindex"):
                connection.execute(text('DROP INDEX "'+index["name"].replace('"','""')+'"'))
        connection.execute(text("PRAGMA legacy_alter_table=ON"))
        connection.execute(text("ALTER TABLE neoscorpion_fuel_work_states RENAME TO scorpion_work_identity_legacy"))
        columns = [c["name"] for c in inspector.get_columns("scorpion_work_identity_legacy")]
        NeoScorpionFuelWorkState.__table__.create(bind=connection)
        quoted = ", ".join('"' + name + '"' for name in columns)
        connection.execute(text(f"INSERT INTO neoscorpion_fuel_work_states ({quoted}) SELECT {quoted} FROM scorpion_work_identity_legacy"))
        connection.execute(text("DROP TABLE scorpion_work_identity_legacy"))
        connection.execute(text("PRAGMA legacy_alter_table=OFF"))
    else:
        raise RuntimeError("Unsupported physical segment identity migration")
    _separate_legacy_event_owners(connection)
    # Legacy containers keep their original PKs and event/tank references. A
    # current completed container can be sealed using its real completion audit.
    # Unfinished legacy physical work is retained as explicit review work, not
    # hidden by the new current pointer or assigned guessed transfer gallons.
    connection.execute(text("""
        UPDATE neoscorpion_fuel_work_states SET
            physical_finalized_at_utc = (SELECT COALESCE(completed_at_utc,fuel_on_board_at_utc) FROM neoscorpion_fuel_assignments a
                WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id),
            physical_finalized_by_user_id = (SELECT COALESCE(completed_by_user_id,fuel_on_board_by_user_id) FROM neoscorpion_fuel_assignments a
                WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id)
        WHERE physical_finalized_at_utc IS NULL AND EXISTS (
            SELECT 1 FROM neoscorpion_fuel_assignments a
            WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id AND (a.completed_at_utc IS NOT NULL OR a.fuel_on_board_at_utc IS NOT NULL))
    """))
    connection.execute(text("""
        UPDATE neoscorpion_fuel_work_states SET
            physical_sequence_number = COALESCE((SELECT MAX(e.sequence_number)+1
                FROM neoscorpion_fueling_events e WHERE e.fuel_work_state_id=neoscorpion_fuel_work_states.id),1),
            physical_cycle_number = COALESCE(manual_on_cycle_number,(SELECT current_cycle_number
                FROM neoscorpion_fuel_assignments a WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id)),
            physical_cycle_type = (SELECT current_cycle_type FROM neoscorpion_fuel_assignments a
                WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id),
            physical_truck_id = COALESCE(manual_on_truck_id,(SELECT assigned_truck_id
                FROM neoscorpion_fuel_assignments a WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id)),
            physical_fueler_user_id = COALESCE(manual_on_by_user_id,(SELECT assigned_fueler_user_id
                FROM neoscorpion_fuel_assignments a WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id)),
            physical_required_fuel_lbs = (SELECT mission.planned_fuel_load
                FROM neoscorpion_fuel_assignments a JOIN sort_date_missions mission ON mission.id=a.sort_date_mission_id
                WHERE a.active_work_state_id=neoscorpion_fuel_work_states.id),
            physical_transfer_review = TRUE
        WHERE physical_sequence_number IS NULL AND physical_finalized_at_utc IS NULL
          AND (manual_on_at_utc IS NOT NULL OR on_at_utc IS NOT NULL OR off_at_utc IS NOT NULL)
    """))
    _ensure_postgres_segment_guards(connection)
    return True


def _separate_legacy_event_owners(connection):
    """Retain legacy event owners; never promote a closed container into a visit.

    Legacy measurements may already have been overwritten before this upgrade.
    Keep those rows/tanks/audits verbatim. A new row receives only a provable
    later manual ON, never the container's stale tanks or assignment-wide T/F.
    """
    from datetime import datetime
    from sqlalchemy import select, func
    from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelAssignment, NeoScorpionFuelingEvent
    work = NeoScorpionFuelWorkState.__table__
    assignment = NeoScorpionFuelAssignment.__table__
    event = NeoScorpionFuelingEvent.__table__
    owners = connection.execute(select(
        event.c.fuel_work_state_id, func.max(event.c.created_at).label("sealed_at"),
        func.max(event.c.ended_at_utc).label("last_off"),
        func.count().label("events"), func.count(event.c.ended_at_utc).label("known_offs"),
    ).group_by(event.c.fuel_work_state_id)).mappings().all()
    for owner in owners:
        old = connection.execute(select(work).where(work.c.id == owner["fuel_work_state_id"])).mappings().one()
        parent = connection.execute(select(assignment).where(
            assignment.c.id == old["fuel_assignment_id"])).mappings().one()
        is_live = (parent["active_work_state_id"] == old["id"] and
                   parent["completed_at_utc"] is None and parent["fuel_on_board_at_utc"] is None)
        if is_live:
            visit = connection.execute(select(func.max(work.c.visit_number)).where(
                work.c.fuel_assignment_id == parent["id"])).scalar_one() + 1
            on = old["manual_on_at_utc"]
            proven_on = (on is not None and owner["known_offs"] == owner["events"] and
                on > owner["last_off"] and old["manual_on_cycle_number"] == parent["current_cycle_number"] and
                old["manual_on_truck_id"] is not None and old["manual_on_truck_id"] == parent["assigned_truck_id"] and
                old["manual_on_by_user_id"] is not None and old["manual_on_by_user_id"] == parent["assigned_fueler_user_id"])
            now = datetime.utcnow()
            values = dict(fuel_assignment_id=parent["id"], tail_number=old["tail_number"],
                visit_number=visit, physical_sequence_number=visit,
                physical_cycle_number=parent["current_cycle_number"], physical_cycle_type=parent["current_cycle_type"],
                physical_transfer_review=True, created_at=now, updated_at=now,
                performance_context={"exclusions": ["Legacy visit lacks verified historical timing/settings provenance"],
                    "legacy_identity_review": {"source_work_id": old["id"],
                    "reason": "Closed legacy event owner separated; reconfirm readings and segment transfer."}})
            if proven_on:
                values.update(on_at_utc=on, manual_on_at_utc=on,
                    manual_on_by_user_id=old["manual_on_by_user_id"], manual_on_truck_id=old["manual_on_truck_id"],
                    manual_on_cycle_number=old["manual_on_cycle_number"],
                    physical_truck_id=old["manual_on_truck_id"], physical_fueler_user_id=old["manual_on_by_user_id"])
                if old["off_at_utc"] is not None and old["off_at_utc"] >= on:
                    values.update(off_at_utc=old["off_at_utc"], off_by_user_id=old["off_by_user_id"],
                                  off_inventory_epoch=old["off_inventory_epoch"])
            new_id = connection.execute(work.insert().values(**values)).inserted_primary_key[0]
            connection.execute(assignment.update().where(assignment.c.id == parent["id"]).values(active_work_state_id=new_id))
        # Seal using the real event's persisted finalization, not guessed ON/OFF.
        # Original fuel data, event FKs, tank IDs and audit references stay put.
        values = {}
        if old["physical_finalized_at_utc"] is None:
            values["physical_finalized_at_utc"] = owner["sealed_at"]
            values["physical_transfer_review"] = True
        if is_live:
            values["physical_transfer_review"] = True
        if values:
            connection.execute(work.update().where(work.c.id == old["id"]).values(**values))


def _ensure_postgres_segment_guards(connection):
    if connection.dialect.name != "postgresql":
        return
    if connection.execute(text("SELECT 1 FROM neoscorpion_fuel_work_states WHERE visit_number IS NULL LIMIT 1")).scalar():
        raise RuntimeError("Physical visit identity is missing; review legacy schema before enabling writes.")
    connection.execute(text("ALTER TABLE neoscorpion_fuel_work_states ALTER COLUMN visit_number SET NOT NULL"))
    connection.execute(text("""
        CREATE OR REPLACE FUNCTION neoscorpion_row_pending(stored_xid text) RETURNS boolean AS $$
        DECLARE current_id bigint; candidate bigint;
        BEGIN
            IF stored_xid::bigint<3 THEN RETURN FALSE; END IF; -- frozen tuples
            current_id:=txid_current();
            candidate:=(current_id/4294967296)*4294967296+stored_xid::bigint;
            IF candidate-current_id>2147483648 THEN candidate:=candidate-4294967296;
            ELSIF current_id-candidate>2147483648 THEN candidate:=candidate+4294967296; END IF;
            RETURN COALESCE(pg_xact_status(candidate::text::xid8)='in progress',FALSE);
        END $$ LANGUAGE plpgsql
    """))
    # Guard committed history even if an older app worker is draining during
    # auto-deploy. Finalization metadata may still be assembled in its original
    # transaction, but another transaction cannot recycle the sealed row.
    connection.execute(text("""
        CREATE OR REPLACE FUNCTION neoscorpion_work_visit_guard() RETURNS trigger AS $$
        DECLARE stored_xid text;
        BEGIN
            SELECT xmin::text INTO stored_xid FROM neoscorpion_fuel_work_states WHERE id=OLD.id;
            IF OLD.physical_finalized_at_utc IS NOT NULL
               AND NOT neoscorpion_row_pending(stored_xid) THEN
                RAISE EXCEPTION 'Finalized physical segment is immutable; use audited correction history' USING ERRCODE='23514';
            END IF;
            IF TG_OP='UPDATE' THEN
                IF OLD.physical_finalized_at_utc IS NOT NULL AND
                    ROW(NEW.on_at_utc,NEW.confirmed_on_at_utc,NEW.manual_on_at_utc,NEW.off_at_utc,
                        NEW.physical_transfer_gallons,NEW.physical_truck_id,NEW.physical_fueler_user_id,
                        NEW.physical_cycle_number,NEW.apu_running,NEW.apu_allowance_lbs,NEW.apu_source_tank_code,
                        NEW.apu_override_enabled,NEW.apu_override_allowance_lbs)
                    IS DISTINCT FROM
                    ROW(OLD.on_at_utc,OLD.confirmed_on_at_utc,OLD.manual_on_at_utc,OLD.off_at_utc,
                        OLD.physical_transfer_gallons,OLD.physical_truck_id,OLD.physical_fueler_user_id,
                        OLD.physical_cycle_number,OLD.apu_running,OLD.apu_allowance_lbs,OLD.apu_source_tank_code,
                        OLD.apu_override_enabled,OLD.apu_override_allowance_lbs) THEN
                    RAISE EXCEPTION 'Sealed segment fuel data cannot be replaced' USING ERRCODE='23514';
                END IF;
                IF ROW(NEW.id,NEW.fuel_assignment_id,NEW.tail_number,NEW.visit_number)
                   IS DISTINCT FROM ROW(OLD.id,OLD.fuel_assignment_id,OLD.tail_number,OLD.visit_number) THEN
                    RAISE EXCEPTION 'Physical visit identity cannot be reassigned' USING ERRCODE='23514';
                END IF;
                IF OLD.off_at_utc IS NOT NULL AND
                   ROW(NEW.on_at_utc,NEW.confirmed_on_at_utc,NEW.manual_on_at_utc,NEW.manual_on_truck_id,NEW.manual_on_cycle_number)
                   IS DISTINCT FROM ROW(OLD.on_at_utc,OLD.confirmed_on_at_utc,OLD.manual_on_at_utc,OLD.manual_on_truck_id,OLD.manual_on_cycle_number) THEN
                    RAISE EXCEPTION 'OFF segment cannot be recycled for another ON' USING ERRCODE='23514';
                END IF;
                IF OLD.off_at_utc IS NOT NULL AND NEW.off_at_utc IS DISTINCT FROM OLD.off_at_utc
                   AND NOT EXISTS (SELECT 1 FROM neoscorpion_fuel_audit_entries a WHERE a.fuel_work_state_id=OLD.id
                       AND a.action='reopen_off' AND length(trim(a.reason))>0
                       AND neoscorpion_row_pending(a.xmin::text)) THEN
                    RAISE EXCEPTION 'OFF correction requires this transaction audit' USING ERRCODE='23514';
                END IF;
                IF (NEW.manual_on_at_utc IS NOT NULL OR NEW.confirmed_on_at_utc IS NOT NULL)
                   AND NEW.physical_truck_id IS NULL AND EXISTS (SELECT 1 FROM neoscorpion_fuel_assignments a
                       WHERE a.id=NEW.fuel_assignment_id AND a.assigned_truck_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'Bind the physical segment before confirming truck ON' USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            RETURN OLD;
        END $$ LANGUAGE plpgsql
    """))
    connection.execute(text("""
        CREATE OR REPLACE FUNCTION neoscorpion_tank_visit_guard() RETURNS trigger AS $$
        DECLARE owner integer; sealed timestamp; stored_xid text;
        BEGIN
            IF TG_OP='DELETE' THEN owner:=OLD.fuel_work_state_id; ELSE owner:=NEW.fuel_work_state_id; END IF;
            IF TG_OP='UPDATE' AND NEW.fuel_work_state_id<>OLD.fuel_work_state_id THEN
                RAISE EXCEPTION 'Tank measurements cannot be moved between physical visits' USING ERRCODE='23514';
            END IF;
            SELECT physical_finalized_at_utc,xmin::text INTO sealed,stored_xid
                FROM neoscorpion_fuel_work_states WHERE id=owner FOR UPDATE;
            IF sealed IS NOT NULL THEN
                RAISE EXCEPTION 'Finalized segment measurements are immutable' USING ERRCODE='23514';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """))
    connection.execute(text("DROP TRIGGER IF EXISTS neoscorpion_work_visit_guard ON neoscorpion_fuel_work_states"))
    connection.execute(text("CREATE TRIGGER neoscorpion_work_visit_guard BEFORE UPDATE OR DELETE ON neoscorpion_fuel_work_states FOR EACH ROW EXECUTE FUNCTION neoscorpion_work_visit_guard()"))
    connection.execute(text("DROP TRIGGER IF EXISTS neoscorpion_tank_visit_guard ON neoscorpion_fuel_tank_states"))
    connection.execute(text("CREATE TRIGGER neoscorpion_tank_visit_guard BEFORE INSERT OR UPDATE OR DELETE ON neoscorpion_fuel_tank_states FOR EACH ROW EXECUTE FUNCTION neoscorpion_tank_visit_guard()"))
    connection.execute(text("""
        CREATE OR REPLACE FUNCTION neoscorpion_event_visit_seal() RETURNS trigger AS $$
        BEGIN
            IF NEW.transfer_fuel_gallons>0 THEN
                UPDATE neoscorpion_fuel_work_states SET physical_finalized_at_utc=NEW.created_at
                    WHERE id=NEW.fuel_work_state_id AND physical_finalized_at_utc IS NULL;
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """))
    connection.execute(text("DROP TRIGGER IF EXISTS neoscorpion_event_visit_seal ON neoscorpion_fueling_events"))
    connection.execute(text("CREATE TRIGGER neoscorpion_event_visit_seal AFTER INSERT ON neoscorpion_fueling_events FOR EACH ROW EXECUTE FUNCTION neoscorpion_event_visit_seal()"))
    connection.execute(text("""
        CREATE OR REPLACE FUNCTION neoscorpion_event_visit_guard() RETURNS trigger AS $$
        DECLARE segment record; stored_xid text;
        BEGIN
            IF TG_OP='INSERT' THEN
                SELECT * INTO segment FROM neoscorpion_fuel_work_states
                    WHERE id=NEW.fuel_work_state_id FOR UPDATE;
                IF segment.physical_finalized_at_utc IS NOT NULL THEN
                    RAISE EXCEPTION 'A sealed physical visit cannot receive another transfer event' USING ERRCODE='23514';
                END IF;
                IF segment.physical_transfer_review OR segment.physical_cycle_type IS NULL
                   OR ROW(NEW.fuel_assignment_id,NEW.tail_number,NEW.fuel_truck_id,NEW.fueler_user_id,
                          NEW.cycle_number,NEW.event_type,NEW.transfer_fuel_gallons,NEW.sequence_number)
                      IS DISTINCT FROM ROW(segment.fuel_assignment_id,segment.tail_number,segment.physical_truck_id,
                          segment.physical_fueler_user_id,segment.physical_cycle_number,segment.physical_cycle_type,
                          segment.physical_transfer_gallons,segment.physical_sequence_number) THEN
                    RAISE EXCEPTION 'Transfer event must match its reviewed physical segment' USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            SELECT xmin::text INTO stored_xid FROM neoscorpion_fueling_events WHERE id=OLD.id;
            IF NOT neoscorpion_row_pending(stored_xid) OR TG_OP='DELETE' THEN
                RAISE EXCEPTION 'Physical transfer history is immutable' USING ERRCODE='23514';
            END IF;
            IF ROW(NEW.fuel_work_state_id,NEW.tail_number,NEW.fuel_truck_id,NEW.fueler_user_id,
                   NEW.transfer_fuel_gallons,NEW.started_at_utc,NEW.ended_at_utc,NEW.cycle_number,NEW.sequence_number)
               IS DISTINCT FROM ROW(OLD.fuel_work_state_id,OLD.tail_number,OLD.fuel_truck_id,OLD.fueler_user_id,
                   OLD.transfer_fuel_gallons,OLD.started_at_utc,OLD.ended_at_utc,OLD.cycle_number,OLD.sequence_number) THEN
                RAISE EXCEPTION 'Physical transfer facts cannot be replaced' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """))
    connection.execute(text("DROP TRIGGER IF EXISTS neoscorpion_event_visit_guard ON neoscorpion_fueling_events"))
    connection.execute(text("CREATE TRIGGER neoscorpion_event_visit_guard BEFORE INSERT OR UPDATE OR DELETE ON neoscorpion_fueling_events FOR EACH ROW EXECUTE FUNCTION neoscorpion_event_visit_guard()"))
    connection.execute(text("""
        CREATE OR REPLACE FUNCTION neoscorpion_event_tanks_guard() RETURNS trigger AS $$
        DECLARE owner integer; stored_xid text;
        BEGIN
            IF TG_OP='DELETE' THEN owner:=OLD.fueling_event_id; ELSE owner:=NEW.fueling_event_id; END IF;
            IF TG_OP='UPDATE' AND NEW.fueling_event_id<>OLD.fueling_event_id THEN
                RAISE EXCEPTION 'Event measurements cannot be reassigned' USING ERRCODE='23514';
            END IF;
            SELECT xmin::text INTO stored_xid FROM neoscorpion_fueling_events WHERE id=owner FOR UPDATE;
            IF NOT neoscorpion_row_pending(stored_xid) THEN
                RAISE EXCEPTION 'Physical event measurements are immutable' USING ERRCODE='23514';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """))
    connection.execute(text("DROP TRIGGER IF EXISTS neoscorpion_event_tanks_guard ON neoscorpion_fueling_event_tank_snapshots"))
    connection.execute(text("CREATE TRIGGER neoscorpion_event_tanks_guard BEFORE INSERT OR UPDATE OR DELETE ON neoscorpion_fueling_event_tank_snapshots FOR EACH ROW EXECUTE FUNCTION neoscorpion_event_tanks_guard()"))
