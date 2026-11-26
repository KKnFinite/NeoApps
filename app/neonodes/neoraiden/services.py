"""Gateway-scoped, read-only projections of committed operational facts."""
from collections import Counter
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import load_only

from app.extensions import db
from app.models import (
    NeoErmacBuildingLineup, NeoErmacDoorPull, NeoNode, NeoSubZeroDepartureDeiceEvent, NeoSubZeroOperationContext, NeoSubZeroPretreatState,
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
from app.services.neorain_performance import (
    ARRIVAL_COMPLETE, average_lateness, completed_actual, completed_variance_minutes,
    eligible_for_performance, late_inclusion, mission_status,
)
from app.services.neoscorpion_fueling_status import STAGE_COLORS
from app.services.neosektor_live_counts import native_uld_summary
from app.services.permission_rules import user_can
from app.services.neosubzero_evidence import SHARED_TAIL_REVIEW, normalized_tail
from app.services.neosubzero_radio import radio_pending
from app.services.neosubzero_weather import cached_metar_observation
from app.services.departure_labels import departure_occurrences, departure_order, destination_label, effective_departure_utc
from app.services.time_display import format_local_hhmm
from app.services.sort_date_operations import _planned_datetime_utc, normalize_wave
from .deice import treatment_by_tail, treatment_label
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


def _utc_instant(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _overnight_arrival_sta(instant, window):
    zone, start, end = window
    if start is None or instant >= start:
        return instant
    local = instant.astimezone(zone)
    if local.date() != start.astimezone(zone).date():
        return instant
    # Legacy imports sometimes date a post-midnight STA on the Sort's first day.
    next_day = local + timedelta(days=1)
    shifted = _utc_instant(next_day)
    alternate = _utc_instant(next_day.replace(fold=1))
    if start <= shifted < end or start <= alternate < end:
        return shifted if shifted == alternate else None
    return instant


def _arrival_sort_time(mission, planned, window):
    reported = mission.actual_block_in_datetime_utc or mission.eta_datetime_utc
    value = reported or planned
    if value is None or mission.origin.strip().upper() == "HOT":
        return None
    instant = _utc_instant(value)
    if reported is None:
        instant = _overnight_arrival_sta(instant, window)
    _, start, end = window
    return instant if start is None or (instant is not None and start <= instant < end) else None


def _arrival_variance_minutes(mission, planned, window):
    status = mission_status(mission)
    if status == "en_route":
        observed = mission.eta_datetime_utc
    elif status == "on_ground":
        observed = mission.actual_block_in_datetime_utc or mission.eta_datetime_utc
    elif status in ARRIVAL_COMPLETE:
        observed = mission.actual_block_in_datetime_utc
    else:
        return None
    if planned is None or observed is None:
        return None
    if mission.planned_datetime_utc is None:
        # A wall-clock-only STA in a DST gap or fold cannot identify an instant.
        try:
            local = mission.planned_datetime_local.replace(tzinfo=ZoneInfo(mission.timezone or "America/Chicago"), fold=0)
        except ZoneInfoNotFoundError:
            return None
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            return None
    sta = _overnight_arrival_sta(_utc_instant(planned), window)
    return int((_utc_instant(observed) - sta).total_seconds() / 60) if sta is not None else None


def board_sources(user, gateway, operation):
    access = _source_access(user, gateway)
    uld = native_uld_summary(gateway, operation.sort_date, operation.sort_name) if operation and "sektor" in access else None
    return access, uld


def board_weather(gateway, access):
    allowed = "subzero" in access
    return dict(
        allowed=allowed, supported=gateway.code == "RFD",
        observation=cached_metar_observation(gateway) if allowed else None,
        precipitation=None,
    )


def _active_precipitation(operation_id):
    return db.select(NeoSubZeroOperationContext.active_precipitation).where(
        NeoSubZeroOperationContext.sort_date_operation_id == operation_id
    ).scalar_subquery()


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
            versions(NeoSubZeroPretreatState, NeoSubZeroPretreatState.sort_date_operation_id == op_id),
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
    # Read the human-set value in the existing revision statement, including on 304s.
    precipitation = _active_precipitation(op_id) if "subzero" in access else db.null()
    *versions, active_precipitation = db.session.execute(select(*columns, precipitation)).one()
    return tuple(map(str, versions)), active_precipitation


def _flight_performance(missions):
    from app.neonodes.neorain.services import _late_average_display, _late_summary

    summary = _late_summary(missions)
    recorded_waves = {
        normalize_wave(mission.wave) for mission in missions
        if eligible_for_performance(mission) and completed_actual(mission) is not None
    }
    first, second = summary["first_wave"], summary["second_wave"]
    count = first["aircraft_late"] + second["aircraft_late"]
    minutes = first["late_minutes"] + second["late_minutes"]
    combined = dict(aircraft_late=count, late_minutes=minutes, average=_late_average_display(minutes, count))
    return {
        "first_wave": first if "1" in recorded_waves else None,
        "second_wave": second if "2" in recorded_waves else None,
        "combined": combined if recorded_waves & {"1", "2"} else None,
    }


def _live_arrival_performance(evidence):
    from app.neonodes.neorain.services import _late_average_display

    overall, included = [], []
    waves = {"1": [], "2": []}
    for mission, minutes in evidence:
        if minutes is None:
            continue
        overall.append(minutes)
        if late_inclusion(mission)["included"]:
            included.append(minutes)
            wave = normalize_wave(mission.wave)
            if wave in waves:
                waves[wave].append(minutes)

    def late_totals(values):
        late = [value for value in values if value > 0]
        return len(late), sum(late)

    def wave_summary(values):
        if not values:
            return None
        count, minutes = late_totals(values)
        return dict(aircraft_late=count, late_minutes=minutes, average=_late_average_display(minutes, count))

    def comparison_average(values):
        if not values:
            return None
        count, minutes = late_totals(values)
        return minutes / count if count else 0.0

    return (
        dict(overall=comparison_average(overall), included=comparison_average(included)),
        dict(first_wave=wave_summary(waves["1"]), second_wave=wave_summary(waves["2"]),
             combined=wave_summary(waves["1"] + waves["2"])),
    )


def snapshot(user, gateway, operation, *, sources=None, weather=None):
    if operation is not None and (operation.gateway_code != gateway.code or operation.gateway_id not in (None, gateway.id)):
        raise ValueError("Sort does not belong to the selected gateway.")
    access, uld = sources if sources is not None else board_sources(user, gateway, operation)
    weather_from_revision = weather is not None
    if not weather_from_revision:
        weather = board_weather(gateway, access)
    result = dict(gateway=gateway.code, operation_id=operation.id if operation else None,
                  sort_label=None, arrivals=[], departures=[], staffing=[], marked_here=None, window=None,
                  uld=uld, performance={}, pull_roster={}, weather=weather, timezone=gateway_timezone(gateway))
    if operation is None:
        return result
    op_id = operation.id
    result.update(sort_label=f"{operation.sort_date} · {operation.sort_name.title()}",
                  window=operation.window_minutes)
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
    arrival_evidence = []
    pulls = {}
    if "ermac" in access and usable_pull_lineup(gateway, operation, window):
        result["pull_roster"], pulls = completed_pulls(gateway, operation, departures)
    treatment, pretreats = {}, {}
    tail_states = {}
    if "subzero" in access:
        treatment, pretreats = treatment_by_tail(op_id)
        tail_states = {normalized_tail(s.tail_number): s for s in SortDateTailState.query.options(
            load_only(SortDateTailState.tail_number, SortDateTailState.deice_status)).filter_by(
            sort_date=operation.sort_date, gateway_code=gateway.code, sort_name=operation.sort_name)
            .filter(SortDateTailState.deice_status != "unknown")}
    fuel = scorpion_board_fuel_by_mission(operation, departures) if "scorpion" in access else {}
    departures.sort(key=lambda m: departure_order(m, operation))
    ranks = departure_occurrences(departures, operation)
    tails = Counter(normalized_tail(m.assigned_tail_number) for m in departures)
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
                   variance=None)
        if "rain" in access:
            row["variance"] = (_arrival_variance_minutes(mission, planned, window) if inbound
                               else completed_variance_minutes(mission))
        if inbound:
            if "rain" in access:
                arrival_evidence.append((mission, row["variance"]))
            row["wave"] = normalize_wave(mission.wave)
            instant = _arrival_sort_time(mission, planned, window)
            arrivals.append(((instant is None, instant, mission.id), row))
            continue
        row["ordinal"] = ranks.get(mission.id)
        row["route_label"] = destination_label(mission.destination, row["ordinal"])
        fuel_row = fuel.get(mission.id, {})
        if "rain" in access:
            ramp, crew = mission.ramp_load_completed_at_utc, mission.crew_load_completed_at_utc
            row.update(ramp=format_local_hhmm(ramp, mission.timezone),
                       crew=format_local_hhmm(crew, mission.timezone),
                       load_mismatch=bool(ramp and crew and abs(_utc_instant(ramp) - _utc_instant(crew)) > timedelta(minutes=1)),
                       block=row["actual"], no_return=mission.departure_status == "departed")
        event, review = treatment.get(tail, (None, None))
        if event and tails[tail] > 1 and event.sort_date_mission_id != mission.id:
            event, review = None, SHARED_TAIL_REVIEW
        if mission.id in detached_radio:
            review = "Review the former tail's pending radio call."
        deice_state = deice = None
        tail_state = tail_states.get(tail) if tails[tail] == 1 else None
        if tail:
            deice_state, deice = treatment_label(mission, tail, event, review, tail_state,
                                                pretreats.get(tail, ()), shared_tail=tails[tail] > 1)
        pure, mix = pulls.get(mission.id, (None, None))
        row.update(fuel_status=mission.fuel_status if "scorpion" in access else None,
                   planned_fuel=mission.planned_fuel_load if "scorpion" in access else None,
                   neo_fuel=fuel_row.get("scorpion_fuel_display"),
                   fuel_color=STAGE_COLORS.get(fuel_row.get("scorpion_fuel_status")),
                   etd=format_local_hhmm(effective_departure_utc(mission, operation), mission.timezone),
                   pure=pure, mix=mix, deice=deice, deice_state=deice_state)
        result["departures"].append(row)
    result["arrivals"] = [row for _, row in sorted(arrivals, key=lambda arrival: arrival[0])]
    if "rain" in access:
        averages, waves = _live_arrival_performance(arrival_evidence)
        result["performance"] = dict(inbound=averages, outbound=average_lateness(departures))
        result["wave_performance"] = dict(inbound=waves, outbound=_flight_performance(departures))
    # Attendance marks are distinct people, independent of the finalized payroll summary.
    attendance = StaffingDailyAttendance
    attendance_read = db.session.query(db.func.count(db.distinct(attendance.person_id)),
        db.func.count(db.distinct(db.case((attendance.status == "here", attendance.person_id)))))
    read_precipitation = not weather_from_revision and weather["allowed"]
    if read_precipitation:
        attendance_read = attendance_read.add_columns(_active_precipitation(op_id))
    marked, here, *precipitation = attendance_read.filter(
        attendance.sort_date_operation_id == op_id, attendance.attendance_date == operation.sort_date).one()
    if read_precipitation:
        weather["precipitation"], = precipitation
    result["marked_here"] = here if marked else None
    result["staffing"] = [dict(area=name, payroll=payroll, worked=worked)
        for name, payroll, worked in db.session.query(StaffingUnit.name,
            StaffingAttendanceSummary.on_payroll_count, StaffingAttendanceSummary.worked_count)
        .join(StaffingAttendanceSummary, StaffingAttendanceSummary.scope_unit_id == StaffingUnit.id)
        .filter(StaffingAttendanceSummary.sort_date_operation_id == op_id,
                StaffingAttendanceSummary.scope_type == "operation").order_by(StaffingUnit.name, StaffingUnit.id)]
    return result
