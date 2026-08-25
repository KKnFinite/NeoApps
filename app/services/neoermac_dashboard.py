import hashlib
import json
from datetime import datetime

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import (
    NeoErmacBuildingLineup,
    NeoErmacDoorPull,
    SortDateMission,
    SortDateParkingAssignment,
)
from app.services.neoermac_building_lineup import (
    get_building_lineup_assignments,
    load_building_lineup_rows,
    normalize_destination,
)
from app.services.neoermac_door_view import PULL_FIELDS
from app.services.neoermac_live_refresh import neoermac_live_refresh_status
from app.services.operation_scope import current_unarchived_operation
from app.services.sort_date_operations import mission_display_timing_data


SIDE_LIMIT = 5
EAST_MAX_DOOR = 17
WEST_MIN_DOOR = 21
_OPERATION_UNSET = object()


def neoermac_dashboard_context(
    gateway,
    *,
    operation=_OPERATION_UNSET,
    refresh_status=None,
    initialize_lineup=True,
):
    if operation is _OPERATION_UNSET:
        operation = _current_operation(gateway)
    if not operation:
        return {
            "operation": None,
            "has_current_sort": False,
            "refresh_status": refresh_status
            or neoermac_live_refresh_status(gateway),
            "east": [],
            "west": [],
        }

    lineup_load = load_building_lineup_rows(
        gateway,
        initialize=initialize_lineup,
    )
    assignments_by_destination = _lineup_assignments_by_destination(
        gateway,
        initialize=initialize_lineup,
        lineup_rows=lineup_load.rows,
    )
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
                if planned_time is None:
                    continue
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
        "refresh_status": refresh_status
        or neoermac_live_refresh_status(gateway),
        "east": rows["east"],
        "west": rows["west"],
        "_initialization_changed": lineup_load.persistent_state_changed,
    }


def current_upcoming_pulls_operation(gateway):
    """Resolve the operation without constructing Upcoming Pulls board state."""
    return _current_operation(gateway)


def upcoming_pulls_refresh_status(gateway, *, operation=None):
    """Resolve current-board status without re-querying the operation set."""
    return neoermac_live_refresh_status(gateway)


def upcoming_pulls_revision(gateway, *, operation=_OPERATION_UNSET):
    """Return a compact fingerprint for persisted Upcoming Pulls inputs."""
    if operation is _OPERATION_UNSET:
        operation = _current_operation(gateway)

    operation_id = operation.id if operation else None
    operation_criterion = lambda model: (
        model.sort_date_operation_id == operation_id
        if operation_id is not None
        else model.sort_date_operation_id.is_(None)
    )
    aggregate_queries = (
        _revision_aggregate(
            "lineup",
            NeoErmacBuildingLineup,
            NeoErmacBuildingLineup.updated_at,
            NeoErmacBuildingLineup.gateway_id == gateway.id,
        ),
        _revision_aggregate(
            "departure_missions",
            SortDateMission,
            SortDateMission.updated_at,
            operation_criterion(SortDateMission),
            SortDateMission.mission_type == "departure",
        ),
        _revision_aggregate(
            "door_pulls",
            NeoErmacDoorPull,
            NeoErmacDoorPull.updated_at,
            NeoErmacDoorPull.gateway_id == gateway.id,
            operation_criterion(NeoErmacDoorPull),
        ),
        _revision_aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            operation_criterion(SortDateParkingAssignment),
        ),
    )
    rows = sorted(
        db.session.execute(union_all(*aggregate_queries)).all(),
        key=lambda row: row.source,
    )
    payload = {
        "gateway_id": gateway.id,
        "operation_id": operation_id,
        "operation_updated_at": _revision_value(
            getattr(operation, "updated_at", None)
        ),
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
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _revision_aggregate(source, model, timestamp_column, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        func.max(timestamp_column).label("latest_updated_at"),
    ).where(*criteria)


def _revision_value(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")


def _lineup_assignments_by_destination(
    gateway,
    *,
    initialize=True,
    lineup_rows=None,
):
    assignments_by_destination = {}
    assignment_index = {}

    for slot in get_building_lineup_assignments(
        gateway,
        initialize=initialize,
        rows=lineup_rows,
    ):
        destination = slot["destination"]
        primary_door = slot["supervising_door"]
        side = _side_for_door(primary_door)
        if not side:
            continue

        assignment_key = (destination, side)
        assignment = assignment_index.get(assignment_key)
        if not assignment:
            assignment = {
                "door": "",
                "location": "",
                "locations": [],
                "side": side,
                "required_doors": [],
            }
            assignment_index[assignment_key] = assignment
            assignments_by_destination.setdefault(destination, []).append(assignment)
        if primary_door not in assignment["required_doors"]:
            assignment["required_doors"].append(primary_door)
        location = _dashboard_belt_label(slot["display_label"])
        if location not in assignment["locations"]:
            assignment["locations"].append(location)

    for assignments in assignments_by_destination.values():
        for assignment in assignments:
            assignment["required_doors"] = tuple(assignment["required_doors"])
            assignment["door"] = "/".join(assignment["required_doors"])
            assignment["location"] = " / ".join(assignment.pop("locations"))

    return assignments_by_destination


def _dashboard_belt_label(slot_label):
    return str(slot_label or "").strip().upper()


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
    return current_unarchived_operation(gateway)


def _planned_pull_time(mission, operation, pull_key):
    timing_data = mission_display_timing_data(mission, operation)
    adjusted_key = {
        "pure": "adjusted_pure_pull_time",
        "mix": "adjusted_mix_pull_time",
    }[pull_key]
    planned_attr = {
        "pure": "pure_pull_time_local",
        "mix": "mix_pull_time_local",
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
