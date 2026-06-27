from sqlalchemy import or_

from app.models import (
    NeoErmacDoorPull,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
)
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    normalize_destination,
)
from app.services.sort_date_operations import mission_display_timing_data


PULL_KEYS = (
    ("pure", "Pure", "pure_pull_time_local", "actual_pure_pull_time_local", "no_pure_pull"),
    (
        "first_mix",
        "1st Mix",
        "first_mix_pull_time_local",
        "actual_first_mix_pull_time_local",
        "no_first_mix_pull",
    ),
    (
        "second_mix",
        "2nd Mix",
        "final_mix_pull_time_local",
        "actual_second_mix_pull_time_local",
        "no_second_mix_pull",
    ),
)


def view_outbound_context(gateway):
    operation = _current_operation(gateway)
    assignments_by_destination = _lineup_assignments_by_destination(gateway)
    pulls_by_destination = _door_pulls_by_destination(gateway, operation)
    parking_by_tail = _parking_assignments_by_tail(operation)
    missions = _departure_missions(operation)

    rows = []
    seen_destinations = set()
    for mission in missions:
        destination = normalize_destination(mission.destination)
        if not destination:
            continue
        seen_destinations.add(destination)
        rows.append(
            _row_for_destination(
                destination,
                assignments_by_destination.get(destination, []),
                pulls_by_destination.get(destination),
                operation,
                mission,
                parking_by_tail,
            )
        )

    for destination in sorted(set(assignments_by_destination) - seen_destinations):
        rows.append(
            _row_for_destination(
                destination,
                assignments_by_destination.get(destination, []),
                pulls_by_destination.get(destination),
                operation,
                None,
                parking_by_tail,
            )
        )

    rows.sort(key=_row_sort_key)

    return {
        "operation": operation,
        "operation_window_minutes": getattr(operation, "window_minutes", None),
        "rows": rows,
        "pull_labels": PULL_KEYS,
    }


def _row_for_destination(
    destination,
    assignments,
    door_pull,
    operation,
    mission,
    parking_by_tail,
):
    timing_data = mission_display_timing_data(mission, operation) if mission else {}
    row_window = timing_data.get("effective_window_minutes", 0)
    planned_pulls = {}
    adjusted_pulls = {}
    actual_pulls = {}
    no_pulls = {}
    sort_pull = None

    for key, _label, planned_attr, actual_attr, no_attr in PULL_KEYS:
        base_value = getattr(mission, planned_attr, None) if mission else None
        adjusted_value = _adjusted_pull_value(timing_data, key) or base_value
        no_pull = bool(getattr(door_pull, no_attr, False))
        actual_value = None
        if not no_pull:
            actual_value = getattr(mission, actual_attr, None) if mission else None
            actual_value = actual_value or getattr(door_pull, actual_attr, None)

        planned_pulls[key] = _time_value(base_value)
        adjusted_pulls[key] = _time_value(adjusted_value)
        actual_pulls[key] = _time_value(actual_value)
        no_pulls[key] = no_pull
        if sort_pull is None:
            sort_pull = adjusted_value or base_value

    assigned_doors = _unique(
        assignment["door"] for assignment in assignments if assignment.get("door")
    )
    assignment_locations = _unique(
        assignment["location"] for assignment in assignments if assignment.get("location")
    )
    if not assigned_doors and door_pull:
        assigned_doors = [door_pull.door]

    return {
        "destination": destination,
        "flight_number": _text_value(getattr(mission, "flight_number", "")),
        "tail": _text_value(getattr(mission, "assigned_tail_number", "")),
        "parking": _parking_for_mission(mission, parking_by_tail),
        "status": _status_for_mission(mission),
        "etd": _time_value(getattr(mission, "planned_datetime_local", None)),
        "assigned_doors": assigned_doors,
        "assignment_locations": assignment_locations,
        "planned_pulls": planned_pulls,
        "adjusted_pulls": adjusted_pulls,
        "actual_pulls": actual_pulls,
        "no_pulls": no_pulls,
        "window_minutes": row_window,
        "has_window_adjustment": bool(row_window),
        "has_mission": bool(mission),
        "sort_pull": sort_pull,
        "sort_etd": getattr(mission, "planned_datetime_local", None),
    }


def _lineup_assignments_by_destination(gateway):
    assignments_by_destination = {}
    for row in get_building_lineup_rows(gateway):
        for field_name in DESTINATION_FIELDS:
            destination = normalize_destination(getattr(row, field_name, None))
            if not destination:
                continue
            slot_label = row.slot_labels.get(field_name, field_name.replace("_", " ").upper())
            assignments_by_destination.setdefault(destination, []).append(
                {
                    "door": row.belt_group_label,
                    "location": f"{row.belt_group_label} {slot_label}",
                }
            )
    return assignments_by_destination


def _door_pulls_by_destination(gateway, operation):
    query = NeoErmacDoorPull.query.filter_by(gateway_id=gateway.id)
    if operation:
        query = query.filter_by(sort_date_operation_id=operation.id)
    else:
        query = query.filter(NeoErmacDoorPull.sort_date_operation_id.is_(None))

    rows = query.order_by(NeoErmacDoorPull.updated_at.desc(), NeoErmacDoorPull.id.desc()).all()
    pulls_by_destination = {}
    for row in rows:
        destination = normalize_destination(row.destination)
        if not destination:
            continue
        existing = pulls_by_destination.get(destination)
        if existing is None or (_pull_has_data(row) and not _pull_has_data(existing)):
            pulls_by_destination[destination] = row
    return pulls_by_destination


def _parking_assignments_by_tail(operation):
    if not operation:
        return {}
    return {
        _text_value(assignment.tail_number): _text_value(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _text_value(assignment.tail_number) and _text_value(assignment.position_code)
    }


def _parking_for_mission(mission, parking_by_tail):
    if not mission:
        return ""
    tail = _text_value(getattr(mission, "assigned_tail_number", ""))
    if not tail:
        return ""
    return parking_by_tail.get(tail, "")


def _status_for_mission(mission):
    if not mission:
        return "NO MISSION"
    status = _text_value(getattr(mission, "departure_status", ""))
    if not status:
        return "SCHEDULED"
    return status.replace("_", " ")


def _departure_missions(operation):
    if not operation:
        return []

    return (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )


def _current_operation(gateway):
    return (
        SortDateOperation.query.filter(
            SortDateOperation.archived_at_utc.is_(None),
            or_(
                SortDateOperation.gateway_id == gateway.id,
                SortDateOperation.gateway_code == gateway.code,
            ),
        )
        .order_by(
            SortDateOperation.sort_date.desc(),
            SortDateOperation.generated_at_utc.desc(),
            SortDateOperation.id.desc(),
        )
        .first()
    )


def _adjusted_pull_value(timing_data, pull_key):
    return timing_data.get(
        {
            "pure": "adjusted_pure_pull_time",
            "first_mix": "adjusted_first_mix_pull_time",
            "second_mix": "adjusted_final_mix_pull_time",
        }[pull_key]
    )


def _pull_has_data(door_pull):
    return any(
        (
            door_pull.actual_pure_pull_time_local,
            door_pull.no_pure_pull,
            door_pull.actual_first_mix_pull_time_local,
            door_pull.no_first_mix_pull,
            door_pull.actual_second_mix_pull_time_local,
            door_pull.no_second_mix_pull,
        )
    )


def _row_sort_key(row):
    return (
        row["sort_pull"] is None,
        row["sort_pull"] or "",
        row["sort_etd"] is None,
        row["sort_etd"] or "",
        row["destination"],
        row["flight_number"],
    )


def _unique(values):
    unique_values = []
    seen = set()
    for value in values:
        value = _text_value(value)
        if not value or value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _text_value(value):
    return str(value or "").strip().upper()


def _time_value(value):
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)
