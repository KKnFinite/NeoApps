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
    get_outbound_door_options,
    normalize_destination,
)
from app.services.neoermac_door_view import PULL_FIELDS
from app.services.node_refresh import node_auto_refresh_status
from app.services.sort_date_operations import mission_display_timing_data


SIDE_LIMIT = 5
EAST_MAX_DOOR = 17
WEST_MIN_DOOR = 21


def neoermac_dashboard_context(gateway):
    operation = _current_operation(gateway)
    if not operation:
        return {
            "operation": None,
            "has_current_sort": False,
            "refresh_status": node_auto_refresh_status(gateway),
            "east": [],
            "west": [],
        }

    assignments_by_destination = _lineup_assignments_by_destination(gateway)
    door_pulls_by_destination = _door_pulls_by_destination(gateway, operation)
    missions = _departure_missions(operation)
    parking_by_tail = _parking_assignments_by_tail(operation)
    rows = {"east": [], "west": []}

    for mission in missions:
        destination = normalize_destination(mission.destination)
        if not destination:
            continue
        tail = _text_value(mission.assigned_tail_number)
        parking = _parking_for_tail(parking_by_tail, tail)

        for assignment in assignments_by_destination.get(destination, []):
            side = assignment["side"]
            if side not in rows:
                continue
            required_doors = assignment["required_doors"]
            related_door_pulls = [
                door_pull
                for door_pull in door_pulls_by_destination.get(destination, [])
                if door_pull.door in required_doors
            ]

            for pull_order, pull_field in enumerate(PULL_FIELDS):
                planned_time = _planned_pull_time(mission, operation, pull_field["key"])
                if _pull_is_complete(mission, related_door_pulls, pull_field, required_doors):
                    continue
                rows[side].append(
                    {
                        "planned_time": _time_value(planned_time),
                        "planned_sort": planned_time,
                        "pull_type": pull_field["label"],
                        "pull_order": pull_order,
                        "destination": destination,
                        "tail": tail or "-",
                        "parking": parking or "-",
                        "location": assignment["location"],
                        "door": assignment["door"],
                        "etd_sort": mission.planned_datetime_local,
                    }
                )

    for side in rows:
        rows[side].sort(key=_pull_sort_key)
        rows[side] = rows[side][:SIDE_LIMIT]

    return {
        "operation": operation,
        "has_current_sort": True,
        "refresh_status": node_auto_refresh_status(gateway),
        "east": rows["east"],
        "west": rows["west"],
    }


def _lineup_assignments_by_destination(gateway):
    assignments_by_destination = {}
    assignment_index = {}

    for row in get_building_lineup_rows(gateway):
        for field_name in DESTINATION_FIELDS:
            destination = normalize_destination(getattr(row, field_name, None))
            if not destination:
                continue

            primary_door = row.door_start if field_name.startswith("east_") else row.door_end
            side = _side_for_door(primary_door)
            if not side:
                continue

            slot_label = row.slot_labels.get(field_name, field_name.replace("_", " ").upper())
            location = f"{row.belt_group_label} {_dashboard_belt_label(slot_label)}"
            assignment_key = (destination, side, location)
            assignment = assignment_index.get(assignment_key)
            if not assignment:
                assignment = {
                    "door": row.belt_group_label,
                    "location": location,
                    "side": side,
                    "required_doors": [],
                }
                assignment_index[assignment_key] = assignment
                assignments_by_destination.setdefault(destination, []).append(assignment)
            if primary_door not in assignment["required_doors"]:
                assignment["required_doors"].append(primary_door)

    for assignments in assignments_by_destination.values():
        for assignment in assignments:
            assignment["required_doors"] = tuple(assignment["required_doors"])

    return assignments_by_destination


def _dashboard_belt_label(slot_label):
    parts = str(slot_label or "").strip().upper().split()
    if parts and parts[0] in {"EAST", "WEST"}:
        parts = parts[1:]
    return " ".join(parts)


def _door_pulls_by_destination(gateway, operation):
    query = NeoErmacDoorPull.query.filter_by(gateway_id=gateway.id)
    if operation:
        query = query.filter_by(sort_date_operation_id=operation.id)
    else:
        query = query.filter(NeoErmacDoorPull.sort_date_operation_id.is_(None))

    rows = query.order_by(NeoErmacDoorPull.updated_at.desc(), NeoErmacDoorPull.id.desc()).all()
    door_pulls_by_destination = {}
    for row in rows:
        destination = normalize_destination(row.destination)
        if not destination:
            continue
        door_pulls_by_destination.setdefault(destination, []).append(row)
    return door_pulls_by_destination


def _departure_missions(operation):
    return (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )


def _parking_assignments_by_tail(operation):
    return {
        _text_value(assignment.tail_number): assignment.position_code
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _text_value(assignment.tail_number) and assignment.position_code
    }


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


def _planned_pull_time(mission, operation, pull_key):
    timing_data = mission_display_timing_data(mission, operation)
    adjusted_key = {
        "pure": "adjusted_pure_pull_time",
        "first_mix": "adjusted_first_mix_pull_time",
        "second_mix": "adjusted_final_mix_pull_time",
    }[pull_key]
    planned_attr = {
        "pure": "pure_pull_time_local",
        "first_mix": "first_mix_pull_time_local",
        "second_mix": "final_mix_pull_time_local",
    }[pull_key]
    return timing_data.get(adjusted_key) or getattr(mission, planned_attr, None)


def _pull_is_complete(mission, door_pulls, pull_field, required_doors=()):
    actual_attr = pull_field["actual_attr"]
    no_attr = pull_field["no_attr"]
    if required_doors:
        pulls_by_door = {door_pull.door: door_pull for door_pull in door_pulls}
        for door in required_doors:
            door_pull = pulls_by_door.get(door)
            if not door_pull:
                return False
            if not (
                getattr(door_pull, actual_attr, None)
                or getattr(door_pull, no_attr, False)
            ):
                return False
        return True

    if getattr(mission, actual_attr, None):
        return True
    return any(
        getattr(door_pull, actual_attr, None) or getattr(door_pull, no_attr, False)
        for door_pull in door_pulls
    )


def _pull_sort_key(row):
    return (
        row["planned_sort"] is None,
        row["planned_sort"] or "",
        row["etd_sort"] is None,
        row["etd_sort"] or "",
        row["destination"],
        row["tail"],
        row["parking"],
        row["pull_order"],
    )


def _parking_for_tail(parking_by_tail, tail):
    if not tail:
        return ""
    return _text_value(parking_by_tail.get(tail))


def _side_for_door(door):
    number = _door_number(door)
    if number is None:
        return None
    if 1 <= number <= EAST_MAX_DOOR:
        return "east"
    if WEST_MIN_DOOR <= number <= 37:
        return "west"
    return None


def _door_number(door):
    value = str(door or "").strip().upper()
    if not value:
        return None
    if value.startswith("D"):
        value = value[1:]
    if not value.isdigit():
        return None
    return int(value)


def _time_value(value):
    if not value:
        return "--"
    return value.strftime("%H:%M")


def _text_value(value):
    return str(value or "").strip().upper()
