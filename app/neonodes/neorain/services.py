"""Read-only current-sort data for NeoRain operational screens."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import aliased, joinedload

from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    SortDateMission,
    SortDateParkingAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.services.live_screen_refresh import live_screen_refresh_value
from app.services.live_collaboration import entity_version
from app.services.neostaffing import attendance_operation_department_counts
from app.services.node_refresh import node_auto_refresh_status
from app.services.operation_scope import current_operational_sort_operation
from app.services.sort_date_operations import mission_display_timing_data, normalize_wave
from app.services.time_display import format_local_hhmm
from app.services.departure_progress import (
    DEPARTURE_STATUS_RANK,
    recompute_departure_status_after_external_clear,
)
from app.services.google_motherbrain_live_missions import (
    _parse_optional_live_datetime,
)


NEORAIN_OUTBOUND_REFRESH_KEY = "neorain.outbound"
_OPERATION_UNSET = object()
NEORAIN_MILESTONE_SOURCE = "neorain"
_UNOWNED_SOURCES = {"", "unknown"}
_HHMM_INPUT_PATTERN = re.compile(r"^\d{4}$")
_RAIN_MILESTONES = {
    "ramp_load_complete": {
        "label": "Ramp Load Complete",
        "timestamp_attr": "ramp_load_completed_at_utc",
        "source_attr": "ramp_load_completed_source",
        "status": "ramp_load_complete",
    },
    "crew_load_complete": {
        "label": "Crew Load Complete",
        "timestamp_attr": "crew_load_completed_at_utc",
        "source_attr": "crew_load_completed_source",
        "status": "crew_load_complete",
    },
    "official_block_out": {
        "label": "Official Block-Out",
        "timestamp_attr": "actual_block_out_datetime_utc",
        "source_attr": "actual_block_out_source",
        "status": "blocked_out",
    },
}
NEORAIN_MUTABLE_MILESTONE_FIELDS = frozenset(
    (*_RAIN_MILESTONES, "no_return")
)
_LOAD_PLANNER_UNIT_PATH = ("night", "ramp", "load planning", "load planners")


class NeoRainMilestoneError(ValueError):
    """Safe validation/conflict error for a later NeoRain mutation route."""


class LoadPlannerAssignmentError(ValueError):
    """Safe validation error for canonical NeoRain Load Planner assignments."""


def neorain_outbound_context(gateway, *, operation=_OPERATION_UNSET):
    """Build the current-sort Outbound board without mutating operational state."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    refresh = neorain_outbound_refresh_status(gateway, operation=operation)
    missions = _outbound_departure_missions(operation)
    return {
        "operation": operation,
        "rows": _outbound_rows(operation, missions=missions),
        "late_summary": _outbound_late_summary(missions),
        "staffing_summary": neorain_outbound_staffing_summary(operation),
        "refresh_status": refresh,
    }


def current_neorain_outbound_operation(gateway):
    """Return an existing lifecycle-current operation, never a historical fallback."""
    return current_operational_sort_operation(gateway)


def neorain_inbound_context(gateway, *, operation=_OPERATION_UNSET):
    """Build the bounded current-sort arrival board."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    arrivals = _inbound_arrival_missions(operation)
    departures = _outbound_departure_missions(operation)
    parking_by_tail = {
        _tail_key(a.tail_number): _text(a.position_code)
        for a in (SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id).all() if operation else [])
        if _tail_key(a.tail_number)
    }
    departures_by_tail = _inbound_departures_by_tail(departures, operation)
    rows = [
        _inbound_row(mission, parking_by_tail, departures_by_tail)
        for mission in arrivals
    ]
    rows.sort(key=lambda row: (row["sort_time"] is None, row["sort_time"] or datetime.max, row["flight_number"], row["mission_id"]))
    return {
        "operation": operation,
        "rows": rows,
        "late_summary": _inbound_late_summary(arrivals),
        "staffing_summary": neorain_outbound_staffing_summary(operation),
    }


def neorain_inbound_revision(gateway, *, operation=_OPERATION_UNSET):
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    operation_id = operation.id if operation else None
    criterion = (SortDateMission.sort_date_operation_id == operation_id if operation_id is not None else SortDateMission.sort_date_operation_id.is_(None))
    parking_criterion = (SortDateParkingAssignment.sort_date_operation_id == operation_id if operation_id is not None else SortDateParkingAssignment.sort_date_operation_id.is_(None))
    rows = sorted(db.session.execute(union_all(
        _revision_aggregate("arrivals", SortDateMission, SortDateMission.updated_at, criterion, SortDateMission.mission_type == "arrival"),
        _revision_aggregate("departures", SortDateMission, SortDateMission.updated_at, criterion, SortDateMission.mission_type == "departure"),
        _revision_aggregate("parking", SortDateParkingAssignment, SortDateParkingAssignment.updated_at, parking_criterion),
    )).all(), key=lambda row: row.source)
    payload = {"gateway_id": gateway.id, "operation_id": operation_id, "inputs": [{"source": r.source, "row_count": int(r.row_count or 0), "max_id": int(r.max_id or 0), "id_sum": int(r.id_sum or 0), "latest_updated_at": _revision_value(r.latest_updated_at)} for r in rows]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mutate_neorain_departure_milestone(mission, operation, field, value):
    """Mutate one Rain-owned departure fact without committing the transaction.

    Timestamp inputs are strict operational HHMM values; an empty string clears
    only a NeoRain-owned timestamp. No Return is a separate explicit boolean
    action so changing a factual timestamp never silently reverses departure.
    """
    _validate_departure_mission(mission, operation)
    normalized_field = str(field or "").strip().lower()
    if normalized_field == "no_return":
        return _mutate_no_return(mission, value)
    specification = _RAIN_MILESTONES.get(normalized_field)
    if specification is None:
        raise NeoRainMilestoneError("Choose a valid NeoRain milestone.")

    timestamp_utc = _parse_operational_hhmm(value, operation, specification["label"])
    timestamp_attr = specification["timestamp_attr"]
    source_attr = specification["source_attr"]
    current_timestamp = getattr(mission, timestamp_attr)
    current_source = _normalized_source(getattr(mission, source_attr))
    if not _neorain_may_mutate_timestamp(current_timestamp, current_source):
        raise NeoRainMilestoneError(
            f"{specification['label']} is owned by {current_source} and cannot be changed in NeoRain."
        )

    next_source = NEORAIN_MILESTONE_SOURCE if timestamp_utc else "unknown"
    changed = (
        current_timestamp != timestamp_utc or current_source != next_source
    )
    if changed:
        setattr(mission, timestamp_attr, timestamp_utc)
        setattr(mission, source_attr, next_source)

    status_changed = False
    if timestamp_utc is not None:
        status_changed = _advance_departure_status(
            mission,
            specification["status"],
        )
    elif current_timestamp is not None:
        status_changed = recompute_departure_status_after_external_clear(
            mission,
            specification["status"],
        )

    return _mutation_result(
        mission,
        normalized_field,
        changed=changed or status_changed,
        value=getattr(mission, timestamp_attr),
        source=getattr(mission, source_attr),
    )


def neorain_departure_milestone_value(mission, field):
    """Return the canonical value accepted by the focused Google writer."""
    normalized_field = str(field or "").strip().lower()
    if normalized_field == "no_return":
        return _normalized_status(mission.departure_status) == "departed"
    specification = _RAIN_MILESTONES.get(normalized_field)
    if specification is None:
        raise NeoRainMilestoneError("Choose a valid NeoRain milestone.")
    return getattr(mission, specification["timestamp_attr"])


def neorain_late_metrics_inclusion(mission):
    """Return the effective Neo-owned late-metrics eligibility for one mission."""
    override = mission.late_metrics_included_override
    if override is not None:
        return {"included": bool(override), "source": "override"}
    return {
        "included": normalize_wave(mission.wave) in {"1", "2"},
        "source": "default",
    }


def set_neorain_late_metrics_included(mission, included):
    """Persist an explicit Neo-only late-metrics inclusion override without commit."""
    if type(included) is not bool:
        raise ValueError("Late-metrics inclusion must be true or false.")
    changed = mission.late_metrics_included_override is not included
    if changed:
        mission.late_metrics_included_override = included
    inclusion = neorain_late_metrics_inclusion(mission)
    return {"changed": changed, **inclusion}


def _mutate_no_return(mission, value):
    desired = _parse_no_return(value)
    current_status = _normalized_status(mission.departure_status)
    current_source = _normalized_source(mission.departure_status_source)

    if desired:
        missing = _missing_no_return_prerequisites(mission)
        if missing:
            raise NeoRainMilestoneError(
                "No Return requires " + ", ".join(missing) + "."
            )
        if current_status == "departed":
            return _mutation_result(
                mission,
                "no_return",
                changed=False,
                value=True,
                source=mission.departure_status_source,
            )
        mission.departure_status = "departed"
        mission.departure_status_source = NEORAIN_MILESTONE_SOURCE
        return _mutation_result(
            mission,
            "no_return",
            changed=True,
            value=True,
            source=NEORAIN_MILESTONE_SOURCE,
        )

    if current_status != "departed":
        return _mutation_result(
            mission,
            "no_return",
            changed=False,
            value=False,
            source=mission.departure_status_source,
        )
    if current_source != NEORAIN_MILESTONE_SOURCE:
        raise NeoRainMilestoneError("No Return is not owned by NeoRain.")
    changed = recompute_departure_status_after_external_clear(mission, "departed")
    return _mutation_result(
        mission,
        "no_return",
        changed=changed,
        value=False,
        source=mission.departure_status_source,
    )


def _parse_operational_hhmm(value, operation, field_label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    raw_value = str(value).strip()
    if not _HHMM_INPUT_PATTERN.fullmatch(raw_value):
        raise NeoRainMilestoneError(
            f"{field_label} must use four-digit HHMM time."
        )
    hour = int(raw_value[:2])
    minute = int(raw_value[2:])
    if hour > 23 or minute > 59:
        raise NeoRainMilestoneError(f"{field_label} is not a valid time.")
    _local_value, timestamp_utc = _parse_optional_live_datetime(
        time(hour, minute),
        operation,
        field_label,
    )
    return timestamp_utc


def _parse_no_return(value):
    if value is True or str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "set",
    }:
        return True
    if value is False or str(value or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
        "clear",
        "",
    }:
        return False
    raise NeoRainMilestoneError("No Return must be set or cleared.")


def _validate_departure_mission(mission, operation):
    if mission is None or operation is None:
        raise NeoRainMilestoneError("A current departure mission is required.")
    if mission.mission_type != "departure":
        raise NeoRainMilestoneError("NeoRain milestones apply only to departures.")
    if mission.sort_date_operation_id != operation.id:
        raise NeoRainMilestoneError("Departure mission is outside the current sort.")


def _neorain_may_mutate_timestamp(current_timestamp, current_source):
    return current_source == NEORAIN_MILESTONE_SOURCE or (
        current_timestamp is None and current_source in _UNOWNED_SOURCES
    )


def _advance_departure_status(mission, target_status):
    current_status = _normalized_status(mission.departure_status)
    if current_status == "cancelled":
        return False
    if DEPARTURE_STATUS_RANK.get(current_status, -1) >= DEPARTURE_STATUS_RANK[
        target_status
    ]:
        return False
    mission.departure_status = target_status
    mission.departure_status_source = NEORAIN_MILESTONE_SOURCE
    return True


def _missing_no_return_prerequisites(mission):
    required = (
        ("Ramp Load Complete", mission.ramp_load_completed_at_utc),
        ("Crew Load Complete", mission.crew_load_completed_at_utc),
        ("Official Block-Out", mission.actual_block_out_datetime_utc),
    )
    return tuple(label for label, value in required if value is None)


def _mutation_result(mission, field, *, changed, value, source):
    return {
        "changed": bool(changed),
        "field": field,
        "departure_status": _normalized_status(mission.departure_status),
        "value": value,
        "source": _normalized_source(source),
    }


def _normalized_status(value):
    return str(value or "").strip().lower()


def _normalized_source(value):
    return str(value or "").strip().lower()


def neorain_outbound_refresh_status(gateway, *, operation=None):
    """Apply the shared live-screen interval to the shared operational window policy."""
    status = dict(node_auto_refresh_status(gateway, operation=operation))
    setting = live_screen_refresh_value(gateway, NEORAIN_OUTBOUND_REFRESH_KEY)
    status["live_screen_refresh_interval_ms"] = setting.effective_interval_ms
    if not setting.enabled:
        status["auto_refresh_enabled"] = False
        status["reason"] = "disabled"
        status["message"] = "Live updates off"
        status["live_status_label"] = "Live updates off"
    return status


def neorain_inbound_refresh_status(gateway, *, operation=None):
    status = dict(node_auto_refresh_status(gateway, operation=operation))
    setting = live_screen_refresh_value(gateway, "neorain.inbound")
    status["live_screen_refresh_interval_ms"] = setting.effective_interval_ms
    if not setting.enabled:
        status.update({"auto_refresh_enabled": False, "reason": "disabled", "message": "Live updates off", "live_status_label": "Live updates off"})
    return status


def neorain_outbound_revision(gateway, *, operation=_OPERATION_UNSET):
    """Compact revision for the persisted inputs rendered on the Outbound board."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    operation_id = operation.id if operation else None
    criterion = (
        SortDateMission.sort_date_operation_id == operation_id
        if operation_id is not None
        else SortDateMission.sort_date_operation_id.is_(None)
    )
    parking_criterion = (
        SortDateParkingAssignment.sort_date_operation_id == operation_id
        if operation_id is not None
        else SortDateParkingAssignment.sort_date_operation_id.is_(None)
    )
    aggregates = (
        _revision_aggregate(
            "missions",
            SortDateMission,
            SortDateMission.updated_at,
            criterion,
            SortDateMission.mission_type == "departure",
        ),
        _revision_aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            parking_criterion,
        ),
        _revision_aggregate(
            "master_planners",
            MasterFlightSchedule,
            MasterFlightSchedule.updated_at,
            MasterFlightSchedule.gateway_code == gateway.code,
            MasterFlightSchedule.sort_name == (operation.sort_name if operation else "night"),
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
        ),
    )
    rows = sorted(
        db.session.execute(union_all(*aggregates)).all(),
        key=lambda row: row.source,
    )
    payload = {
        "gateway_id": gateway.id,
        "operation_id": operation_id,
        "operation_updated_at": _revision_value(getattr(operation, "updated_at", None)),
        "inputs": [
            {
                "source": row.source,
                "row_count": int(row.row_count or 0),
                "max_id": int(row.max_id or 0),
                "id_sum": int(row.id_sum or 0),
                "latest_updated_at": _revision_value(row.latest_updated_at),
            }
            for row in rows
        ],
        # Keep the live-board revision tied to exactly the canonical staffing
        # totals displayed by Rain. The helper is bounded to this operation and
        # does not create or persist any staffing state.
        "staffing_summary": neorain_outbound_staffing_summary(operation),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _outbound_rows(operation, *, missions=None):
    if operation is None:
        return []
    parking_by_tail = {
        _tail_key(assignment.tail_number): _text(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _tail_key(assignment.tail_number)
    }
    if missions is None:
        missions = _outbound_departure_missions(operation)
    eligible_person_ids = _eligible_load_planner_person_ids()
    rows = [
        _outbound_row(
            mission,
            operation,
            parking_by_tail,
            eligible_person_ids=eligible_person_ids,
        )
        for mission in missions
    ]
    return sorted(rows, key=_row_sort_key)


def neorain_outbound_late_summary(operation):
    """Return derived late metrics for current-sort departure missions only."""
    return _outbound_late_summary(_outbound_departure_missions(operation))


def eligible_neorain_load_planners():
    """Return active people assigned to the canonical Load Planners Work Area."""
    work_area_ids = _load_planner_work_area_ids()
    if not work_area_ids:
        return []
    return (
        StaffingPerson.query.join(StaffingWorkAssignment)
        .filter(
            StaffingPerson.active.is_(True),
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id.in_(work_area_ids),
        )
        .order_by(
            StaffingPerson.last_name,
            StaffingPerson.first_name,
            StaffingPerson.id,
        )
        .all()
    )


def effective_neorain_load_planner(mission, *, eligible_person_ids=None):
    """Resolve a departure's valid master or current-sort-only planner."""
    if mission is None or mission.mission_type != "departure":
        return None
    if mission.master_flight_schedule_id:
        planner = getattr(
            getattr(mission, "master_flight_schedule", None),
            "load_planner_person",
            None,
        )
    else:
        planner = getattr(mission, "load_planner_person", None)
    if planner is None:
        return None
    if eligible_person_ids is None:
        eligible_person_ids = _eligible_load_planner_person_ids()
    return planner if planner.id in eligible_person_ids else None


def assign_master_departure_load_planner(master_departure, planner=None):
    """Stage the long-term planner assignment for one Master departure."""
    if master_departure is None or master_departure.mission_type != "departure":
        raise LoadPlannerAssignmentError(
            "Load Planner assignments apply only to departure Master Schedule rows."
        )
    master_departure.load_planner_person_id = _validated_load_planner_id(planner)
    return master_departure


def assign_current_sort_only_departure_load_planner(mission, planner=None):
    """Stage a temporary planner assignment for one unlinked departure mission."""
    if mission is None or mission.mission_type != "departure":
        raise LoadPlannerAssignmentError(
            "Load Planner assignments apply only to departure missions."
        )
    if mission.master_flight_schedule_id:
        raise LoadPlannerAssignmentError(
            "Master-linked departures use their Master Schedule Load Planner."
        )
    mission.load_planner_person_id = _validated_load_planner_id(planner)
    return mission


def neorain_load_planner_lineup(gateway, operation=None):
    """Build bounded persistent and current-sort-only Load Planner sections."""
    eligible_person_ids = _eligible_load_planner_person_ids()
    sort_name = operation.sort_name if operation is not None else "night"
    master_departures = (
        MasterFlightSchedule.query.options(
            joinedload(MasterFlightSchedule.load_planner_person)
        )
        .filter_by(
            gateway_code=gateway.code,
            sort_name=sort_name,
            mission_type="departure",
            active=True,
        )
        .order_by(
            MasterFlightSchedule.planned_time_local,
            MasterFlightSchedule.flight_number,
            MasterFlightSchedule.id,
        )
        .all()
    )
    current_sort_only = []
    if operation is not None:
        current_sort_only = (
            SortDateMission.query.options(joinedload(SortDateMission.load_planner_person))
            .filter_by(
                sort_date_operation_id=operation.id,
                mission_type="departure",
                master_flight_schedule_id=None,
            )
            .order_by(
                SortDateMission.planned_datetime_utc,
                SortDateMission.flight_number,
                SortDateMission.id,
            )
            .all()
        )
    return {
        "master_departures": tuple(
            {
                "departure": departure,
                "planner": (
                    departure.load_planner_person
                    if departure.load_planner_person_id in eligible_person_ids
                    else None
                ),
                "planned_time": _time_value(departure.planned_time_local),
                "version": entity_version(departure),
            }
            for departure in master_departures
        ),
        "current_sort_only_departures": tuple(
            {
                "departure": mission,
                "planner": effective_neorain_load_planner(
                    mission,
                    eligible_person_ids=eligible_person_ids,
                ),
                "planned_time": _time_value(
                    mission_display_timing_data(mission, operation).get(
                        "adjusted_planned_departure_time"
                    )
                    or mission.planned_datetime_local
                ),
                "version": entity_version(mission),
            }
            for mission in current_sort_only
        ),
    }


def _validated_load_planner_id(planner):
    if planner is None:
        return None
    if not isinstance(planner, StaffingPerson) or planner.id is None:
        raise LoadPlannerAssignmentError("Choose an eligible Load Planner.")
    if planner.id not in _eligible_load_planner_person_ids():
        raise LoadPlannerAssignmentError("Choose an active eligible Load Planner.")
    return planner.id


def _eligible_load_planner_person_ids():
    return {person.id for person in eligible_neorain_load_planners()}


def _load_planner_work_area_ids():
    work_area = aliased(StaffingUnit)
    department = aliased(StaffingUnit)
    operation = aliased(StaffingUnit)
    staffing_sort = aliased(StaffingUnit)
    return list(
        db.session.scalars(
            select(work_area.id)
            .join(department, work_area.parent_id == department.id)
            .join(operation, department.parent_id == operation.id)
            .join(staffing_sort, operation.parent_id == staffing_sort.id)
            .where(
                work_area.active.is_(True),
                department.active.is_(True),
                operation.active.is_(True),
                staffing_sort.active.is_(True),
                work_area.unit_type == "work_area",
                department.unit_type == "department",
                operation.unit_type == "operation",
                staffing_sort.unit_type == "sort",
                func.lower(work_area.name) == _LOAD_PLANNER_UNIT_PATH[3],
                func.lower(department.name) == _LOAD_PLANNER_UNIT_PATH[2],
                func.lower(operation.name) == _LOAD_PLANNER_UNIT_PATH[1],
                func.lower(staffing_sort.name) == _LOAD_PLANNER_UNIT_PATH[0],
            )
        )
    )


def neorain_outbound_staffing_summary(operation):
    """Project canonical current-sort Hub/Ramp attendance totals for Rain."""
    summary = {
        "hub": {"on_payroll": 0, "worked": 0},
        "ramp": {"on_payroll": 0, "worked": 0},
    }
    if operation is None:
        return summary

    try:
        counts = attendance_operation_department_counts(operation)
    except ValueError:
        # A gateway without a matching NeoStaffing hierarchy has no applicable
        # staffing totals; preserve the read-only board with neutral values.
        return summary

    for scope_count in counts["scopes"]:
        scope = scope_count["scope"]
        if scope.unit_type != "operation":
            continue
        key = str(scope.name or "").strip().casefold()
        if key not in summary:
            continue
        summary[key] = {
            "on_payroll": int(scope_count["on_payroll"] or 0),
            "worked": int(scope_count["working"] or 0),
        }
    return summary


def _outbound_departure_missions(operation):
    if operation is None:
        return []
    return (
        SortDateMission.query.options(
            joinedload(SortDateMission.load_planner_person),
            joinedload(SortDateMission.master_flight_schedule).joinedload(
                MasterFlightSchedule.load_planner_person
            ),
        )
        .filter_by(sort_date_operation_id=operation.id, mission_type="departure")
        .all()
    )


def _inbound_arrival_missions(operation):
    if operation is None:
        return []
    return SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type="arrival",
    ).all()


def _inbound_row(mission, parking_by_tail, departures_by_tail=None):
    effective = mission.eta_datetime_utc or mission.planned_datetime_utc
    arrival_anchor = mission.actual_block_in_datetime_utc or effective
    late_metrics = neorain_late_metrics_inclusion(mission)
    connection = _inbound_connection(mission, arrival_anchor, departures_by_tail or {})
    return {
        "wave": normalize_wave(mission.wave) or "-",
        "flight_number": _text(mission.flight_number),
        "tail": _text(mission.assigned_tail_number),
        "origin": _text(mission.origin),
        "parking": parking_by_tail.get(_tail_key(mission.assigned_tail_number), ""),
        "eta_sta": format_local_hhmm(effective, mission.timezone or None),
        "status": _normalized_status(mission.arrival_status or "scheduled").replace("_", " ").upper(),
        "block_in": format_local_hhmm(mission.actual_block_in_datetime_utc, mission.timezone or None),
        "arrival_variance": _arrival_variance(mission),
        "late_metrics_included": late_metrics["included"],
        "late_metrics_inclusion_source": late_metrics["source"],
        "connecting_outbound": connection["flight_number"],
        "ground_time": connection["ground_time"],
        "sort_time": effective,
        "mission_id": mission.id,
        "version": entity_version(mission),
    }


def neorain_inbound_row(mission, operation):
    """Format one arrival for the in-place late-inclusion response."""
    parking_by_tail = {
        _tail_key(assignment.tail_number): _text(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _tail_key(assignment.tail_number)
    }
    row = _inbound_row(
        mission,
        parking_by_tail,
        _inbound_departures_by_tail(_outbound_departure_missions(operation), operation),
    )
    row.pop("sort_time", None)
    return row


def _inbound_departures_by_tail(departures, operation):
    values = {}
    for departure in departures:
        tail = _tail_key(departure.assigned_tail_number)
        if not tail:
            continue
        effective = departure.actual_block_out_datetime_utc or departure.planned_datetime_utc
        if effective is not None:
            values.setdefault(tail, []).append((effective, departure))
    for candidates in values.values():
        candidates.sort(key=lambda item: (item[0], item[1].flight_number, item[1].id))
    return values


def _inbound_connection(mission, arrival_anchor, departures_by_tail):
    if arrival_anchor is None:
        return {"flight_number": "", "ground_time": ""}
    for departure_time, departure in departures_by_tail.get(_tail_key(mission.assigned_tail_number), ()):
        if departure_time < arrival_anchor:
            continue
        minutes = int((departure_time - arrival_anchor).total_seconds() / 60)
        return {
            "flight_number": _text(departure.flight_number),
            "ground_time": _duration_hhmm(minutes),
        }
    return {"flight_number": "", "ground_time": ""}


def _duration_hhmm(minutes):
    hours, remainder = divmod(max(0, int(minutes)), 60)
    return f"{hours}:{remainder:02d}"


def _outbound_late_summary(missions):
    summary = {
        "first_wave": {"aircraft_late": 0, "late_minutes": 0},
        "second_wave": {"aircraft_late": 0, "late_minutes": 0},
        "total": {"aircraft_late": 0, "late_minutes": 0},
    }
    for mission in missions:
        minutes = _departure_variance_minutes(mission)
        if (
            minutes is None
            or minutes <= 0
            or not neorain_late_metrics_inclusion(mission)["included"]
        ):
            continue
        buckets = ["total"]
        wave = normalize_wave(mission.wave)
        if wave == "1":
            buckets.insert(0, "first_wave")
        elif wave == "2":
            buckets.insert(0, "second_wave")
        for bucket in buckets:
            summary[bucket]["aircraft_late"] += 1
            summary[bucket]["late_minutes"] += minutes
    for values in summary.values():
        count = values["aircraft_late"]
        minutes = values["late_minutes"]
        values["average"] = _late_average_display(minutes, count)
    return summary


def neorain_inbound_late_summary(operation):
    """Return derived Block-In-versus-STA metrics for current-sort arrivals."""
    return _inbound_late_summary(_inbound_arrival_missions(operation))


def _inbound_late_summary(missions):
    summary = {
        "first_wave": {"aircraft_late": 0, "late_minutes": 0},
        "second_wave": {"aircraft_late": 0, "late_minutes": 0},
        "total": {"aircraft_late": 0, "late_minutes": 0},
    }
    for mission in missions:
        if (
            mission.actual_block_in_datetime_utc is None
            or mission.planned_datetime_utc is None
            or not neorain_late_metrics_inclusion(mission)["included"]
        ):
            continue
        minutes = int(
            (mission.actual_block_in_datetime_utc - mission.planned_datetime_utc)
            .total_seconds()
            / 60
        )
        if minutes <= 0:
            continue
        buckets = ["total"]
        wave = normalize_wave(mission.wave)
        if wave == "1":
            buckets.insert(0, "first_wave")
        elif wave == "2":
            buckets.insert(0, "second_wave")
        for bucket in buckets:
            summary[bucket]["aircraft_late"] += 1
            summary[bucket]["late_minutes"] += minutes
    for values in summary.values():
        values["average"] = _late_average_display(
            values["late_minutes"], values["aircraft_late"]
        )
    return summary


def _late_average_display(minutes, count):
    if not count:
        return "0"
    quotient, remainder = divmod(minutes, count)
    return str(quotient) if remainder == 0 else f"{minutes / count:.1f}"


def neorain_outbound_row(mission, operation):
    """Format one updated mission with the same canonical Outbound presentation."""
    parking_by_tail = {
        _tail_key(assignment.tail_number): _text(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _tail_key(assignment.tail_number)
    }
    row = _outbound_row(
        mission,
        operation,
        parking_by_tail,
        eligible_person_ids=_eligible_load_planner_person_ids(),
    )
    row.pop("sort_time", None)
    row["departure_status"] = _normalized_status(mission.departure_status)
    return row


def _outbound_row(mission, operation, parking_by_tail, *, eligible_person_ids=None):
    timing = mission_display_timing_data(mission, operation)
    planned = timing.get("adjusted_planned_departure_time") or mission.planned_datetime_local
    timezone_name = mission.timezone or None
    status = str(mission.departure_status or "scheduled").strip().lower()
    late_metrics = neorain_late_metrics_inclusion(mission)
    planner = effective_neorain_load_planner(
        mission,
        eligible_person_ids=eligible_person_ids,
    )
    return {
        "wave": timing.get("wave") or "-",
        "flight_number": _text(mission.flight_number),
        "tail": _text(mission.assigned_tail_number),
        "destination": _text(mission.destination),
        "parking": parking_by_tail.get(_tail_key(mission.assigned_tail_number), ""),
        "planned_time": _time_value(planned),
        "status": status.replace("_", " ").upper(),
        "load_planner": planner.full_name if planner else "UNASSIGNED",
        "elmac": format_local_hhmm(mission.elmac_completed_at_utc, timezone_name),
        "ramp_load_complete": format_local_hhmm(
            mission.ramp_load_completed_at_utc, timezone_name
        ),
        "crew_load_complete": format_local_hhmm(
            mission.crew_load_completed_at_utc, timezone_name
        ),
        "official_block_out": format_local_hhmm(
            mission.actual_block_out_datetime_utc, timezone_name
        ),
        "departure_variance": _departure_variance(mission),
        "late_metrics_included": late_metrics["included"],
        "late_metrics_inclusion_source": late_metrics["source"],
        "no_return": "NO RETURN" if status == "departed" else "",
        "sort_time": planned,
        "mission_id": mission.id,
        "version": entity_version(mission),
    }


def _departure_variance(mission):
    """Return signed whole minutes from canonical STD to official Block-Out."""
    minutes = _departure_variance_minutes(mission)
    if minutes is None:
        return "-"
    return f"+{minutes}" if minutes > 0 else str(minutes)


def _arrival_variance(mission):
    if mission.actual_block_in_datetime_utc is None or mission.planned_datetime_utc is None:
        return "-"
    minutes = int(
        (mission.actual_block_in_datetime_utc - mission.planned_datetime_utc)
        .total_seconds()
        / 60
    )
    return f"+{minutes}" if minutes > 0 else str(minutes)


def _departure_variance_minutes(mission):
    scheduled_departure = mission.planned_datetime_utc
    official_block_out = mission.actual_block_out_datetime_utc
    if scheduled_departure is None or official_block_out is None:
        return None
    return int((official_block_out - scheduled_departure).total_seconds() / 60)


def _revision_aggregate(source, model, timestamp_column, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        func.max(timestamp_column).label("latest_updated_at"),
    ).where(*criteria)


def _row_sort_key(row):
    return (
        row["sort_time"] is None,
        row["sort_time"] or datetime.max,
        row["flight_number"],
        row["mission_id"],
    )


def _tail_key(value):
    return _text(value)


def _text(value):
    return str(value or "").strip().upper()


def _time_value(value):
    return value.strftime("%H:%M") if value else ""


def _revision_value(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")
