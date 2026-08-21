"""Pure dispatcher fuel-demand and nightly truck projection calculations."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta


DEFAULT_PLANNING_INBOUND_FALLBACK_LBS = 12_000
FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES = {
    "B757": Decimal("12"),
    "A300": Decimal("13"),
    "B767ER": Decimal("13"),
    "B747-400": Decimal("12"),
    "B747-8": Decimal("12"),
}


@dataclass(frozen=True)
class EstimatedFuelDemand:
    gallons: int | None
    effective_inbound_lbs: int | None
    source: str
    source_label: str


@dataclass(frozen=True)
class TruckProjection:
    gallons: int | None
    short: bool


@dataclass(frozen=True)
class AssignmentMissionTiming:
    available: bool
    aircraft_type: str | None
    planning_demand_gallons: Decimal | None
    setup_minutes: Decimal | None
    pump_rate_gallons_per_minute: Decimal | None
    pump_minutes: Decimal | None
    finishing_minutes: Decimal | None
    total_duration_minutes: Decimal | None
    aircraft_ready_utc: datetime | None
    aircraft_ready_source: str | None
    fuel_complete_deadline_utc: datetime | None
    earliest_possible_finish_utc: datetime | None
    deadline_feasible: bool | None
    unavailable_reason: str | None


@dataclass(frozen=True)
class ResourceBusyWindow:
    resource_type: str
    resource_id: int
    assignment_id: int
    mission_id: int | None
    start_utc: datetime
    finish_utc: datetime
    start_source: str
    deadline_feasible: bool | None


@dataclass(frozen=True)
class UnknownResourceCommitment:
    resource_type: str
    resource_id: int
    assignment_id: int
    mission_id: int | None
    reason: str


@dataclass(frozen=True)
class ResourceCalendar:
    resource_type: str
    resource_id: int
    busy_windows: tuple[ResourceBusyWindow, ...]
    unknown_commitments: tuple[UnknownResourceCommitment, ...]
    has_manual_overlap: bool


@dataclass(frozen=True)
class ResourceGap:
    available: bool
    start_utc: datetime | None
    finish_utc: datetime | None
    deadline_feasible: bool | None
    unavailable_reason: str | None


@dataclass(frozen=True)
class AssignmentResourceRecommendation:
    available: bool
    fueler_id: int | None
    truck_id: int | None
    feasible_start_utc: datetime | None
    predicted_finish_utc: datetime | None
    fuel_complete_deadline_utc: datetime | None
    duration_minutes: Decimal | None
    unavailable_reason: str | None


def assignment_mission_timing(
    *,
    mission,
    operation,
    aircraft_type,
    planning_demand_gallons,
    planning_settings,
):
    """Derive mission timing facts from already-loaded operational planning data."""
    demand = _decimal_or_none(planning_demand_gallons)
    setup_minutes = _decimal_or_none(getattr(planning_settings, "setup_minutes", None))
    finishing_minutes = _decimal_or_none(
        getattr(planning_settings, "finishing_minutes", None)
    )
    pump_rate = _decimal_or_none(
        planning_settings.pump_rate_for(aircraft_type)
        if aircraft_type in FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES
        else None
    )
    eta_safety_buffer_minutes = _decimal_or_none(
        getattr(planning_settings, "eta_safety_buffer_minutes", None)
    )
    base = {
        "aircraft_type": aircraft_type,
        "planning_demand_gallons": demand,
        "setup_minutes": setup_minutes,
        "pump_rate_gallons_per_minute": pump_rate,
        "finishing_minutes": finishing_minutes,
    }
    if aircraft_type not in FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES:
        return _timing_unavailable(base, "unsupported_aircraft")
    if (
        not planning_settings.is_complete_for(aircraft_type)
        or eta_safety_buffer_minutes is None
    ):
        return _timing_unavailable(base, "planning_settings_incomplete")
    if demand is None:
        return _timing_unavailable(base, "fuel_demand_unavailable")

    actual_block_in = getattr(mission, "actual_block_in_datetime_utc", None)
    eta = getattr(mission, "eta_datetime_utc", None)
    if actual_block_in is not None:
        aircraft_ready_utc = actual_block_in
        aircraft_ready_source = "actual_block_in"
    elif eta is not None:
        aircraft_ready_utc = eta + _decimal_timedelta(
            eta_safety_buffer_minutes
        )
        aircraft_ready_source = "eta_plus_buffer"
    else:
        return _timing_unavailable(base, "arrival_timing_unavailable")

    departure = getattr(mission, "planned_datetime_utc", None)
    if departure is None:
        return _timing_unavailable(base, "departure_timing_unavailable")

    pump_minutes = abs(demand) / pump_rate
    total_duration_minutes = setup_minutes + pump_minutes + finishing_minutes
    fuel_complete_deadline_utc = departure + _decimal_timedelta(
        Decimal(getattr(operation, "window_minutes", 0) or 0)
        - FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES[aircraft_type]
    )
    earliest_possible_finish_utc = aircraft_ready_utc + _decimal_timedelta(
        total_duration_minutes
    )
    return AssignmentMissionTiming(
        available=True,
        **base,
        pump_minutes=pump_minutes,
        total_duration_minutes=total_duration_minutes,
        aircraft_ready_utc=aircraft_ready_utc,
        aircraft_ready_source=aircraft_ready_source,
        fuel_complete_deadline_utc=fuel_complete_deadline_utc,
        earliest_possible_finish_utc=earliest_possible_finish_utc,
        deadline_feasible=earliest_possible_finish_utc <= fuel_complete_deadline_utc,
        unavailable_reason=None,
    )


def _timing_unavailable(base, reason):
    return AssignmentMissionTiming(
        available=False,
        **base,
        pump_minutes=None,
        total_duration_minutes=None,
        aircraft_ready_utc=None,
        aircraft_ready_source=None,
        fuel_complete_deadline_utc=None,
        earliest_possible_finish_utc=None,
        deadline_feasible=None,
        unavailable_reason=reason,
    )


def _decimal_timedelta(minutes):
    microseconds = (
        Decimal(minutes) * Decimal("60") * Decimal("1000000")
    ).to_integral_value(rounding=ROUND_HALF_UP)
    return timedelta(microseconds=int(microseconds))


def build_resource_calendars(
    committed_assignments,
    *,
    operation,
    planning_settings,
    now_utc,
    exclude_assignment_id=None,
):
    """Build current-sort resource calendars from already-loaded committed rows.

    Each input record must provide assignment, mission, work_state, aircraft_type,
    and planning_demand_gallons attributes (or equivalent mapping keys).
    """
    known = {}
    unknown = {}
    for record in committed_assignments:
        assignment = _record_value(record, "assignment")
        assignment_id = _record_value(record, "assignment_id", getattr(assignment, "id", None))
        if assignment is None or assignment_id == exclude_assignment_id:
            continue
        work_state = _record_value(record, "work_state")
        if _assignment_finished(assignment, work_state):
            continue
        resource_ids = (
            ("fueler", getattr(assignment, "assigned_fueler_user_id", None)),
            ("truck", getattr(assignment, "assigned_truck_id", None)),
        )
        resource_ids = tuple((kind, identifier) for kind, identifier in resource_ids if identifier is not None)
        if not resource_ids:
            continue
        mission = _record_value(record, "mission")
        timing = assignment_mission_timing(
            mission=mission,
            operation=operation,
            aircraft_type=_record_value(record, "aircraft_type"),
            planning_demand_gallons=_record_value(record, "planning_demand_gallons"),
            planning_settings=planning_settings,
        )
        mission_id = getattr(mission, "id", None)
        if not timing.available:
            for resource_type, resource_id in resource_ids:
                item = UnknownResourceCommitment(
                    resource_type, resource_id, assignment_id, mission_id,
                    "existing_commitment_timing_unavailable",
                )
                unknown.setdefault((resource_type, resource_id), []).append(item)
            continue
        actual_start = getattr(work_state, "on_at_utc", None) if work_state else None
        if actual_start is not None:
            start_utc = actual_start
            start_source = "actual_on"
        else:
            start_utc = max(now_utc, timing.aircraft_ready_utc)
            start_source = "projected_aircraft_ready"
        finish_utc = start_utc + _decimal_timedelta(timing.total_duration_minutes)
        if actual_start is not None and finish_utc < now_utc:
            finish_utc = now_utc
        for resource_type, resource_id in resource_ids:
            window = ResourceBusyWindow(
                resource_type, resource_id, assignment_id, mission_id,
                start_utc, finish_utc, start_source, timing.deadline_feasible,
            )
            known.setdefault((resource_type, resource_id), []).append(window)

    calendars = {}
    for key in set(known) | set(unknown):
        windows = tuple(sorted(known.get(key, ()), key=lambda item: (item.start_utc, item.finish_utc, item.assignment_id)))
        calendars[key] = ResourceCalendar(
            key[0], key[1], windows, tuple(unknown.get(key, ()),),
            _has_manual_overlap(windows),
        )
    return calendars


def find_earliest_resource_gap(
    *,
    candidate_earliest_start_utc,
    candidate_duration_minutes,
    busy_windows=(),
    unknown_commitments=(),
    now_utc,
    completion_deadline_utc=None,
):
    """Find the earliest exact-boundary-safe gap without changing commitments."""
    if unknown_commitments:
        return ResourceGap(False, None, None, None, "existing_commitment_timing_unavailable")
    duration = _decimal_or_none(candidate_duration_minutes)
    if duration is None or duration < 0:
        return ResourceGap(False, None, None, None, "candidate_duration_unavailable")
    start_utc = max(now_utc, candidate_earliest_start_utc)
    merged = _merged_busy_windows(busy_windows)
    for busy_start, busy_finish in merged:
        finish_utc = start_utc + _decimal_timedelta(duration)
        if finish_utc <= busy_start:
            return _gap_with_deadline(start_utc, finish_utc, completion_deadline_utc)
        if start_utc < busy_finish:
            start_utc = busy_finish
    return _gap_with_deadline(
        start_utc,
        start_utc + _decimal_timedelta(duration),
        completion_deadline_utc,
    )


def _gap_with_deadline(start_utc, finish_utc, deadline_utc):
    feasible = deadline_utc is None or finish_utc <= deadline_utc
    return ResourceGap(
        feasible, start_utc if feasible else None, finish_utc if feasible else None,
        feasible if deadline_utc is not None else None,
        None if feasible else "completion_deadline_infeasible",
    )


def _merged_busy_windows(windows):
    merged = []
    for item in sorted(windows, key=lambda window: (window.start_utc, window.finish_utc)):
        if not merged or item.start_utc > merged[-1][1]:
            merged.append([item.start_utc, item.finish_utc])
        elif item.finish_utc > merged[-1][1]:
            merged[-1][1] = item.finish_utc
    return tuple((start, finish) for start, finish in merged)


def _has_manual_overlap(windows):
    latest_finish = None
    for window in windows:
        if latest_finish is not None and window.start_utc < latest_finish:
            return True
        latest_finish = max(latest_finish, window.finish_utc) if latest_finish else window.finish_utc
    return False


def _assignment_finished(assignment, work_state):
    return bool(
        getattr(assignment, "completed_at_utc", None)
        or getattr(assignment, "fuel_on_board_at_utc", None)
        or (work_state and (
            getattr(work_state, "off_at_utc", None)
            or getattr(work_state, "ended_early_at_utc", None)
        ))
    )


def _record_value(record, name, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def recommend_assignment_resources(
    *,
    assignment,
    mission_timing,
    fueler_candidates,
    truck_candidates,
    resource_calendars,
    now_utc,
):
    """Choose the earliest feasible advisory pair from preloaded candidates/maps."""
    deadline = mission_timing.fuel_complete_deadline_utc
    duration = mission_timing.total_duration_minutes
    if not mission_timing.available:
        return _recommendation_unavailable("mission_timing_unavailable", deadline, duration)
    if mission_timing.deadline_feasible is False:
        return _recommendation_unavailable("deadline_at_risk", deadline, duration)

    assignment_id = getattr(assignment, "id", None)
    fixed_fueler_id = getattr(assignment, "assigned_fueler_user_id", None)
    fixed_truck_id = getattr(assignment, "assigned_truck_id", None)
    if fixed_fueler_id is not None and fixed_truck_id is not None:
        return _recommendation_unavailable("already_assigned", deadline, duration)

    fuelers = _recommendation_candidates(fueler_candidates, fixed_fueler_id)
    trucks = _recommendation_candidates(truck_candidates, fixed_truck_id)
    if not fuelers:
        return _recommendation_unavailable("no_eligible_fueler", deadline, duration)
    if not trucks:
        return _recommendation_unavailable("no_eligible_truck", deadline, duration)

    candidates = []
    for fueler in fuelers:
        fueler_id = _candidate_id(fueler)
        fueler_calendar = _calendar_without_assignment(
            resource_calendars.get(("fueler", fueler_id)), assignment_id
        )
        for truck in trucks:
            truck_id = _candidate_id(truck)
            truck_calendar = _calendar_without_assignment(
                resource_calendars.get(("truck", truck_id)), assignment_id
            )
            gap = find_earliest_resource_gap(
                candidate_earliest_start_utc=mission_timing.aircraft_ready_utc,
                candidate_duration_minutes=duration,
                busy_windows=(fueler_calendar.busy_windows + truck_calendar.busy_windows),
                unknown_commitments=(
                    fueler_calendar.unknown_commitments + truck_calendar.unknown_commitments
                ),
                now_utc=now_utc,
                completion_deadline_utc=deadline,
            )
            if not gap.available:
                continue
            candidates.append((
                gap.start_utc, gap.finish_utc, _candidate_sort_key(fueler),
                _candidate_sort_key(truck), fueler_id, truck_id,
            ))
    if not candidates:
        return _recommendation_unavailable("no_feasible_pair", deadline, duration)
    start_utc, finish_utc, _fueler_key, _truck_key, fueler_id, truck_id = min(candidates)
    return AssignmentResourceRecommendation(
        True, fueler_id, truck_id, start_utc, finish_utc, deadline, duration, None
    )


def _recommendation_unavailable(reason, deadline, duration):
    return AssignmentResourceRecommendation(
        False, None, None, None, None, deadline, duration, reason
    )


def _recommendation_candidates(candidates, fixed_id):
    if fixed_id is not None:
        return (fixed_id,)
    return tuple(sorted(candidates, key=_candidate_sort_key))


def _candidate_id(candidate):
    if isinstance(candidate, int):
        return candidate
    return candidate.get("id") if isinstance(candidate, dict) else candidate.id


def _candidate_sort_key(candidate):
    if isinstance(candidate, int):
        return ("", candidate)
    if isinstance(candidate, dict):
        return (str(candidate.get("sort_key", candidate.get("display_name", ""))), candidate["id"])
    return (str(getattr(candidate, "sort_key", getattr(candidate, "display_name", ""))), candidate.id)


def _calendar_without_assignment(calendar, assignment_id):
    if calendar is None:
        return ResourceCalendar("", 0, (), (), False)
    windows = tuple(item for item in calendar.busy_windows if item.assignment_id != assignment_id)
    unknown = tuple(item for item in calendar.unknown_commitments if item.assignment_id != assignment_id)
    return ResourceCalendar(
        calendar.resource_type, calendar.resource_id, windows, unknown,
        _has_manual_overlap(windows),
    )


def estimate_fuel_demand_gallons(
    required_fuel_lbs,
    inbound_fuel_lbs,
    fuel_density_lbs_per_gallon,
    *,
    measured_fob_lbs=None,
    fallback_inbound_lbs=DEFAULT_PLANNING_INBOUND_FALLBACK_LBS,
):
    """Return a whole-gallon estimate without mutating any persisted state."""
    required = _decimal_or_none(required_fuel_lbs)
    density = _decimal_or_none(fuel_density_lbs_per_gallon)
    if required is None or density is None or density <= 0:
        return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")

    measured_fob = _decimal_or_none(measured_fob_lbs)
    if measured_fob is not None:
        if measured_fob < 0:
            return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
        effective_inbound = measured_fob
        source = "measured_fob"
        source_label = ""
    else:
        inbound = _decimal_or_none(inbound_fuel_lbs)
        if inbound is not None:
            if inbound < 0:
                return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
            effective_inbound = inbound
            source = "inbound"
            source_label = ""
        else:
            effective_inbound = _decimal_or_none(fallback_inbound_lbs)
            if effective_inbound is None or effective_inbound < 0:
                return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
            source = "fallback"
            source_label = (
                f"{effective_inbound / Decimal('1000'):.1f}K ASSUMED INBOUND"
            )

    if effective_inbound is None:
        return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")

    fuel_needed_lbs = max(Decimal("0"), required - effective_inbound)
    gallons = int(
        (fuel_needed_lbs / density).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return EstimatedFuelDemand(
        gallons,
        int(effective_inbound),
        source,
        source_label,
    )


def project_truck_remaining(current_gallons_by_truck_id, ordered_demands):
    """Project remaining gallons for demand rows already ordered by departure."""
    remaining = {
        truck_id: _int_or_none(gallons)
        for truck_id, gallons in current_gallons_by_truck_id.items()
    }
    incomplete_trucks = set()
    projections = {}
    for row_key, truck_id, demand_gallons in ordered_demands:
        if truck_id is None:
            projections[row_key] = TruckProjection(None, False)
            continue
        demand = _int_or_none(demand_gallons)
        current = remaining.get(truck_id)
        if truck_id in incomplete_trucks or current is None or demand is None:
            incomplete_trucks.add(truck_id)
            projections[row_key] = TruckProjection(None, False)
            continue
        current -= demand
        remaining[truck_id] = current
        projections[row_key] = TruckProjection(current, current < 0)
    return projections


def _decimal_or_none(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int_or_none(value):
    parsed = _decimal_or_none(value)
    if parsed is None:
        return None
    return int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
