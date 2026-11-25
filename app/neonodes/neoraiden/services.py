"""Gateway-scoped, read-only projections of committed operational facts."""
from collections import Counter, defaultdict
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import load_only

from app.extensions import db
from app.models import (
    NeoErmacBuildingLineup, NeoErmacDoorPull, NeoNode, NeoSubZeroDepartureDeiceEvent,
    NeoScorpionAircraftFuelSetting, NeoScorpionDirectEntry, NeoScorpionFuelAssignment,
    NeoScorpionFuelAuditEntry, NeoScorpionFuelTankState, NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionFuelingEventTankSnapshot,
    NeoScorpionSettings, NeoScorpionSortTruck, NeoScorpionTailFuelState,
    SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState,
    StaffingDailyAttendance, StaffingAttendanceSummary, StaffingUnit,
)
from app.models.user import ROLE_LEVELS
from app.services.account_authority import Scope
from app.services.account_scope_runtime import authority
from app.services.gateway_matrix import configured_sort_window_utc, current_gateway_local_datetime, gateway_timezone
from app.services.neorain_fuel_authority import scorpion_board_fuel_by_mission
from app.services.neorain_performance import average_lateness, completed_variance_minutes
from app.services.neoscorpion_fueling_status import STAGE_COLORS
from app.services.neosektor_live_counts import native_uld_summary
from app.services.permission_rules import user_can
from app.services.neosubzero_evidence import SHARED_TAIL_REVIEW, normalized_tail, select_tail_event
from app.services.neosubzero_operations import departure_presentation
from app.services.neosubzero_radio import radio_complete, radio_pending
from app.services.departure_labels import departure_occurrences, departure_order, destination_label, effective_departure_utc
from app.services.time_display import format_local_hhmm
from app.services.sort_date_operations import _planned_datetime_utc, normalize_wave
from .ermac import completed_pulls


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
        SortDateOperation.sort_date, SortDateOperation.sort_name, SortDateOperation.archived_at_utc, SortDateOperation.window_minutes,
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
        NeoNode.code.in_(("scorpion","ermac","subzero","sektor","rain")), NeoNode.is_active.is_(True))
    return {code for node_id, code in nodes if evidence.role(
        Scope("node","neogateway",gateway.id,node_id))}


def arrival_sort_window(gateway, operation):
    try:
        zone = ZoneInfo(gateway_timezone(gateway))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None, None, None
    start, end = configured_sort_window_utc(gateway, operation.sort_date, operation.sort_name, zone)
    return zone, start, end


def usable_pull_lineup(gateway, operation, window):
    # NeoErmac's lineup is mutable and gateway-wide. Use it only for the
    # operating date, including yesterday's overnight Sort while it is active.
    if operation.archived_at_utc:
        return False
    local_now = current_gateway_local_datetime(gateway)
    if operation.sort_date == local_now.date():
        return True
    zone, start, end = window
    return bool(start is not None and start <= local_now.replace(tzinfo=zone) < end)


def _arrival_sort_time(mission, planned, window):
    reported = mission.actual_block_in_datetime_utc or mission.eta_datetime_utc
    value = reported or planned
    if value is None or mission.origin.strip().upper() == "HOT":
        return None
    instant = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    zone, start, end = window
    if start is None or start <= instant < end:
        return instant
    if reported is None and instant < start:
        local = instant.astimezone(zone)
        # Legacy imported STAs can carry the Sort date for the after-midnight leg.
        if local.date() == start.astimezone(zone).date():
            next_day = local + timedelta(days=1)
            shifted = next_day.astimezone(timezone.utc)
            if start <= shifted < end and shifted.astimezone(zone) == next_day:
                return shifted
    return None


def board_sources(user, gateway, operation):
    access = _source_access(user, gateway)
    uld = native_uld_summary(gateway, operation.sort_date, operation.sort_name) if "sektor" in access else None
    return access, uld


def postgres_board_versions(gateway, operation, access):
    """One Sort-scoped read. Ordered IDs plus MVCC row versions cover inserts, edits and removals."""
    from sqlalchemy import String, cast, func, literal_column, select
    from sqlalchemy.dialects.postgresql import aggregate_order_by

    op_id = operation.id
    def versions(model, *where):
        version = func.concat(cast(model.id, String), ':', literal_column('xmin::text'))
        return select(func.md5(func.string_agg(version, aggregate_order_by(',', model.id)))).where(*where).scalar_subquery()

    summary = StaffingAttendanceSummary
    summary_scope = (summary.sort_date_operation_id == op_id, summary.scope_type == "operation")
    columns = [
        func.pg_postmaster_start_time(),
        versions(SortDateMission, SortDateMission.sort_date_operation_id == op_id, SortDateMission.master_retirement.is_(None)),
        versions(SortDateParkingAssignment, SortDateParkingAssignment.sort_date_operation_id == op_id),
        versions(StaffingDailyAttendance, StaffingDailyAttendance.sort_date_operation_id == op_id,
                 StaffingDailyAttendance.attendance_date == operation.sort_date),
        versions(summary, *summary_scope),
        versions(StaffingUnit, StaffingUnit.id.in_(select(summary.scope_unit_id).where(*summary_scope))),
    ]
    if 'ermac' in access:
        columns.extend((
            versions(NeoErmacDoorPull, NeoErmacDoorPull.gateway_id == gateway.id,
                     NeoErmacDoorPull.sort_date_operation_id == op_id),
            versions(NeoErmacBuildingLineup, NeoErmacBuildingLineup.gateway_id == gateway.id),
        ))
    if 'subzero' in access:
        columns.extend((
            versions(NeoSubZeroDepartureDeiceEvent, NeoSubZeroDepartureDeiceEvent.sort_date_operation_id == op_id,
                     NeoSubZeroDepartureDeiceEvent.reset_at_utc.is_(None)),
            versions(SortDateTailState, SortDateTailState.gateway_code == gateway.code,
                     SortDateTailState.sort_date == operation.sort_date, SortDateTailState.sort_name == operation.sort_name),
        ))
    if 'scorpion' in access:
        assignment = NeoScorpionFuelAssignment
        work = NeoScorpionFuelWorkState
        event = NeoScorpionFuelingEvent
        assignments = select(assignment.id).where(assignment.sort_date_operation_id == op_id)
        works = select(work.id).where(work.fuel_assignment_id.in_(assignments))
        events = select(event.id).where(event.sort_date_operation_id == op_id)
        columns.extend(versions(model, model.sort_date_operation_id == op_id) for model in (
            assignment, event, NeoScorpionDirectEntry, NeoScorpionFuelAuditEntry,
            NeoScorpionSortTruck, NeoScorpionTailFuelState,
        ))
        columns.extend(versions(model, model.gateway_id == gateway.id) for model in (
            NeoScorpionFuelTruck, NeoScorpionSettings, NeoScorpionAircraftFuelSetting,
        ))
        columns.extend((
            versions(work, work.fuel_assignment_id.in_(assignments)),
            versions(NeoScorpionFuelTankState, NeoScorpionFuelTankState.fuel_work_state_id.in_(works)),
            versions(NeoScorpionFuelingEventTankSnapshot, NeoScorpionFuelingEventTankSnapshot.fueling_event_id.in_(events)),
        ))
        if 'subzero' not in access:
            columns.append(versions(SortDateTailState, SortDateTailState.gateway_code == gateway.code,
                SortDateTailState.sort_date == operation.sort_date, SortDateTailState.sort_name == operation.sort_name))
    return tuple(str(value) for value in db.session.execute(select(*columns)).one())


def snapshot(user, gateway, operation, *, sources=None):
    result = dict(gateway=gateway.code, operation_id=operation.id if operation else None,
                  sort_label=None, arrivals=[], departures=[], staffing=[], marked_here=None, window=None,
                  uld=None, performance={}, pull_roster={})
    if operation is None:
        return result
    if operation.gateway_code != gateway.code or operation.gateway_id not in (None, gateway.id):
        raise ValueError("Sort does not belong to the selected gateway.")
    op_id = operation.id
    result.update(sort_label=f"{operation.sort_date} · {operation.sort_name.title()}",
                  window=operation.window_minutes)
    access, result["uld"] = sources if sources is not None else board_sources(user, gateway, operation)
    m = SortDateMission
    query = m.query.filter_by(sort_date_operation_id=op_id,
        gateway_code=gateway.code, sort_date=operation.sort_date, sort_name=operation.sort_name
    ).filter(m.master_retirement.is_(None))
    # Scorpion can project detached visits from the full mission row.
    if "scorpion" not in access:
        query = query.options(load_only(m.id, m.mission_type, m.flight_number, m.assigned_tail_number,
            m.sort_name, m.origin, m.destination, m.timezone, m.wave, m.planned_datetime_utc, m.planned_datetime_local,
            m.eta_datetime_utc, m.actual_block_in_datetime_utc, m.actual_block_out_datetime_utc,
            m.departure_status, m.arrival_status, m.master_flight_schedule_id, m.late_metrics_included_override,
            m.ramp_load_completed_at_utc, m.crew_load_completed_at_utc))
    missions = query.all()
    positions = {normalized_tail(tail): (position if str(position).startswith(str(ramp)) else f"{ramp or ''}{position}")
                 + (f" · Lane {lane}" if lane and lane != 1 else "")
        for tail, ramp, position, lane in db.session.query(SortDateParkingAssignment.tail_number,
            SortDateParkingAssignment.ramp_code, SortDateParkingAssignment.position_code,
            SortDateParkingAssignment.lane_number).filter_by(sort_date_operation_id=op_id)
        if tail and position}
    inbound_missions = [m for m in missions if m.mission_type == "arrival"]
    departures = [m for m in missions if m.mission_type == "departure"]
    window = arrival_sort_window(gateway, operation)
    if "rain" in access:
        result["performance"] = dict(inbound=average_lateness(inbound_missions), outbound=average_lateness(departures))
    pulls = {}
    if "ermac" in access and usable_pull_lineup(gateway, operation, window):
        result["pull_roster"], pulls = completed_pulls(gateway, operation, departures)
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
    fuel = scorpion_board_fuel_by_mission(operation, departures) if "scorpion" in access else {}
    departures.sort(key=lambda m: departure_order(m, operation))
    ranks = departure_occurrences(departures, operation)
    tails = Counter(normalized_tail(m.assigned_tail_number) for m in departures)
    treatment = {tail: select_tail_event(candidates) for tail, candidates in events.items()}
    detached_radio = {event.sort_date_mission_id for tail, (event, _) in treatment.items()
                      if tail not in tails and radio_pending(event)}
    arrivals = []
    for mission in inbound_missions + departures:
        inbound = mission.mission_type == "arrival"
        tail = normalized_tail(mission.assigned_tail_number)
        actual = mission.actual_block_in_datetime_utc if inbound else mission.actual_block_out_datetime_utc
        planned = mission.planned_datetime_utc
        if inbound and planned is None and mission.planned_datetime_local is not None:
            planned = _planned_datetime_utc(mission.planned_datetime_local, mission.timezone)
        row = dict(id=mission.id, flight=mission.flight_number, tail=tail,
                   route=mission.origin if inbound else mission.destination,
                   parking=positions.get(tail), ordinal=None,
                   eta=format_local_hhmm(mission.eta_datetime_utc, mission.timezone) if inbound else None,
                   planned=format_local_hhmm(planned,mission.timezone),
                   actual=format_local_hhmm(actual,mission.timezone),
                   status=mission.arrival_status if inbound else mission.departure_status,
                   variance=completed_variance_minutes(mission) if "rain" in access else None)
        if inbound:
            row["wave"] = normalize_wave(mission.wave)
            instant = _arrival_sort_time(mission, planned, window)
            arrivals.append(((instant is None, instant, mission.id), row))
            continue
        row["ordinal"] = ranks.get(mission.id)
        row["route_label"] = destination_label(mission.destination, row["ordinal"])
        fuel_row = fuel.get(mission.id, {})
        if "rain" in access:
            row.update(ramp=format_local_hhmm(mission.ramp_load_completed_at_utc, mission.timezone),
                       crew=format_local_hhmm(mission.crew_load_completed_at_utc, mission.timezone),
                       block=row["actual"], no_return=mission.departure_status == "departed")
        event, review = treatment.get(tail, (None, None))
        if event and tails[tail] > 1 and event.sort_date_mission_id != mission.id:
            event, review = None, SHARED_TAIL_REVIEW
        if mission.id in detached_radio:
            review = "Review the former tail's pending radio call."
        deice_state = deice = None
        tail_state = tail_states.get(tail) if tails[tail] == 1 else None
        if tail and (event or review or tail_state):
            deice_state, deice = departure_presentation(dict(event=event, tail=tail, ownership_review=review,
                radio_complete=radio_complete(event),radio_pending=radio_pending(event)), tail_state)
        pure, mix = pulls.get(mission.id, (None, None))
        row.update(fuel_status=mission.fuel_status if "scorpion" in access else None,
                   planned_fuel=mission.planned_fuel_load if "scorpion" in access else None,
                   neo_fuel=fuel_row.get("scorpion_fuel_display"),
                   fuel_color=STAGE_COLORS.get(fuel_row.get("scorpion_fuel_status")),
                   etd=format_local_hhmm(effective_departure_utc(mission, operation), mission.timezone),
                   pure=pure, mix=mix, deice=deice, deice_state=deice_state)
        result["departures"].append(row)
    result["arrivals"] = [row for _, row in sorted(arrivals, key=lambda arrival: arrival[0])]
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
