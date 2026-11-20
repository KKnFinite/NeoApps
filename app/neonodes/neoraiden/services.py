"""Gateway-scoped, read-only projections of committed operational facts."""
from collections import Counter, defaultdict
from sqlalchemy.orm import load_only

from app.extensions import db
from app.models import (
    NeoErmacDoorPull, NeoNode, NeoSubZeroDepartureDeiceEvent,
    SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState,
    StaffingDailyAttendance, StaffingAttendanceSummary, StaffingUnit,
)
from app.models.user import ROLE_LEVELS
from app.services.account_authority import Scope
from app.services.account_scope_runtime import authority
from app.services.neosektor_live_counts import native_uld_summary
from app.services.permission_rules import user_can
from app.services.neosubzero_evidence import normalized_tail, select_tail_event
from app.services.neosubzero_operations import departure_presentation
from app.services.neosubzero_radio import radio_complete, radio_pending
from app.services.departure_labels import departure_occurrences, departure_order, destination_label, effective_departure_utc
from app.services.time_display import format_local_hhmm
from app.services.sort_date_operations import mission_display_timing_data


def authorized_gateways(user):
    evidence = authority(user)
    role = evidence.role(Scope("application", "neogateway"))
    if role not in ROLE_LEVELS or ROLE_LEVELS[role] < ROLE_LEVELS["watcher"]:
        return frozenset()
    if not user_can("neogateway.landing.view", user=user):
        return frozenset()
    return evidence.gateways


def gateway_operations(gateway):
    # Older canonical operations may have a code without a populated gateway FK.
    return SortDateOperation.query.options(load_only(
        SortDateOperation.id, SortDateOperation.gateway_id, SortDateOperation.gateway_code,
        SortDateOperation.sort_date, SortDateOperation.sort_name, SortDateOperation.window_minutes,
        SortDateOperation.first_wave_window_minutes, SortDateOperation.second_wave_window_minutes,
    )).filter(
        SortDateOperation.gateway_code == gateway.code,
        db.or_(SortDateOperation.gateway_id == gateway.id,
               SortDateOperation.gateway_id.is_(None)),
    )


def can_open_raiden(user, gateway):
    return gateway.id in authorized_gateways(user)


def _source_access(user, gateway):
    evidence = authority(user)
    nodes = db.session.query(NeoNode.id, NeoNode.code).filter(
        NeoNode.code.in_(("scorpion","ermac","subzero","sektor")), NeoNode.is_active.is_(True))
    return {code for node_id, code in nodes if evidence.role(
        Scope("node","neogateway",gateway.id,node_id))}


def _clock(value):
    return value.strftime("%H:%M") if value is not None else None


def snapshot(user, gateway, operation):
    result = dict(gateway=gateway.code, operation_id=operation.id if operation else None,
                  sort_label=None, arrivals=[], departures=[], staffing=[], marked_here=None, window=None, uld=None)
    if operation is None:
        return result
    if operation.gateway_code != gateway.code or operation.gateway_id not in (None, gateway.id):
        raise ValueError("Sort does not belong to the selected gateway.")
    op_id = operation.id
    result.update(sort_label=f"{operation.sort_date} · {operation.sort_name.title()}",
                  window=operation.window_minutes)
    m = SortDateMission
    missions = m.query.options(load_only(m.id, m.mission_type, m.flight_number, m.assigned_tail_number,
        m.origin, m.destination, m.timezone, m.wave, m.planned_datetime_utc, m.planned_datetime_local,
        m.eta_datetime_utc, m.actual_block_in_datetime_utc, m.actual_block_out_datetime_utc,
        m.departure_status, m.arrival_status, m.fuel_status, m.planned_fuel_load, m.pure_pull_time_local,
        m.mix_pull_time_local, m.actual_pure_pull_time_local, m.actual_mix_pull_time_local)).filter_by(sort_date_operation_id=op_id,
        gateway_code=gateway.code, sort_date=operation.sort_date, sort_name=operation.sort_name
    ).filter(m.master_retirement.is_(None)).order_by(SortDateMission.planned_datetime_utc.asc().nullslast(), SortDateMission.id).all()
    positions = {normalized_tail(tail): (position if str(position).startswith(str(ramp)) else f"{ramp or ''}{position}")
                 + (f" · Lane {lane}" if lane else "")
        for tail, ramp, position, lane in db.session.query(SortDateParkingAssignment.tail_number,
            SortDateParkingAssignment.ramp_code, SortDateParkingAssignment.position_code,
            SortDateParkingAssignment.lane_number).filter_by(sort_date_operation_id=op_id)
        if tail and position}
    access = _source_access(user, gateway)
    if "sektor" in access:
        result["uld"] = native_uld_summary(gateway, operation.sort_date, operation.sort_name)
    pulls = defaultdict(list)
    if "ermac" in access:
        for pull in NeoErmacDoorPull.query.filter_by(gateway_id=gateway.id, sort_date_operation_id=op_id
                ).filter(NeoErmacDoorPull.sort_date_mission_id.isnot(None)).order_by(NeoErmacDoorPull.door, NeoErmacDoorPull.id):
            pulls[pull.sort_date_mission_id].append(dict(door=pull.door,
                pure="No pull" if pull.no_pure_pull else _clock(pull.actual_pure_pull_time_local),
                mix="No pull" if pull.no_mix_pull else _clock(pull.actual_mix_pull_time_local)))
    events = defaultdict(list)
    tail_states = {}
    if "subzero" in access:
        e = NeoSubZeroDepartureDeiceEvent
        for event in e.query.options(load_only(e.id, e.sort_date_mission_id, e.tail_number, e.status,
                e.reset_at_utc, e.radio_state, e.radio_completed_at_utc, e.radio_completed_by_user_id,
                e.cleared_at_utc, e.pass1_started_at_utc, e.configured_at_utc)).filter_by(sort_date_operation_id=op_id, reset_at_utc=None):
            events[normalized_tail(event.tail_number)].append(event)
        tail_states = {normalized_tail(s.tail_number): s for s in SortDateTailState.query.options(
            load_only(SortDateTailState.tail_number, SortDateTailState.deice_status)).filter_by(
            sort_date=operation.sort_date, gateway_code=gateway.code, sort_name=operation.sort_name)
            .filter(SortDateTailState.deice_status != "unknown")}
    departures = [m for m in missions if m.mission_type == "departure"]
    timings = {m.id: mission_display_timing_data(m, operation) for m in departures}
    departures.sort(key=lambda m: departure_order(m, operation))
    ranks = departure_occurrences(departures, operation)
    tails = Counter(normalized_tail(m.assigned_tail_number) for m in departures)
    for mission in [m for m in missions if m.mission_type == "arrival"] + departures:
        inbound = mission.mission_type == "arrival"
        tail = normalized_tail(mission.assigned_tail_number)
        actual = mission.actual_block_in_datetime_utc if inbound else mission.actual_block_out_datetime_utc
        row = dict(id=mission.id, flight=mission.flight_number, tail=tail,
                   route=mission.origin if inbound else mission.destination,
                   parking=positions.get(tail), ordinal=None,
                   eta=format_local_hhmm(mission.eta_datetime_utc, mission.timezone) if inbound else None,
                   planned=format_local_hhmm(mission.planned_datetime_utc,mission.timezone),
                   actual=format_local_hhmm(actual,mission.timezone),
                   status=mission.arrival_status if inbound else mission.departure_status)
        if inbound:
            result["arrivals"].append(row)
            continue
        row["ordinal"] = ranks.get(mission.id)
        row["route_label"] = destination_label(mission.destination, row["ordinal"])
        candidates = events.get(tail, ())
        if tails[tail] > 1:
            candidates = [e for e in candidates if e.sort_date_mission_id == mission.id]
        event, review = select_tail_event(candidates)
        deice = None
        tail_state = tail_states.get(tail) if tails[tail] == 1 else None
        if tail and (event or review or tail_state):
            _, deice = departure_presentation(dict(event=event, tail=tail, ownership_review=review,
                radio_complete=radio_complete(event),radio_pending=radio_pending(event)), tail_state)
        row.update(fuel_status=mission.fuel_status if "scorpion" in access else None,
                   planned_fuel=mission.planned_fuel_load if "scorpion" in access else None,
                   etd=format_local_hhmm(effective_departure_utc(mission, operation), mission.timezone),
                   pulls=pulls[mission.id], pure=_clock(timings[mission.id]["adjusted_pure_pull_time"]) if "ermac" in access else None,
                   mix=_clock(timings[mission.id]["adjusted_mix_pull_time"]) if "ermac" in access else None,
                   actual_pure=_clock(mission.actual_pure_pull_time_local) if "ermac" in access else None,
                   actual_mix=_clock(mission.actual_mix_pull_time_local) if "ermac" in access else None, deice=deice)
        result["departures"].append(row)
    # Attendance marks are distinct people, independent of the finalized payroll summary.
    attendance = StaffingDailyAttendance
    marked, here = (db.session.query(db.func.count(db.distinct(attendance.person_id)),
        db.func.count(db.distinct(db.case((attendance.status == "here", attendance.person_id)))))
        .filter(attendance.sort_date_operation_id == op_id, attendance.attendance_date == operation.sort_date).one())
    result["marked_here"] = here if marked else None
    result["staffing"] = [dict(area=name, payroll=payroll, worked=worked)
        for name, payroll, worked in db.session.query(StaffingUnit.name,
            StaffingAttendanceSummary.on_payroll_count, StaffingAttendanceSummary.worked_count)
        .join(StaffingAttendanceSummary, StaffingAttendanceSummary.scope_unit_id == StaffingUnit.id)
        .filter(StaffingAttendanceSummary.sort_date_operation_id == op_id,
                StaffingAttendanceSummary.scope_type == "operation").order_by(StaffingUnit.name, StaffingUnit.id)]
    return result
