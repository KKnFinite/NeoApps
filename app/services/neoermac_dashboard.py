from sqlalchemy import or_

from app.models import NeoErmacDoorPull, SortDateMission, SortDateOperation
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    get_outbound_door_options,
    normalize_destination,
)
from app.services.neoermac_door_view import PULL_FIELDS
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
            "east": [],
            "west": [],
        }

    assignments_by_destination = _lineup_assignments_by_destination(gateway)
    door_pulls_by_destination = _door_pulls_by_destination(gateway, operation)
    rows = {"east": [], "west": []}

    for mission in _departure_missions(operation):
        destination = normalize_destination(mission.destination)
        if not destination:
            continue

        for assignment in assignments_by_destination.get(destination, []):
            side = assignment["side"]
            if side not in rows:
                continue
            related_door_pulls = [
                door_pull
                for door_pull in door_pulls_by_destination.get(destination, [])
                if door_pull.door in assignment["candidate_doors"]
            ]

            for pull_order, pull_field in enumerate(PULL_FIELDS):
                planned_time = _planned_pull_time(mission, operation, pull_field["key"])
                if _pull_is_complete(mission, related_door_pulls, pull_field):
                    continue
                rows[side].append(
                    {
                        "planned_time": _time_value(planned_time),
                        "planned_sort": planned_time,
                        "pull_type": pull_field["label"],
                        "pull_order": pull_order,
                        "flight_number": _text_value(mission.flight_number),
                        "destination": destination,
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
        "east": rows["east"],
        "west": rows["west"],
    }


def _lineup_assignments_by_destination(gateway):
    assignments_by_destination = {}
    seen_assignments = set()
    real_doors = get_outbound_door_options()
    real_door_numbers = {door: _door_number(door) for door in real_doors}

    for row in get_building_lineup_rows(gateway):
        start_number = _door_number(row.door_start)
        end_number = _door_number(row.door_end)
        if start_number is None or end_number is None:
            continue
        low, high = sorted((start_number, end_number))
        candidate_doors = tuple(
            door
            for door, door_number in real_door_numbers.items()
            if door_number is not None and low <= door_number <= high
        )

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
            if assignment_key in seen_assignments:
                continue
            seen_assignments.add(assignment_key)
            assignments_by_destination.setdefault(destination, []).append(
                {
                    "door": row.belt_group_label,
                    "location": location,
                    "side": side,
                    "candidate_doors": candidate_doors,
                }
            )

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


def _pull_is_complete(mission, door_pulls, pull_field):
    actual_attr = pull_field["actual_attr"]
    no_attr = pull_field["no_attr"]
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
        row["flight_number"],
        row["destination"],
        row["pull_order"],
    )


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
