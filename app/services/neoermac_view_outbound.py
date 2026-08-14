import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import (
    NeoErmacBuildingLineup,
    NeoErmacDoorPull,
    SortDateGoogleMissionLink,
    SortDateMission,
    SortDateParkingAssignment,
    SortTimelineSettings,
    SortTimelineSortSetting,
)
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    gateway_timezone,
)
from app.services.neoermac_building_lineup import (
    get_building_lineup_assignments,
    normalize_destination,
)
from app.services.node_refresh import sort_window_auto_refresh_status
from app.services.operation_scope import current_unarchived_operation
from app.services.neoermac_tail_presence import (
    arrival_presence_by_tail,
    departure_tail_presence,
    normalize_tail_number,
    tail_presence_status_override,
)
from app.services.sort_date_operations import mission_display_timing_data


PULL_KEYS = (
    ("pure", "Pure", "pure_pull_time_local", "actual_pure_pull_time_local", "no_pure_pull"),
    (
        "mix",
        "Mix Pull",
        "mix_pull_time_local",
        "actual_mix_pull_time_local",
        "no_mix_pull",
    ),
)
_OPERATION_UNSET = object()


def view_outbound_context(
    gateway,
    *,
    operation=_OPERATION_UNSET,
    refresh_status=None,
    initialize_lineup=True,
):
    if operation is _OPERATION_UNSET:
        operation = _current_operation(gateway)
    assignments_by_destination = _lineup_assignments_by_destination(
        gateway,
        initialize=initialize_lineup,
    )
    pulls_by_destination = _door_pulls_by_destination(gateway, operation)
    parking_by_tail = _parking_assignments_by_tail(operation)
    arrivals_by_tail = arrival_presence_by_tail(operation)
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
                pulls_by_destination.get(destination, []),
                operation,
                mission,
                parking_by_tail,
                arrivals_by_tail,
            )
        )

    for destination in sorted(set(assignments_by_destination) - seen_destinations):
        rows.append(
            _row_for_destination(
                destination,
                assignments_by_destination.get(destination, []),
                pulls_by_destination.get(destination, []),
                operation,
                None,
                parking_by_tail,
                arrivals_by_tail,
            )
        )

    rows.sort(key=_row_sort_key)

    return {
        "operation": operation,
        "operation_window_minutes": getattr(operation, "window_minutes", None),
        "refresh_status": refresh_status
        or sort_window_auto_refresh_status(gateway),
        "rows": rows,
        "pull_labels": PULL_KEYS,
    }


def current_view_outbound_operation(gateway):
    """Resolve the operation without loading View Outbound row state."""
    return _current_operation(gateway)


def view_outbound_refresh_status(gateway, operation=None):
    return sort_window_auto_refresh_status(gateway, operation=operation)


def view_outbound_revision(gateway, *, operation=_OPERATION_UNSET, now=None):
    """Return a compact fingerprint for every persisted View Outbound input."""
    if operation is _OPERATION_UNSET:
        operation = _current_operation(gateway)
    local_now = current_gateway_local_datetime(gateway, now=now)
    try:
        now_utc = (
            local_now.replace(tzinfo=ZoneInfo(gateway_timezone(gateway)))
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    except ZoneInfoNotFoundError:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
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
            "missions",
            SortDateMission,
            SortDateMission.updated_at,
            operation_criterion(SortDateMission),
        ),
        _revision_aggregate(
            "assumed_arrived_missions",
            SortDateMission,
            SortDateMission.updated_at,
            operation_criterion(SortDateMission),
            SortDateMission.mission_type == "arrival",
            SortDateMission.api_assumed_arrived_time_utc.is_not(None),
            SortDateMission.api_assumed_arrived_time_utc <= now_utc,
        ),
        _revision_aggregate(
            "google_mission_links",
            SortDateGoogleMissionLink,
            SortDateGoogleMissionLink.updated_at,
            operation_criterion(SortDateGoogleMissionLink),
        ),
        _revision_aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            operation_criterion(SortDateParkingAssignment),
        ),
        _revision_aggregate(
            "door_pulls",
            NeoErmacDoorPull,
            NeoErmacDoorPull.updated_at,
            NeoErmacDoorPull.gateway_id == gateway.id,
            operation_criterion(NeoErmacDoorPull),
        ),
        _revision_aggregate(
            "timeline_settings",
            SortTimelineSettings,
            SortTimelineSettings.updated_at,
            SortTimelineSettings.gateway_id == gateway.id,
        ),
        _revision_aggregate(
            "sort_windows",
            SortTimelineSortSetting,
            SortTimelineSortSetting.updated_at,
            SortTimelineSortSetting.gateway_id == gateway.id,
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


def _row_for_destination(
    destination,
    assignments,
    door_pulls,
    operation,
    mission,
    parking_by_tail,
    arrivals_by_tail,
):
    timing_data = mission_display_timing_data(mission, operation) if mission else {}
    row_window = timing_data.get("effective_window_minutes")
    planned_pulls = {}
    adjusted_pulls = {}
    actual_pulls = {}
    no_pulls = {}
    sort_pull = None
    assigned_doors = _unique(
        assignment["door"] for assignment in assignments if assignment.get("door")
    )
    pulls_by_door = {door_pull.door: door_pull for door_pull in door_pulls}

    for key, _label, planned_attr, actual_attr, no_attr in PULL_KEYS:
        base_value = getattr(mission, planned_attr, None) if mission else None
        adjusted_value = _adjusted_pull_value(timing_data, key) or base_value
        actual_value = getattr(mission, actual_attr, None) if mission else None
        if actual_value is None:
            actual_values = [
                getattr(door_pull, actual_attr, None)
                for door_pull in door_pulls
                if not getattr(door_pull, no_attr, False)
                and getattr(door_pull, actual_attr, None) is not None
            ]
            actual_value = max(actual_values) if actual_values else None
        no_pull = bool(assigned_doors) and all(
            pulls_by_door.get(door) is not None
            and bool(getattr(pulls_by_door[door], no_attr, False))
            for door in assigned_doors
        )
        if actual_value is not None:
            no_pull = False

        planned_pulls[key] = _time_value(base_value)
        adjusted_pulls[key] = _time_value(adjusted_value)
        actual_pulls[key] = _time_value(actual_value)
        no_pulls[key] = no_pull
        if sort_pull is None:
            sort_pull = adjusted_value or base_value

    assignment_locations = _unique(
        assignment["location"] for assignment in assignments if assignment.get("location")
    )
    if not assigned_doors and door_pulls:
        assigned_doors = _unique(door_pull.door for door_pull in door_pulls)
    tail_presence = departure_tail_presence(mission, arrivals_by_tail) if mission else None

    return {
        "destination": destination,
        "flight_number": _text_value(getattr(mission, "flight_number", "")),
        "tail": _text_value(getattr(mission, "assigned_tail_number", "")),
        "parking": _parking_for_mission(mission, parking_by_tail),
        "status": _status_for_mission(mission, tail_presence),
        "tail_presence": tail_presence,
        "etd": _time_value(
            timing_data.get("adjusted_planned_departure_time")
            or getattr(mission, "planned_datetime_local", None)
        ),
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
        "sort_etd": (
            timing_data.get("adjusted_planned_departure_time")
            or getattr(mission, "planned_datetime_local", None)
        ),
    }


def _lineup_assignments_by_destination(gateway, *, initialize=True):
    assignments_by_destination = {}
    for slot in get_building_lineup_assignments(gateway, initialize=initialize):
        assignments_by_destination.setdefault(slot["destination"], []).append(
            {
                "door": slot["supervising_door"],
                "location": slot["display_label"],
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
        pulls_by_destination.setdefault(destination, []).append(row)
    return pulls_by_destination


def _parking_assignments_by_tail(operation):
    if not operation:
        return {}
    return {
        normalize_tail_number(assignment.tail_number): _text_value(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _text_value(assignment.tail_number) and _text_value(assignment.position_code)
    }


def _parking_for_mission(mission, parking_by_tail):
    if not mission:
        return ""
    tail = normalize_tail_number(getattr(mission, "assigned_tail_number", ""))
    if not tail:
        return ""
    return parking_by_tail.get(tail, "")


def _status_for_mission(mission, tail_presence=None):
    if not mission:
        return "NO MISSION"
    override = tail_presence_status_override(mission, tail_presence)
    if override:
        return override
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
    return current_unarchived_operation(gateway)


def _adjusted_pull_value(timing_data, pull_key):
    return timing_data.get(
        {
            "pure": "adjusted_pure_pull_time",
            "mix": "adjusted_mix_pull_time",
        }[pull_key]
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
