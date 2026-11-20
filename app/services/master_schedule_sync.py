"""Reconcile one master edit with current operations in its caller's transaction."""
from datetime import time

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.gateway_matrix import (
    current_gateway_local_datetime, current_operations_for_gateway, sort_lookup_window_for_operation,
)
from app.services.mission_identity import flight_identity


class MasterScheduleConflict(Exception):
    """A live or concurrent decision prevents applying the complete master edit."""


def lock_schedule_gateway(gateway_code):
    db.session.query(Gateway.id).filter_by(code=gateway_code).with_for_update().first()


def lock_master_editor(gateway):
    # Other editors (including Rain Load Planner) lock the operation before its master.
    lock_schedule_gateway(gateway.code)
    ids = [op.id for op in current_operations_for_gateway(gateway)]
    if ids:
        db.session.query(SortDateOperation.id).filter(SortDateOperation.id.in_(ids)).order_by(
            SortDateOperation.id).with_for_update().all()


def master_template(master):
    fields = ("sort_name", "mission_type", "flight_number", "origin", "destination", "wave",
              "timezone", "planned_time_local", "pure_pull_time_local", "mix_pull_time_local")
    values = {field: getattr(master, field) for field in fields}
    return {key: value.isoformat() if isinstance(value, time) else value for key, value in values.items()}


def _check_value(mission, field, previous, desired, source=None):
    value = getattr(mission, field)
    if value == desired:
        return
    if value != previous or (source and source not in ("master", "unknown")):
        raise MasterScheduleConflict(
            f"{mission.flight_number} has a current-Sort override for {field.replace('_', ' ')}. "
            "The schedule edit was rolled back."
        )
    setattr(mission, field, desired)


def update_linked_template(mission, master, previous, operation):
    from app.services.sort_date_operations import _planned_datetime_local, _planned_datetime_utc, normalize_wave
    desired = master_template(master)
    if previous == desired:
        return False
    if mission.mission_type != master.mission_type:
        raise MasterScheduleConflict(f"{mission.flight_number} already exists as a {mission.mission_type} in this Sort.")
    if flight_identity(previous["flight_number"]) != flight_identity(desired["flight_number"]):
        raise MasterScheduleConflict(f"{mission.flight_number} already has a current-Sort mission. Its flight identity is protected.")
    for field in ("flight_number", "origin", "destination", "wave", "timezone"):
        if desired[field] != previous[field]:
            old, new = previous[field], desired[field]
            if field == "wave":
                old, new = normalize_wave(old), normalize_wave(new)
            _check_value(mission, field, old, new)
    if any(previous[key] != desired[key] for key in ("planned_time_local", "timezone")):
        def planned(template):
            local = _planned_datetime_local(operation.sort_date, operation.sort_name, time.fromisoformat(template["planned_time_local"]))
            return local, _planned_datetime_utc(local, template["timezone"])
        old, new = planned(previous), planned(desired)
        for field, prior, updated in zip(("planned_datetime_local", "planned_datetime_utc"), old, new):
            _check_value(mission, field, prior, updated, mission.planned_source)
        if mission.planned_source in (None, "unknown"):
            mission.planned_source = "master"
    for field in ("pure_pull_time_local", "mix_pull_time_local"):
        if previous[field] != desired[field]:
            old = time.fromisoformat(previous[field]) if previous[field] else None
            _check_value(mission, field, old, getattr(master, field), mission.pull_time_source)
    if any(previous[field] != desired[field] for field in ("pure_pull_time_local", "mix_pull_time_local")):
        if mission.pull_time_source in (None, "master", "unknown"):
            mission.pull_time_source = "master" if mission.pure_pull_time_local or mission.mix_pull_time_local else None
    mission.master_template = desired
    return True


def _protect_work_in_progress(mission):
    from app.models import NeoScorpionFuelAssignment as Assignment, NeoScorpionFuelWorkState as Work
    from app.models import NeoSubZeroDepartureDeiceEvent as Spray, NeoSubZeroSprayRecord as Record
    if mission.mission_type != "departure":
        return
    fueling = (db.session.query(Work.id).join(Assignment, Assignment.id == Work.fuel_assignment_id)
               .filter(Assignment.sort_date_mission_id == mission.id, Work.physical_finalized_at_utc.is_(None),
                       db.or_(Work.on_at_utc.isnot(None), Work.confirmed_on_at_utc.isnot(None),
                              Work.manual_on_at_utc.isnot(None))).first())
    if fueling:
        raise MasterScheduleConflict(f"{mission.flight_number} has an open fueling visit. Finish or resolve it before retirement.")
    truck_spray = db.session.query(Record.id).filter(
        Record.departure_deice_event_id == Spray.id,
        db.or_(Record.started_at_utc.isnot(None), Record.ended_at_utc.isnot(None)),
    ).exists()
    spraying = Spray.query.filter_by(sort_date_operation_id=mission.sort_date_operation_id, reset_at_utc=None,
                                     radio_completed_at_utc=None).filter(
        db.or_(Spray.sort_date_mission_id == mission.id, Spray.tail_number == mission.assigned_tail_number),
        db.or_(Spray.radio_state == "pending", Spray.status == "finished", truck_spray,
               Spray.pass1_started_at_utc.isnot(None), Spray.pass1_ended_at_utc.isnot(None),
               Spray.pass2_started_at_utc.isnot(None), Spray.pass2_ended_at_utc.isnot(None))).first()
    if spraying:
        raise MasterScheduleConflict(f"{mission.flight_number} has open treatment or radio work. Resolve it before retirement.")


def _reconcile_operation(master, previous, operation):
    from app.services.sort_date_operations import (
        _build_mission_from_master, create_default_crew_assignments_for_mission,
        ensure_tail_state_for_mission, master_schedule_runs_on_date,
    )
    eligible = master.active and master.sort_name == operation.sort_name and master_schedule_runs_on_date(master, operation.sort_date)
    linked = (SortDateMission.query.filter_by(sort_date_operation_id=operation.id, master_flight_schedule_id=master.id)
              .populate_existing().with_for_update().all())
    if len(linked) > 1:
        raise MasterScheduleConflict(f"{master.flight_number} has multiple linked missions. Review this Sort before editing.")
    mission = next(iter(linked), None)
    if not eligible and not mission:
        return False
    if operation.locked_at_utc:
        raise MasterScheduleConflict(f"The current {operation.sort_name} Sort is locked.")
    if mission:
        status_field = "departure_status" if mission.mission_type == "departure" else "arrival_status"
        status = getattr(mission, status_field)
        if not eligible:
            if status != "cancelled":
                _protect_work_in_progress(mission)
                mission.master_retirement = {"status": status}
                mission.master_template = mission.master_template or previous
                setattr(mission, status_field, "cancelled")
                return True
            return False
        restoring = bool(mission.master_retirement)
        if restoring:
            if status != "cancelled":
                raise MasterScheduleConflict(f"{mission.flight_number} changed after retirement. Review its operational status.")
            setattr(mission, status_field, mission.master_retirement["status"])
            mission.master_retirement = None
        elif status == "cancelled":
            return False  # Only the original cancellation authority can restore this mission.
        updated = update_linked_template(mission, master, mission.master_template or previous, operation)
        mission.master_template = master_template(master)
        return restoring or updated

    identity = flight_identity(master.flight_number)
    numbers = db.session.query(SortDateMission.flight_number).filter_by(sort_date_operation_id=operation.id)
    if any(flight_identity(number) == identity for (number,) in numbers):
        raise MasterScheduleConflict(f"{master.flight_number} already belongs to a different mission in this Sort.")
    mission = _build_mission_from_master(operation, master, operation.sort_date)
    db.session.add(mission)
    db.session.flush()
    tail = ensure_tail_state_for_mission(mission)
    create_default_crew_assignments_for_mission(mission, tail.aircraft_type if tail else "unknown")
    return True


def reconcile_current_sort(master, gateway, previous):
    if not gateway:
        return
    db.session.add(master)
    db.session.flush()
    now = current_gateway_local_datetime(gateway)
    names = {master.sort_name}
    if previous:
        names.add(previous["sort_name"])
    candidates = [op.id for op in current_operations_for_gateway(gateway)
                  if op.sort_name in names and now < sort_lookup_window_for_operation(op, gateway)[1]]
    for operation in (SortDateOperation.query.filter(SortDateOperation.id.in_(candidates))
                      .filter_by(archived_at_utc=None).order_by(SortDateOperation.id)
                      .populate_existing().with_for_update()):
        if _reconcile_operation(master, previous, operation):
            from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change
            _, state = lock_nightly_asset_scope_for_mutation(operation)
            record_nightly_operational_change(state, operation.id)
