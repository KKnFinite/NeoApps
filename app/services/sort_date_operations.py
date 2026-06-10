import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import has_app_context

from app.extensions import db
from app.models import (
    Gateway,
    MasterFlightSchedule,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
)
from app.services.flight_rules import (
    default_required_crew_sections,
    derive_aircraft_type_from_tail_number,
)

WAVE_OPTIONS = ("1", "2")


def create_sort_date_operation(
    sort_date,
    gateway_code,
    sort_name,
    window_minutes=0,
    generated_by_user_id=None,
):
    gateway_code = str(gateway_code).strip().upper()
    window_minutes = normalize_window_minutes(window_minutes)
    gateway = Gateway.query.filter_by(code=gateway_code).first() if has_app_context() else None
    return SortDateOperation(
        sort_date=sort_date,
        gateway_id=gateway.id if gateway else None,
        gateway_code=gateway_code,
        sort_name=sort_name,
        window_minutes=window_minutes,
        generated_by_user_id=generated_by_user_id,
    )


def generate_sort_date_operation_from_master(
    sort_date,
    gateway_code,
    sort_name,
    generated_by_user_id=None,
):
    gateway_code = str(gateway_code).strip().upper()
    sort_name = str(sort_name).strip().lower()
    existing_operation = SortDateOperation.query.filter_by(
        sort_date=sort_date,
        gateway_code=gateway_code,
        sort_name=sort_name,
    ).first()
    if existing_operation:
        raise ValueError("SortDateOperation already exists for this sort date, gateway, and sort.")

    master_rows = (
        MasterFlightSchedule.query.filter_by(
            gateway_code=gateway_code,
            sort_name=sort_name,
            active=True,
        )
        .order_by(MasterFlightSchedule.flight_number.asc())
        .all()
    )
    matching_master_rows = [
        master_row
        for master_row in master_rows
        if master_schedule_runs_on_date(master_row, sort_date)
    ]

    _raise_for_duplicate_master_flight_numbers(matching_master_rows)

    operation = create_sort_date_operation(
        sort_date=sort_date,
        gateway_code=gateway_code,
        sort_name=sort_name,
        window_minutes=0,
        generated_by_user_id=generated_by_user_id,
    )
    db.session.add(operation)

    for master_row in matching_master_rows:
        mission = _build_mission_from_master(operation, master_row, sort_date)
        db.session.add(mission)
        db.session.flush()

        tail_state = ensure_tail_state_for_mission(mission)
        aircraft_type = tail_state.aircraft_type if tail_state else "unknown"
        create_default_crew_assignments_for_mission(mission, aircraft_type)

    db.session.commit()
    return operation


def sync_sort_operation_with_master(operation):
    gateway_code = str(operation.gateway_code).strip().upper()
    sort_name = str(operation.sort_name).strip().lower()
    master_rows = (
        MasterFlightSchedule.query.filter_by(
            gateway_code=gateway_code,
            sort_name=sort_name,
            active=True,
        )
        .order_by(MasterFlightSchedule.flight_number.asc())
        .all()
    )
    matching_master_rows = [
        master_row
        for master_row in master_rows
        if master_schedule_runs_on_date(master_row, operation.sort_date)
    ]
    existing_missions = SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    missions_by_master_id = {
        mission.master_flight_schedule_id: mission
        for mission in existing_missions
        if mission.master_flight_schedule_id
    }
    existing_flight_numbers = {mission.flight_number for mission in existing_missions}
    added = []
    updated = []
    skipped = []

    for master_row in matching_master_rows:
        linked_mission = missions_by_master_id.get(master_row.id)
        if linked_mission:
            if _master_updated_after_operation_generation(master_row, operation):
                other_flight_numbers = {
                    mission.flight_number
                    for mission in existing_missions
                    if mission.id != linked_mission.id
                }
                if master_row.flight_number in other_flight_numbers:
                    skipped.append(master_row)
                    continue
                if _apply_master_template_to_mission(linked_mission, master_row, operation):
                    updated.append(linked_mission)
            continue

        if master_row.flight_number in existing_flight_numbers:
            skipped.append(master_row)
            continue

        mission = _build_mission_from_master(operation, master_row, operation.sort_date)
        db.session.add(mission)
        db.session.flush()

        tail_state = ensure_tail_state_for_mission(mission)
        aircraft_type = tail_state.aircraft_type if tail_state else "unknown"
        create_default_crew_assignments_for_mission(mission, aircraft_type)
        existing_flight_numbers.add(mission.flight_number)
        added.append(mission)

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
    }


def master_schedule_runs_on_date(master_schedule, sort_date):
    return sort_date.strftime("%A").lower() in parse_active_days(master_schedule.active_days)


def parse_active_days(active_days):
    if not active_days:
        return set()

    if isinstance(active_days, (list, tuple, set)):
        return {_normalize_active_day(day) for day in active_days if _normalize_active_day(day)}

    active_days = str(active_days).strip()
    if not active_days:
        return set()

    if active_days.startswith("["):
        try:
            parsed_days = json.loads(active_days)
        except json.JSONDecodeError:
            parsed_days = []
        return {_normalize_active_day(day) for day in parsed_days if _normalize_active_day(day)}

    return {
        _normalize_active_day(day)
        for day in active_days.split(",")
        if _normalize_active_day(day)
    }


def ensure_tail_state_for_mission(mission, parking_position=None):
    if not mission.assigned_tail_number:
        return None

    mission.assigned_tail_number = mission.assigned_tail_number.strip().upper()
    tail_state = SortDateTailState.query.filter_by(
        sort_date=mission.sort_date,
        gateway_code=mission.gateway_code,
        sort_name=mission.sort_name,
        tail_number=mission.assigned_tail_number,
    ).first()

    if not tail_state:
        tail_state = SortDateTailState(
            sort_date=mission.sort_date,
            gateway_code=mission.gateway_code,
            sort_name=mission.sort_name,
            tail_number=mission.assigned_tail_number,
        )
        db.session.add(tail_state)

    if tail_state.aircraft_type_source != "manual":
        aircraft_type = derive_aircraft_type_from_tail_number(mission.assigned_tail_number)
        if aircraft_type == "unknown":
            tail_state.aircraft_type = None
            tail_state.aircraft_type_source = "unknown"
        else:
            tail_state.aircraft_type = aircraft_type
            tail_state.aircraft_type_source = "derived"

    if tail_state.parking_position:
        tail_state.parking_position = tail_state.parking_position.strip().upper()
    if parking_position and not tail_state.parking_position:
        tail_state.parking_position = parking_position.strip().upper()

    db.session.flush()
    return tail_state


def create_default_crew_assignments_for_mission(mission, aircraft_type="unknown"):
    assignments = []
    for aircraft_section in default_required_crew_sections(aircraft_type):
        assignment = SortDateCrewAssignment(
            sort_date_mission=mission,
            aircraft_section=aircraft_section,
            required=True,
        )
        db.session.add(assignment)
        assignments.append(assignment)

    return assignments


def normalize_window_minutes(window_minutes):
    if window_minutes is None:
        return 0

    window_minutes = int(window_minutes)
    if window_minutes < 0:
        raise ValueError("window_minutes cannot be negative.")

    return window_minutes


def normalize_optional_window_minutes(window_minutes):
    if window_minutes in (None, ""):
        return None

    return normalize_window_minutes(window_minutes)


def normalize_wave(wave):
    wave = str(wave or "").strip().lower()
    if wave in ("1", "1st", "first", "first wave", "1st wave"):
        return "1"
    if wave in ("2", "2nd", "second", "second wave", "2nd wave"):
        return "2"
    return "1"


def effective_window_minutes_for_mission(mission, operation=None):
    default_window = normalize_window_minutes(
        getattr(operation, "window_minutes", 0) if operation else 0
    )
    if not operation:
        return default_window

    wave = normalize_wave(getattr(mission, "wave", None))
    if wave == "1":
        wave_window = getattr(operation, "first_wave_window_minutes", None)
    else:
        wave_window = getattr(operation, "second_wave_window_minutes", None)

    if wave_window is None:
        return default_window

    return normalize_window_minutes(wave_window)


def apply_window_minutes(value, window_minutes):
    if value is None:
        return None

    window_minutes = normalize_window_minutes(window_minutes)
    if window_minutes == 0:
        return value

    if isinstance(value, datetime):
        return value + timedelta(minutes=window_minutes)

    anchor = datetime.combine(datetime.min.date(), value)
    return (anchor + timedelta(minutes=window_minutes)).time()


def mission_display_timing_data(mission, operation=None):
    window_minutes = effective_window_minutes_for_mission(mission, operation)

    if mission.mission_type == "departure":
        return {
            "wave": normalize_wave(getattr(mission, "wave", None)),
            "effective_window_minutes": window_minutes,
            "base_planned_departure_time": mission.planned_datetime_local,
            "adjusted_planned_departure_time": apply_window_minutes(
                mission.planned_datetime_local,
                window_minutes,
            ),
            "base_pure_pull_time": mission.pure_pull_time_local,
            "adjusted_pure_pull_time": apply_window_minutes(
                mission.pure_pull_time_local,
                window_minutes,
            ),
            "base_first_mix_pull_time": mission.first_mix_pull_time_local,
            "adjusted_first_mix_pull_time": apply_window_minutes(
                mission.first_mix_pull_time_local,
                window_minutes,
            ),
            "base_final_mix_pull_time": mission.final_mix_pull_time_local,
            "adjusted_final_mix_pull_time": apply_window_minutes(
                mission.final_mix_pull_time_local,
                window_minutes,
            ),
        }

    return {
        "wave": normalize_wave(getattr(mission, "wave", None)),
        "effective_window_minutes": window_minutes,
        "base_planned_arrival_time": mission.planned_datetime_local,
        "adjusted_planned_arrival_time": mission.planned_datetime_local,
        "base_eta_time_utc": mission.eta_datetime_utc,
        "adjusted_eta_time_utc": mission.eta_datetime_utc,
        "base_actual_block_in_time_utc": mission.actual_block_in_datetime_utc,
        "adjusted_actual_block_in_time_utc": mission.actual_block_in_datetime_utc,
    }


def _build_mission_from_master(operation, master_row, sort_date):
    planned_datetime_local = _planned_datetime_local(
        sort_date,
        master_row.planned_time_local,
    )
    planned_datetime_utc = _planned_datetime_utc(
        planned_datetime_local,
        master_row.timezone,
    )
    assigned_tail_number = getattr(master_row, "assigned_tail_number", None)
    assigned_tail_number = assigned_tail_number.strip().upper() if assigned_tail_number else None

    mission = SortDateMission(
        sort_date_operation=operation,
        sort_date=sort_date,
        gateway_code=master_row.gateway_code,
        sort_name=master_row.sort_name,
        mission_type=master_row.mission_type,
        mission_source="master",
        wave=normalize_wave(getattr(master_row, "wave", None)),
        master_flight_schedule_id=master_row.id,
        flight_number=master_row.flight_number.strip().upper(),
        origin=master_row.origin.strip().upper(),
        destination=master_row.destination.strip().upper(),
        timezone=master_row.timezone,
        planned_datetime_local=planned_datetime_local,
        planned_datetime_utc=planned_datetime_utc,
        planned_source="master",
        assigned_tail_number=assigned_tail_number,
    )

    if master_row.mission_type == "departure":
        mission.pure_pull_time_local = master_row.pure_pull_time_local
        mission.first_mix_pull_time_local = master_row.first_mix_pull_time_local
        mission.final_mix_pull_time_local = master_row.final_mix_pull_time_local
        if any(
            (
                mission.pure_pull_time_local,
                mission.first_mix_pull_time_local,
                mission.final_mix_pull_time_local,
            )
        ):
            mission.pull_time_source = "master"
    else:
        mission.arrival_status = "scheduled"

    return mission


def _apply_master_template_to_mission(mission, master_row, operation):
    before = _master_template_snapshot(mission)
    planned_datetime_local = _planned_datetime_local(
        operation.sort_date,
        master_row.planned_time_local,
    )
    planned_datetime_utc = _planned_datetime_utc(
        planned_datetime_local,
        master_row.timezone,
    )

    mission.sort_date = operation.sort_date
    mission.gateway_code = master_row.gateway_code
    mission.sort_name = master_row.sort_name
    mission.mission_type = master_row.mission_type
    mission.mission_source = "master"
    mission.wave = normalize_wave(getattr(master_row, "wave", None))
    mission.master_flight_schedule_id = master_row.id
    mission.flight_number = master_row.flight_number.strip().upper()
    mission.origin = master_row.origin.strip().upper()
    mission.destination = master_row.destination.strip().upper()
    mission.timezone = master_row.timezone
    mission.planned_datetime_local = planned_datetime_local
    mission.planned_datetime_utc = planned_datetime_utc
    mission.planned_source = "master"

    if master_row.mission_type == "arrival":
        mission.arrival_status = mission.arrival_status or "scheduled"
        mission.pure_pull_time_local = None
        mission.first_mix_pull_time_local = None
        mission.final_mix_pull_time_local = None
        mission.pull_time_source = None
    else:
        mission.arrival_status = None
        mission.pure_pull_time_local = master_row.pure_pull_time_local
        mission.first_mix_pull_time_local = master_row.first_mix_pull_time_local
        mission.final_mix_pull_time_local = master_row.final_mix_pull_time_local
        mission.pull_time_source = (
            "master"
            if any(
                (
                    mission.pure_pull_time_local,
                    mission.first_mix_pull_time_local,
                    mission.final_mix_pull_time_local,
                )
            )
            else None
        )

    return _master_template_snapshot(mission) != before


def _master_template_snapshot(mission):
    return (
        mission.sort_date,
        mission.gateway_code,
        mission.sort_name,
        mission.mission_type,
        mission.mission_source,
        mission.wave,
        mission.master_flight_schedule_id,
        mission.flight_number,
        mission.origin,
        mission.destination,
        mission.timezone,
        mission.planned_datetime_local,
        mission.planned_datetime_utc,
        mission.planned_source,
        mission.arrival_status,
        mission.pure_pull_time_local,
        mission.first_mix_pull_time_local,
        mission.final_mix_pull_time_local,
        mission.pull_time_source,
    )


def _master_updated_after_operation_generation(master_row, operation):
    if not master_row.updated_at:
        return False
    if not operation.generated_at_utc:
        return True
    return master_row.updated_at > operation.generated_at_utc


def _planned_datetime_local(sort_date, planned_time_local):
    return datetime.combine(sort_date, planned_time_local)


def _planned_datetime_utc(planned_datetime_local, timezone):
    timezone = timezone or "America/Chicago"
    try:
        localized_datetime = planned_datetime_local.replace(tzinfo=ZoneInfo(timezone))
        return localized_datetime.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return planned_datetime_local + timedelta(
            hours=_fallback_utc_offset_hours(planned_datetime_local, timezone) * -1,
        )


def _raise_for_duplicate_master_flight_numbers(master_rows):
    seen_flight_numbers = set()
    duplicate_flight_numbers = set()

    for master_row in master_rows:
        if master_row.flight_number in seen_flight_numbers:
            duplicate_flight_numbers.add(master_row.flight_number)
        seen_flight_numbers.add(master_row.flight_number)

    if duplicate_flight_numbers:
        duplicates = ", ".join(sorted(duplicate_flight_numbers))
        raise ValueError(f"Duplicate flight_number values for operation generation: {duplicates}")


def _normalize_active_day(day):
    return str(day).strip().lower()


def _fallback_utc_offset_hours(planned_datetime_local, timezone):
    if timezone != "America/Chicago":
        return 0

    return -5 if _is_us_central_daylight_time(planned_datetime_local) else -6


def _is_us_central_daylight_time(planned_datetime_local):
    year = planned_datetime_local.year
    dst_start = _nth_weekday_of_month(year, 3, 6, 2).replace(hour=2)
    dst_end = _nth_weekday_of_month(year, 11, 6, 1).replace(hour=2)
    return dst_start <= planned_datetime_local < dst_end


def _nth_weekday_of_month(year, month, weekday, occurrence):
    candidate = datetime(year, month, 1)
    days_until_weekday = (weekday - candidate.weekday()) % 7
    return candidate + timedelta(days=days_until_weekday + (occurrence - 1) * 7)
