from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.extensions import db
from app.models import (
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    SortTimelineSettings,
)
from app.services.flight_rules import derive_aircraft_type_from_tail_number
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    current_operations_for_gateway,
    gateway_timezone,
    operation_is_active_at,
)
from app.services.parking_physical_validator import (
    parking_physical_validation_context,
    sync_parking_physical_alerts,
)


PARKING_RAMP_GROUPS = (
    ("Alpha", "A", tuple(f"A{number:02d}" for number in range(1, 11))),
    ("Bravo", "B", tuple(f"B{number:02d}" for number in range(1, 11))),
    ("Charlie", "C", tuple(f"C{number:02d}" for number in range(1, 11))),
    ("Delta", "D", tuple(f"D{number:02d}" for number in range(1, 11))),
    ("Echo", "E", tuple(f"E{number:02d}" for number in range(1, 11))),
    ("Remote", "R", ("R04", "R03", "R02", "R01")),
)

PARKING_LANES = (1, 2)
QUICK_TURN_THRESHOLDS = {
    "757": 45,
    "A300": 75,
    "767": 75,
}


class ParkingPlanError(ValueError):
    pass


class ParkingLaneOccupied(ParkingPlanError):
    def __init__(self, occupied_tail):
        self.occupied_tail = occupied_tail
        super().__init__(f"{occupied_tail} is already assigned to that slot.")


def parking_plan_landing_context(gateway):
    local_now = current_gateway_local_datetime(gateway)
    operations = current_operations_for_gateway(gateway, now=local_now)
    options = []
    for operation in operations:
        tail_rows = tail_rows_for_operation(gateway, operation)
        summary = _summary_for_rows(tail_rows)
        options.append(
            {
                "operation": operation,
                "is_active": operation_is_active_at(operation, local_now, gateway),
                "mission_count": len(operation.missions),
                "assigned_count": summary["assigned_tails"],
                "unassigned_tail_count": summary["unassigned_tails"],
                "total_tails": summary["total_tails"],
            }
        )

    return {
        "operation_options": options,
        "local_date": local_now.date(),
    }


def parking_plan_context(gateway, operation=None):
    operation = operation or current_active_sort_operation(gateway)
    if not operation:
        return {
            "operation": None,
            "summary": _empty_summary(),
            "tail_rows": [],
            "unassigned_tail_rows": [],
            "ramp_groups": _empty_ramp_groups(),
            "positions": PARKING_RAMP_GROUPS,
        }

    tail_rows = tail_rows_for_operation(gateway, operation)
    assignments = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    assignment_by_tail = {row["tail"]: row["assignment"] for row in tail_rows}
    _apply_departure_order(tail_rows)
    ramp_groups = _ramp_groups_for_rows(tail_rows, assignment_by_tail)
    unassigned = [
        row
        for row in tail_rows
        if not row["assigned_position"]
    ]
    parking_status = parking_status_for_rows(tail_rows, assignments)
    physical_validation = parking_physical_validation_context(operation, tail_rows=tail_rows)
    parking_status["physical_conflicts"] = physical_validation["conflicts"]
    parking_status["physical_conflict_count"] = physical_validation["conflict_count"]
    parking_status["conflict_count"] += physical_validation["conflict_count"]
    parking_status["has_conflicts"] = parking_status["has_conflicts"] or physical_validation[
        "has_conflicts"
    ]
    parking_status["has_warnings"] = parking_status["has_warnings"] or physical_validation[
        "has_conflicts"
    ]
    parking_status["is_clean"] = not parking_status["has_warnings"]
    alert_sync = sync_parking_physical_alerts(gateway, operation, physical_validation)
    summary = _summary_for_rows(tail_rows)
    summary["conflict_count"] = parking_status["conflict_count"]

    return {
        "operation": operation,
        "summary": summary,
        "parking_status": parking_status,
        "parking_physical_validation": physical_validation,
        "parking_physical_alert_sync": alert_sync,
        "tail_rows": tail_rows,
        "unassigned_tail_rows": unassigned,
        "ramp_groups": ramp_groups,
        "positions": PARKING_RAMP_GROUPS,
    }


def parking_status_for_rows(tail_rows, assignments=None):
    assignments = [
        assignment
        for assignment in (assignments or [])
        if assignment is not None
    ]
    unassigned_rows = [
        row
        for row in tail_rows
        if not row.get("assigned_position")
    ]
    duplicate_tail_conflicts = _duplicate_tail_conflicts(assignments)
    duplicate_slot_conflicts = _duplicate_slot_conflicts(assignments)
    parked_unlinked_tails = _parked_unlinked_tail_warnings(tail_rows)
    not_parked_missions = _not_parked_mission_warnings(tail_rows)
    oos_active_missions = _oos_active_mission_warnings(tail_rows)
    has_warnings = bool(
        unassigned_rows
        or duplicate_tail_conflicts
        or duplicate_slot_conflicts
        or parked_unlinked_tails
        or not_parked_missions
        or oos_active_missions
    )

    return {
        "summary": {
            "total_tails_needing_parking": len(tail_rows),
            "assigned_tails": len(tail_rows) - len(unassigned_rows),
            "unassigned_tails": len(unassigned_rows),
        },
        "unassigned_tails": [row["tail"] for row in unassigned_rows],
        "duplicate_tail_conflicts": duplicate_tail_conflicts,
        "duplicate_slot_conflicts": duplicate_slot_conflicts,
        "parked_unlinked_tails": parked_unlinked_tails,
        "not_parked_missions": not_parked_missions,
        "oos_active_missions": oos_active_missions,
        "conflict_count": len(duplicate_tail_conflicts) + len(duplicate_slot_conflicts),
        "has_conflicts": bool(duplicate_tail_conflicts or duplicate_slot_conflicts),
        "has_warnings": has_warnings,
        "is_clean": not has_warnings,
    }


def current_active_sort_operation(gateway):
    local_now = current_gateway_local_datetime(gateway)
    for operation in current_operations_for_gateway(gateway, now=local_now):
        if operation_is_active_at(operation, local_now, gateway):
            return operation
    return None


def tail_rows_for_operation(gateway, operation):
    assignment_rows = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    assignments = {assignment.tail_number: assignment for assignment in assignment_rows}
    tail_states = {
        state.tail_number.strip().upper(): state
        for state in SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
        ).all()
        if state.tail_number
    }
    grouped = {}
    missions = (
        SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
        .filter(SortDateMission.assigned_tail_number.isnot(None))
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )

    for mission in missions:
        tail = _normalize_tail(mission.assigned_tail_number)
        if not tail:
            continue
        grouped.setdefault(tail, {"arrivals": [], "departures": []})
        if mission.mission_type == "arrival":
            grouped[tail]["arrivals"].append(mission)
        elif mission.mission_type == "departure":
            grouped[tail]["departures"].append(mission)

    for assignment in assignment_rows:
        tail = _normalize_tail(assignment.tail_number)
        if tail and assignment.position_code:
            grouped.setdefault(tail, {"arrivals": [], "departures": []})

    rows = []
    taxi_minutes = _taxi_to_ramp_minutes(gateway)
    timezone_name = gateway_timezone(gateway)
    for tail, mission_group in sorted(grouped.items()):
        arrival = _first_mission(mission_group["arrivals"])
        departure = _first_mission(mission_group["departures"])
        tail_missions = mission_group["arrivals"] + mission_group["departures"]
        active_missions = _active_missions_for_tail(
            mission_group["arrivals"],
            mission_group["departures"],
        )
        tail_state = tail_states.get(tail)
        assignment = assignments.get(tail)
        block_in_local, arrival_source = _operational_block_in_local(
            arrival,
            timezone_name,
            taxi_minutes,
        )
        departure_local = _departure_local(departure)
        ground_minutes = _ground_minutes(block_in_local, departure_local)
        aircraft_type = _aircraft_type_for_tail(tail, arrival, departure, tail_state)
        quick_turn = _is_quick_turn(aircraft_type, ground_minutes)
        assigned_position = _assignment_position_label(assignment)

        rows.append(
            {
                "tail": tail,
                "arrival": arrival,
                "departure": departure,
                "arrival_origin": _mission_origin(arrival),
                "arrival_time": _format_local_time(block_in_local),
                "arrival_source": arrival_source,
                "arrival_block_in_local": block_in_local,
                "departure_destination": _mission_destination(departure),
                "departure_time": _format_local_time(departure_local),
                "departure_datetime_local": departure_local,
                "ground_minutes": ground_minutes,
                "ground_time": _format_ground_time(ground_minutes),
                "aircraft_type": aircraft_type,
                "quick_turn": quick_turn,
                "active_mission_lines": [
                    _mission_display_line(mission) for mission in active_missions
                ],
                "all_mission_lines": [
                    _mission_display_line(mission) for mission in tail_missions
                ],
                "cancelled_mission_lines": [
                    _mission_display_line(mission)
                    for mission in tail_missions
                    if _mission_is_cancelled(mission)
                ],
                "has_active_mission": bool(active_missions),
                "has_cancelled_mission": any(
                    _mission_is_cancelled(mission) for mission in tail_missions
                ),
                "mission_attachment_label": _mission_attachment_label(
                    len(tail_missions),
                    active_missions,
                ),
                "assignment": assignment,
                "assigned_position": assigned_position,
                "is_hot": bool(assignment and assignment.is_hot),
                "is_out_of_service": bool(tail_state and tail_state.is_out_of_service),
                "note": assignment.note if assignment else "",
                "status": _row_status(assignment),
                "departure_order": None,
            }
        )

    return rows


def assign_tail_to_lane(
    operation,
    tail_number,
    ramp_code,
    position_code,
    lane_number,
    user=None,
    replace_occupied=False,
    is_hot=None,
    note=None,
):
    tail_number = _normalize_tail(tail_number)
    ramp_code = _normalize_ramp_code(ramp_code)
    position_code = _normalize_position_code(position_code)
    lane_number = _normalize_lane(lane_number)
    if not tail_number:
        raise ParkingPlanError("Select a tail before assigning parking.")
    _validate_position(ramp_code, position_code)

    if tail_number not in _current_operation_tails(operation):
        raise ParkingPlanError(f"{tail_number or 'Tail'} is not in the current sort.")

    assignment = _assignment_for_tail(operation, tail_number, create=True)
    occupied = _assignment_for_lane(operation, ramp_code, position_code, lane_number)
    if occupied and occupied.tail_number != tail_number:
        if not replace_occupied:
            raise ParkingLaneOccupied(occupied.tail_number)
        occupied.ramp_code = None
        occupied.position_code = None
        occupied.lane_number = None
        occupied.assigned_by_user_id = getattr(user, "id", None)
        occupied.assigned_at = _utc_now()

    assignment.ramp_code = ramp_code
    assignment.position_code = position_code
    assignment.lane_number = lane_number
    if is_hot is not None:
        assignment.is_hot = bool(is_hot)
    if note is not None:
        assignment.note = str(note or "").strip()
    assignment.assigned_by_user_id = getattr(user, "id", None)
    assignment.assigned_at = _utc_now()
    db.session.flush()
    return assignment


def unassign_tail(operation, tail_number, user=None):
    assignment = _assignment_for_tail(operation, tail_number)
    if not assignment:
        return None
    assignment.ramp_code = None
    assignment.position_code = None
    assignment.lane_number = None
    assignment.assigned_by_user_id = getattr(user, "id", None)
    assignment.assigned_at = _utc_now()
    db.session.flush()
    return assignment


def clear_parking_assignments(operation, user=None):
    assignments = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
    ).all()
    assigned = [
        assignment
        for assignment in assignments
        if assignment.ramp_code or assignment.position_code or assignment.lane_number
    ]
    now = _utc_now()
    user_id = getattr(user, "id", None)
    for assignment in assigned:
        assignment.ramp_code = None
        assignment.position_code = None
        assignment.lane_number = None
        assignment.assigned_by_user_id = user_id
        assignment.assigned_at = now
    db.session.flush()
    return len(assigned)


def set_tail_hot(operation, tail_number, is_hot, user=None, note=None):
    tail_number = _normalize_tail(tail_number)
    if tail_number not in _current_operation_tail_assets(operation):
        raise ParkingPlanError(f"{tail_number or 'Tail'} is not in the current sort.")
    assignment = _assignment_for_tail(operation, tail_number, create=True)
    if is_hot is not None:
        assignment.is_hot = bool(is_hot)
    if note is not None:
        assignment.note = str(note or "").strip()
    assignment.assigned_by_user_id = getattr(user, "id", None)
    assignment.assigned_at = _utc_now()
    db.session.flush()
    return assignment


def set_tail_out_of_service(operation, tail_number, is_out_of_service, user=None):
    tail_number = _normalize_tail(tail_number)
    if tail_number not in _current_operation_tail_assets(operation):
        raise ParkingPlanError(f"{tail_number or 'Tail'} is not in the current sort.")

    tail_state = _tail_state_for_operation(operation, tail_number, create=True)
    tail_state.is_out_of_service = bool(is_out_of_service)
    db.session.flush()
    return tail_state


def parking_position_options():
    return PARKING_RAMP_GROUPS


def _empty_ramp_groups():
    return [
        {
            "name": name,
            "code": code,
            "positions": [
                {
                    "code": position,
                    **_position_layout(code, position),
                    "lanes": [
                        {"number": lane, "tail": None, "row": None}
                        for lane in PARKING_LANES
                    ],
                }
                for position in positions
            ],
        }
        for name, code, positions in PARKING_RAMP_GROUPS
    ]


def _ramp_groups_for_rows(tail_rows, assignment_by_tail):
    row_by_tail = {row["tail"]: row for row in tail_rows}
    groups = _empty_ramp_groups()
    for group in groups:
        for position in group["positions"]:
            for lane in position["lanes"]:
                assignment = _assignment_for_position(
                    assignment_by_tail.values(),
                    group["code"],
                    position["code"],
                    lane["number"],
                )
                if not assignment:
                    continue
                lane["tail"] = assignment.tail_number
                lane["row"] = row_by_tail.get(assignment.tail_number)
    return groups


def _assignment_for_position(assignments, ramp_code, position_code, lane_number):
    return next(
        (
            assignment
            for assignment in assignments
            if assignment
            and assignment.ramp_code == ramp_code
            and assignment.position_code == position_code
            and assignment.lane_number == lane_number
        ),
        None,
    )


def _position_layout(ramp_code, position_code):
    if ramp_code == "R":
        remote_slots = {
            "R04": ("remote-top", "remote-top-left"),
            "R03": ("remote-top", "remote-top-right"),
            "R02": ("remote-bottom", "remote-bottom-left"),
            "R01": ("remote-bottom", "remote-bottom-right"),
        }
        side, slot = remote_slots.get(position_code, ("remote", "remote"))
        return {"side": side, "slot": slot}

    try:
        number = int(position_code[1:])
    except (TypeError, ValueError):
        return {"side": "unknown", "slot": "unknown"}

    if 1 <= number <= 4:
        return {"side": "left", "slot": f"left-{number}"}
    if 5 <= number <= 8:
        return {"side": "right", "slot": f"right-{number - 4}"}
    if number == 9:
        return {"side": "bottom", "slot": "bottom"}
    if number == 10:
        return {"side": "top", "slot": "top"}
    return {"side": "unknown", "slot": "unknown"}


def _apply_departure_order(tail_rows):
    rows_by_ramp = {}
    for row in tail_rows:
        assignment = row["assignment"]
        if not assignment or not assignment.ramp_code or not row["departure_datetime_local"]:
            continue
        rows_by_ramp.setdefault(assignment.ramp_code, []).append(row)

    for rows in rows_by_ramp.values():
        rows.sort(
            key=lambda row: (
                row["departure_datetime_local"],
                row["tail"],
            )
        )
        for index, row in enumerate(rows, start=1):
            row["departure_order"] = index


def _summary_for_rows(rows):
    assigned = [row for row in rows if row["assigned_position"]]
    hot = [row for row in rows if row["is_hot"]]
    quick_turn = [row for row in rows if row["quick_turn"]]
    conflicts = [row for row in rows if row["status"] == "conflict"]
    return {
        "total_tails": len(rows),
        "assigned_tails": len(assigned),
        "unassigned_tails": len(rows) - len(assigned),
        "hot_count": len(hot),
        "quick_turn_count": len(quick_turn),
        "conflict_count": len(conflicts),
    }


def _empty_summary():
    return {
        "total_tails": 0,
        "assigned_tails": 0,
        "unassigned_tails": 0,
        "hot_count": 0,
        "quick_turn_count": 0,
        "conflict_count": 0,
    }


def _duplicate_tail_conflicts(assignments):
    grouped = defaultdict(list)
    for assignment in assignments:
        tail = _normalize_tail(getattr(assignment, "tail_number", ""))
        if tail:
            grouped[tail].append(assignment)

    conflicts = []
    for tail, tail_assignments in sorted(grouped.items()):
        if len(tail_assignments) <= 1:
            continue
        conflicts.append(
            {
                "tail": tail,
                "locations": [
                    _assignment_location_label(assignment)
                    for assignment in tail_assignments
                ],
                "anchor": _parking_anchor_fragment(f"parking-tail-{tail}"),
            }
        )
    return conflicts


def _duplicate_slot_conflicts(assignments):
    grouped = defaultdict(list)
    for assignment in assignments:
        ramp_code = _normalize_ramp_code(getattr(assignment, "ramp_code", ""))
        position_code = _normalize_position_code(getattr(assignment, "position_code", ""))
        lane_number = getattr(assignment, "lane_number", None)
        if not ramp_code or not position_code or lane_number is None:
            continue
        grouped[(ramp_code, position_code, lane_number)].append(assignment)

    conflicts = []
    for (_ramp_code, position_code, lane_number), slot_assignments in sorted(grouped.items()):
        if len(slot_assignments) <= 1:
            continue
        conflicts.append(
            {
                "position": f"{position_code} Slot {lane_number}",
                "position_code": position_code,
                "lane_number": lane_number,
                "anchor": _parking_anchor_fragment(f"parking-position-{position_code}"),
                "tails": [
                    _normalize_tail(getattr(assignment, "tail_number", ""))
                    for assignment in slot_assignments
                    if _normalize_tail(getattr(assignment, "tail_number", ""))
                ],
            }
        )
    return conflicts


def _parked_unlinked_tail_warnings(tail_rows):
    warnings = []
    for row in tail_rows:
        if not row.get("assigned_position") or row.get("has_active_mission"):
            continue
        tail = _normalize_tail(row.get("tail"))
        if not tail:
            continue
        warnings.append(
            {
                "tail": tail,
                "position": row.get("assigned_position") or "-",
                "status": row.get("mission_attachment_label") or "NO ACTIVE MISSION",
                "mission_lines": _row_mission_lines(row, active_only=False),
                "anchor": _parking_anchor_fragment(f"parking-tail-{tail}"),
            }
        )
    return warnings


def _not_parked_mission_warnings(tail_rows):
    warnings = []
    for row in tail_rows:
        if row.get("assigned_position") or not row.get("has_active_mission"):
            continue
        tail = _normalize_tail(row.get("tail"))
        if not tail:
            continue
        warnings.append(
            {
                "tail": tail,
                "mission_lines": _row_mission_lines(row),
                "anchor": _parking_anchor_fragment(f"parking-tail-{tail}"),
            }
        )
    return warnings


def _oos_active_mission_warnings(tail_rows):
    warnings = []
    for row in tail_rows:
        if not row.get("is_out_of_service") or not row.get("has_active_mission"):
            continue
        tail = _normalize_tail(row.get("tail"))
        if not tail:
            continue
        warnings.append(
            {
                "tail": tail,
                "position": row.get("assigned_position") or "-",
                "mission_lines": _row_mission_lines(row),
                "anchor": _parking_anchor_fragment(f"parking-tail-{tail}"),
            }
        )
    return warnings


def _row_mission_lines(row, active_only=True):
    key = "active_mission_lines" if active_only else "all_mission_lines"
    lines = [line for line in (row.get(key) or []) if line]
    if lines:
        return lines
    if not active_only:
        return [line for line in (row.get("cancelled_mission_lines") or []) if line]
    return []


def _assignment_location_label(assignment):
    position_code = _normalize_position_code(getattr(assignment, "position_code", ""))
    lane_number = getattr(assignment, "lane_number", None)
    if position_code and lane_number:
        return f"{position_code} Slot {lane_number}"
    return "UNASSIGNED"


def _parking_anchor_fragment(value):
    text = str(value or "").strip().upper()
    cleaned = "".join(character if character.isalnum() else "-" for character in text)
    return "-".join(part for part in cleaned.split("-") if part)


def _current_operation_tails(operation):
    if not operation:
        return set()
    return {
        _normalize_tail(tail)
        for tail, in db.session.query(SortDateMission.assigned_tail_number)
        .filter_by(sort_date_operation_id=operation.id)
        .filter(SortDateMission.assigned_tail_number.isnot(None))
        .all()
        if _normalize_tail(tail)
    }


def _current_operation_tail_assets(operation):
    tails = _current_operation_tails(operation)
    if not operation:
        return tails
    assigned_tails = {
        _normalize_tail(tail)
        for tail, in db.session.query(SortDateParkingAssignment.tail_number)
        .filter_by(sort_date_operation_id=operation.id)
        .filter(SortDateParkingAssignment.position_code.isnot(None))
        .all()
        if _normalize_tail(tail)
    }
    return tails | assigned_tails


def _tail_state_for_operation(operation, tail_number, create=False):
    tail_number = _normalize_tail(tail_number)
    tail_state = SortDateTailState.query.filter_by(
        sort_date=operation.sort_date,
        gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,
        tail_number=tail_number,
    ).first()
    if not tail_state and create:
        tail_state = SortDateTailState(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            tail_number=tail_number,
            aircraft_type_source="unknown",
            is_out_of_service=False,
        )
        db.session.add(tail_state)
    return tail_state


def _assignment_for_tail(operation, tail_number, create=False):
    tail_number = _normalize_tail(tail_number)
    assignment = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
        tail_number=tail_number,
    ).first()
    if not assignment and create:
        assignment = SortDateParkingAssignment(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
        )
        db.session.add(assignment)
    return assignment


def _assignment_for_lane(operation, ramp_code, position_code, lane_number):
    return SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
        ramp_code=ramp_code,
        position_code=position_code,
        lane_number=lane_number,
    ).first()


def _first_mission(missions):
    return missions[0] if missions else None


def _active_missions_for_tail(arrivals, departures):
    active = []
    active.extend(
        mission for mission in arrivals if not _mission_is_cancelled(mission)
    )
    active.extend(
        mission for mission in departures if not _mission_is_cancelled(mission)
    )
    return sorted(
        active,
        key=lambda mission: (
            getattr(mission, "planned_datetime_local", None) or datetime.max,
            getattr(mission, "id", 0) or 0,
        ),
    )


def _mission_is_cancelled(mission):
    if not mission:
        return False
    status = (
        mission.arrival_status
        if mission.mission_type == "arrival"
        else mission.departure_status
    )
    return str(status or "").strip().lower() == "cancelled"


def _mission_display_line(mission):
    if not mission:
        return ""
    mission_label = "ARR" if mission.mission_type == "arrival" else "DEP"
    route = (
        f"{_mission_origin(mission)}-{_mission_destination(mission)}"
        if (_mission_origin(mission) or _mission_destination(mission))
        else "-"
    )
    return " ".join(
        part
        for part in (
            mission_label,
            _normalize_flight_display(mission.flight_number),
            route,
            _format_local_time(mission.planned_datetime_local),
        )
        if part
    )


def _mission_attachment_label(mission_count, active_missions):
    if active_missions:
        return ""
    if mission_count:
        return "NO ACTIVE MISSION"
    return "UNATTACHED TAIL"


def _operational_block_in_local(mission, timezone_name, taxi_minutes):
    if not mission:
        return None, "missing"
    if mission.api_assumed_arrived_time_utc:
        return _utc_to_local(mission.api_assumed_arrived_time_utc, timezone_name), "api block-in"
    if mission.actual_block_in_datetime_utc:
        return _utc_to_local(mission.actual_block_in_datetime_utc, timezone_name), "actual block-in"
    if mission.eta_datetime_utc:
        return (
            _utc_to_local(mission.eta_datetime_utc, timezone_name)
            + timedelta(minutes=taxi_minutes),
            "api eta + taxi",
        )
    if mission.planned_datetime_local:
        return mission.planned_datetime_local + timedelta(minutes=taxi_minutes), "sta + taxi"
    return None, "missing"


def _departure_local(mission):
    return mission.planned_datetime_local if mission else None


def _ground_minutes(block_in_local, departure_local):
    if not block_in_local or not departure_local:
        return None
    departure = departure_local
    while departure < block_in_local:
        departure += timedelta(days=1)
    return int((departure - block_in_local).total_seconds() // 60)


def _aircraft_type_for_tail(tail, arrival, departure, tail_state):
    for value in (
        getattr(departure, "api_aircraft_model", None),
        getattr(arrival, "api_aircraft_model", None),
        getattr(tail_state, "aircraft_type", None),
        derive_aircraft_type_from_tail_number(tail),
    ):
        normalized = _normalize_aircraft_type(value)
        if normalized:
            return normalized
    return ""


def _normalize_aircraft_type(value):
    text = str(value or "").strip().upper()
    if not text or text == "UNKNOWN":
        return ""
    if "A300" in text or "A-300" in text:
        return "A300"
    for aircraft_type in ("747", "757", "767"):
        if aircraft_type in text:
            return aircraft_type
    return text


def _is_quick_turn(aircraft_type, ground_minutes):
    if ground_minutes is None:
        return False
    threshold = QUICK_TURN_THRESHOLDS.get(_normalize_aircraft_type(aircraft_type))
    return bool(threshold is not None and ground_minutes <= threshold)


def _taxi_to_ramp_minutes(gateway):
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        return 10
    try:
        return max(0, int(settings.taxi_to_ramp_minutes or 0))
    except (TypeError, ValueError):
        return 10


def _utc_to_local(value, timezone_name):
    if not value:
        return None
    if value.tzinfo:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        return value.replace(tzinfo=timezone.utc).astimezone(
            ZoneInfo(timezone_name)
        ).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        if timezone_name == "America/Chicago":
            standard_local = value - timedelta(hours=6)
            if _is_us_central_daylight_time(standard_local):
                return value - timedelta(hours=5)
        return value


def _is_us_central_daylight_time(local_datetime):
    year = local_datetime.year
    dst_start = _nth_weekday_of_month(year, 3, 6, 2).replace(hour=2)
    dst_end = _nth_weekday_of_month(year, 11, 6, 1).replace(hour=2)
    return dst_start <= local_datetime < dst_end


def _nth_weekday_of_month(year, month, weekday, occurrence):
    candidate = datetime(year, month, 1)
    days_until_weekday = (weekday - candidate.weekday()) % 7
    return candidate + timedelta(days=days_until_weekday + (occurrence - 1) * 7)


def _format_local_time(value):
    if not value:
        return "-"
    return value.strftime("%H:%M")


def _normalize_flight_display(value):
    return str(value or "").strip().upper() or "-"


def _format_ground_time(minutes):
    if minutes is None:
        return "GT -"
    hours, remainder = divmod(max(0, minutes), 60)
    return f"GT {hours}:{remainder:02d}"


def _mission_origin(mission):
    return str(getattr(mission, "origin", "") or "-").strip().upper() or "-"


def _mission_destination(mission):
    return str(getattr(mission, "destination", "") or "-").strip().upper() or "-"


def _assignment_position_label(assignment):
    if not assignment or not assignment.ramp_code or not assignment.position_code:
        return ""
    lane = assignment.lane_number or "-"
    return f"{assignment.position_code}-{lane}"


def _row_status(assignment):
    if not assignment or not assignment.ramp_code or not assignment.position_code:
        return "unassigned"
    return "assigned"


def _validate_position(ramp_code, position_code):
    positions = {
        code: set(positions)
        for _name, code, positions in PARKING_RAMP_GROUPS
    }
    if ramp_code not in positions or position_code not in positions[ramp_code]:
        raise ParkingPlanError("Select a valid parking position.")


def _normalize_tail(value):
    return str(value or "").strip().upper()


def _normalize_ramp_code(value):
    return str(value or "").strip().upper()


def _normalize_position_code(value):
    value = str(value or "").strip().upper().replace(" ", "")
    if len(value) >= 2:
        ramp_code = value[0]
        slot_digits = value[1:]
        if ramp_code in {"A", "B", "C", "D", "E", "R"} and slot_digits.isdigit():
            return f"{ramp_code}{int(slot_digits):02d}"
    return value


def _normalize_lane(value):
    try:
        lane = int(value)
    except (TypeError, ValueError) as exc:
        raise ParkingPlanError("Select slot 1 or 2.") from exc
    if lane not in PARKING_LANES:
        raise ParkingPlanError("Select slot 1 or 2.")
    return lane


def _utc_now():
    return datetime.utcnow()
